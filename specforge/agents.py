"""The agent chain:

Document Reader -> Scope Analyst -> Specification Analyst -> Requirements Extractor (per module)
-> Coverage Sweep -> Reviewer -> Refiner -> Access Analyst -> Use Case Writer -> Writer.

Nothing here is domain-specific: modules, roles and search queries are all derived from the uploaded documents.
The prompt texts live in prompts/runtime/ (see specforge/prompts.py).
"""
import json
import logging
import re
import unicodedata

from . import prompts
from .config import Settings
from .ingest import Chunk
from .llm import LLM, LLMError
from .models import (AccessModel, Module, OpenQuestion, ProjectContext, Requirement, RequirementList, Review,
                     Specification, Summary, UseCase, UseCaseList)
from .retriever import HybridRetriever

log = logging.getLogger(__name__)


def format_chunks(chunks: list[Chunk]) -> str:
    return "\n\n".join(f"[{c.id}] ({c.cite()})\n{c.text}" for c in chunks)


def _compact(items, fields: tuple[str, ...]) -> str:
    return json.dumps([i.model_dump(include=set(fields)) for i in items], indent=1, ensure_ascii=False)


def _words(chunks: list[Chunk]) -> int:
    return sum(len(c.text.split()) for c in chunks)


def _batches(chunks: list[Chunk], max_words: int) -> list[list[Chunk]]:
    batches, current = [], []
    for c in chunks:
        if current and _words(current) + len(c.text.split()) > max_words:
            batches.append(current)
            current = []
        current.append(c)
    return batches + [current] if current else batches


def _shared() -> dict[str, str]:
    return {"grounding": prompts.text("grounding")}


# --- Agent 1: Document Reader ------------------------------------------------------------------

