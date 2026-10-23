# 开发进度（每个规格文件一行）

格式：`[日期] 规格/任务 — 结果 — 验收/验证方式`

- [阶段 1] `01_architecture.md` — 目录分层落地：`api/ → engine/ → testitems/ → hal|dut|pattern → config|datalog`；core 不 import gui，gui 只 import api/engine.events — `pytest tests/unit/test_layering.py`
- [阶段 1] `01_architecture.md` — EventBus（同步/异步队列、订阅者异常隔离、`wait_idle`）+ EventKind 全量定义 — `tests/unit/test_events.py`
- [阶段 2] `02_config_system.md` — pydantic v2 全量 schema、`include`/`includes`、`${ENV:default}`、`--set` 点号覆盖（含 section 级覆盖） — `tests/unit/test_config_loader.py`
- [阶段 2] `02_config_system.md` — 跨文件校验 `validate_session`（通道、limits 唯一性、flow test_num、pattern 引脚、DUT 型号） — `pyate config validate configs/demo_cp|demo_ft` 均 OK
- [阶段 2] `02_config_system.md` — 示例配置生成器 `tools/gen_demo_configs.py`（demo_cp 4 site/300 mm，demo_ft 8 site/2 tray） — 幂等重生成 + 校验通过
- [阶段 3] `03_virtual_instruments_hal.md` — HAL ABC（DPS/PMU/Relay/DigitalChannel/TimingController）+ `SimulatedHAL`（噪声可复现、site 隔离、`self_check`） — `tests/component/test_hal.py`
- [阶段 4] `04_dut_models.md` — 7 个 DUT 模型（counter/shiftreg/gates/adder/lfsr/sram/composite）+ 注册表 + 缺陷注入 + 空间良率采样 — `tests/unit/test_dut_models.py`
- [阶段 5] `05_digital_pattern_engine.md` — `.pat` 解析（pins/timing/levels/label/repeat/loop/jump/halt）+ 周期级引擎（首失败周期/行号/引脚、时序违例） — `tests/unit/test_pattern_parser.py`
- [阶段 6] `06_test_items_library.md` — 15 个内置测试项（continuity/leakage/idd×2/vol_voh/power_up_down/functional/vdd_min_search/fmax_search/shmoo/sram_march/delay/user_code…） — `tests/component/test_testitems.py`
- [阶段 7] `07_testflow_engine.md` — FlowExecutor（item/group/branch/loop/goto_bin、on_fail、安全表达式、HAL 错误 → fail_code 99、插件钩子） — `tests/component/test_executor_flow.py`
- [阶段 8] `08_multi_dut_site.md` — SiteBarrier + SiteScheduler（thread/sequential/async）、UPH 估算、site 禁用 — `tests/concurrency/test_multisite.py`
- [阶段 9] `09_cp_ft_scenarios.md` — WaferMap（binmap txt/svg/csv）、ProberSimulator（band 扫描、mask/contact fail）、HandlerSimulator（tray 切块、jam、contact fail） — CP 2 wafer × 673 die yield 90.7 %，die 数与解析估算偏差 <1 %
- [阶段 10] `10_batch_execution.md` — `batch/spec.py`（sweep 笛卡尔积展开）+ `queue.py`（状态机）+ `runner.py`（serial/thread/process、fail_fast）+ `report.py`（yield/pareto/Cpk 聚合） — 6 job（3 温度 × 2 profile）34 s 跑完，`batch_summary.json|csv` + `batch_report.md`
- [阶段 10] `10_batch_execution.md` — 重测策略 `engine/retest.py`（bins/max_retries/on/keep）+ STDF 重测语义（多次 PIR/PRR + `PART_FLG` retest bit） — FT contact_fail 0.2 场景 yield 85 % → 95 %，STDF 回读 45 PRR/10 retest bit
- [阶段 11] `11_stdf_output.md` — 自研 STDF V4 writer/records（字节序修正、k 数组无长度前缀、MD5 无关的严格字段顺序）+ `reader_check` 结构校验 + pystdf 交叉验证 — `pyate stdf verify` → `=> PASS`、`pystdf: OK`
- [阶段 11] `11_stdf_output.md` — CSV datalog、Text datalog、`StatsSink`（Cpk/直方图）、`build_sinks` 工厂 — `out/*/stats.json`
- [阶段 12] `12_gui_cli.md` — CLI 全部命令（config validate / run / batch run|status|report / pattern run / stdf verify|dump / gui / plugin new） — `pyate run … --sites 2` 84 die 92.9 % yield
- [阶段 12] `12_gui_cli.md` — `api.Session`/`RunOptions`/`RunHandle`（后台线程 + 事件桥）+ viewmodels（无 Qt）+ PySide6 面板（Config/TestFlow/Limits/Run/Batch/Pattern） — `tests/gui/*`，离屏 GUI 跑完 CP 并导出 wafer map PNG
- [阶段 13] `13_extension_plugin.md` — pluggy hookspec + PluginManager（entry points/目录扫描、api_version 校验、hook 异常隔离策略）+ `plugin new` 脚手架 — `tests/unit/test_plugins.py`
- [阶段 13] `13_extension_plugin.md` — 参考插件 `plugin_adc_dut`（ADC 模型 + adc_inl_dnl/adc_offset_gain）与 `plugin_json_sink`（JSONL datalog） — 不改 core，装插件后 `pyate run example/session.yaml` 出 STDF（yield 70 %）
- [阶段 14] `14_testing_acceptance.md` — 测试分层（unit/component/integration/concurrency/gui/regression）+ E2E-CP/FT/FLOW/BATCH/EXT/REPRO/GUI — `pytest`（见 README 的运行方式）
- [阶段 14] 文档 — README / QUICKSTART / CONFIG_REFERENCE / STDF_MAPPING / EXTENDING / DECISIONS — 本目录
- [阶段 8] `08_multi_dut_site.md` — 并发用例补齐：serial/thread 结果一致、每 site 结果隔离、事件计数、skipped site、`SiteBarrier` 释放/abort/leave、**同一 barrier 实例被 4 个 site 共享且逐 item 同步**、sequential 不参与同步、UPH 随 site 数与 index time 变化 — `tests/concurrency/test_multisite.py`（14 例）
- [阶段 8] 修复 — `SiteScheduler(mode=...)` 对未知并行模式不再静默退回 thread，改为抛 `ValueError`；非法值在配置层本就被 `Literal` 拦住，只有编程误用会走到这里
- [阶段 12] `12_gui_cli.md` — E2E-GUI 冒烟：离屏创建 `MainWindow`、6 个页签、会话加载后 stage/site/wafer 几何同步、深浅主题切换、limits 编辑回写、跑完 CP 后 view model 的 dies/bins 与 `RunSummary` 对齐、datalog 过滤、wafer map 导出 PNG、batch/pattern 面板 — `tests/gui/test_gui_smoke.py`（21 例）
- [阶段 12] 修复 — wafer map 把 die 网格坐标当毫米乘 scale，整片被画成小团；改为按 die 尺寸作步距，并把画布配色改为跟随主题（`theme.canvas_colors`）
- [阶段 12] 修复 — `RunPanel` 从未读取 wafer 直径/die 尺寸（一直用 300/10 默认值），改为从 `session.loaded.wafer` 取值；`start_run` 换 bus 前先 `vm.detach()` 避免重复订阅
- [阶段 12] 修复 — 无头环境 Qt 报告的字体族为 0（文字渲染成方块），新增 `gui.app.ensure_fonts()` 在需要时注册系统字体
- [阶段 12] 重构 — 新增 `api.pattern`（`run_pattern`/`available_models`/`default_profile`），`PatternPanel` 不再直接 import `dut.registry`/`pattern.engine`/`config.models`；`Session.update_limit()` 承接 limits 编辑，GUI 只依赖 `pyate.api` — `tests/unit/test_layering.py`
- [阶段 14] `14_testing_acceptance.md` — 文档四件套补齐：`README.md`、`docs/QUICKSTART.md`、`docs/CONFIG_REFERENCE.md`、`docs/EXTENDING.md`、`docs/STDF_MAPPING.md`；README 中的每条命令均实测通过
- [阶段 14] 交付物 — `examples/out/{cp,ft,lot}`（STDF + summary/snapshot/binmap/stats + wafer map PNG + `lot_report.md`），由 `tools/export_wafer_map.py` 渲染（复用 GUI 控件，不引入 matplotlib）
- [阶段 14] 质量门槛 — 环境离线无法安装 ruff/mypy/pytest-cov（`pip` 无可用索引），改用标准库 `trace` 自建 `tools/coverage_std.py` 统计 `src/pyate` 行覆盖率，并把原始计数缓存成 JSON 以便复用
- [阶段 12] GUI 视觉打磨 — 新增 `gui.panels._fmt_num`（长小数收敛到 4 位、微量级走科学计数、None 显示 `-`），datalog/limits/batch 三张表统一右对齐 + 隐藏行号列；配置树把 `key: value` 拆成两列（长路径不再被截断）；`RunPanel` 统计栏补 `unit:` 并把误用为 bins 的 `uph` 标签改名成 `bins`；`PatternPanel` 换成 `QGroupBox + QFormLayout`（字段等高、按钮右对齐）；`theme` 重写页签/菜单/分组框/滚动条/分隔条样式 — `tests/gui/test_gui_smoke.py`（23 例，新增 `_fmt_num` 与配置树 value 列断言）
- [阶段 12] 新增 `tools/shoot_gui.py` — 离屏渲染真实 `MainWindow`（可选跑一片 wafer 填充 wafer map），产出 `docs/images/gui_*.png` 与浅色主题版本，README 的「界面」章节直接引用这些图
