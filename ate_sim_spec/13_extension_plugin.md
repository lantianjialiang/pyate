# 13 扩展与插件机制

## 可扩展点(均通过注册表 + entry_points `pyate.plugins`)
| 扩展点 | 接口 | 用途 |
|---|---|---|
| TestItem | `TestItem` 子类 + `@register_testitem` | 新测试项 |
| DUT 模型 | `DUTModel` 子类 + `@register_dut` | 新器件(如 ADC/SPI 器件) |
| HAL 后端 | `HALBackend` | 替换为真实仪器驱动或 SCPI/VISA |
| Instrument 类型 | `register_instrument` | 新增仪器类别(AWG、Digitizer) |
| Scenario | `Scenario` 基类(`touchdowns()` 生成器) | 新场景:SLT、Burn-in、Final QA |
| 输出格式 | `DatalogSink` 接口 | 新增 ATDF、JSON、数据库、MQTT |
| Binning 策略 | `BinningStrategy` | 客户定制分 bin |
| Pattern 前端 | `PatternLoader` | 导入 STIL/WGL/真实 ATE 格式 |
| 配置扩展 | `ConfigSection` 注册 | 插件自带配置段与校验 |
| Hook(pluggy) | `before_lot / after_part / on_fail / before_item ...` | 切面式扩展 |

## 实现
- 使用 `pluggy` 定义 hookspec(plugins/hookspec.py),`loader.py` 通过 entry_points + `plugins/` 目录扫描加载。
- 插件依赖/版本兼容检查(`api_version`),加载失败隔离并告警,不影响内核。
- 提供脚手架:`pyate plugin new <name>` 生成插件工程模板。
- 提供 2 个**参考插件**作为样例并作为测试:
  1. `plugin_adc_dut`:新增 ADC DUT 模型 + `adc_inl_dnl` TestItem。
  2. `plugin_json_sink`:新增 JSON DatalogSink。
- 文档 `docs/EXTENDING.md`:每个扩展点最小示例。

## 任务
- T13.1 注册表统一抽象(registry 通用泛型)。
- T13.2 pluggy hooks 与调用点植入(engine/scenario 中)。
- T13.3 插件加载器与隔离。
- T13.4 脚手架命令。
- T13.5 两个参考插件及文档。

## 验收标准
- 不改动 core 源码,仅安装参考插件即可在 flow 中使用 `adc_inl_dnl` 并产出 STDF。
- 插件崩溃不导致运行终止(按 hook 配置的策略:忽略/告警/中止)。
