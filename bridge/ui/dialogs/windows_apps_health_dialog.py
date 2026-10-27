"""Windows Apps Health Check. Shows scan_windows_apps_health()'s findings and
offers to repair the safely-automatable ones (missing placeholders only,
everything else needs a human decision)."""

from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from bridge.services import windows_apps_service as svc
from bridge.ui.widgets.card import Card
from shared.qt_theme import Fonts, GREEN, TEXT_DIM

_SECTIONS = [
    ("missing_placeholders", "Missing placeholders", "Safe to repair automatically."),
    ("orphan_placeholders", "Orphan placeholders", "A .pcgame file has no mapping. Left untouched."),
    ("missing_executables", "Missing executables", "The EXE is missing or invalid. Fix or remove the mapping."),
    ("uninstalled_steam", "Steam games not installed", "Steam has no installed manifest for it right now. Left untouched."),
    ("invalid_uris", "Invalid URIs", "Edit or remove the URI mapping."),
    ("invalid_entries", "Invalid/unknown entries", "Malformed entry or unknown launch type."),
]


class WindowsAppsHealthDialog(QDialog):
    def __init__(self, parent, windows_dir: Path, on_repaired):
        super().__init__(parent)
        self.setWindowTitle("Windows Apps Health Check")
        self.resize(760, 580)
        self._windows_dir = windows_dir
        self._on_repaired = on_repaired

        apps = svc.load_windows_apps()
        self._findings = svc.scan_windows_apps_health(apps, windows_dir, svc.installed_steam_ids())
        problem_total = sum(len(v) for v in self._findings.values())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)

        title = QLabel("Windows Apps Health Check")
        title.setFont(Fonts.heading())
        layout.addWidget(title)
        status = QLabel("Everything looks healthy." if problem_total == 0 else f"Found {problem_total} item(s) worth reviewing.")
        status.setStyleSheet(f"color: {GREEN if problem_total == 0 else TEXT_DIM};")
        layout.addWidget(status)

        summary = Card()
        summary_layout = QVBoxLayout(summary)
        for key, label, _note in _SECTIONS + [("duplicate_steam_ids", "Duplicate Steam App IDs", "")]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch(1)
            count = len(self._findings[key])
            count_label = QLabel(str(count))
            count_label.setStyleSheet(f"color: {GREEN if count == 0 else ''};")
            row.addWidget(count_label)
            summary_layout.addLayout(row)
        layout.addWidget(summary)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFont(Fonts.mono())
        self._fill_details()
        layout.addWidget(self.details, 1)

        controls = QHBoxLayout()
        self.repair_button = QPushButton("Repair Safe Issues")
        self.repair_button.setObjectName("accent")
        self.repair_button.clicked.connect(self._repair_safe)
        controls.addWidget(self.repair_button)
        controls.addStretch(1)
        close_button = QPushButton("Close")
        close_button.setObjectName("ghost")
        close_button.clicked.connect(self.accept)
        controls.addWidget(close_button)
        layout.addLayout(controls)

    def _fill_details(self) -> None:
        lines = []
        for key, title, note in _SECTIONS:
            items = self._findings[key]
            if not items:
                continue
            lines.append(f"{title} ({len(items)})")
            lines.append(note)
            lines.extend(f"  • {item}" for item in items)
            lines.append("")
        if self._findings["duplicate_steam_ids"]:
            lines.append(f"Duplicate Steam App IDs ({len(self._findings['duplicate_steam_ids'])})")
            lines.append("Several mappings share a Steam App ID. Left untouched.")
            for appid, names in self._findings["duplicate_steam_ids"]:
                lines.append(f"  • {appid}: {', '.join(names)}")
            lines.append("")
        if not lines:
            lines.append("No issues found.")
        self.details.setPlainText("\n".join(lines))

    def _repair_safe(self) -> None:
        names = self._findings["missing_placeholders"]
        if not names:
            self.details.appendPlainText("\nNothing to repair.")
            return
        repaired = svc.repair_missing_placeholders(self._windows_dir, names)
        self._on_repaired()
        self.details.appendPlainText(f"\nRecreated {repaired} missing placeholder(s).")
        self.repair_button.setEnabled(False)
