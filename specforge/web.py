import asyncio
import json
import logging
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from . import __version__
from .config import Settings
from .ingest import SUPPORTED, is_hidden
from .llm import Cancelled, LLMError
from .pipeline import STAGES, build_frd

log = logging.getLogger(__name__)
# Job progress is streamed from INFO records, whichever way the server was started.
logging.getLogger("specforge").setLevel(logging.INFO)

RUNS = Path(__file__).resolve().parent.parent / "runs"
STATIC = Path(__file__).parent / "static"
MAX_FILES = 200
MAX_FILE_MB = 25
MAX_TOTAL_MB = 200
DOWNLOADS = {
    "docx": ("FRD.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "md": ("FRD.md", "text/markdown; charset=utf-8"),
    "json": ("frd.json", "application/json"),
}


@dataclass
class Job:
    id: str
    title: str
    files: list[str]
    status: str = "queued"  # queued | running | done | error | cancelled
    stage: int = 0
    error: str | None = None
    created: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def out_dir(self) -> Path:
        return RUNS / self.id / "output"

    def emit(self, **event) -> None:
        self.events.append({**event, "t": time.time()})


class JobLogHandler(logging.Handler):
    """Copies log records from one job's worker thread into that job's event stream."""

    def __init__(self, job: Job):
        super().__init__(logging.INFO)
        self.job = job
        self.thread_id = threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id or self.job.cancel.is_set():
            return
        if stage := getattr(record, "stage", None):
            self.job.stage = stage
        self.job.emit(type="log", level=record.levelname.lower(), message=record.getMessage(), stage=self.job.stage,
                       source=record.name)


jobs: dict[str, Job] = {}
# One job at a time: free-tier models are rate-limited per account, so parallel runs only slow each other down.
executor = ThreadPoolExecutor(max_workers=1)
app = FastAPI(title="SpecForge", version=__version__)


TERMINAL = ("done", "error", "cancelled")


def _run(job: Job) -> None:
    if job.cancel.is_set():
        return
    handler = JobLogHandler(job)
    logging.getLogger("specforge").addHandler(handler)
    job.status = "running"
    job.emit(type="status", status="running")
    try:
        # Top-level uploads are passed one by one so a dropped folder keeps its own name in file paths.
        inputs = sorted((RUNS / job.id / "inputs").iterdir())
        build_frd(inputs, job.out_dir, Settings(), job.title or None, job.cancel)
        job.status = "done"
    except Cancelled:
        log.info("Job %s stopped by the user", job.id)
    except ValueError as e:
        job.status, job.error = "error", str(e)
    except LLMError as e:
        log.error("Job %s failed: %s", job.id, e)
        job.status, job.error = "error", _friendly(e)
    except Exception:
        log.exception("Job %s failed", job.id)
        job.status, job.error = "error", "Something went wrong while generating the document. Please try again."
    finally:
        logging.getLogger("specforge").removeHandler(handler)
        if job.cancel.is_set():
            job.status, job.error = "cancelled", None
        else:
            job.emit(type="status", status=job.status, error=job.error)


def _friendly(error: LLMError) -> str:
    text = str(error)
    if "rejected the request" in text:
        return "The analysis service rejected the request. Check the service credentials in the server configuration."
    if "valid" in text and "JSON" in text:
        return "The analysis returned an incomplete result. Please try again."
    return ("The analysis service is at capacity right now. Completed steps are saved, "
            "so trying again in a few minutes resumes where this run stopped.")


# Names Windows cannot use for files or folders, whatever the extension.
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe_part(text: str, limit: int, default: str) -> str:
    part = re.sub(r"[^\w\- ]+", "_", text).strip(" ._")[:limit] or default
    return f"_{part}" if part.upper() in _RESERVED else part


def _safe_name(name: str | None) -> str:
    base = Path(name or "").name
    return _safe_part(Path(base).stem, 100, "document") + Path(base).suffix.lower()


def _safe_rel_path(name: str) -> Path | None:
    """Turns a browser-supplied relative path into one that stays inside the job folder.
    Returns None for hidden or temporary files, which are ignored like they are for folders on disk."""
    parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".", "..")]
    if not parts or any(is_hidden(p) for p in parts):
        return None
    folders = [_safe_part(p, 80, "folder") for p in parts[:-1][-8:]]
    return Path(*folders, _safe_name(parts[-1]))


