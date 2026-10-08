import json
import logging
import threading
import time
from datetime import date
from pathlib import Path

from . import agents
from .config import Settings
from .ingest import SUPPORTED, InputCollection, chunk_documents, collect_inputs
from .llm import LLM, Cancelled
from .models import FRD, DocumentInfo, SourceFile
from .render import build_blocks, to_docx, to_html, to_markdown
from .retriever import HybridRetriever

log = logging.getLogger(__name__)

STAGES = [
    ("Preparing documents", "Extracting text and indexing every passage"),
    ("Reading the material", "Building an understanding of the full document set"),
    ("Defining scope", "Identifying purpose, scope, user roles, people and modules"),
    ("Capturing specifications", "Assumptions, dependencies, statuses, non-functional, integration and UI requirements"),
    ("Extracting requirements", "Writing testable requirements for each module"),
    ("Checking coverage", "Re-reading passages that no requirement cites yet"),
    ("Quality review", "Finding duplicates, conflicts, vague wording and open questions"),
    ("Refining", "Rewriting flagged requirements against the sources"),
    ("Access and use cases", "Role-based access rules, the access matrix and detailed use cases"),
    ("Writing the document", "Objective, overview and export"),
]


DEFAULT_AUTHOR = "SpecForge (automated draft)"


def build_frd(inputs: list[Path], out_dir: Path, settings: Settings, title: str | None = None,
              cancel: threading.Event | None = None, prepared_by: str | None = None) -> FRD:
    """Runs the agent chain. Setting `cancel` stops it at the next step or model request."""
    started = time.time()

    def _stage(n: int) -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        log.info(f"Stage {n}/{len(STAGES)}: {STAGES[n - 1][0]}", extra={"stage": n})

    collection = collect_inputs(inputs, settings.dedup)
    for record in collection.with_status("skipped", "duplicate", "failed"):
        log.warning(f"{record.status.capitalize()}: {record.path} ({record.reason})")

    _stage(1)
    chunks = chunk_documents(collection, settings.chunk_words, settings.chunk_overlap)
    if not chunks:
        raise ValueError(_nothing_readable(collection))
    log.info(input_summary(collection.records) + f"; {len(chunks)} passages indexed")
    retriever = HybridRetriever(chunks, settings)
    llm = LLM(settings, cancel)
    steps = out_dir / "steps"
    steps.mkdir(parents=True, exist_ok=True)

    def save(name: str, data) -> None:
        (steps / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    save("0_inputs", [r.model_dump() for r in collection.records])

    _stage(2)
    material = agents.document_reader(llm, chunks, settings)

    _stage(3)
    ctx = agents.scope_analyst(llm, material, title)
    log.info(f"{ctx.title}: modules = {', '.join(m.name for m in ctx.modules)}")
    save("1_context", ctx.model_dump())

    _stage(4)
    spec = agents.specification_analyst(llm, material, ctx)
    log.info(f"{len(spec.assumptions)} assumptions, {len(spec.dependencies)} dependencies, "
             f"{len(spec.non_functional)} non-functional requirements, {len(spec.integrations)} integrations")
    save("2_specification", spec.model_dump())

    _stage(5)
    reqs = agents.requirements_extractor(llm, retriever, settings, ctx)

    _stage(6)
    reqs = agents.coverage_sweep(llm, retriever, settings, ctx, reqs)
    known = set(retriever.by_id)
    reqs = agents.number_requirements(ctx, reqs, known)
    save("3_extracted", [r.model_dump() for r in reqs])

    _stage(7)
    review = agents.reviewer(llm, ctx, reqs)
    log.info(f"{len(review.merges)} merges, {len(review.issues)} issues, {len(review.open_questions)} open questions")
    save("4_review", review.model_dump())
    reqs = agents.apply_merges(reqs, review)

    _stage(8)
    reqs = agents.refiner(llm, retriever, settings, reqs, review)
    save("5_refined", [r.model_dump() for r in reqs])

    _stage(9)
    access = agents.access_analyst(llm, retriever, settings, ctx, reqs)
    log.info(f"{len(access.access_requirements)} access requirements, {len(access.matrix)} roles in the access matrix")
    use_cases = agents.use_case_writer(llm, retriever, ctx, spec, reqs)
    agents.assign_ids(spec, access, use_cases, known)
    save("6_access", access.model_dump())
    save("7_use_cases", [u.model_dump() for u in use_cases])

    _stage(10)
    summary = agents.writer(llm, ctx, reqs)
    open_questions = agents.collect_open_questions(ctx, spec, review, access, reqs, known)

    info = DocumentInfo(title=f"{ctx.title}: Functional Requirements Document",
                        prepared_by=(prepared_by or "").strip() or DEFAULT_AUTHOR, date=date.today().isoformat())
    frd = FRD(info=info, context=ctx, summary=summary, specification=spec, requirements=reqs, access=access,
              use_cases=use_cases, open_questions=open_questions,
              sources={r.doc_id: r.path for r in collection.with_status("processed")}, files=collection.records)
    blocks = build_blocks(frd, {c.id: c.cite() for c in chunks})
    (out_dir / "FRD.md").write_text(to_markdown(blocks), encoding="utf-8")
    to_docx(blocks, out_dir / "FRD.docx")
    (out_dir / "preview.html").write_text(to_html(blocks), encoding="utf-8")
    (out_dir / "frd.json").write_text(frd.model_dump_json(indent=2), encoding="utf-8")

    log.info(f"Done: {len(reqs)} requirements, {len(use_cases)} use cases, {len(open_questions)} open questions, "
             f"{llm.calls} LLM calls, {time.time() - started:.0f}s")
    return frd


def input_summary(records: list[SourceFile]) -> str:
    counts = {s: sum(r.status == s for r in records) for s in ("processed", "skipped", "duplicate", "failed")}
    labels = {"skipped": "skipped", "duplicate": "duplicate" if counts["duplicate"] == 1 else "duplicates", "failed": "failed"}
    parts = [f"{counts['processed']} processed"] + [f"{counts[s]} {label}" for s, label in labels.items() if counts[s]]
    return f"{len(records)} files: " + ", ".join(parts)


def _nothing_readable(collection: InputCollection) -> str:
    problems = collection.with_status("failed")
    if not problems:
        skipped = len(collection.with_status("skipped"))
        found = f" {skipped} unsupported file(s) were skipped." if skipped else ""
        return f"No supported documents found ({', '.join(SUPPORTED)}).{found}"
    listed = "; ".join(f"{r.path} ({r.reason})" for r in problems[:10])
    more = f"; and {len(problems) - 10} more" if len(problems) > 10 else ""
    return f"None of the documents could be read: {listed}{more}."
