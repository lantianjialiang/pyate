"""Main window wiring every panel to the shared view models."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..api import ConfigProblem, EventBus, EventKind, ItemRunReport, Session, run_item
from .app import application_icon
from .panels import ConfigPanel, FlowPanel, LimitsPanel, PatternPanel, RunPanel
from .plugin_panel import PluginPanel
from .stdf_panel import StdfPanel
from .theme import set_theme as set_canvas_theme
from .theme import stylesheet
from .viewmodels import ConfigViewModel, RunViewModel

__all__ = ["MainWindow"]


class MainWindow(QMainWindow):
    """The pyate GUI shell: one tab per workflow step."""

    itemRunFinished = Signal(object)

    def __init__(self, session_path: str | Path | None = None,
                 *, theme: str = "light") -> None:
        super().__init__()
        self.setWindowIcon(application_icon())
        self.setWindowTitle("pyate - ATE simulator")
        self.resize(1280, 800)
        self.setMinimumSize(1024, 640)
        self.theme = theme
        self.events = EventBus()
        self.config_vm = ConfigViewModel()
        self.run_vm = RunViewModel().attach(self.events)
        self.session_path: str | None = None

        self.tabs = QTabWidget()
        self.config_panel = ConfigPanel(self.config_vm)
        self.flow_panel = FlowPanel(self.config_vm)
        self.limits_panel = LimitsPanel(self.config_vm)
        self.run_panel = RunPanel(self.run_vm)
        self.pattern_panel = PatternPanel()
        self.stdf_panel = StdfPanel()
        self.plugin_panel = PluginPanel()

        self.tabs.addTab(self.config_panel, "Config")
        self.tabs.addTab(self.flow_panel, "TestFlow")
        self.tabs.addTab(self.limits_panel, "Limits/Bins")
        self.tabs.addTab(self.run_panel, "Run")
        self.tabs.addTab(self.pattern_panel, "Pattern")
        self.tabs.addTab(self.stdf_panel, "STDF")
        self.tabs.addTab(self.plugin_panel, "Plugins")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.tabs)
        self.setCentralWidget(container)

        self.status = QLabel("ready")
        self.statusBar().addWidget(self.status)
        self.copyright = QLabel("© lantianjialiang")
        self.statusBar().addPermanentWidget(self.copyright)
        self._build_menu()
        set_canvas_theme(theme)
        self.setStyleSheet(stylesheet(theme))

        self.config_panel.sessionOpened.connect(self.on_session_opened)
        self.config_panel.filesSaved.connect(self.on_files_saved)
        self.flow_panel.runItemRequested.connect(self.run_single_item)
        self.plugin_panel.pluginsChanged.connect(self.status.setText)
        self.itemRunFinished.connect(self._on_item_run_finished)
        self._item_thread: threading.Thread | None = None
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
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")
        self.theme_actions: dict[str, QAction] = {}
        themes = QActionGroup(self)
        themes.setExclusive(True)
        for name in ("light", "dark"):
            action = QAction(f"{name.capitalize()} theme", self)
            action.setCheckable(True)
            action.setChecked(name == self.theme)
            action.triggered.connect(lambda _checked=False, n=name: self.set_theme(n))
            themes.addAction(action)
            view_menu.addAction(action)
            self.theme_actions[name] = action

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About pyate", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

        tools_menu = self.menuBar().addMenu("&Tools")
        plugins = QAction("&Plugins...", self)
        plugins.setShortcut("Ctrl+Shift+P")
        plugins.triggered.connect(self.show_plugins)
        tools_menu.addAction(plugins)

    def show_plugins(self) -> None:
        """Switch to the plugin page (refreshed, so it reflects what runs will use)."""
        self.plugin_panel.refresh()
        self.tabs.setCurrentWidget(self.plugin_panel)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About pyate",
            "pyate — offline ATE simulator\n\nMIT License\n© lantianjialiang",
        )

    def set_theme(self, theme: str) -> None:
        """Switch between the dark and light style sheets."""
        self.theme = "light" if str(theme).lower() == "light" else "dark"
        set_canvas_theme(self.theme)
        self.setStyleSheet(stylesheet(self.theme))
        for name, action in getattr(self, "theme_actions", {}).items():
            action.setChecked(name == self.theme)

    # ------------------------------------------------------------------ slots
    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open session", "",
                                              "Session (*.yaml *.yml)")
        if path:
            self.config_panel.path_edit.setText(path)
            self.load_session(path)

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
        self.flow_panel.set_sites(info.sites)
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

    # ------------------------------------------------------------ single item
    def run_single_item(self, request: dict[str, Any]) -> None:
        """Run one flow item off the wafer and show it on the Run page.

        A run parked at a breakpoint does not touch the HAL, so the debug run is
        allowed there too: it builds its own DUT and publishes on its own bus,
        which keeps the paused run's data and STDF file clean.
        """
        if self.session_path is None:
            self.status.setText("open a session first")
            self.flow_panel.set_item_status("open a session first")
            return
        paused = self.run_panel.is_paused()
        if self.run_vm.running and not paused:
            self.flow_panel.set_item_status("stop the run first")
            QMessageBox.information(self, "single item", "stop the run before running a single item")
            return
        if request.get("clear"):
            if paused:
                # clearing stops the live run, so its data is kept and the
                # debug result is simply appended to it
                self.run_panel.log_view.appendPlainText(
                    f"run paused: {request.get('name')} results are appended to the run data")
            else:
                self.run_panel.clear_results()
        bus = self._item_bus()
        self.tabs.setCurrentWidget(self.run_panel)
        self.status.setText(f"running {request.get('name')} #{request.get('test_num')} ...")
        self._item_thread = threading.Thread(
            target=self._item_worker, args=(dict(request), bus), name="pyate-item", daemon=True
        )
        self._item_thread.start()

    def _item_bus(self) -> EventBus:
        """Bus a single item run publishes on, with the Run page listening.

        A live run owns the view model and its bus also feeds the run's datalog
        sinks, so the debug results must not go there - they would end up in the
        run's STDF. They use a private bus that only the view model watches.
        """
        if self.run_vm.events is self.events:
            return self.events
        bus = EventBus()
        bus.subscribe(EventKind.TEST_RESULT, self.run_vm.on_event)
        bus.subscribe(EventKind.LOG, self.run_vm.on_event)
        return bus

    def _item_worker(self, request: dict[str, Any], bus: EventBus) -> None:
        try:
            report = self._run_single_item_now(request, events=bus)
        except Exception as exc:  # surfaced on the main thread
            self.itemRunFinished.emit({"error": str(exc)})
            return
        self.itemRunFinished.emit(report)

    def _run_single_item_now(self, request: dict[str, Any], *,
                             events: EventBus | None = None) -> ItemRunReport:
        """Synchronous core of :meth:`run_single_item` (also used by tests)."""
        if self.session_path is None:
            raise RuntimeError("no session loaded")
        site = int(request.get("site") or 0)
        return run_item(
            Session(self.session_path),
            name=str(request.get("name") or ""),
            test_num=int(request.get("test_num") or 0),
            stage=request.get("stage") or None,
            sites=[site] if site else None,
            params=request.get("params"),
            events=events or self._item_bus(),
        )

    def _on_item_run_finished(self, report: Any) -> None:
        if isinstance(report, dict) and report.get("error"):
            text = f"item run failed: {report['error']}"
            self.status.setText(text)
            self.flow_panel.set_item_status(text)
            self.run_panel.log_view.appendPlainText(text)
            return
        text = report.text()
        self.status.setText(text)
        self.flow_panel.set_item_status(text)
        self.run_panel.log_view.appendPlainText(f"item run: {text}")

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
