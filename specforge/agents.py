"""The agent chain:

Document Reader -> Scope Analyst -> Requirements Extractor (per module) -> Coverage Sweep
-> Reviewer -> Refiner -> Writer.

Nothing here is domain-specific: modules, roles and search queries are all derived from the uploaded documents.
"""
import json
import logging
import re

from .config import Settings
from .ingest import Chunk
from .llm import LLM, LLMError
from .models import Module, ProjectContext, Requirement, RequirementList, Review, Summary
from .retriever import HybridRetriever

log = logging.getLogger(__name__)

GROUNDING = ("Use only the source material. Do not invent features, values, roles or rules it does not support. "
             "When the sources are silent on something, leave that field empty.")

REQUIREMENT_FIELDS = """For each requirement:
- title: 3-8 words.
- description: one "The system shall ..." sentence, specific and testable. Keep exact values from the sources (limits, time windows, amounts, formats).
- actor: the user role or external system that triggers or uses it.
- priority: "Must", "Should" or "Could". Follow the sources; when they say nothing, use "Must" for core flows and "Should" otherwise.
- inputs / outputs: data the behaviour consumes and produces, if the sources say.
- business_rules: rules and validations that constrain it, if the sources say.
- acceptance_criteria: 2-4 checkable criteria, in Given/When/Then form where it fits.
- sources: IDs of the excerpts that support it, e.g. ["D1-004"]. At least one."""


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

READER_PROMPT = """You are a business analyst reading part {part} of {parts} of a project's source documents.

Write dense notes on everything in this part that matters for a Functional Requirements Document, under these headings:
Purpose and goals; Users and roles; Features and behaviours; Business rules and exact values; Data and records; Integrations and external systems; Constraints and non-functional needs; Explicitly out of scope; Open issues or conflicts.

Put the excerpt IDs in brackets after each point, e.g. [D1-004]. Skip a heading when this part says nothing about it. {grounding}

EXCERPTS:
{excerpts}"""


