# 设计决定（歧义与取舍）

规格未明确、或存在多种实现方式时，按“最简单可扩展”选择，并记录在此。

## D1 站点编号从 1 开始
`SiteContext.site_id`、`--set run.sites`、channel map 的 `sites:` 键、STDF `SITE_NUM` 全部 1-based，
与真实 ATE 和 STDF 一致。`range(1, sites+1)` 是唯一遍历方式。

## D2 `--set` 覆盖支持“section 级”路径
`run.seed=1` 覆盖 session.yaml 本身；`dut_profile.model=counter` 覆盖被引用文件
（`dut_profile.yaml`）的内容，在 `model_validate` 之前注入。section 名单见
`config/loader.py::SECTIONS`。`limits` 不支持覆盖（CSV 行按 test_num 定位，改文件更清晰），
显式报错而不是静默忽略。

## D3 配置校验“收集全部错误”
`validate_session` 不在第一个错误处中止，返回 `ValidationReport(errors=[...], warnings=[...])`，
便于一次修完。CLI `config validate` 有 error 时退出码 1。

## D4 Pattern 总线的 bit0 在左侧
`Q=LLLLLLLH` 表示 Q0=H、Q1..Q7=L。解析器允许用已声明的 `Q0..Qn` 自动聚合出 `Q` 总线。

## D5 仿真时间与真实时间解耦
`simulation.sim_time_scale=0` 表示“不睡眠”，只做逻辑计时（毫秒级累加），
所以 UPH 是估算值而不是实测墙钟值。这样 1 万 die 的批次能在秒级跑完。

## D6 retest 复用同一个 DUT 种子
重测是“同一颗物理器件再测一次”，因此重测时用**相同 seed/坐标**重建 DUT：
缺陷集合不变，只有接触条件（contact_fail）变化。若换种子，等于换了一颗芯片，
yield 不会随重测提升（实现过程中验证过这一点）。E2E-FT 因此从 85 % → 95 %。

## D7 重测的 STDF 语义
被覆盖的旧尝试以独立的 `PIR → PTR/FTR → PRR` 写入，`PART_FLG |= 0x04`（retest bit），
最终结果再写一组并同样带 retest bit；这样回读能看到完整测试历史，
`PCR/HBR/SBR` 计数与 PRR 数保持一致（校验器交叉核对）。

`PART_FLG` 取值：`0`=pass、`0x01`=not tested、`0x02`=fail、`0x04`=retest、`0x08`=abnormal。

## D8 STDF 数组字段（`k<n><type>`）不带长度前缀
`RTN_INDX = k12U2`，元素个数取自记录内第 12 个字段（`RTN_ICNT`），与 pystdf 的约定一致；
写侧和读侧都不写/读前缀字节。字节序用 `normalize_endian()` 统一，避免
`"little"` 与 `"<"` 混用导致的多字节字段大小端不一致（曾是真实 bug）。

## D9 `Dn` 位图字段
`FAIL_PIN` 写为 STDF `Dn`：1 字节 bit 数 + 向上取整的字节。`encode_dn(positions, width)`
决定最小宽度（至少 8 bit）。

## D10 Batch 的 sweep 轴是“任意配置点号路径”
不限定轴必须是温度/DUT，`sweep: {run.temperature: [...], dut_profile.yield.pass: [...]}`
即可生成 3×2=6 个 job，job id 形如 `job001_-40_0.9`。示例 batch 用“温度 × 良率模型”
（而不是两个不同 DUT 型号），因为不同型号的引脚集不同，需要各自的 channel map / pattern；
更换型号请单独建 session。

## D11 YAML 1.1 的布尔键
`on:` 会被 PyYAML 解析成 `True`，`batch/spec.py::_normalize_keys` 递归把 `True/False`
键还原为 `"on"/"off"`，因此 `retest: {on: [contact_fail]}` 可直接写。

## D12 插件加载：类式或模块式
插件包内若有 `Plugin` 类（hookimpl 在类上），加载器实例化后注册；否则按模块级 hookimpl
注册。`pyate_api_version` 主版本不匹配 → 拒绝加载并记入 `rejected`，不影响其他插件。

## D13 插件钩子失败策略
`HookPolicy.WARN`（默认）/`IGNORE`/`ABORT`。默认只告警：一个坏插件不能让整跑崩掉；
`FlowExecutor._call_hook` 另外再包一层 try/except，保证执行器自身安全。

## D14 GUI 用 MVVM + 轮询节流
`gui/viewmodels/*` 不 import Qt，可 headless 单测；Qt 层用 50 ms `QTimer` 读取
viewmodel 的 `should_render()` 决定是否重绘（相当于 20 fps 上限），因此 10 万条
`TEST_RESULT` 事件不会阻塞 UI。事件本身在 worker 线程产生，viewmodel 只做累加。

GUI 冒烟测试用 `QT_QPA_PLATFORM=offscreen` 直接构造窗口，未引入 `pytest-qt`
（离线环境无法安装额外依赖）；测试内容等价于 pytest-qt 的 `qtbot` 冒烟。

