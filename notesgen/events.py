"""Progress reporting for callers that aren't a terminal.

The CLI reports by printing as it goes. The web UI needs the same information
as structured data, so the long-running passes take an optional `progress`
callback and call it once per finished unit.

Deliberately a plain parameter rather than a module-global or a contextvar:
the generate passes do their work in a `ThreadPoolExecutor`, and
`Executor.submit` does not propagate contextvars, so a contextvar-based
emitter would silently be `None` inside every worker. Passing the callback
explicitly also keeps it obvious, at each call site, which thread fires it -
always the collecting thread, never a worker - so no callback ever needs to be
thread-safe.

A progress callback is a reporting detail. If one raises, that is the UI's
problem and must not take the run down with it, so `notify` swallows.
"""

from __future__ import annotations

from typing import Any, Callable

Progress = Callable[[dict], None]
ShouldCancel = Callable[[], bool]

# type= values
UNIT = "unit"    # one lecture / section / document finished
STAGE = "stage"  # a pipeline stage started or ended
LOG = "log"      # a line of human-readable output
DONE = "done"
ERROR = "error"


class Cancelled(RuntimeError):
    """Raised when a run is cancelled between units."""


def notify(progress: Progress | None, type: str, **fields: Any) -> None:
    if progress is None:
        return
    try:
        progress({"type": type, **fields})
    except Exception:  # noqa: BLE001 - reporting must never break the run
        pass


def unit(
    progress: Progress | None,
    stage: str,
    done: int,
    total: int,
    label: str,
    **fields: Any,
) -> None:
    notify(progress, UNIT, stage=stage, done=done, total=total, label=label, **fields)


def stage(progress: Progress | None, name: str, state: str, **fields: Any) -> None:
    notify(progress, STAGE, stage=name, state=state, **fields)


def log(progress: Progress | None, message: str) -> None:
    notify(progress, LOG, message=message)


def check(should_cancel: ShouldCancel | None) -> None:
    """Raise `Cancelled` if the caller has asked to stop.

    Called between units, never mid-call: an in-flight model call is already
    paid for, and the manifest records each unit as it lands, so stopping at a
    unit boundary means a re-run resumes instead of repeating work.
    """
    if should_cancel is not None and should_cancel():
        raise Cancelled("cancelled")