def document_reader(llm: LLM, chunks: list[Chunk], settings: Settings) -> str:
    """Returns material for the Scope Analyst: the full text when it fits the context budget, otherwise digests."""
    if _words(chunks) <= settings.full_context_words:
        log.info(f"Reading all {len(chunks)} excerpts ({_words(chunks):,} words) in full")
        return "SOURCE EXCERPTS:\n" + format_chunks(chunks)
    parts = _batches(chunks, settings.full_context_words // 2)
    digests = []
    for i, part in enumerate(parts, 1):
        log.info(f"Reading part {i}/{len(parts)} ({_words(part):,} words)")
        digests.append(f"--- Notes on part {i} ---\n" + llm.complete(READER_PROMPT.format(
            part=i, parts=len(parts), grounding=GROUNDING, excerpts=format_chunks(part))))
    return "NOTES FROM READING THE SOURCE DOCUMENTS:\n" + "\n\n".join(digests)


# --- Agent 2: Scope Analyst --------------------------------------------------------------------

SCOPE_PROMPT = """You are a senior business analyst preparing a Functional Requirements Document (FRD).
Read the source material below, whatever domain it is from, and establish the project context.

Identify the functional modules of the system: distinct capability areas named after what the system does in this domain, not after document sections. Use as many as the material needs, usually 3 to 10; a small system may need only 2. Every feature in the material should belong to one module. For each module write 3 or 4 short search queries in the vocabulary of the sources; they will be used to retrieve the evidence for that module's requirements.

{grounding}

Return only a JSON object of this shape:
{{
  "title": "system or project name",
  "purpose": "2-3 sentences on the business problem and the goal",
  "scope_in": ["capability in scope"],
  "scope_out": ["item explicitly out of scope"],
  "stakeholders": [{{"role": "role name", "description": "what they need from the system"}}],
  "assumptions": ["assumption stated or clearly implied by the sources"],
  "constraints": ["constraint: regulatory, technical, budget, timeline"],
  "modules": [{{"name": "module name", "description": "what this module does", "search_queries": ["query"]}}]
}}
{title_hint}
{material}"""


def scope_analyst(llm: LLM, material: str, title: str | None) -> ProjectContext:
    hint = f'\nThe project is called "{title}"; use that as the title.\n' if title else ""
    ctx = llm.complete_json(SCOPE_PROMPT.format(grounding=GROUNDING, title_hint=hint, material=material),
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


# --- Agent 3: Requirements Extractor (one run per module) --------------------------------------

EXTRACT_PROMPT = """You are a business analyst writing the functional requirements for the "{module}" module of {title}.

Module scope: {description}
Other modules (leave their requirements to them): {others}

Extract every functional requirement for this module that the source excerpts support. A functional requirement is a behaviour the system must perform. Split compound statements so each requirement covers one behaviour.

{fields}

{grounding} If the excerpts hold nothing for this module, return an empty list.

Return only a JSON object of this shape:
{{"requirements": [{{"title": "", "description": "The system shall ...", "actor": "", "priority": "Must", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"]}}]}}

SOURCE EXCERPTS:
{excerpts}"""


def requirements_extractor(llm: LLM, retriever: HybridRetriever, settings: Settings,
                           ctx: ProjectContext) -> list[Requirement]:
    all_reqs: list[Requirement] = []
    for module in ctx.modules:
        queries = module.search_queries or [module.name]
        chunks = retriever.search([f"{module.name}: {module.description}", *queries],
                                  settings.top_k, settings.max_context_chunks)
        log.info(f"Extracting {module.name} from {len(chunks)} excerpts")
        others = ", ".join(m.name for m in ctx.modules if m is not module) or "none"
        result = llm.complete_json(EXTRACT_PROMPT.format(
            module=module.name, title=ctx.title, description=module.description, others=others,
            fields=REQUIREMENT_FIELDS, grounding=GROUNDING, excerpts=format_chunks(chunks)), RequirementList)
        for req in result.requirements:
            req.module = module.name
        log.info(f"{module.name}: {len(result.requirements)} requirements")
        all_reqs += result.requirements
    return all_reqs


# --- Agent 4: Coverage Sweep -------------------------------------------------------------------

SWEEP_PROMPT = """You are a business analyst checking that the FRD for {title} misses nothing.

No requirement cites the excerpts below yet. Many may be background with no requirements in them. Extract only the functional requirements they contain that the existing requirements do not already cover, and assign each to the best-fitting module: {modules}.

{fields}
- module: one of the module names above, spelled exactly.

{grounding} Returning an empty list is fine when there is nothing new.

Return only a JSON object of this shape:
{{"requirements": [{{"module": "", "title": "", "description": "The system shall ...", "actor": "", "priority": "Must", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"]}}]}}

EXISTING REQUIREMENTS:
{existing}

UNCITED EXCERPTS:
{excerpts}"""

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
        result = llm.complete_json(SWEEP_PROMPT.format(
            title=ctx.title, modules=", ".join(names.values()), fields=REQUIREMENT_FIELDS, grounding=GROUNDING,
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

REVIEW_PROMPT = """You are a requirements quality reviewer for the FRD of {title}.

Review the requirements below as a set and report:
1. merges: requirements that describe the same behaviour (often across modules). Name the one to keep and the ones to drop.
2. issues: requirements that are ambiguous (vague words such as "fast", "easy", "appropriate"), conflicting with another requirement, incomplete (missing a rule, limit or outcome), or untestable. Give a concrete note saying what is wrong.
3. open_questions: decisions the stakeholders must make before build, such as conflicts the sources do not resolve and missing values.

Report only real problems; an empty list is fine.

Return only a JSON object of this shape:
{{"merges": [{{"keep": "FR-X-001", "drop": ["FR-Y-003"], "reason": ""}}],
 "issues": [{{"req_id": "FR-X-002", "type": "ambiguous|conflict|incomplete|untestable", "note": ""}}],
 "open_questions": ["question"]}}

REQUIREMENTS:
{requirements}"""


def reviewer(llm: LLM, ctx: ProjectContext, reqs: list[Requirement]) -> Review:
    fields = ("id", "module", "title", "description", "business_rules", "acceptance_criteria")
    review = llm.complete_json(REVIEW_PROMPT.format(title=ctx.title, requirements=_compact(reqs, fields)), Review)
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

REFINE_PROMPT = """You are a business analyst fixing requirements that a reviewer flagged.

For each requirement below, rewrite it to resolve the reviewer's note using the source excerpts: make it specific and testable, add the missing rule or value, and tighten its acceptance criteria. Keep its id.
If the sources cannot resolve the issue (for example two sources conflict), keep the best supported wording and put a one-sentence explanation in "notes" so a stakeholder can decide.

{grounding}

Return only a JSON object of this shape, with one entry per flagged requirement:
{{"requirements": [{{"id": "FR-X-001", "title": "", "description": "The system shall ...", "actor": "", "priority": "Must", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"], "notes": ["..."]}}]}}

FLAGGED REQUIREMENTS (with reviewer notes):
{flagged}

SOURCE EXCERPTS:
{excerpts}"""

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
        result = llm.complete_json(REFINE_PROMPT.format(
            grounding=GROUNDING, flagged=json.dumps(payload, indent=1, ensure_ascii=False),
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

WRITER_PROMPT = """You are a technical writer finishing the Functional Requirements Document for {title}.

Purpose: {purpose}

Write:
- executive_summary: one or two paragraphs for business readers covering what the system does, who it serves, and the main capabilities.
- module_overviews: for each module, 2-3 sentences introducing it, keyed by the exact module name.

Base everything on the purpose and the requirements below; add no new features.

Return only a JSON object of this shape:
{{"executive_summary": "", "module_overviews": {{"Module Name": ""}}}}

REQUIREMENTS BY MODULE:
{requirements}"""


def writer(llm: LLM, ctx: ProjectContext, reqs: list[Requirement]) -> Summary:
    grouped: dict[str, list[str]] = {m.name: [] for m in ctx.modules}
    for r in reqs:
        grouped.setdefault(r.module, []).append(f"{r.title}: {r.description}")
    return llm.complete_json(WRITER_PROMPT.format(
        title=ctx.title, purpose=ctx.purpose,
        requirements=json.dumps(grouped, indent=1, ensure_ascii=False)), Summary)
