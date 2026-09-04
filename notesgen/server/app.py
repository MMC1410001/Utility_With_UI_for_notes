"""The local web UI's HTTP surface.

Everything here is a thin wrapper over `notesgen.pipeline`. No pipeline logic
lives in this file - the point of the service layer is that the CLI and the
web UI cannot drift apart.
"""

from __future__ import annotations

import asyncio
import json
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse

from .. import engine, gdocs, links as links_mod, outputs as outputs_mod, pipeline, setup_cmd
from ..manifest import Manifest
from . import files as files_mod
from . import security
from .importer import ImportError_, import_course
from .jobs import JobQueue

STATIC = Path(__file__).parent / "static"

# The extension's origin is a 32-character id; also allow the UI's own origin.
ORIGIN_RE = r"^chrome-extension://[a-p]{32}$|^http://(127\.0\.0\.1|localhost)(:\d+)?$"

# How often the SSE stream checks for new events, and how often it sends a
# keep-alive. A provider retry can sleep 60s, so a silent stream is normal and
# the client must not treat quiet as death.
POLL_SECONDS = 0.25
PING_SECONDS = 15


def create_app(
    *,
    output_dir: Path,
    input_dir: Path,
    config_dir: Path,
    token: str,
) -> FastAPI:
    app = FastAPI(title="notesgen", docs_url=None, redoc_url=None)
    app.state.output_dir = output_dir
    app.state.input_dir = input_dir
    app.state.config_dir = config_dir
    app.state.token = token
    app.state.queue = JobQueue()

    app.add_middleware(security.TokenMiddleware, token=token)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=ORIGIN_RE,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["content-type", security.HEADER],
        max_age=600,
    )
    app.add_middleware(security.PrivateNetworkMiddleware)

    _register(app)
    return app