## D15 温度只影响 limits 查找与 STDF 记录
DUT 模型本身没有温度参数化（规格 04 未要求）。批量扫温度时若希望良率随温度变化，
请同时扫 `dut_profile.yield.pass` / `process.*` 等参数（示例 batch 即如此）。

## D16 `run.only` / `run.skip` 是调试开关
只跑部分测试项时，前序测试建立的物理条件（例如 VDD 上电）不会自动补上，
因此 `only: [continuity, functional]` 会让 functional 全 fail。这是“按配置执行”的
直接结果，不做隐式依赖推导。

## D17 恢复（resume）以 touchdown 为粒度
`out/<run>/resume.json` 记录 `unit_index` 与 `touchdowns_done`；die 级断点恢复代价
大于收益（每 die 只有几十毫秒）。

## D18 跨阶段 lot 流转以「die 集合」传递 KGD
`LotRunner` 只在 wafer 阶段（`CP*`）收集 KGD：把 `UnitSummary.bins` 里 pass bin 的 die 坐标
收成 `set[(x, y)]`，下一阶段只测这些 die；FT 阶段则把 KGD 转成 `part_id` 列表交给 handler。
理由：CP→CP 之间是同一片 wafer 的复测，坐标是最自然的键；CP→FT 之间 wafer 已经切开，
只剩 part_id。代价是 FT 结束没有 die 级 KGD（CLI 因此对非 wafer 阶段显示 `good=` 而不是
`KGD=`，避免出现误导性的 0）。累计良率 `cumulative_yield()` 用「各阶段良率相乘」而不是
末阶段良率，报表里同时给出 `first_pass_yield`（首阶段）与 `final_yield`（末阶段）。

## D19 离线环境的质量门槛：自建行计数器替代 coverage，ruff/mypy 记录为未执行
目标环境没有 pip 索引，`ruff`/`mypy`/`pytest-cov` 都装不上。为了让「覆盖率 ≥85%」这条
门槛仍然可验证，新增 `tools/coverage_std.py`：用 `sys.settrace` + `threading.settrace`
装一个只统计 `src/pyate` 前缀帧的行计数器，跑完 pytest 后与
`trace._find_executable_linenos` 求得的可执行行比对，输出逐模块行覆盖率，并支持
`--save-counts/--counts` 复用计数（避免每次重跑十几分钟）。
最初版本直接用标准库 `trace` 的 count 模式，但实测在整套 pytest 跑下来时 `engine/events.py`、
`pattern/parser.py`、`cli/main.py` 等模块被算成 0%（早期导入的模块计数丢失），
结论明显不可信，因此改为自建 hook。
`pyproject.toml` 里仍保留完整的 ruff/mypy 配置，联网环境直接 `pip install ruff mypy` +
`ruff check src tests tools` / `mypy` 即可；本仓库不声称它们在脱机环境跑过。
类型标注按 strict 规则书写，且由 `tests/unit/test_layering.py` 的 AST 检查兜住分层约束。

## D20 wafer map 的 die 坐标是网格索引，不是毫米
`binmap.csv` 里的 `x/y` 是 die 网格下标（`center_die` 为原点），而 `wafer_size_mm` /
`die_size_mm` 是物理尺寸。GUI 早期把下标直接乘 `side/wafer_size`，导致 300 mm 圆片上的
die 挤成中心一小团。现在按「一个 die 尺寸 = 一个网格步距」换算：`cell = die_size * scale`，
`px = cx + x * cell`。渲染工具的 `--die-size` 就是步距，画出来的图与真实 CP binmap 一致。

## D21 GUI 依赖收敛到 `pyate.api` 单一面门
最初 GUI 直接 import `engine.events`、`batch`、`dut.registry`、`pattern.engine`，与「GUI 只
调用 core 公开 API」的约定不一致。现在 `pyate.api` 导出 EventBus/Event/EventKind、
BatchRunner/BatchSpec/JobSpec/load_batch 以及 `api.pattern` 的
`run_pattern`/`available_models`/`default_profile`；`Session.update_limit()` 承接 limits
编辑。`tests/unit/test_layering.py` 用 AST 检查四件事：core 不含 `pyate.gui`、core 不含
`PySide6`、gui 只 import `pyate.api`（及自身）、cli 对 gui 只能惰性 import。
唯一保留的例外是 `gui/` 内部的相对 import（同包），以及 cli 的 `gui` 子命令在函数体内
`from ..gui.app import main`——那正是「没有 PySide6 也能 `pyate --version`」的原因。

## D22 界面截图来自真实窗口，不是手工原型
`tools/shoot_gui.py` 用 `QT_QPA_PLATFORM=offscreen` 创建真正的 `MainWindow`（同一套面板、
同一份样式表、同一批 view model），`window.grab()` 存 PNG。好处是截图同时充当视觉回归：
样式表写坏、面板没填充、wafer map 几何跑偏都会在图上立刻看出来（本轮就靠它发现了
datalog 里 `0.2496411896983` 这种满屏长小数、配置树 `value` 列一直空着、
`uph` 标签被拿去显示 bins 三个问题）。代价是截图脚本要跑一次完整 GUI 初始化，
因此 demo run 只跑 `lot.wafers=["W01"]`、2 site，并把产物写到 `out/gui_shots`
（只截界面时不加 `--run`）。
