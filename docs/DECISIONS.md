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
重测使用同一 `PhysicalPartId` 和 `DUTInstanceSpec`。工艺、固有缺陷与量测随机流独立，
缺陷采样不依赖共享随机数游标；跨 stage 传递同一 population，接触失败重测改变接触条件。

## D7 重测的 STDF 语义
每次尝试以独立的 `PIR → PTR/FTR → PRR` 写入。后续尝试设置 `PART_FLG` bit 0，
表示按 PART_ID 覆盖此前记录；首测不设置覆盖位。保留完整历史并分别报告尝试与最终结果。

`PART_FLG`：`0x01`=按 PART_ID 覆盖、`0x02`=按 XY 覆盖、`0x04`=异常结束、
`0x08`=fail、`0x10`=pass/fail 无效。`TEST_FLG` 的失败位是 `0x80`。

## D8 STDF 数组字段（`k<n><type>`）不带长度前缀
`RTN_INDX = k12U2`，元素个数取自记录内第 12 个字段（`RTN_ICNT`），与 pystdf 的约定一致；
写侧和读侧都不写/读前缀字节。字节序用 `normalize_endian()` 统一，避免
`"little"` 与 `"<"` 混用导致的多字节字段大小端不一致（曾是真实 bug）。

## D9 `Dn` 位图字段
`FAIL_PIN` 写为 STDF `Dn`：2 字节无符号 bit 数（跟随文件字节序），再接向上取整的位图字节。
PMR 从 1 开始编号，FAIL_PIN 的位号等于 PMR 索引；bit 0 保留。

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
逐插件调度隔离失败；ABORT 通过专用异常传播到运行控制器，不被执行器转成普通 DUT 失败。

## D14 GUI 用 MVVM + 轮询节流
`gui/viewmodels/*` 不 import Qt，可 headless 单测；Qt 层用 50 ms `QTimer` 读取
viewmodel 的 `should_render()` 决定是否重绘（20 fps 上限，并非实测帧率）。
事件在 worker 线程产生；viewmodel 更新、统计遍历和 Qt 渲染共享 RLock，避免并发修改容器。
十万事件的实际 FPS、增量渲染和锁竞争仍需要专项验证。

GUI 冒烟测试用 `QT_QPA_PLATFORM=offscreen` 直接构造窗口，未引入 `pytest-qt`
（离线环境无法安装额外依赖）；测试内容等价于 pytest-qt 的 `qtbot` 冒烟。

## D15 温度只影响 limits 查找与 STDF 记录
DUT 模型的温度物理响应尚未实现，属于规格 03/09 的待办。当前批量扫温度时若希望良率随温度变化，
请同时扫 `dut_profile.yield.pass` / `process.*` 等参数（示例 batch 即如此）。

## D16 `run.only` / `run.skip` 是调试开关
只跑部分测试项时，前序测试建立的物理条件（例如 VDD 上电）不会自动补上，
因此 `only: [continuity, functional]` 会让 functional 全 fail。这是“按配置执行”的
直接结果，不做隐式依赖推导。

## D17 恢复（resume）以 touchdown 为粒度
`results.sqlite3` 原子提交每个完成 touchdown 的结果和尝试历史，绑定配置与 pattern 指纹。
恢复时重放已提交结果以重建汇总和输出，再运行未提交 touchdown；`resume.json` 是原子更新的辅助游标。
STDF 先写 `.partial`，关闭后替换最终文件；Windows 短暂共享锁采用有界重试，永久失败继续向上传播。

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

## D23 GUI 去掉 Batch 页，批量能力留在命令行
Batch 页只是 `batch.yaml` 的进度表，配置和产物都在命令行侧，页面本身没有不可替代的
操作；它在界面里反而把「一套配置跑一轮」的主线拉长。因此 `BatchPanel`、`BatchViewModel`、
File → Open batch 一并删除，页签变成 Config / TestFlow / Limits/Bins / Run / Pattern / STDF。
`batch` 引擎、`api.batch`、`pyate batch run|status|report` 全部保留，E2E-BATCH 验收不受影响。

## D24 composite 的子块必须继承同一组电气参数
`CompositeModel.min_period_ns` 取两个子块中较慢的一个，但子块是 `CounterModel` /
`ShiftRegModel` 自己 `new` 出来的，早期只传了 `bits`，于是 `dut_profile.params` 里的
`base_period_ns` / `vth_v` 对 composite 完全无效（永远按 12 ns、2.0 V 算），配置改不动
速度边界。现在把父模型的 `params`（除 `bits`）原样下传，和已有的 `set_temperature` /
`apply_process_variation` 下传方式一致。现有示例都没显式设置这两个键，默认值与子块
默认值相同，故 demo_cp/demo_ft 的数字不变。

