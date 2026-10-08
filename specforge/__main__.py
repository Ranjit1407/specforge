import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from .config import Settings
from .llm import LLMError
from .pipeline import build_frd


def main() -> None:
    p = argparse.ArgumentParser(prog="specforge", description="Generate an FRD from source documents with a RAG agent chain.")
    p.add_argument("inputs", nargs="+", type=Path, help="files or folders (.pdf, .docx, .md, .txt)")
    p.add_argument("-o", "--out", type=Path, default=Path("output"), help="output folder (default: output)")
    p.add_argument("--title", help="project name to use as the FRD title")
    p.add_argument("--top-k", type=int, help="chunks retrieved per query")
    p.add_argument("--no-dense", action="store_true", help="keyword (BM25) retrieval only; skips the embedding model")
    p.add_argument("--no-cache", action="store_true", help="ignore cached LLM replies")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", datefmt="[%X]",
                        handlers=[RichHandler(console=Console(emoji=False), show_path=False, markup=False)])
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    settings = Settings(use_dense=not args.no_dense, use_cache=not args.no_cache)
    if args.top_k:
        settings.top_k = args.top_k
    try:
        build_frd(args.inputs, args.out, settings, args.title)
    except (LLMError, ValueError) as e:
        sys.exit(f"Error: {e}")
    print(f"Wrote {args.out / 'FRD.md'}, {args.out / 'FRD.docx'}, {args.out / 'frd.json'}")


if __name__ == "__main__":
    main()
