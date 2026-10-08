import json
import logging
import threading
import time
from pathlib import Path

from . import agents
from .config import Settings
from .ingest import SUPPORTED, InputCollection, chunk_documents, collect_inputs
from .llm import LLM, Cancelled
from .models import FRD, SourceFile
from .render import build_blocks, to_docx, to_markdown
from .retriever import HybridRetriever

log = logging.getLogger(__name__)

STAGES = [
    ("Preparing documents", "Extracting text and indexing every passage"),
    ("Reading the material", "Building an understanding of the full document set"),
    ("Defining scope", "Identifying purpose, stakeholders, constraints and modules"),
    ("Extracting requirements", "Writing testable requirements for each module"),
    ("Checking coverage", "Re-reading passages that no requirement cites yet"),
    ("Quality review", "Finding duplicates, conflicts and vague wording"),
    ("Refining", "Rewriting flagged requirements against the sources"),
    ("Writing the document", "Executive summary, module overviews and export"),
]


def build_frd(inputs: list[Path], out_dir: Path, settings: Settings, title: str | None = None,
              cancel: threading.Event | None = None) -> FRD:
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
    reqs = agents.requirements_extractor(llm, retriever, settings, ctx)

    _stage(5)
    reqs = agents.coverage_sweep(llm, retriever, settings, ctx, reqs)
    reqs = agents.number_requirements(ctx, reqs, set(retriever.by_id))
    save("2_extracted", [r.model_dump() for r in reqs])

    _stage(6)
    review = agents.reviewer(llm, ctx, reqs)
    log.info(f"{len(review.merges)} merges, {len(review.issues)} issues, {len(review.open_questions)} open questions")
    save("3_review", review.model_dump())
    reqs = agents.apply_merges(reqs, review)

    _stage(7)
    reqs = agents.refiner(llm, retriever, settings, reqs, review)
    save("4_refined", [r.model_dump() for r in reqs])

    _stage(8)
    summary = agents.writer(llm, ctx, reqs)

    frd = FRD(context=ctx, summary=summary, requirements=reqs, open_questions=review.open_questions,
              sources={r.doc_id: r.path for r in collection.with_status("processed")}, files=collection.records)
    blocks = build_blocks(frd, {c.id: c.cite() for c in chunks})
    (out_dir / "FRD.md").write_text(to_markdown(blocks), encoding="utf-8")
    to_docx(blocks, out_dir / "FRD.docx")
    (out_dir / "frd.json").write_text(frd.model_dump_json(indent=2), encoding="utf-8")

    log.info(f"Done: {len(reqs)} requirements, {llm.calls} LLM calls, {time.time() - started:.0f}s")
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