def document_reader(llm: LLM, chunks: list[Chunk], settings: Settings) -> str:
    """Returns material for the analysts: the full text when it fits the context budget, otherwise digests."""
    if _words(chunks) <= settings.full_context_words:
        log.info(f"Reading all {len(chunks)} excerpts ({_words(chunks):,} words) in full")
        return "SOURCE EXCERPTS:\n" + format_chunks(chunks)
    parts = _batches(chunks, settings.full_context_words // 2)
    digests = []
    for i, part in enumerate(parts, 1):
        log.info(f"Reading part {i}/{len(parts)} ({_words(part):,} words)")
        digests.append(f"--- Notes on part {i} ---\n" + llm.complete(prompts.render(
            "document_reader", part=i, parts=len(parts), excerpts=format_chunks(part), **_shared())))
    return "NOTES FROM READING THE SOURCE DOCUMENTS:\n" + "\n\n".join(digests)


# --- Agent 2: Scope Analyst --------------------------------------------------------------------

def scope_analyst(llm: LLM, material: str, title: str | None) -> ProjectContext:
    hint = f'\nThe project is called "{title}"; use that as the title.\n' if title else ""
    ctx = llm.complete_json(prompts.render("scope_analyst", title_hint=hint, material=material, **_shared()),
                            ProjectContext)
    if title:
        ctx.title = title
    seen: set[str] = set()
    unique = []
    for module in ctx.modules:
        key = module.name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            module.name = module.name.strip()
            unique.append(module)
    ctx.modules = unique
    if not ctx.modules:
        raise LLMError("the scope analysis did not identify any modules")
    return ctx


# --- Agent 3: Specification Analyst ------------------------------------------------------------

def specification_analyst(llm: LLM, material: str, ctx: ProjectContext) -> Specification:
    return llm.complete_json(prompts.render("specification_analyst", title=ctx.title, material=material, **_shared()),
                             Specification)


# --- Agent 4: Requirements Extractor (one run per module) --------------------------------------

def requirements_extractor(llm: LLM, retriever: HybridRetriever, settings: Settings,
                           ctx: ProjectContext) -> list[Requirement]:
    all_reqs: list[Requirement] = []
    for module in ctx.modules:
        queries = module.search_queries or [module.name]
        chunks = retriever.search([f"{module.name}: {module.description}", *queries],
                                  settings.top_k, settings.max_context_chunks)
        log.info(f"Extracting {module.name} from {len(chunks)} excerpts")
        others = ", ".join(m.name for m in ctx.modules if m is not module) or "none"
        result = llm.complete_json(prompts.render(
            "requirements_extractor", module=module.name, title=ctx.title, description=module.description,
            others=others, fields=prompts.text("requirement_fields"), priority_rules=prompts.text("priority_rules"),
            excerpts=format_chunks(chunks), **_shared()), RequirementList)
        for req in result.requirements:
            req.module = module.name
        log.info(f"{module.name}: {len(result.requirements)} requirements")
        all_reqs += result.requirements
    return all_reqs


# --- Agent 5: Coverage Sweep -------------------------------------------------------------------

GENERAL_MODULE = "General"


def coverage_sweep(llm: LLM, retriever: HybridRetriever, settings: Settings, ctx: ProjectContext,
                   reqs: list[Requirement]) -> list[Requirement]:
    cited = {s for r in reqs for s in r.sources}
    uncited = [c for c in retriever.chunks if c.id not in cited]
    if not uncited:
        log.info("Every excerpt is already cited")
        return reqs
    names = {m.name.lower(): m.name for m in ctx.modules}
    existing = "\n".join(f"- [{r.module}] {r.title}: {r.description}" for r in reqs) or "(none)"
    found: list[Requirement] = []
    batches = _batches(uncited, settings.sweep_batch_words)
    for i, batch in enumerate(batches, 1):
        log.info(f"Checking {len(batch)} uncited excerpts for missed requirements ({i}/{len(batches)})")
        result = llm.complete_json(prompts.render(
            "coverage_sweep", title=ctx.title, modules=", ".join(names.values()),
            fields=prompts.text("requirement_fields"), priority_rules=prompts.text("priority_rules"),
            existing=existing, excerpts=format_chunks(batch), **_shared()), RequirementList)
        for req in result.requirements:
            req.module = names.get(req.module.strip().lower(), GENERAL_MODULE)
        found += result.requirements
    if any(r.module == GENERAL_MODULE for r in found) and GENERAL_MODULE.lower() not in names:
        ctx.modules.append(Module(name=GENERAL_MODULE, description="Requirements that fit no other module."))
    log.info(f"Coverage sweep found {len(found)} additional requirements")
    return reqs + found


# --- Numbering and citation checks -------------------------------------------------------------

def known_sources(sources: list[str], known: set[str]) -> list[str]:
    return [s for s in dict.fromkeys(sources) if s in known]


def _keep_valid_sources(req: Requirement, known: set[str]) -> None:
    valid = known_sources(req.sources, known)
    if len(valid) < len(req.sources):
        log.warning(f"{req.id}: dropped citations to unknown excerpts {sorted(set(req.sources) - known)}")
    req.sources = valid
    if not valid and not any("No traceable source" in n for n in req.notes):
        req.notes.append("No traceable source; confirm with stakeholders.")


def number_requirements(ctx: ProjectContext, reqs: list[Requirement], known: set[str]) -> list[Requirement]:
    """Orders requirements by module and numbers them FR-001, FR-002, ..."""
    ordered = [r for m in ctx.modules for r in reqs if r.module == m.name]
    for n, req in enumerate(ordered, 1):
        req.id = f"FR-{n:03d}"
        _keep_valid_sources(req, known)
    return ordered


# --- Agent 6: Reviewer -------------------------------------------------------------------------

def reviewer(llm: LLM, ctx: ProjectContext, reqs: list[Requirement]) -> Review:
    fields = ("id", "module", "title", "description", "business_rules", "acceptance_criteria", "sources")
    review = llm.complete_json(prompts.render("reviewer", title=ctx.title, requirements=_compact(reqs, fields)),
                               Review)
    ids = {r.id for r in reqs}
    review.issues = [i for i in review.issues if i.req_id in ids]
    review.merges = [m for m in review.merges if m.keep in ids]
    return review


def apply_merges(reqs: list[Requirement], review: Review) -> list[Requirement]:
    by_id = {r.id: r for r in reqs}
    dropped: set[str] = set()
    for merge in review.merges:
        if merge.keep in dropped:
            continue
        keep = by_id[merge.keep]
        for rid in merge.drop:
            if rid in by_id and rid != merge.keep and rid not in dropped:
                keep.sources = list(dict.fromkeys(keep.sources + by_id[rid].sources))
                dropped.add(rid)
    if dropped:
        log.info(f"Merged away duplicates: {', '.join(sorted(dropped))}")
    return [r for r in reqs if r.id not in dropped]


# --- Agent 7: Refiner --------------------------------------------------------------------------

REFINE_BATCH = 12


def refiner(llm: LLM, retriever: HybridRetriever, settings: Settings,
            reqs: list[Requirement], review: Review) -> list[Requirement]:
    notes: dict[str, list[str]] = {}
    for issue in review.issues:
        notes.setdefault(issue.req_id, []).append(f"{issue.type}: {issue.note}")
    flagged = [r for r in reqs if r.id in notes]
    if not flagged:
        return reqs
    by_id = {r.id: r for r in reqs}
    known = set(retriever.by_id)
    for start in range(0, len(flagged), REFINE_BATCH):
        batch = flagged[start:start + REFINE_BATCH]
        log.info(f"Refining {len(batch)} flagged requirements")
        cited = [retriever.by_id[s] for r in batch for s in r.sources]
        related = retriever.search([r.description for r in batch], 2, settings.max_context_chunks)
        chunks = sorted({c.id: c for c in cited + related}.values(), key=lambda c: c.id)
        payload = [{**r.model_dump(exclude={"module", "notes"}), "reviewer_notes": notes[r.id]} for r in batch]
        result = llm.complete_json(prompts.render(
            "refiner", priority_rules=prompts.text("priority_rules"),
            flagged=json.dumps(payload, indent=1, ensure_ascii=False), excerpts=format_chunks(chunks), **_shared()),
            RequirementList)
        batch_ids = {r.id for r in batch}
        for new in result.requirements:
            if new.id not in batch_ids:
                continue
            old = by_id[new.id]
            new.module = old.module
            new.notes = old.notes + new.notes
            new.sources = new.sources or old.sources
            _keep_valid_sources(new, known)
            by_id[new.id] = new
    return [by_id[r.id] for r in reqs]


# --- Agent 8: Access Analyst -------------------------------------------------------------------

ACCESS_QUERIES = ["access rights permissions authorization", "approve approval sign off",
                  "administrator manage users", "view edit delete restricted"]


def access_analyst(llm: LLM, retriever: HybridRetriever, settings: Settings, ctx: ProjectContext,
                   reqs: list[Requirement]) -> AccessModel:
    queries = [f"{r.role} access permissions" for r in ctx.roles] + ACCESS_QUERIES
    chunks = retriever.search(queries, settings.top_k, settings.max_context_chunks)
    log.info(f"Defining role-based access for {len(ctx.roles)} roles from {len(chunks)} excerpts")
    model = llm.complete_json(prompts.render(
        "access_analyst", title=ctx.title,
        roles=_compact(ctx.roles, ("role", "description", "responsibilities", "access")),
        requirements=_compact(reqs, ("id", "module", "title", "description", "actor", "business_rules")),
        priority_rules=prompts.text("priority_rules"), excerpts=format_chunks(chunks), **_shared()), AccessModel)
    known = set(retriever.by_id)
    for item in [*model.access_requirements, *model.matrix]:
        item.sources = known_sources(item.sources, known)
    return model


# --- Agent 9: Use Case Writer ------------------------------------------------------------------

USE_CASE_BATCH = 12
USE_CASE_EXCERPTS = 24


def _module_batches(reqs: list[Requirement], size: int) -> list[list[Requirement]]:
    """Packs whole modules into batches of about `size` requirements, so a use case never spans two calls."""
    batches, current = [], []
    for module in dict.fromkeys(r.module for r in reqs):
        group = [r for r in reqs if r.module == module]
        if current and len(current) + len(group) > size:
            batches.append(current)
            current = []
        current += group
    return batches + [current] if current else batches


def use_case_writer(llm: LLM, retriever: HybridRetriever, ctx: ProjectContext, spec: Specification,
                    reqs: list[Requirement]) -> list[UseCase]:
    roles = ", ".join(r.role for r in ctx.roles) or "not stated"
    statuses = "; ".join(f"{s.status}: {s.description}".rstrip(": ") for s in spec.statuses) or "none stated"
    by_id = {r.id: r for r in reqs}
    known = set(retriever.by_id)
    use_cases: list[UseCase] = []
    batches = _module_batches(reqs, USE_CASE_BATCH)
    for i, batch in enumerate(batches, 1):
        log.info(f"Writing use cases for {len(batch)} requirements ({i}/{len(batches)})")
        cited = list(dict.fromkeys(s for r in batch for s in r.sources))[:USE_CASE_EXCERPTS]
        result = llm.complete_json(prompts.render(
            "use_case_writer", title=ctx.title, roles=roles, statuses=statuses,
            requirements=_compact(batch, ("id", "module", "title", "description", "actor", "inputs", "outputs",
                                          "business_rules", "acceptance_criteria", "sources")),
            excerpts=format_chunks([retriever.by_id[s] for s in cited]), **_shared()), UseCaseList)
        batch_ids = {r.id for r in batch}
        for uc in result.use_cases:
            uc.requirements = [rid for rid in dict.fromkeys(uc.requirements) if rid in batch_ids]
            if not uc.requirements:
                continue
            uc.module = uc.module if uc.module in {r.module for r in batch} else by_id[uc.requirements[0]].module
            linked = [s for rid in uc.requirements for s in by_id[rid].sources]
            uc.sources = known_sources(uc.sources + linked, known)
            use_cases.append(uc)
    covered = {rid for uc in use_cases for rid in uc.requirements}
    missing = [r.id for r in reqs if r.id not in covered]
    if missing:
        log.warning(f"Requirements without a use case: {', '.join(missing)}")
    log.info(f"{len(use_cases)} use cases")
    return use_cases


# --- Agent 10: Writer --------------------------------------------------------------------------

def writer(llm: LLM, ctx: ProjectContext, reqs: list[Requirement]) -> Summary:
    grouped: dict[str, list[str]] = {m.name: [] for m in ctx.modules}
    for r in reqs:
        grouped.setdefault(r.module, []).append(f"{r.title}: {r.description}")
    return llm.complete_json(prompts.render(
        "writer", title=ctx.title, purpose=ctx.purpose, scope="; ".join(ctx.scope_in) or "not stated",
        requirements=json.dumps(grouped, indent=1, ensure_ascii=False)), Summary)


# --- Identifiers and open questions ------------------------------------------------------------

_STOP_WORDS = set("a an the of to for in on or and be is are should what which when does do can by with from per it "
                  "its this that than vs versus".split())


def _content_words(text: str) -> set[str]:
    text = re.sub(r"[‐-―-]", " ", unicodedata.normalize("NFKC", text).lower())
    return {w for w in re.findall(r"[a-z0-9]+", text) if w not in _STOP_WORDS and not re.fullmatch(r"(fr|d\d+)\d*", w)}


def _same_question(a: set[str], b: set[str]) -> bool:
    # Agents phrase the same open issue differently; a large shared core of content words means one question.
    shared = len(a & b)
    return shared >= 3 and shared / min(len(a), len(b)) >= 0.6


def assign_ids(spec: Specification, access: AccessModel, use_cases: list[UseCase], known: set[str]) -> None:
    """Gives every supporting item a stable identifier in document order and drops unknown citations."""
    for prefix, items in (("AS", spec.assumptions), ("DEP", spec.dependencies), ("NFR", spec.non_functional),
                          ("RBAR", access.access_requirements), ("UC", use_cases)):
        for n, item in enumerate(items, 1):
            item.id = f"{prefix}-{n:03d}"
    for item in [*spec.assumptions, *spec.dependencies, *spec.statuses, *spec.non_functional, *spec.integrations,
                 *spec.ui_guidelines]:
        item.sources = known_sources(item.sources, known)


def collect_open_questions(ctx: ProjectContext, spec: Specification, review: Review, access: AccessModel,
                           reqs: list[Requirement], known: set[str]) -> list[OpenQuestion]:
    """Merges questions from every agent with gaps found in the requirements, without duplicates."""
    req_ids = {r.id for r in reqs}
    questions: list[OpenQuestion] = [*ctx.open_questions, *spec.open_questions, *review.open_questions,
                                     *access.open_questions]
    for r in reqs:
        for note in r.notes:
            text = (f"{r.id} ({r.title}) has no traceable source in the material; confirm it with stakeholders."
                    if "No traceable source" in note else f"{r.id} ({r.title}): {note}")
            questions.append(OpenQuestion(question=text, related_requirements=[r.id], sources=r.sources))
        if not r.acceptance_criteria:
            questions.append(OpenQuestion(question=f"Acceptance criteria for {r.id} ({r.title}) need to be defined.",
                                          related_requirements=[r.id], sources=r.sources))
    if not any(p.signoff_authority for p in ctx.people):
        questions.append(OpenQuestion(
            question="Who are the sign-off authorities for this document? The source material does not name them."))
    unique: list[OpenQuestion] = []
    keys: list[set[str]] = []
    for q in questions:
        words = _content_words(q.question)
        if not words:
            continue
        related = set(q.related_requirements)
        # Questions about entirely different requirements stay separate even when worded alike
        # (e.g. "Acceptance criteria for FR-002 ..." and "... for FR-004 ...").
        match = next((i for i, k in enumerate(keys) if _same_question(k, words)
                      and not (related and unique[i].related_requirements
                               and related.isdisjoint(unique[i].related_requirements))), None)
        if match is None:
            unique.append(q)
            keys.append(words)
            continue
        # The same question raised by several agents: keep the first wording, with everything each of them knew.
        first = unique[match]
        first.related_requirements += q.related_requirements
        first.sources += q.sources
        first.owner = first.owner or q.owner
    for q in unique:
        q.related_requirements = [rid for rid in dict.fromkeys(q.related_requirements) if rid in req_ids]
        q.sources = known_sources(q.sources, known)
    for n, q in enumerate(unique, 1):
        q.id = f"OQ-{n:03d}"
        q.status = q.status or "Open"
    return unique
