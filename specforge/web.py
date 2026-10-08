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

from .config import Settings
from .ingest import SUPPORTED
from .llm import Cancelled, LLMError
from .pipeline import STAGES, build_frd

log = logging.getLogger(__name__)
# Job progress is streamed from INFO records, whichever way the server was started.
logging.getLogger("specforge").setLevel(logging.INFO)

RUNS = Path(__file__).resolve().parent.parent / "runs"
STATIC = Path(__file__).parent / "static"
MAX_FILES = 20
MAX_FILE_MB = 25
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
app = FastAPI(title="SpecForge")


TERMINAL = ("done", "error", "cancelled")


def _run(job: Job) -> None:
    if job.cancel.is_set():
        return
    handler = JobLogHandler(job)
    logging.getLogger("specforge").addHandler(handler)
    job.status = "running"
    job.emit(type="status", status="running")
    try:
        build_frd([RUNS / job.id / "inputs"], job.out_dir, Settings(), job.title or None, job.cancel)
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


def _safe_name(name: str | None) -> str:
    base = Path(name or "").name
    stem, suffix = Path(base).stem, Path(base).suffix.lower()
    stem = re.sub(r"[^\w\- ]+", "_", stem).strip(" ._")[:100] or "document"
    return stem + suffix


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
    return {"accept": sorted(SUPPORTED), "max_files": MAX_FILES, "max_file_mb": MAX_FILE_MB,
            "stages": [{"name": name, "description": desc} for name, desc in STAGES]}


@app.post("/api/jobs", status_code=201)
async def create_job(files: list[UploadFile] = File(...), title: str = Form("")) -> dict:
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Upload at most {MAX_FILES} files.")
    job_id = uuid.uuid4().hex[:12]
    inputs = RUNS / job_id / "inputs"
    inputs.mkdir(parents=True)
    names: list[str] = []
    try:
        for upload in files:
            name = _safe_name(upload.filename)
            if Path(name).suffix not in SUPPORTED:
                raise HTTPException(400, f"{upload.filename}: unsupported type. Use {', '.join(sorted(SUPPORTED))}.")
            data = await upload.read(MAX_FILE_MB * 1024 * 1024 + 1)
            if len(data) > MAX_FILE_MB * 1024 * 1024:
                raise HTTPException(413, f"{upload.filename} is larger than {MAX_FILE_MB} MB.")
            if not data:
                raise HTTPException(400, f"{upload.filename} is empty.")
            dest, n = inputs / name, 2
            while dest.exists():
                dest, n = inputs / f"{Path(name).stem} ({n}){Path(name).suffix}", n + 1
            dest.write_bytes(data)
            names.append(dest.name)
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
