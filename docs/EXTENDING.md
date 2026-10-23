# 扩展指南

三种最常用的扩展：加一个 **TestItem**、加一个 **DUT 模型**、加一个 **datalog sink**。
三者都可以做成插件包，**不需要改 core 源码**。

## 1. 插件包长什么样

一个插件就是一个普通 Python 包，入口类实现 pluggy hook：

```python
# my_plugin/__init__.py
from typing import Any
from pyate.plugins.hookspec import API_VERSION, hookimpl

class Plugin:
    @hookimpl
    def pyate_api_version(self) -> str:
        return API_VERSION          # 必须与 core 的 API_VERSION 兼容（1.0）

    @hookimpl
    def pyate_register(self, registry: Any) -> None:
        registry.add_testitem("my_item", MyItem)
        registry.add_dut("my_dut", MyModel)
        registry.note("my_plugin loaded")   # 出现在 plugin info 里
```

`registry` 提供：

| 方法 | 用途 |
|---|---|
| `add_testitem(name, cls)` | 注册测试项 |
| `add_dut(name, cls)` | 注册 DUT 模型 |
| `note(text)` | 记录一条加载信息 |
| `warnings` | 加载器给出的告警（例如忘了 `@hookimpl`） |

### 装到哪里

按顺序尝试三种发现方式：

1. **entry points**：包 `pyproject.toml` 里声明

   ```toml
   [project.entry-points."pyate.plugins"]
   my_plugin = "my_plugin:Plugin"
   ```

2. **目录扫描**：`PYATE_PLUGINS` 环境变量或 `plugin_dirs` 指向的目录，里面的
   `pyate_plugin_*` 包/模块会被导入。
3. **显式加载**：`pyate.api.Session` 的 `plugins=[...]`。

脚手架空包：

```bash
pyate plugin new my_plugin --dir examples/plugins
```

> 只实现了 hook 方法但忘了 `@hookimpl` 是最常见的坑，加载器会把它记到 `warnings` 里，
> 可以用 `pyate.plugins.loader.PluginManager(...).info()` 看到。

### 全部 hook

`pyate_register` 之外都是可选的运行期钩子，参数一律关键字传参：

| hook | 时机 |
|---|---|
| `before_lot(run=...)` / `after_lot(summary=...)` | 整轮前后 |
| `before_unit(unit=...)` / `after_unit(summary=...)` | 每片 wafer / 每个 tray 前后 |
| `before_part(ctx=..., run_ctx=...)` | 每个 part 第一个测试项之前 |
| `after_part(ctx=..., part=...)` | 每个 part 分 bin 之后 |
| `before_item(ctx=..., item=...)` / `after_item(ctx=..., item=..., results=...)` | 每个测试项前后（`after_item` 可改结果） |
| `on_fail(ctx=..., item=..., results=...)` | 测试项产生 fail 时 |

## 2. 写一个 TestItem

```python
from pyate.engine.context import SiteContext, TestResult
from pyate.hal.base import HALBackend
from pyate.testitems.base import TestItem, TestItemParams
from pyate.testitems.registry import register_testitem

class VbgParams(TestItemParams):
    pin: str = "VBG"
    samples: int = 4
    fail_code: int = 6

@register_testitem("vbg")
class VbgItem(TestItem):
    """Measures the bandgap voltage through the PMU."""

    name = "vbg"
    Params = VbgParams
    description = "bandgap reference voltage"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        res = hal.site(ctx.site_id)
        res.pmu.connect(self.params.pin)
        volts = [res.pmu.measure_voltage(self.params.pin)
                 for _ in range(max(1, self.params.samples))]
        return [self.make_result(
            ctx,
            name="VBG",
            value=sum(volts) / len(volts),
            unit="V",
            lo=1.15, hi=1.35,
            pin=self.params.pin,
        )]
```

规则：

