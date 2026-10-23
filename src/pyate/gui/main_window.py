"""Main window wiring every panel to the shared view models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..api import ConfigProblem, EventBus, Session
from .panels import BatchPanel, ConfigPanel, FlowPanel, LimitsPanel, PatternPanel, RunPanel
from .theme import set_theme as set_canvas_theme, stylesheet
from .viewmodels import BatchViewModel, ConfigViewModel, RunViewModel

__all__ = ["MainWindow"]


class MainWindow(QMainWindow):
    """The pyate GUI shell: one tab per workflow step."""

    def __init__(self, session_path: str | Path | None = None,
                 *, theme: str = "dark") -> None:
        super().__init__()
        self.setWindowTitle("pyate - ATE simulator")
        self.resize(1280, 800)
        self.setMinimumSize(1024, 640)
        self.theme = theme
        self.events = EventBus()
        self.config_vm = ConfigViewModel()
        self.run_vm = RunViewModel().attach(self.events)
        self.batch_vm = BatchViewModel(events=self.events)
        self.session_path: str | None = None

        self.tabs = QTabWidget()
        self.config_panel = ConfigPanel(self.config_vm)
        self.flow_panel = FlowPanel(self.config_vm)
        self.limits_panel = LimitsPanel(self.config_vm)
        self.run_panel = RunPanel(self.run_vm)
        self.batch_panel = BatchPanel(self.batch_vm)
        self.pattern_panel = PatternPanel()

        self.tabs.addTab(self.config_panel, "Config")
        self.tabs.addTab(self.flow_panel, "TestFlow")
        self.tabs.addTab(self.limits_panel, "Limits/Bins")
        self.tabs.addTab(self.run_panel, "Run")
        self.tabs.addTab(self.batch_panel, "Batch")
        self.tabs.addTab(self.pattern_panel, "Pattern")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.tabs)
        self.setCentralWidget(container)

        self.status = QLabel("ready")
        self.statusBar().addWidget(self.status)
        self._build_menu()
        set_canvas_theme(theme)
        self.setStyleSheet(stylesheet(theme))

        self.config_panel.sessionOpened.connect(self.on_session_opened)
        self.config_panel.filesSaved.connect(self.on_files_saved)
        self.config_panel.path_edit.setText(str(session_path) if session_path else "")
        if session_path:
            self.load_session(str(session_path))

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("&Open session...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_dialog)
        file_menu.addAction(open_action)
        open_batch = QAction("Open &batch...", self)
        open_batch.triggered.connect(self._open_batch)
        file_menu.addAction(open_batch)
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")
        for name in ("dark", "light"):
            action = QAction(f"{name} theme", self)
            action.triggered.connect(lambda _checked=False, n=name: self.set_theme(n))
            view_menu.addAction(action)

    def set_theme(self, theme: str) -> None:
        """Switch between the dark and light style sheets."""
        self.theme = theme
        set_canvas_theme(theme)
        self.setStyleSheet(stylesheet(theme))

    # ------------------------------------------------------------------ slots
    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open session", "",
                                              "Session (*.yaml *.yml)")
        if path:
            self.config_panel.path_edit.setText(path)
            self.load_session(path)

    def _open_batch(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open batch", "",
                                              "Batch (*.yaml *.yml)")
        if path:
            self.batch_panel.path_edit.setText(path)
            self.batch_panel._load()
            self.tabs.setCurrentWidget(self.batch_panel)

    def load_session(self, path: str) -> bool:
        """Open a session and populate every panel."""
        result = self.config_vm.open(path)
        self.config_panel.refresh_tree()
        self.config_panel.output.setPlainText(result.text)
        if not result.ok:
            self.status.setText(f"configuration error: {path}")
            QMessageBox.warning(self, "configuration", result.text)
            return False
        self.session_path = path
        try:
            session = Session(path)
            info = session.info()
        except ConfigProblem as exc:  # pragma: no cover - guarded by validate
            self.status.setText(str(exc))
            return False
        self.run_panel.session_path = path
        self.run_panel.set_session(session)
        self.flow_panel.refresh()
        self.limits_panel.refresh()
        self.status.setText(f"{info.name}: stage {info.stage}, {info.sites} site(s), "
                            f"dut {info.dut_model}")
        self.tabs.setCurrentWidget(self.run_panel)
        return True

    def on_session_opened(self, path: str) -> None:
        """Called by the config panel after a successful load."""
        self.load_session(path)

    def on_files_saved(self, path: str) -> None:
        """Refresh panels that read the session after a file was written."""
        self.flow_panel.refresh()
        self.limits_panel.refresh()
        if self.config_vm.session is not None:
            self.run_panel.set_session(self.config_vm.session)
        self.status.setText(f"saved {path}")

    # ------------------------------------------------------------------ misc
    def run_smoke(self, *, sites: int | None = None, stage: str | None = None) -> Any:
        """Start a run from code (used by the GUI smoke test)."""
        if self.session_path is None:
            raise RuntimeError("no session loaded")
        if sites:
            self.run_panel.sites_spin.setValue(sites)
        if stage:
            self.run_panel.stage_box.setCurrentText(stage)
        self.run_panel.start_run()
        return self.run_panel.handle