def _get(job_id: str) -> Job:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found. It may have been lost when the server restarted.")
    return job


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config() -> dict:
    return {"accept": sorted(SUPPORTED), "max_files": MAX_FILES, "max_file_mb": MAX_FILE_MB, "max_total_mb": MAX_TOTAL_MB,
            "stages": [{"name": name, "description": desc} for name, desc in STAGES]}


@app.post("/api/jobs", status_code=201)
async def create_job(files: list[UploadFile] = File(...), paths: list[str] = Form([]),
                     title: str = Form("")) -> dict:
    """Accepts individual files and whole folders. `paths` carries each file's relative path
    (for example "Specs/Billing/rules.pdf"), in the same order as `files`."""
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Upload at most {MAX_FILES} files.")
    if paths and len(paths) != len(files):
        raise HTTPException(400, "Each uploaded file needs exactly one path.")
    job_id = uuid.uuid4().hex[:12]
    inputs = RUNS / job_id / "inputs"
    inputs.mkdir(parents=True)
    names: list[str] = []
    total = 0
    try:
        for i, upload in enumerate(files):
            # Unsupported types are stored too: the shared pipeline records them as skipped, as it does for the CLI.
            rel = _safe_rel_path(paths[i] if paths else upload.filename or "")
            if rel is None:
                continue
            data = await upload.read(MAX_FILE_MB * 1024 * 1024 + 1)
            if len(data) > MAX_FILE_MB * 1024 * 1024:
                raise HTTPException(413, f"{rel.as_posix()} is larger than {MAX_FILE_MB} MB.")
            total += len(data)
            if total > MAX_TOTAL_MB * 1024 * 1024:
                raise HTTPException(413, f"The upload is larger than {MAX_TOTAL_MB} MB in total.")
            dest, n = inputs / rel, 2
            while dest.exists():
                dest, n = inputs / rel.parent / f"{rel.stem} ({n}){rel.suffix}", n + 1
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            names.append(dest.relative_to(inputs).as_posix())
        if not names:
            raise HTTPException(400, "No files to process.")
    except HTTPException:
        shutil.rmtree(RUNS / job_id, ignore_errors=True)
        raise

    job = Job(id=job_id, title=title.strip()[:120], files=names)
    jobs[job_id] = job
    ahead = sum(j.status in ("queued", "running") for j in jobs.values()) - 1
    job.emit(type="status", status="queued", ahead=ahead)
    executor.submit(_run, job)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _get(job_id)
    result = {"id": job.id, "title": job.title, "files": job.files, "status": job.status, "stage": job.stage,
              "error": job.error, "created": job.created}
    if job.status == "done":
        result["frd"] = json.loads((job.out_dir / "frd.json").read_text(encoding="utf-8"))
    return result


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    job = _get(job_id)
    try:
        start = int(request.headers.get("last-event-id", "-1")) + 1
    except ValueError:
        start = 0

    async def stream():
        i = start
        while not await request.is_disconnected():
            while i < len(job.events):
                yield f"id: {i}\ndata: {json.dumps(job.events[i])}\n\n"
                i += 1
            if job.status in TERMINAL:
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    job = _get(job_id)
    if job.status not in TERMINAL:
        # The worker stops at its next checkpoint; the user sees the run as stopped straight away.
        job.cancel.set()
        job.status = "cancelled"
        job.emit(type="status", status="cancelled")
    return {"status": job.status}


@app.get("/api/jobs/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str) -> FileResponse:
    job = _get(job_id)
    if fmt not in DOWNLOADS:
        raise HTTPException(404, "Unknown format.")
    if job.status != "done":
        raise HTTPException(409, "The FRD is not ready yet.")
    filename, media_type = DOWNLOADS[fmt]
    stem = re.sub(r"[^\w\-]+", "-", job.title or "FRD").strip("-") or "FRD"
    download_name = filename if stem == "FRD" else f"{stem}-{filename}"
    return FileResponse(job.out_dir / filename, media_type=media_type, filename=download_name)


def main() -> None:
    import argparse

    import uvicorn
    from rich.console import Console
    from rich.logging import RichHandler

    p = argparse.ArgumentParser(prog="specforge.web", description="Run the SpecForge web UI.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", datefmt="[%X]",
                        handlers=[RichHandler(console=Console(emoji=False), show_path=False, markup=False)])
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    print(f"SpecForge UI: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
