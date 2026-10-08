import hashlib
import logging
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from .models import SourceFile

log = logging.getLogger(__name__)
# pypdf logs low-level parse warnings; a file that really fails is reported as failed instead.
logging.getLogger("pypdf").setLevel(logging.ERROR)

SUPPORTED = {".pdf": "PDF", ".docx": "Word", ".md": "Markdown", ".txt": "Text"}
# content: identical files are processed once. path: only the same file reached twice is.
DEDUP_POLICIES = ("content", "path")


class InputError(ValueError):
    """The inputs cannot be used at all, so nothing is processed."""


@dataclass
class Chunk:
    id: str
    source: str
    page: int | None
    text: str

    def cite(self) -> str:
        return f"{self.source}, p.{self.page}" if self.page else self.source


@dataclass
class InputItem:
    path: Path
    record: SourceFile


@dataclass
class InputCollection:
    """Every file found in the inputs, in a stable order, each with its status."""
    items: list[InputItem]

    @property
    def records(self) -> list[SourceFile]:
        return [item.record for item in self.items]

    def with_status(self, *statuses: str) -> list[SourceFile]:
        return [r for r in self.records if r.status in statuses]


def is_hidden(name: str) -> bool:
    # Dot-files/folders (.git, .DS_Store) and Office lock files (~$report.docx) are never documents.
    return name.startswith((".", "~$"))


def _walk(root: Path):
    """Yields (path, relative display path, error) for every file under an input path."""
    if root.is_file():
        yield root, root.name, None
        return
    label = root.resolve().name or root.resolve().as_posix()
    errors: list[OSError] = []
    for folder, dirs, names in os.walk(root, onerror=errors.append):
        dirs[:] = sorted(d for d in dirs if not is_hidden(d))
        rel_folder = Path(folder).relative_to(root)
        for name in sorted(names):
            if not is_hidden(name):
                yield Path(folder) / name, (Path(label) / rel_folder / name).as_posix(), None
    for e in errors:
        rel = Path(e.filename).relative_to(root) if e.filename else Path()
        yield Path(e.filename or root), (Path(label) / rel).as_posix(), f"folder cannot be opened ({e.strerror or e})"


def _check(path: Path, suffix: str) -> tuple[str | None, str | None]:
    """Validates a file before processing. Returns (problem, sha256 of the content)."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            head = fh.read(8192)
            digest.update(head)
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError as e:
        return f"cannot be read ({e.strerror or e})", None
    if not head:
        return "empty file", None
    if suffix == ".pdf" and not head.startswith(b"%PDF-"):
        return "not a valid PDF file", None
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as z:
                if "word/document.xml" not in z.namelist():
                    return "not a valid Word (.docx) file", None
        except zipfile.BadZipFile:
            return "not a valid Word (.docx) file", None
    if suffix in (".md", ".txt") and b"\x00" in head:
        return "binary content, not a text file", None
    return None, digest.hexdigest()


def collect_inputs(inputs: list[Path], dedup: str = "content") -> InputCollection:
    """Normalises files, folders (scanned recursively) and mixes of both into one collection.

    Nothing is read for content here beyond validation; unsupported, duplicate and invalid
    files are recorded with a reason instead of stopping the run.
    """
    if dedup not in DEDUP_POLICIES:
        raise InputError(f"unknown deduplication policy {dedup!r}; use one of: {', '.join(DEDUP_POLICIES)}")
    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        raise InputError(f"input path not found: {', '.join(missing)}")

    items: list[InputItem] = []
    by_path: dict[Path, str] = {}
    by_hash: dict[str, str] = {}
    for root in inputs:
        for path, rel, walk_error in _walk(root):
            suffix = path.suffix.lower()
            try:
                size = path.stat().st_size if walk_error is None else 0
            except OSError:
                size = 0
            file_type = "Folder" if walk_error else SUPPORTED.get(suffix, suffix.lstrip(".").upper() or "Unknown")
            record = SourceFile(path=rel, type=file_type, size=size)
            items.append(InputItem(path, record))
            resolved = path.resolve()

            if walk_error:
                record.status, record.reason = "failed", walk_error
            elif suffix not in SUPPORTED:
                record.status, record.reason = "skipped", "unsupported file type"
            elif resolved in by_path:
                record.status, record.reason = "duplicate", f"same file as {by_path[resolved]}"
            else:
                by_path[resolved] = rel
                problem, sha = _check(path, suffix)
                if problem:
                    record.status, record.reason = "failed", problem
                elif dedup == "content" and sha in by_hash:
                    record.status, record.reason = "duplicate", f"identical content to {by_hash[sha]}"
                elif sha:
                    by_hash.setdefault(sha, rel)
    return InputCollection(items)


def _read(path: Path) -> list[tuple[int | None, str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(path)
        if reader.is_encrypted:
            # PDFs with only an owner password (print/copy restrictions) open with an empty password.
            try:
                opened = reader.decrypt("")
            except Exception as e:
                raise ValueError("password-protected PDF") from e
            if not opened:
                raise ValueError("password-protected PDF")
        return [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]
    if suffix == ".docx":
        doc = Document(path)
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            parts += [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        return [(None, "\n\n".join(parts))]
    return [(None, path.read_text(encoding="utf-8", errors="replace"))]


def _split(text: str, size: int, overlap: int) -> list[str]:
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces, current = [], []
    for para in paragraphs:
        words = para.split()
        if current and len(current) + len(words) > size:
            pieces.append(" ".join(current))
            current = current[-overlap:]
        current.extend(words)
        while len(current) > size * 1.5:
            pieces.append(" ".join(current[:size]))
            current = current[size - overlap:]
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_documents(collection: InputCollection, size: int, overlap: int) -> list[Chunk]:
    """Extracts and splits every valid file. A file that fails is marked failed; the rest continue."""
    chunks: list[Chunk] = []
    doc_no = 0
    for item in collection.items:
        record = item.record
        if record.status != "pending":
            continue
        try:
            pieces = [(page, piece) for page, text in _read(item.path) for piece in _split(text, size, overlap)]
        except Exception as e:
            record.status, record.reason = "failed", _describe(e, record.type)
            log.warning(f"Could not read {record.path}: {record.reason}")
            continue
        if not pieces:
            record.status = "failed"
            record.reason = "no extractable text" + (" (scanned PDFs need OCR first)" if record.type == "PDF" else "")
            log.warning(f"Could not read {record.path}: {record.reason}")
            continue
        doc_no += 1
        record.status, record.doc_id, record.passages = "processed", f"D{doc_no}", len(pieces)
        chunks += [Chunk(id=f"D{doc_no}-{n:03d}", source=record.path, page=page, text=piece)
                   for n, (page, piece) in enumerate(pieces, 1)]
    return chunks


def _describe(error: Exception, file_type: str) -> str:
    if isinstance(error, ValueError) and str(error) == "password-protected PDF":
        return str(error)
    detail = str(error).strip().splitlines()[0][:160] if str(error).strip() else type(error).__name__
    return f"could not be parsed as {file_type} ({detail})"
