import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .config import Settings
from .ingest import DEDUP_POLICIES, SUPPORTED, InputError, chunk_documents, collect_inputs
from .llm import LLMError
from .pipeline import build_frd, input_summary

STATUS_STYLE = {"processed": "green", "skipped": "yellow", "duplicate": "cyan", "failed": "red"}


def _print_files(console: Console, records, title: str) -> None:
    table = Table(title=title, title_justify="left", show_lines=False)
    for column in ("File", "Type", "Status", "Detail"):
        table.add_column(column, overflow="fold")
    for r in records:
        detail = f"{r.doc_id}, {r.passages} passage{'' if r.passages == 1 else 's'}" if r.status == "processed" else r.reason
        table.add_row(r.path, r.type, f"[{STATUS_STYLE.get(r.status, '')}]{r.status}[/]", detail)
    console.print(table)


def _report(console: Console, records) -> None:
    console.print(input_summary(records))
    failed = [r for r in records if r.status == "failed"]
    if failed:
        console.print()
        _print_files(console, failed, f"Failed files ({len(failed)})")


def main() -> None:
    p = argparse.ArgumentParser(
        prog="specforge",
        description="Generate an FRD from source documents with a RAG agent chain.",
        epilog="Examples:\n"
               "  python -m specforge brd.pdf\n"
               "  python -m specforge brd.pdf notes.txt minutes.docx --title \"Billing\"\n"
               "  python -m specforge project-docs/ -o output/billing\n"
               "  python -m specforge project-docs/ --check",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", type=Path,
                   help=f"files and/or folders; folders are scanned recursively ({', '.join(SUPPORTED)})")
    p.add_argument("-o", "--out", type=Path, default=Path("output"), help="output folder (default: output)")
    p.add_argument("--title", help="project name to use as the FRD title")
    p.add_argument("--check", action="store_true",
                   help="validate and read the inputs, list what would be processed, and exit without calling the model")
    p.add_argument("--dedup", choices=DEDUP_POLICIES, default="content",
                   help="content: identical files are processed once (default); path: only the same file listed twice is")
    p.add_argument("--chunk-words", type=int, help="words per indexed passage (default: 220)")
    p.add_argument("--chunk-overlap", type=int, help="words shared between neighbouring passages (default: 40)")
    p.add_argument("--top-k", type=int, help="passages retrieved per search query (default: 6)")
    p.add_argument("--no-dense", action="store_true", help="keyword (BM25) retrieval only; skips the embedding model")
    p.add_argument("--no-cache", action="store_true", help="ignore cached LLM replies")
    args = p.parse_args()

    settings = Settings(use_dense=not args.no_dense, use_cache=not args.no_cache, dedup=args.dedup)
    for name in ("chunk_words", "chunk_overlap", "top_k"):
        value = getattr(args, name)
        if value is not None:
            if value < (0 if name == "chunk_overlap" else 1):
                p.error(f"--{name.replace('_', '-')} must be a positive number")
            setattr(settings, name, value)
    if settings.chunk_overlap >= settings.chunk_words:
        p.error("--chunk-overlap must be smaller than --chunk-words")

    console = Console(emoji=False)
    logging.basicConfig(level=logging.INFO, format="%(message)s", datefmt="[%X]",
                        handlers=[RichHandler(console=console, show_path=False, markup=False)])
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if args.check:
        try:
            collection = collect_inputs(args.inputs, settings.dedup)
        except InputError as e:
            sys.exit(f"Error: {e}")
        chunk_documents(collection, settings.chunk_words, settings.chunk_overlap)
        _print_files(console, collection.records, "Input files")
        console.print(input_summary(collection.records))
        sys.exit(0 if collection.with_status("processed") else 1)

    try:
        frd = build_frd(args.inputs, args.out, settings, args.title)
    except (LLMError, ValueError) as e:
        sys.exit(f"Error: {e}")
    console.print(f"\nWrote {args.out / 'FRD.md'}, {args.out / 'FRD.docx'}, {args.out / 'frd.json'}")
    _report(console, frd.files)


if __name__ == "__main__":
    main()
