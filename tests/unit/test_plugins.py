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