- `execute()` 只返回**原始量测**，判定交给执行引擎（`limits.csv` 优先，其次 `default_limits()`）。
- 参数 schema 用 `TestItemParams` 子类（`extra="forbid"`，写错字段名会立刻报错）。
- 用 `self.make_result(...)` 构造结果：`cycle_cnt`/`num_fail`/`fail_pins` 等字段会自动
  落到 `TestResult` 真实字段上，从而出现在 STDF FTR 里。
- 想返回多个结果（例如 INL 与 DNL）就返回多个 `TestResult`，每个带自己的 `test_num`。
- `setup()/cleanup()` 用于仪器准备与释放；`cleanup` 一定会被调用。
- 需要随机噪声时用 `self.noise(hal, ctx.site_id, value, sigma)`，它走机台的 seed，保持可复现。

在 flow 里直接用名字引用：

```yaml
- item: vbg
  test_num: 4100
  params: {pin: VBG, samples: 8}
```

## 3. 写一个 DUT 模型

DUT 模型是 pin 级的数字行为模型，core 只要求实现 `DUTModel` 的接口：

```python
from pyate.dut.base import DUTModel
from pyate.dut.registry import register_dut

@register_dut("divider")
class DividerModel(DUTModel):
    """Divide-by-2 clock divider."""

    def reset(self) -> None: ...
    def drive(self, pin: str, value: int | None) -> None: ...
    def release(self, pin: str) -> None: ...
    def read(self, pin: str) -> int: ...
    def settle(self) -> None: ...
```

要点：

- `drive()` 收到的是 0/1/None（None = 释放，用于双向 pin）。
- `read()` 返回 0/1 或 `None`（高阻）；只有 `evaluate()` 之后输出才反映新输入。
- 可测量属性（`supply_current()`、`pin_leakage(pin)`、`continuity_vdiode(pin)`、
  `output_voltage(pin)`、`min_period_ns()`）按需实现，内置测试项会自动使用。
- 缺陷由 `dut_profile.defects` 注入，模型只要提供被注入点（例如 `_stuck`、`_idd`），
  注入逻辑在 `pyate.dut.registry.create_dut` 里统一完成。

参考实现：`examples/plugins/plugin_adc_dut/plugin_adc_dut/dut.py`（含 `convert()` 与
INL/DNL 所需的传输曲线）。

## 4. 写一个 datalog sink

sink 只依赖 `PartResult`，不需要 Qt 也不需要测试项：

```python
class MySink:
    def __init__(self, output_dir: str = "out", *, site_count: int = 1) -> None: ...

    def on_unit_start(self, unit) -> None: ...      # 新 wafer/tray
    def on_part(self, part: PartResult, unit=None) -> None: ...
    def on_unit_end(self, summary) -> None: ...     # 可选
    def close(self) -> None: ...                    # 可选
```

注册：`registry.add_sink("mysink", MySink)`，然后在配置里 `stdf.write_csv: true` 这类开关
不管用——自定义 sink 由 API 传入：

```python
from pyate.api import Session, RunHandle, RunOptions
handle = RunHandle(Session("session.yaml"),
                   RunOptions(sinks=[MySink("out")]))
handle.start(); handle.join()
```

参考实现：`examples/plugins/plugin_json_sink/`（每 unit 一个 `.jsonl`）。

## 5. 写一个自定义 scenario（CP/FT 之外）

`pyate.scenario.base.Scenario` 只需要提供 `units()` 迭代器，每次产出一个
`UnitSpec`（unit_id / kind / parts / 坐标）。CP 与 FT 是两个现成实现，
特殊形态（例如 strip、panel）可以按同样接口加。

## 6. 测试你的扩展

```bash
pytest -q                          # 全量回归，确认没破坏内置行为
pyate config validate my_session.yaml
pyate run my_session.yaml --set lot.wafers='["W01"]'
pyate stdf verify out/<run>/LOT-XXX_W01_CP1.stdf
```

插件的单元测试可以直接照抄 `tests/unit/test_plugins.py` 的结构：用
`PluginManager().load_module(...)` 加载，断言 `manager.loaded`、`registry.get("name")`，
再跑一个小 flow 检查结果。
