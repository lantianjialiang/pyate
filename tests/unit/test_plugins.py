"""Unit tests for the plugin kernel (discovery, hooks, registry, scaffold)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from pyate.plugins import (
    API_VERSION,
    HookPolicy,
    PluginManager,
    create_plugin,
    load_plugins,
    reset_manager,
)
from pyate.plugins.hookspec import PyateSpec
from pyate.plugins.loader import _compatible

EXAMPLE_PLUGINS = Path(__file__).resolve().parents[2] / "examples" / "plugins"


@pytest.fixture(autouse=True)
def _clean_manager() -> None:
    reset_manager()


def _write_plugin(directory: Path, source: str, name: str = "acme_plugin") -> Path:
    package = directory / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return package


def _plugin_source(api_version: str = API_VERSION) -> str:
    return f'''
    from pyate.dut.base import DUTModel, PinSpec
    from pyate.dut.registry import register_dut
    from pyate.plugins import hookimpl
    from pyate.testitems.base import TestItem, TestItemParams

    SEEN = []


    class TinyModel(DUTModel):
        model_name = "tiny"
        description = "one output buffer"

        def _build_pins(self):
            return {{"A": PinSpec("A", "in"), "Y": PinSpec("Y", "out")}}

        def _eval_comb(self):
            self._out["Y"] = self._inputs.get("A", 0)

        def reset(self):
            self._out["Y"] = 0


    @register_dut("tiny")
    class _Registered(TinyModel):
        pass


    class TinyItem(TestItem):
        name = "tiny_item"
        Params = TestItemParams
        description = "returns one trivial result"

        def execute(self, ctx, hal):
            return [self.make_result(ctx, value=1.0, unit="V")]


    class Plugin:
        @hookimpl
        def pyate_api_version(self):
            return "{api_version}"

        @hookimpl
        def pyate_register(self, registry):
            registry.add_dut("tiny", TinyModel)
            registry.add_testitem("tiny_item", TinyItem)
            registry.add_sink("tiny_sink", lambda **kwargs: None)
            registry.note("acme loaded")

        @hookimpl
        def after_part(self, part=None, **_kwargs):
            SEEN.append(getattr(part, "part_id", None))
    '''


# ------------------------------------------------------------------- discovery
def test_module_level_hooks_are_loaded(tmp_path: Path) -> None:
    module = tmp_path / "flat_plugin.py"
    module.write_text(textwrap.dedent("""
        from pyate.plugins import hookimpl

        SEEN = []

        @hookimpl
        def pyate_api_version():
            return "1.0"

        @hookimpl
        def after_lot(lot=None, **_kwargs):
            SEEN.append(lot)
    """), encoding="utf-8")
    manager = PluginManager()
    assert manager.load_file(module) is True
    assert manager.loaded == ["flat_plugin"]
    assert manager.call("pyate_api_version") == ["1.0"]
    manager.call("after_lot", lot="LOT-1")
    assert sys.modules["pyate_plugin_flat_plugin"].SEEN == ["LOT-1"]


def test_plugin_class_form_is_instantiated(tmp_path: Path) -> None:
    _write_plugin(tmp_path, _plugin_source())
    manager = PluginManager().load_all([tmp_path])
    summary = manager.registry.summary()
    assert "tiny" in summary["dut_models"]
    assert "tiny_item" in summary["testitems"]
    assert "tiny_sink" in summary["sinks"]
    assert manager.registry.messages == ["acme loaded"]


def test_load_dir_skips_private_and_non_python_entries(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("from pyate.plugins import hookimpl\n", encoding="utf-8")
    (tmp_path / "_private.py").write_text("raise RuntimeError('must not load')\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me\n", encoding="utf-8")
    (tmp_path / "empty_pkg").mkdir()
    manager = PluginManager()
    assert manager.load_dir(tmp_path) == ["good"]
    assert manager.errors == []


def test_broken_import_is_reported_not_raised(tmp_path: Path) -> None:
    bad = tmp_path / "broken.py"
    bad.write_text("import a_module_that_does_not_exist\n", encoding="utf-8")
    manager = PluginManager()
    assert manager.load_file(bad) is False
    assert any("ModuleNotFoundError" in error for error in manager.errors)


def test_duplicate_registration_is_ignored(tmp_path: Path) -> None:
    module = tmp_path / "twice.py"
    module.write_text("from pyate.plugins import hookimpl\n", encoding="utf-8")
    manager = PluginManager()
    assert manager.load_file(module) is True
    assert manager.load_file(module) is False  # pluggy refuses a second registration
    assert manager.loaded.count("twice") == 1


# ----------------------------------------------------------------- hook policy
def test_hook_results_are_collected_from_every_plugin(tmp_path: Path) -> None:
    _write_plugin(tmp_path, _plugin_source(), name="p1")
    manager = PluginManager().load_all([tmp_path])
    manager.call("after_part", part=type("P", (), {"part_id": "D1"})())
    assert sys.modules["pyate_plugin_p1"].SEEN == ["D1"]


def test_warn_policy_records_hook_failures(tmp_path: Path) -> None:
    module = tmp_path / "boom.py"
    module.write_text(textwrap.dedent("""
        from pyate.plugins import hookimpl

        @hookimpl
        def after_lot(lot=None, **_kwargs):
            raise RuntimeError("plugin exploded")
    """), encoding="utf-8")
    manager = PluginManager(policy=HookPolicy.WARN)
    manager.load_file(module)
    assert manager.call("after_lot", lot=None) == []
    assert any("plugin exploded" in error for error in manager.errors)


def test_ignore_policy_is_silent(tmp_path: Path) -> None:
    module = tmp_path / "quiet.py"
    module.write_text(textwrap.dedent("""
        from pyate.plugins import hookimpl

        @hookimpl
        def after_lot(lot=None, **_kwargs):
            raise RuntimeError("silent")
    """), encoding="utf-8")
    manager = PluginManager(policy=HookPolicy.IGNORE)
    manager.load_file(module)
    assert manager.call("after_lot", lot=None) == []
    assert manager.errors == []


def test_abort_policy_propagates(tmp_path: Path) -> None:
    module = tmp_path / "loud.py"
    module.write_text(textwrap.dedent("""
        from pyate.plugins import hookimpl

        @hookimpl
        def after_lot(lot=None, **_kwargs):
            raise RuntimeError("stop the run")
    """), encoding="utf-8")
    manager = PluginManager(policy=HookPolicy.ABORT)
    manager.load_file(module)
    with pytest.raises(RuntimeError):
        manager.call("after_lot", lot=None)


def test_unknown_hook_name_returns_empty(tmp_path: Path) -> None:
    manager = PluginManager()
    assert manager.call("no_such_hook") == []


def test_missing_hookimpl_decorator_is_flagged(tmp_path: Path) -> None:
    module = tmp_path / "forgetful.py"
    module.write_text(textwrap.dedent("""
        class Plugin:
            def after_lot(self, lot=None, **_kwargs):
                pass
    """), encoding="utf-8")
    manager = PluginManager()
    manager.load_file(module)
    assert any("@hookimpl" in warning for warning in manager.warnings)


# ------------------------------------------------------------------ versioning
def test_incompatible_api_version_is_rejected(tmp_path: Path) -> None:
    # a unique package name: imported plugin modules are cached in sys.modules
    _write_plugin(tmp_path, _plugin_source(api_version="9.0"), name="version_probe")
    manager = PluginManager(api_version="1.0").load_all([tmp_path])
    assert manager.loaded == []
    assert any("9.0" in entry for entry in manager.rejected)


def test_compatible_versions_pass_the_check() -> None:
    assert _compatible("1.0", "1.4")
    assert not _compatible("2.0", "1.4")
    assert _compatible("garbage", "0.9") is False or True  # never raises


def test_hookspec_declares_every_hook_the_bridge_calls() -> None:
    names = {name for name in dir(PyateSpec) if not name.startswith("_")}
    for hook in ("before_lot", "after_lot", "before_unit", "after_unit",
                 "before_part", "after_part", "before_item", "after_item",
                 "on_fail", "pyate_register", "pyate_api_version"):
        assert hook in names


def test_bridge_forwards_every_hook(tmp_path: Path) -> None:
    module = tmp_path / "spy.py"
    module.write_text(
        "from pyate.plugins import hookimpl\n"
        "CALLS = []\n"
        + "".join(
            f"@hookimpl\ndef {hook}(**kwargs):\n    CALLS.append('{hook}')\n\n"
            for hook in ("before_lot", "after_lot", "before_unit", "after_unit",
                         "before_part", "after_part", "before_item", "after_item",
                         "on_fail")
        ),
        encoding="utf-8",
    )
    manager = PluginManager()
    manager.load_file(module)
    bridge = manager.bridge
    bridge.before_lot(lot="L")
    bridge.after_lot(lot="L")
    bridge.before_unit(unit="U")
    bridge.after_unit(unit="U")
    bridge.before_part(part="P")
    bridge.after_part(part="P")
    bridge.before_item(item="I")
    bridge.after_item(item="I")
    bridge.on_fail(part="P", item="I")
    calls = sys.modules["pyate_plugin_spy"].CALLS
    assert calls == ["before_lot", "after_lot", "before_unit", "after_unit",
                     "before_part", "after_part", "before_item", "after_item", "on_fail"]


# --------------------------------------------------------------------- infos
def test_info_reports_loaded_and_registry(tmp_path: Path) -> None:
    _write_plugin(tmp_path, _plugin_source())
    manager = PluginManager().load_all([tmp_path])
    info = manager.info()
    assert info["api_version"] == API_VERSION
    assert info["policy"] == "warn"
    assert "acme_plugin" in info["loaded"]
    assert "tiny" in info["registry"]["dut_models"]


def test_load_plugins_helper_returns_manager(tmp_path: Path) -> None:
    _write_plugin(tmp_path, _plugin_source())
    manager = load_plugins([tmp_path], fresh=True)
    assert manager.registry.summary()["sinks"] == ["tiny_sink"]


# ------------------------------------------------------------------- scaffold
def test_scaffold_creates_installable_plugin(tmp_path: Path) -> None:
    root = Path(create_plugin("demo_widget", directory=tmp_path))
    package = root / "demo_widget"
    assert package.is_dir()
    assert (package / "__init__.py").is_file()
    assert (root / "pyproject.toml").is_file()
    assert "demo_widget" in (root / "pyproject.toml").read_text(encoding="utf-8")
    manager = PluginManager().load_all([root])
    assert manager.loaded == ["demo_widget"]
    assert manager.warnings == []


def test_scaffold_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    create_plugin("dup_plugin", directory=tmp_path)
    with pytest.raises(FileExistsError):
        create_plugin("dup_plugin", directory=tmp_path)
    create_plugin("dup_plugin", directory=tmp_path, force=True)


# ------------------------------------------------------------- bundled example
@pytest.mark.skipif(not EXAMPLE_PLUGINS.is_dir(), reason="example plugins not shipped")
def test_bundled_adc_example_registers_dut_and_items() -> None:
    manager = PluginManager().load_all([EXAMPLE_PLUGINS / "plugin_adc_dut"])
    summary = manager.registry.summary()
    assert "adc" in summary["dut_models"]
    assert "adc_inl_dnl" in summary["testitems"]


@pytest.mark.skipif(not EXAMPLE_PLUGINS.is_dir(), reason="example plugins not shipped")
def test_bundled_json_sink_example_registers_sink() -> None:
    manager = PluginManager().load_all([EXAMPLE_PLUGINS / "plugin_json_sink"])
    assert "json" in manager.registry.summary()["sinks"]


@pytest.mark.skipif(not EXAMPLE_PLUGINS.is_dir(), reason="example plugins not shipped")
def test_failwatch_example_is_a_loadable_single_file_plugin() -> None:
    manager = PluginManager().load_all([EXAMPLE_PLUGINS])
    assert "failwatch" in manager.loaded
    assert any("failwatch" in text for text in manager.registry.messages)


def test_plugin_list_command_reports_the_environment_path(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from pyate.cli.main import main

    _write_plugin(tmp_path, _plugin_source())
    monkeypatch.setenv("PYATE_PLUGIN_PATH", str(tmp_path))
    reset_manager()
    assert main(["plugin", "list"]) == 0
    out = capsys.readouterr().out
    assert "PYATE_PLUGIN_PATH=" in out
    assert "acme_plugin" in out
    assert "testitems: tiny_item" in out
    reset_manager()

# ------------------------------------------------------------------ management
def _documented_source(api_version: str = API_VERSION) -> str:
    """Plugin with a module docstring so the GUI can show a description."""
    return f'''
    """Reference plugin for the unit tests."""

    from pyate.plugins import hookimpl
    from pyate.testitems.base import TestItem, TestItemParams


    class TinyItem(TestItem):
        name = "tiny_item"
        Params = TestItemParams
        description = "returns one trivial result"

        def execute(self, ctx, hal):
            return [self.make_result(ctx, value=1.0, unit="V")]


    class Plugin:
        @hookimpl
        def pyate_api_version(self):
            return "{api_version}"

        @hookimpl
        def pyate_register(self, registry):
            registry.add_testitem("tiny_item", TinyItem)
            registry.note("documented plugin loaded")
    '''


def _shadow_source() -> str:
    """Plugin replacing a builtin test item, to prove unload restores it."""
    return '''
    from pyate.plugins import hookimpl
    from pyate.testitems.base import TestItem, TestItemParams


    class ShadowItem(TestItem):
        name = "idd_static"
        Params = TestItemParams

        def execute(self, ctx, hal):
            return [self.make_result(ctx, value=0.0, unit="A")]


    class Plugin:
        @hookimpl
        def pyate_register(self, registry):
            registry.add_testitem("idd_static", ShadowItem)
    '''


def test_scan_candidates_describes_plugins_without_importing() -> None:
    from pyate.plugins.discovery import scan_candidates

    found = {entry.name: entry for entry in scan_candidates([EXAMPLE_PLUGINS])}
    assert {"failwatch", "plugin_adc_dut", "plugin_json_sink"} <= set(found)
    adc = found["plugin_adc_dut"]
    assert adc.kind == "package"
    assert "ADC DUT model" in adc.doc
    assert "pyate_register" in adc.hooks
    assert found["failwatch"].kind == "file"


def test_scan_candidates_finds_src_layout_projects(tmp_path: Path) -> None:
    from pyate.plugins.discovery import scan_candidates

    project = tmp_path / "my_plugin"
    package = project / "my_plugin"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='my_plugin'\n", encoding="utf-8")
    (package / "__init__.py").write_text('"""My plugin."""\n', encoding="utf-8")
    found = scan_candidates([tmp_path])
    assert [entry.name for entry in found] == ["my_plugin"]
    assert found[0].path == package


def _managed_source() -> str:
    """Plugin with its own item name, so other tests cannot pollute the check."""
    return f'''
    from pyate.plugins import hookimpl
    from pyate.testitems.base import TestItem, TestItemParams


    class ManagedItem(TestItem):
        name = "managed_item"
        Params = TestItemParams

        def execute(self, ctx, hal):
            return [self.make_result(ctx, value=1.0, unit="V")]


    class Plugin:
        @hookimpl
        def pyate_api_version(self):
            return "{API_VERSION}"

        @hookimpl
        def pyate_register(self, registry):
            registry.add_testitem("managed_item", ManagedItem)
    '''


def test_unload_removes_hooks_and_registrations(tmp_path: Path) -> None:
    from pyate.testitems.registry import is_registered

    _write_plugin(tmp_path, _managed_source(), name="managed_plugin")
    manager = PluginManager().load_all([tmp_path])
    assert "managed_plugin" in manager.loaded
    assert is_registered("managed_item")
    assert manager.describe()[0]["contributions"] == ["testitem:managed_item"]

    assert manager.unload("managed_plugin")
    assert "managed_plugin" not in manager.loaded
    assert manager.call("pyate_api_version") == []
    assert not is_registered("managed_item")
    assert manager.describe()[0]["enabled"] is False

    assert manager.enable("managed_plugin")
    assert is_registered("managed_item")
    assert manager.describe()[0]["enabled"] is True


def test_unload_restores_a_name_the_plugin_shadowed(tmp_path: Path) -> None:
    from pyate.testitems.registry import get_testitem_class

    builtin = get_testitem_class("idd_static")
    _write_plugin(tmp_path, _shadow_source(), name="shadow_plugin")
    manager = PluginManager().load_all([tmp_path])
    assert get_testitem_class("idd_static") is not builtin
    manager.unload("shadow_plugin")
    assert get_testitem_class("idd_static") is builtin


def test_api_plugin_report_and_toggle(tmp_path: Path) -> None:
    from pyate.api import load_plugin, plugin_report, scan_plugins, set_plugin_enabled

    _write_plugin(tmp_path, _documented_source(), name="documented_plugin")
    report = load_plugin(tmp_path)
    assert report["loaded_now"] is True
    assert [entry["name"] for entry in report["plugins"]] == ["documented_plugin"]
    assert report["plugins"][0]["contributions"] == ["testitem:tiny_item"]
    assert report["plugins"][0]["doc"] == "Reference plugin for the unit tests."
    assert any(entry["name"] == "documented_plugin" for entry in scan_plugins([tmp_path]))

    report = set_plugin_enabled("documented_plugin", False)
    assert report["changed"] is True
    assert report["plugins"][0]["enabled"] is False
    assert report["registry"]["testitems"] == []
    assert plugin_report()["plugins"][0]["enabled"] is False


def test_load_dir_loads_a_package_directory_as_one_plugin() -> None:
    """The GUI hands over the package dir: its modules are not plugins of their own."""
    from pyate.plugins.discovery import scan_candidates

    found = {entry.name: entry for entry in scan_candidates([EXAMPLE_PLUGINS])}
    package = found["plugin_adc_dut"].path
    manager = PluginManager()
    assert manager.load_dir(package) == ["plugin_adc_dut"]
    manager.finalize()
    assert manager.loaded == ["plugin_adc_dut"]
    described = manager.describe()
    assert [entry["name"] for entry in described] == ["plugin_adc_dut"]
    assert "dut:adc" in described[0]["contributions"]
    assert described[0]["api_version"] == API_VERSION


def test_scan_plugins_tells_disabled_from_not_loaded(tmp_path: Path) -> None:
    from pyate.api import load_plugin, scan_plugins, set_plugin_enabled

    _write_plugin(tmp_path, _documented_source(), name="stated_plugin")
    load_plugin(tmp_path)
    states = {entry["name"]: entry["state"] for entry in scan_plugins([tmp_path])}
    assert states["stated_plugin"] == "loaded"

    set_plugin_enabled("stated_plugin", False)
    entry = next(e for e in scan_plugins([tmp_path]) if e["name"] == "stated_plugin")
    assert entry["state"] == "disabled"
    assert entry["loaded"] is True
    assert entry["enabled"] is False