def _register(app: FastAPI) -> None:  # noqa: C901 - a route table reads better whole
    # ---------------------------------------------------------------- meta
    @app.get("/api/health")
    def health():
        """Open, so the extension can find the server before it is paired."""
        return {"app": "notesgen", "ok": True, "token_required": True}

    @app.get("/api/status")
    def status():
        try:
            provider, model = engine.active()
        except Exception as exc:  # noqa: BLE001 - no provider configured yet
            provider, model = None, None
            provider_error = str(exc)
        else:
            provider_error = None

        from .. import diagrams as diagrams_mod

        google = gdocs.auth_status(app.state.config_dir)
        return {
            "provider": provider,
            "model": model,
            "provider_error": provider_error,
            "extras": setup_cmd.status(),
            "mermaid": diagrams_mod.renderer_available(),
            "google": {
                "connected": bool(google["valid"] or google["refreshable"]),
                "client": google["client"],
                "help": google["help"],
            },
            "output_dir": str(app.state.output_dir),
            "input_dir": str(app.state.input_dir),
        }

    @app.get("/api/outputs")
    def outputs():
        """The choice table, so the UI never duplicates it."""
        return {
            "choices": outputs_mod.CHOICES,
            "default": list(outputs_mod.DEFAULT),
            "aliases": {k: list(v) for k, v in outputs_mod.ALIASES.items()},
        }

    # ------------------------------------------------------------- courses
    @app.get("/api/courses")
    def courses():
        found = []
        out_root = app.state.output_dir
        if out_root.is_dir():
            for name, course_dir, urls in links_mod.all_courses(out_root):
                manifest_path = Path(course_dir) / "manifest.json"
                cost = 0.0
                if manifest_path.exists():
                    try:
                        cost = Manifest(manifest_path).total_cost
                    except Exception:  # noqa: BLE001
                        cost = 0.0
                found.append({
                    "name": name,
                    "dir": Path(course_dir).name,
                    "links": [{"label": l, "url": u} for l, u in urls],
                    "cost_usd": round(cost, 4),
                })

        captured = []
        in_root = app.state.input_dir
        if in_root.is_dir():
            from ..ingest import looks_like_course

            for child in sorted(in_root.iterdir()):
                if child.is_dir() and looks_like_course(child):
                    captured.append({"dir": child.name, "path": str(child)})

        return {"generated": found, "captured": captured}

    @app.post("/api/courses/import")
    def import_transcripts(payload: dict = Body(...)):
        """Receive a course captured by the Chrome extension."""
        try:
            result = import_course(payload, app.state.input_dir)
        except ImportError_ as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "course_dir": result.course_dir.name,
            "path": str(result.course_dir),
            "name": result.name,
            "lectures": result.lectures,
            "missing": result.missing,
            "sections": result.sections,
        }

    @app.get("/api/courses/{course}/artifacts")
    def artifacts(course: str):
        root = files_mod.course_root(app.state.output_dir, course)
        run = pipeline.CourseRun(
            course_dir=root, name=course, root=root,
            md_root=root / "md", docx_root=root / "docx",
            manifest_path=root / "manifest.json",
        )
        manifest = Manifest(run.manifest_path) if run.manifest_path.exists() else None
        return {"artifacts": _artifact_json(course, pipeline.collect_artifacts(run, manifest))}

    @app.get("/api/files/{course}/{relative:path}")
    def download(course: str, relative: str, inline: bool = False):
        path = files_mod.safe_file(app.state.output_dir, course, relative)
        return files_mod.serve(path, inline=inline)

    # ---------------------------------------------------------------- jobs
    @app.post("/api/jobs")
    def create_job(payload: dict = Body(...)):
        source = (payload.get("source") or "").strip()
        if not source:
            raise HTTPException(status_code=400, detail="source is required")

        try:
            wanted = outputs_mod.parse(payload.get("outputs") or None)
        except outputs_mod.OutputError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Refuse a Drive run now rather than after an hour of model calls.
        try:
            pipeline.check_drive_auth(wanted, app.state.config_dir)
        except pipeline.PipelineError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        opts = pipeline.RunOptions(
            outputs=wanted,
            model=payload.get("model") or "sonnet",
            workers=int(payload.get("workers") or 3),
            force=bool(payload.get("force")),
            no_rollup=bool(payload.get("no_rollup")),
            sections=payload.get("sections") or None,
            only=payload.get("only") or None,
            no_images=bool(payload.get("no_images")),
            split_sections=bool(payload.get("split_sections")),
            config_dir=app.state.config_dir,
            keep_going=bool(payload.get("keep_going")),
        )
        refetch = bool(payload.get("refetch"))

        def run(job):
            course = pipeline.resolve(
                source, app.state.input_dir, app.state.output_dir, refetch=refetch
            )
            job.course_key = course.course_dir.name
            job.title = course.name
            result = pipeline.run(
                course, opts,
                progress=job.emit,
                should_cancel=job.cancel.is_set,
            )
            return {
                "course": result.course,
                "course_dir": course.course_dir.name,
                "outputs": list(result.outputs),
                "stats": result.stats,
                "cost_usd": round(result.cost_usd, 4),
                "cost_total_usd": round(result.cost_total_usd, 4),
                "failed": result.failed,
                "errors": result.errors,
                "cancelled": result.cancelled,
                "links": [{"label": l, "url": u} for l, u in result.links],
                "artifacts": _artifact_json(course.root.name, result.artifacts),
            }

        queue: JobQueue = app.state.queue
        job = queue.submit("run", run, title=source, course_key=None)
        return {"job": job.snapshot()}

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50):
        queue: JobQueue = app.state.queue
        return {"jobs": [j.snapshot() for j in queue.list(limit)]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, after: int = 0):
        job = app.state.queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return {"job": job.snapshot(), "events": job.since(after)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        if not app.state.queue.cancel(job_id):
            raise HTTPException(status_code=409, detail="job is not cancellable")
        return {"job": app.state.queue.get(job_id).snapshot()}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(request: Request, job_id: str, after: int = Query(0)):
        job = app.state.queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")

        async def stream():
            cursor = after
            last_ping = time.monotonic()
            # Replay from `after` first, so a reconnecting client (a reopened
            # popup, a refreshed tab) misses nothing.
            while True:
                if await request.is_disconnected():
                    return
                pending = job.since(cursor)
                for event in pending:
                    cursor = event["seq"]
                    yield f"id: {cursor}\nevent: {event['type']}\n" \
                          f"data: {json.dumps(event)}\n\n"
                if pending:
                    last_ping = time.monotonic()
                elif time.monotonic() - last_ping > PING_SECONDS:
                    last_ping = time.monotonic()
                    yield ": ping\n\n"

                if job.state in ("done", "failed", "cancelled") and not job.since(cursor):
                    yield f"event: end\ndata: {json.dumps(job.snapshot())}\n\n"
                    return
                await asyncio.sleep(POLL_SECONDS)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -------------------------------------------------------------- google
    @app.get("/api/google/status")
    def google_status():
        state = gdocs.auth_status(app.state.config_dir)
        return {**state, "config_dir": str(app.state.config_dir)}

    @app.post("/api/google/connect")
    def google_connect():
        """Open the consent flow, on the job thread so it cannot block a request."""
        def run(job):
            job.emit({"type": "log", "message": "opening a browser for Google consent..."})
            gdocs.connect(app.state.config_dir)
            job.emit({"type": "log", "message": "Google Drive connected."})
            return {"connected": True}

        job = app.state.queue.submit("google-auth", run, title="Connect Google Drive")
        return {"job": job.snapshot()}

    # ------------------------------------------------------------------ UI
    @app.get("/", response_class=HTMLResponse)
    def index():
        page = STATIC / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>notesgen</h1><p>UI files are missing.</p>", 500)
        # The UI needs the token to call its own API; it is same-origin and
        # already local, so injecting it into the page is the simplest handoff.
        return HTMLResponse(
            page.read_text(encoding="utf-8").replace("__NOTESGEN_TOKEN__", app.state.token)
        )

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(pipeline.PipelineError)
    def _pipeline_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)


def _artifact_json(course_dir: str, artifacts) -> list[dict]:
    out = []
    for art in artifacts:
        entry = {"kind": art.kind, "name": art.name, "size": art.size, "url": art.url}
        if art.path is not None:
            # Course and file names contain spaces, commas and brackets. A
            # browser would paper over that; anything else consuming this JSON
            # (the extension, curl, a script) would not.
            segments = "/".join(
                quote(part, safe="") for part in (course_dir, art.kind, art.name)
            )
            entry["download"] = f"/api/files/{segments}"
        out.append(entry)
    return out


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    output_dir: Path,
    input_dir: Path,
    config_dir: Path,
    open_browser: bool = True,
) -> int:
    import uvicorn

    token = security.load_or_create_token()
    app = create_app(
        output_dir=output_dir, input_dir=input_dir, config_dir=config_dir, token=token
    )

    url = f"http://{host}:{port}/"
    print(f"\n  notesgen web UI  ->  {url}")
    print(f"  extension token  ->  {token}")
    print(f"  output           ->  {output_dir}")
    print("\n  Ctrl-C to stop.\n")

    if open_browser:
        threading_timer(1.0, lambda: webbrowser.open(url))

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def threading_timer(delay: float, fn):
    import threading

    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t