## D25 demo_shmoo 的 shmoo 是「时序边界」而不是电平截断
要让 shmoo 有一张像样的图，三个坑必须避开：
1. **VOH/VOL 是绝对值**。默认 `voh: 2.8`、输出高电平 ≈ `VDD - 0.1`，所以 VDD < 2.9 V
   时所有高电平比较都失败，图会出现一条与速度无关的竖墙。demo_shmoo 把 `voh/vol`
   改成 2.0/0.4，让判失败的原因回到时序。
2. **strobe 不能固定在中段**。`sample_time = min(compare, period)`，若 compare 固定
   8 ns，低 VDD 下 `output_delay` 变大就会「未建立」而全红。demo_shmoo 把 compare 设成
   500 ns（远超 period），采样点落在周期末尾，失败只来自 `period < min_period(vdd)`。
3. **DUT 要足够慢**。`vth_v=2.55`、`base_period_ns=30` 让 `min_period` 从 3.6 V 的
   21 ns 涨到 2.75 V 的 119 ns，边界斜着穿过 8–128 ns 的窗口；`vdd_min=2.72` 再给出
   底部一整条「供电不足」的死区。
另外 `yield.pass = 1.0`、无缺陷注入：图的形状只由时序决定，不会因为随机坏 die 变成
一片红。每颗 die 的 `vth_shift` 仍按 `process.vth_sigma` 采样，所以 21 颗的 pass rate
在 57.8–59.3% 之间小幅抖动，和真实硅片一致。

## D26 Run shmoo 与整轮结果必须能对上
早先的 **Run shmoo** 看起来"另跑了一个实验"，原因有三个，全部修掉：
1. **网格被静默覆盖**。按钮把 V/T/cyc 三个 spin box（默认 12×12、16 cycle）盖在 flow 的
   `vdd_steps` / `period_steps` / `cycles`（`demo_shmoo` 是 21×31、32）之上，于是图和整轮
   分辨率不同、pass rate 也不同。现在打开 session 就把三个框填成 flow 那一项的值
   （`RunPanel._sync_shmoo_controls`），色块上方写明 `配置 · stage · VDD 范围 · period 范围 ·
   网格 · nominal die`；用户改这三个框才是主动改网格。
2. **DUT 是随机 die**。`run_shmoo` 原来用 `create_dut(seed=run.seed)`，`vth_shift` 从
   N(0, σ) 抽一次，等于凭空造了一颗"第 0 颗 die"；整轮里每颗 die 各有自己的 `vth_shift`，
   所以边界必然不同。`create_dut` 新增 `variation=False`（不下传工艺抖动），
   `run_shmoo` 默认用它 —— 表征看的应该是标称 die。
3. **没有交代用的是哪个 stage**。`_flow_shmoo` 返回 `(stage, params)`，页面和日志都打印出来。
现在 `demo_shmoo` 的 21×31 网格下，**Run shmoo** 与整轮里"中间那颗"一致（58.2%），
21 颗 die 在 57.8–59.3% 之间小幅散开 —— 这就是工艺抖动该有的样子。
`tests/component/test_testitems.py::test_the_shmoo_button_and_a_run_agree_on_the_same_die`
把"同网格同 die 同结果"钉住。

## D27 TestFlow 里单独跑一项：走 FlowExecutor，但只发 TEST_RESULT
`Run selected item` 用与整轮完全相同的 `FlowExecutor._run_item`（limits CSV、on_fail、
HALT 错误、TEST_START/TEST_RESULT/FLOW_STEP 全部复用），只是把 `program_nodes` 换成
`[node]`。三个取舍：
- **不发 PART_END**。只跑了 flow 的一段，`binning` 算出来的 bin 没有意义；发一个假 part
  会污染 wafer map（(0,0) 那颗）和 parts/yield 计数。数据进 datalog 和 Summary 就够了，
  `yield` 标签在 parts=0 时显示 `-` 而不是 `0.00 %`。
- **标称 die**（`variation=False`，与 D26 一致），多 site 时每个 site 一颗独立实例。
- **参数取表单当前值**，不要求先 Apply to node：调一个数就想看结果是最常见的动作。
  文件不改写，所以关掉窗口也不会留下半套参数。
`FlowExecutor.run_single` 是这次新加的公开入口（`_run_item` 本身仍是私有）。

## D28 断点命中一次即消费，界面必须显示 paused
原来的断点只发一行 `LOG`，没有状态显示；更糟的是它**每颗 die 都会命中同一个 test number**，
点一次 Resume 马上又停在同一项，看起来像"没反应"。三处改动：
1. `FlowExecutor` 命中后 `breakpoints.discard(test_num)`（one shot）。多 site 共用同一个 set，
   一个 site 消费掉之后其它 site 直接走到 barrier 等它，整轮仍然是一起暂停/一起继续。
