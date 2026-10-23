"""Run orchestration for the CLI and the GUI (thread + event bus)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from ..datalog.factory import build_sinks
from ..engine.controller import RunController, RunSummary
from ..engine.events import EventBus
from .session import Session

__all__ = ["RunOptions", "RunHandle", "create_run"]


@dataclass
class RunOptions:
    """Everything a caller may tweak before a run starts."""

    stage: str | None = None
    sites: int | None = None
    seed: int | None = None
    only: list[str] = field(default_factory=list)
    skip: list[str] = field(default_factory=list)
    output_dir: str | Path | None = None
    stdf: bool = True
    stats: bool = True
    sinks: Sequence[Any] | None = None
    resume: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def overrides(self) -> dict[str, Any]:
        """Translate the options into session overrides."""
        out: dict[str, Any] = {}
        if self.stage:
            out["run.stage"] = self.stage
        if self.sites:
            out["run.sites"] = int(self.sites)
        if self.seed is not None:
            out["run.seed"] = int(self.seed)
        if self.only:
            out["run.only"] = list(self.only)
        if self.skip:
            out["run.skip"] = list(self.skip)
        out.update(self.extra)
        return out


class RunHandle:
    """Owns one :class:`RunController` and the thread it runs on."""

    def __init__(
        self,
        session: Session,
        options: RunOptions | None = None,
        *,
        events: EventBus | None = None,
        on_finish: Callable[[RunSummary], None] | None = None,
    ) -> None:
        self.session = session
        self.options = options or RunOptions()
        self.events = events or EventBus()
        self.on_finish = on_finish
        self.controller: RunController | None = None
        self.summary: RunSummary | None = None
        self.error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._finished = threading.Event()

    # ------------------------------------------------------------------ setup
    def prepare(self) -> RunController:
        """Build the controller (loading the session with the run options)."""
        self.session.update(self.options.overrides())
        loaded = self.session.load(force=True)
        stage = self.options.stage or loaded.stage
        output_dir = Path(self.options.output_dir) if self.options.output_dir else None
        sinks = list(self.options.sinks) if self.options.sinks is not None else build_sinks(
            loaded,
            stage=stage,
            output_dir=output_dir,
            site_count=self.options.sites or loaded.site_count(),
            stdf=self.options.stdf,
            stats=self.options.stats,
        )
        self.controller = RunController(
            loaded,
            events=self.events,
            site_count=self.options.sites,
            sinks=sinks,
            stage=stage,
            seed=self.options.seed,
            output_dir=output_dir,
            resume=self.options.resume,
        )
        return self.controller

    # -------------------------------------------------------------- execution
    def run(self) -> RunSummary:
        """Run synchronously in the calling thread."""
        controller = self.controller or self.prepare()
        self.summary = controller.run()
        if self.on_finish is not None:
            self.on_finish(self.summary)
        return self.summary

    def start(self) -> "RunHandle":
        """Start the run in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("run already started")
        self._finished.clear()
        self._thread = threading.Thread(target=self._target, name="pyate-run", daemon=True)
        self._thread.start()
        return self

    def _target(self) -> None:
        try:
            self.run()
        except BaseException as exc:  # noqa: BLE001 - reported through ``error``
            self.error = exc
        finally:
            self._finished.set()

    # ---------------------------------------------------------------- control
    def pause(self) -> None:
        """Pause after the current test item."""
        if self.controller is not None:
            self.controller.pause()

    def resume(self) -> None:
        """Resume a paused run."""
        if self.controller is not None:
            self.controller.resume()

    def stop(self) -> None:
        """Abort the run at the next safe point."""
        if self.controller is not None:
            self.controller.abort()

    def step(self) -> None:
        """Run the next test item, then pause."""
        if self.controller is not None:
            self.controller.step()

    def set_breakpoint(self, test_num: int) -> None:
        """Pause before ``test_num``."""
        if self.controller is not None:
            self.controller.set_breakpoint(test_num)

    def clear_breakpoint(self, test_num: int | None = None) -> None:
        """Drop one breakpoint, or every breakpoint."""
        if self.controller is not None:
            self.controller.clear_breakpoint(test_num)

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the background run to finish; returns True when it did."""
        return self._finished.wait(timeout)

    def join(self, timeout: float | None = None) -> RunSummary | None:
        """Wait for the background run and return its summary."""
        self.wait(timeout)
        if self._thread is not None:
            self._thread.join(timeout)
        if self.error is not None:
            raise self.error
        return self.summary

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        """True while the background thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        """True when the underlying controller is paused."""
        return bool(self.controller and self.controller.paused)

    @property
    def aborted(self) -> bool:
        """True once an abort was requested."""
        return bool(self.controller and self.controller.aborted)

    def stats(self) -> dict[str, Any]:
        """Cheap progress snapshot for the GUI."""
        summary = self.summary
        return {
            "running": self.running,
            "paused": self.paused,
            "aborted": self.aborted,
            "finished": self._finished.is_set(),
            "parts": summary.total_parts if summary else 0,
            "yield_pct": round(summary.yield_pct, 3) if summary else 0.0,
            "error": None if self.error is None else str(self.error),
        }


def create_run(
    session_path: str | Path,
    overrides: dict[str, Any] | None = None,
    options: RunOptions | None = None,
    *,
    events: EventBus | None = None,
) -> RunHandle:
    """Convenience constructor used by the CLI."""
    api_session = Session(session_path, overrides)
    return RunHandle(api_session, options, events=events or EventBus())


def wait_forever(handle: RunHandle, poll: float = 0.1) -> RunSummary:
    """Block until ``handle`` finishes (used by tests and the CLI)."""
    while handle.running:
        time.sleep(poll)
    summary = handle.join()
    if summary is None:  # pragma: no cover - defensive
        raise RuntimeError("run produced no summary")
    return summary
