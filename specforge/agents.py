"""The agent chain:

Document Reader -> Scope Analyst -> Requirements Extractor (per module) -> Coverage Sweep
-> Reviewer -> Refiner -> Writer.

Nothing here is domain-specific: modules, roles and search queries are all derived from the uploaded documents.
The prompt texts live in prompts/runtime/ (see specforge/prompts.py).
"""
import json
import logging
import re

from . import prompts
from .config import Settings
from .ingest import Chunk
from .llm import LLM, LLMError
from .models import Module, ProjectContext, Requirement, RequirementList, Review, Summary
from .retriever import HybridRetriever

log = logging.getLogger(__name__)


def format_chunks(chunks: list[Chunk]) -> str:
    return "\n\n".join(f"[{c.id}] ({c.cite()})\n{c.text}" for c in chunks)


def _compact(reqs: list[Requirement], fields: tuple[str, ...]) -> str:
    return json.dumps([r.model_dump(include=set(fields)) for r in reqs], indent=1, ensure_ascii=False)


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


# --- Agent 1: Document Reader ------------------------------------------------------------------

def document_reader(llm: LLM, chunks: list[Chunk], settings: Settings) -> str:
    """Returns material for the Scope Analyst: the full text when it fits the context budget, otherwise digests."""
    if _words(chunks) <= settings.full_context_words:
        log.info(f"Reading all {len(chunks)} excerpts ({_words(chunks):,} words) in full")
        return "SOURCE EXCERPTS:\n" + format_chunks(chunks)
    parts = _batches(chunks, settings.full_context_words // 2)
    digests = []
    for i, part in enumerate(parts, 1):
        log.info(f"Reading part {i}/{len(parts)} ({_words(part):,} words)")
        digests.append(f"--- Notes on part {i} ---\n" + llm.complete(prompts.render(
            "document_reader", part=i, parts=len(parts), grounding=prompts.text("grounding"),
            excerpts=format_chunks(part))))
    return "NOTES FROM READING THE SOURCE DOCUMENTS:\n" + "\n\n".join(digests)


# --- Agent 2: Scope Analyst --------------------------------------------------------------------

def scope_analyst(llm: LLM, material: str, title: str | None) -> ProjectContext:
    hint = f'\nThe project is called "{title}"; use that as the title.\n' if title else ""
    ctx = llm.complete_json(prompts.render("scope_analyst", grounding=prompts.text("grounding"), title_hint=hint,
                                           material=material), ProjectContext)
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


# --- Agent 3: Requirements Extractor (one run per module) --------------------------------------

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
            others=others, fields=prompts.text("requirement_fields"), grounding=prompts.text("grounding"),
            excerpts=format_chunks(chunks)), RequirementList)
        for req in result.requirements:
            req.module = module.name
        log.info(f"{module.name}: {len(result.requirements)} requirements")
        all_reqs += result.requirements
    return all_reqs


# --- Agent 4: Coverage Sweep -------------------------------------------------------------------

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
            fields=prompts.text("requirement_fields"), grounding=prompts.text("grounding"),
            existing=existing, excerpts=format_chunks(batch)), RequirementList)
        for req in result.requirements:
            req.module = names.get(req.module.strip().lower(), GENERAL_MODULE)
        found += result.requirements
    if any(r.module == GENERAL_MODULE for r in found) and GENERAL_MODULE.lower() not in names:
        ctx.modules.append(Module(name=GENERAL_MODULE, description="Requirements that fit no other module."))
    log.info(f"Coverage sweep found {len(found)} additional requirements")
    return reqs + found


# --- Numbering and citation checks -------------------------------------------------------------

def _module_code(name: str, taken: set[str]) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    code = ("".join(w[0] for w in words) if len(words) > 1 else name[:3]).upper()[:4] or "MOD"
    candidate, n = code, 2
    while candidate in taken:
        candidate, n = f"{code}{n}", n + 1
    taken.add(candidate)
    return candidate


def _keep_valid_sources(req: Requirement, known: set[str]) -> None:
    valid = [s for s in dict.fromkeys(req.sources) if s in known]
    if len(valid) < len(req.sources):
        log.warning(f"{req.id}: dropped citations to unknown excerpts {sorted(set(req.sources) - known)}")
    req.sources = valid
    if not valid and not any("No traceable source" in n for n in req.notes):
        req.notes.append("No traceable source; confirm with stakeholders.")


def number_requirements(ctx: ProjectContext, reqs: list[Requirement], known: set[str]) -> list[Requirement]:
    """Orders requirements by module and gives them IDs such as FR-UM-003."""
    taken: set[str] = set()
    ordered: list[Requirement] = []
    for module in ctx.modules:
        code = _module_code(module.name, taken)
        for n, req in enumerate((r for r in reqs if r.module == module.name), 1):
            req.id = f"FR-{code}-{n:03d}"
            _keep_valid_sources(req, known)
            ordered.append(req)
    return ordered


# --- Agent 5: Reviewer -------------------------------------------------------------------------

def reviewer(llm: LLM, ctx: ProjectContext, reqs: list[Requirement]) -> Review:
    fields = ("id", "module", "title", "description", "business_rules", "acceptance_criteria")
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


# --- Agent 6: Refiner --------------------------------------------------------------------------

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
            "refiner", grounding=prompts.text("grounding"), flagged=json.dumps(payload, indent=1, ensure_ascii=False),
            excerpts=format_chunks(chunks)), RequirementList)
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


# --- Agent 7: Writer ---------------------------------------------------------------------------

def writer(llm: LLM, ctx: ProjectContext, reqs: list[Requirement]) -> Summary:
    grouped: dict[str, list[str]] = {m.name: [] for m in ctx.modules}
    for r in reqs:
        grouped.setdefault(r.module, []).append(f"{r.title}: {r.description}")
    return llm.complete_json(prompts.render(
        "writer", title=ctx.title, purpose=ctx.purpose,
        requirements=json.dumps(grouped, indent=1, ensure_ascii=False)), Summary)