2. 新增 `EventKind.BREAKPOINT`（`site/test_num/item`），`RunViewModel.paused_at` 保存最后一次
   命中，Run 页把它显示成 `hit 9000 shmoo (site 1) - Resume to continue`。
3. `state: running/paused/done/idle` + 按钮可用性随状态切换（Start 跑时禁用、Resume/Step 只在
   paused 时可用），否则用户不知道"停下来"了。这段状态同步放在 `_tick()` 里、**不受**渲染节流
   （`should_render`）控制，否则暂停瞬间界面还可能停在 `running`。
断点选择从裸 spin box 换成可编辑下拉框，列出当前 stage 的 `test_num  item`；点击 datalog 行仍
会把对应编号填进去。

## D29 STDF 统计区改成卡片 + 芯片，并修正 PART_FLG 位
原来是一个 6 行的 `QLabel` 长文本，信息全挤在一起。现在：路径、`lot/part/job/node/stage/temp`
一行、六个指标块（parts/good/yield/retests/PTR/FTR）、hard/soft bin 芯片行、记录条数，
用 `QFrame#card` 的浅底卡片承载（深浅主题各一套 QSS）。表格与图表区域因此多出一些高度。

同时发现读取端用错了标志位：`stdf_view` 把 `TEST_FLG` fail 当 `0x02`、`PART_FLG` fail 当
`0x02`，而 writer 按 `docs/STDF_MAPPING.md` 写的是 `TEST_FLG 0x80`、`PART_FLG 0x08`、
`PART_FLG 0x10` = 未测、`0x01` = 重测。结果 STDF 页的 good/yield 永远是 100%。按文档改正后
`demo_cp` 的 W01 显示 594/673 = 88.26%，和 run 汇总一致。

## D30 菜单快捷键要预留宽度；主题默认浅色且可勾选
给 `QMenu::item` 加 `padding: 5px 14px`。只设 `QMenu::item:selected` 时 Qt 的样式表代理
按文本宽度算菜单宽度，没有把快捷键算进去，于是 `Open session...` 和 `Ctrl+O` 叠在一起
（截图 `out/menu_a.png` 可复现）。加左右 padding 后宽度从 147 px 变成 164 px，两者分开。

主题：所有入口（`MainWindow`、`create_window`、`pyate gui`、打包入口）默认 `light`；View 菜单
改成互斥的可勾选项并反映当前主题，避免"看起来是深色、菜单里却没有勾"的状态。

## D31 GUI 插件管理页：能列出、加载、启停，且停用要真的撤掉注册
插件此前只能靠环境变量在启动时加载，界面上完全看不见，用户不知道 `examples/plugins/` 下
那几个包是干什么的。新增第 7 个页签 **Plugins**（菜单 Tools → Plugins...），数据全部来自
`pyate.api` 的新模块 `api/plugins.py`，GUI 不直接 import `plugins.*`。

三处设计取舍：

1. **扫描不 import**。`plugins/discovery.py` 用 `ast` 解析候选文件，只读 docstring 第一行、
   `__version__` 和被 `@hookimpl` 装饰的钩子名。这样"列出磁盘上有什么"不会执行第三方代码。
   识别三种布局：单文件、包目录、`pyproject.toml` + `<name>/__init__.py` 的项目目录；
   `load_dir` 也补上了第三种，否则 GUI 里点 `examples/plugins` 只会加载 `failwatch.py`。
2. **停用要撤销注册**。`PluginRegistry` 增加 `owners/previous`，`add_*` 时记录"谁注册的、
   覆盖前是什么"；`remove_plugin(name)` 一并回滚（DUT 模型、test item 走全局注册表的
   `unregister_*`，被覆盖时恢复原实现）。`PluginManager.unload/enable` 只做 pluggy 的
   register/unregister + 这套回滚，`_register` 改成**逐个插件**调用 `pyate_register`，
   否则无法归属贡献。停用只影响下一次 run（`RunController` 在构造时取 manager）。
3. **导入期装饰器注册也算插件贡献**。`@register_testitem` 在 import 时就把类塞进全局注册表，
   比 `pyate_register` 早；不做处理的话 ADC 插件的 `adc_inl_dnl` 在 disable 后仍然可用。
   `_claim_imports()` 按 `cls.__module__` 前缀认领这些类，`previous` 记为 None，卸载时直接删除。

顺带修了 `_check_versions` 的老问题：原来对每个插件都取"全局 hook 的第一个返回值"，导致
一个插件报 9.0 会把所有插件都判成不兼容。现在用 `subset_hook_caller` 逐插件取版本。

界面细节：表格列 `plugin / status / api / contributes / source`，选中行下方显示 doc 与注册清单；
状态色 #35a86a / #d05c4c 在深浅主题都可读；`QTableWidget` 的 QSS 补了 `selection-color`
（原先选中行保留单元格前景色，绿字压在浅蓝底上几乎看不见）。
