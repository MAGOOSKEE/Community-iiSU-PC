"""
Streams live setup/uninstall/install logs to the UI. A Signal(str) already
marshals safely from a background thread to the main thread on its own (Qt
queues the delivery), so writing to the log is one emit-and-forget call.
"""

from PySide6.QtCore import QObject, Signal


class LogStreamRedirector(QObject):
    """A writable stream (assignable to sys.stdout) that emits text_written
    per write() call, so a background thread's plain print() calls can
    reach a Qt log widget without the printing code needing to know a GUI
    exists, same role as the old QueueWriter."""

    text_written = Signal(str)

    def write(self, text: str) -> None:
        if text:
            self.text_written.emit(text)

    def flush(self) -> None:
        pass
