"""
Qt replacement for the old tkinter pattern of `threading.Thread(...).start()`
followed by `self.after(0, callback, *args)` once the background work is
done (~30+ call sites across the old bridge/manager.py). A QThreadPool
worker plus queued signals gets the same "do I/O off the main thread, then
safely touch widgets back on the main thread" result, since Qt already
marshals a signal emitted from a worker thread onto its receiver's own
thread automatically (a queued connection), no extra plumbing needed
for that part.
"""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)


class _Runnable(QRunnable):
    def __init__(self, fn: Callable, args: tuple, kwargs: dict, signals: WorkerSignals):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:  # noqa: BLE001; reported to the caller's on_error either way
            self._emit_safely(self._signals.error, str(e))
        else:
            self._emit_safely(self._signals.finished, result)

    @staticmethod
    def _emit_safely(signal, value) -> None:
        # A worker can still be mid-run when the window/app closes, e.g.
        # HomePage's 2-second status poll, and by the time it's done, the
        # WorkerSignals QObject its caller held (self._status_signals etc.)
        # may already be gone along with the rest of the widget tree.
        # emit() on a deleted QObject raises RuntimeError from a background
        # thread; nobody's listening at that point anyway, so it's safe to
        # just drop the result instead of spewing an uncaught-thread-
        # exception traceback on ordinary shutdown.
        try:
            signal.emit(value)
        except RuntimeError:
            pass


def run_in_background(
    fn: Callable[..., Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    *args,
    **kwargs,
) -> WorkerSignals:
    """Runs fn(*args, **kwargs) on a QThreadPool worker thread; on_done(result)
    or on_error(message) fires back on the Qt main thread once it's done.

    Callers must keep the returned WorkerSignals alive (e.g. `self._x_signals
    = run_in_background(...)`) until on_done/on_error fires, same reason
    the old code kept its QueueWriter/thread references on self rather than
    as a throwaway local."""
    signals = WorkerSignals()
    if on_done is not None:
        signals.finished.connect(on_done)
    if on_error is not None:
        signals.error.connect(on_error)
    QThreadPool.globalInstance().start(_Runnable(fn, args, kwargs, signals))
    return signals
