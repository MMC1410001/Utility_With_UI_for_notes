"""A one-at-a-time job queue for pipeline runs.

Runs are serialised onto a single worker thread. That is not laziness - six
separate things in the codebase assume one run at a time:

- `engine.configure()` stores the provider and model in module globals;
- `providers.base.load_dotenv()` mutates `os.environ` process-wide;
- `udemy_fetch` opens one persistent Chrome profile directory, and a second
  launch against it simply fails;
- `gdocs` may open a consent browser;
- `diagrams.render_png` shells out to `npx` against a shared image cache;
- two `Manifest` objects over one file hold different locks, so concurrent
  runs on the same course would double-generate.

Generation is already parallel inside a run (`--workers`), so serialising
whole runs costs almost nothing and removes all six problems at once.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import engine

# How many events one job keeps. A 183-lecture run emits a few hundred; the
# cap only matters for a pathological log flood.
MAX_EVENTS = 5000

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = (DONE, FAILED, CANCELLED)


@dataclass
class Job:
    id: str
    kind: str
    title: str = ""
    course_key: str | None = None
    state: str = QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- events ------------------------------------------------------------
    def emit(self, event: dict) -> None:
        with self._lock:
            self._seq += 1
            event = {**event, "seq": self._seq, "at": time.time()}
            self.events.append(event)
            if len(self.events) > MAX_EVENTS:
                del self.events[: len(self.events) - MAX_EVENTS]

    def since(self, after: int) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["seq"] > after]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    # -- view --------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "course": self.course_key,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seq": self.seq,
            "result": self.result,
            "error": self.error,
            "cancelling": self.cancel.is_set() and self.state == RUNNING,
        }


class _StdoutTee:
    """Route the worker thread's prints into the running job's event log.

    The pipeline stages print as they always have. Rather than rewrite every
    one of those call sites, capture them - but only from the worker thread,
    so uvicorn's own logging on other threads still reaches the terminal.
    """

    def __init__(self, real, owner: threading.Thread, sink: Callable[[str], None]):
        self._real = real
        self._owner = owner
        self._sink = sink
        self._buf = ""

    def write(self, text: str) -> int:
        if threading.current_thread() is self._owner:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    self._sink(line)
        return self._real.write(text)

    def flush(self) -> None:
        self._real.flush()

    def __getattr__(self, name):
        return getattr(self._real, name)


class JobQueue:
    """FIFO, one worker thread, cooperative cancellation."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._q: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._current: Job | None = None
        self._runners: dict[str, Callable[[Job], Any]] = {}
        self._thread = threading.Thread(target=self._loop, daemon=True, name="notesgen-jobs")
        self._thread.start()

    # -- submission --------------------------------------------------------
    def submit(
        self,
        kind: str,
        run: Callable[[Job], Any],
        *,
        title: str = "",
        course_key: str | None = None,
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title, course_key=course_key)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._runners[job.id] = run
        self._q.put(job.id)
        return job

    def active_for(self, course_key: str) -> Job | None:
        """A queued or running job already working on this course."""
        with self._lock:
            for jid in self._order:
                job = self._jobs[jid]
                if job.course_key == course_key and job.state in (QUEUED, RUNNING):
                    return job
        return None

    # -- lookup ------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            ids = self._order[-limit:]
        return [self._jobs[i] for i in reversed(ids)]

    @property
    def current(self) -> Job | None:
        return self._current

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state in TERMINAL:
            return False
        job.cancel.set()
        if job.state == QUEUED:
            # Never started, so finish it here; the worker skips it on pop.
            job.state = CANCELLED
            job.finished_at = time.time()
            job.emit({"type": "done", "state": CANCELLED})
        else:
            job.emit({"type": "log", "message": "cancelling after the current step..."})
        return True

    # -- the worker --------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job_id = self._q.get()
            job = self._jobs.get(job_id)
            run = self._runners.pop(job_id, None)
            if job is None or run is None or job.state == CANCELLED:
                continue
            self._run_one(job, run)

    def _run_one(self, job: Job, run: Callable[[Job], Any]) -> None:
        self._current = job
        job.state = RUNNING
        job.started_at = time.time()
        job.emit({"type": "stage", "stage": job.kind, "state": "start"})

        real_stdout, real_stderr = sys.stdout, sys.stderr
        me = threading.current_thread()
        tee = _StdoutTee(real_stdout, me, lambda line: job.emit({"type": "log", "message": line}))
        err_tee = _StdoutTee(real_stderr, me, lambda line: job.emit({"type": "log", "message": line}))
        sys.stdout, sys.stderr = tee, err_tee
        try:
            result = run(job)
            if job.cancel.is_set():
                job.state = CANCELLED
            else:
                job.state = DONE
                job.result = result if isinstance(result, dict) else {"value": result}
        except Exception as exc:  # noqa: BLE001 - any failure is the job's, not the server's
            job.state = FAILED
            job.error = str(exc) or exc.__class__.__name__
            job.emit({"type": "error", "message": job.error})
            traceback.print_exc(file=real_stderr)
        finally:
            sys.stdout, sys.stderr = real_stdout, real_stderr
            job.finished_at = time.time()
            self._current = None
            job.emit({"type": "done", "state": job.state})

    # -- engine setup for a run -------------------------------------------
    @staticmethod
    def configure_engine(provider: str | None = None, model: str | None = None) -> None:
        engine.configure(provider=provider, model=model)
