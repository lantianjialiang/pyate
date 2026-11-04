# PyATE Review Backlog

来源：[Review 报告](D:/liang/source/pyate/docs/REVIEW_2026-11-03.md)。日期：2026-11-03。此表保留原始任务和验收要求；实际实施及未完成项见 [修复进度](D:/liang/source/pyate/docs/IMPLEMENTATION_PROGRESS.md)。

目标顺序：先让结果可验证，再补运行与流程闭环，最后扩大规模。每个任务都应能独立提交并提供行为验证证据。

工作量仅供初排：S ≈ 半天至 1 人日；M ≈ 1–3 人日；L ≈ 3–5 人日；XL 需要先做设计再细拆。范围包含相应回归验证，不包含外部真实 ATE 对标或部署；任务存在重叠，不能简单相加作为承诺工期。

## 第一批：建立可信基线与修复正确性

| ID | 优先级 / 大小 | 任务与交付物 | 依赖 | 验收标准 | 来源 |
|---|---|---|---|---|---|
| B00 | P1 / S | 修复 CP2 供电顺序和 ADC 示例 DPS 映射；生成当前测试基线 | 无，可立即做 | E2E-FLOW 的 CP2/FT 都有实际器件；ADC 示例可校验并完成 E2E-EXT；各阶段 tested/passed/STDF 按器件身份核对 | F10/F18 |
| B01 | P0 / M | 建立独立 STDF 标准样本和语义 oracle；纠正 D7/D9 | 无 | 样本来自规范/独立实现，覆盖 pass、fail、abnormal、按 ID/XY 覆盖旧记录、空/大位图、大小端；必须验证解释出的 verdict 和时间，而非只证明可以 parse | F01 |
| B02 | P0 / M | 修复 STDF flags、Dn codec 和 byte-order/CPU_TYPE 契约 | B01 | 失败 PRR/PTR/FTR 的标准失败位正确；Dn 使用 U2 位数前缀，覆盖 0/1/8/9/256+ bits 与双字节序；自写与独立 reader 对真实字段一致；错误 endian 配置被拒绝或自动规范化 | F01 |
| B03 | P0 / M | 修复 STDF 字段映射和重测汇总；固定 pin map | B02、B12 | 1234 ms 回读为 1234；不同 die 尺寸/notch/坐标方向正确；不同失败 pin 得到不同正确位图；缺失值标为无效；最终器件数、尝试数、bin/GOOD/RTST 口径有样本和说明，并与独立工具一致 | F01/F09 |
| B04 | P0 / M | 拆分 execution status、measurement validity、functional verdict、limit verdict | 无 | fmax 无通过点、VDD 无工作点、HAL error、NaN/Inf、功能失败在任意宽松限值下都不能变成 pass；合法数值边界行为符合配置；GUI/CSV/STDF verdict 一致 | F04 |
| B05 | P0 / M | 重做限值选择及单位/scale 规范 | 无 | exact stage 优于通配；非重叠温区允许；重叠/边界有明确规则；无匹配限值对“必须有规格”的测试在运行前失败；同物理值不同显示 scale 的比较和输出等价 | F05 |
| B06 | P0 / L | 引入 PhysicalPartId / DUTInstanceSpec；拆分随机流 | 无 | 同一 population 改 1/4/8 sites、扫描顺序、thread/sequential 后，固有缺陷与工艺参数逐器件相同；接触重测不重抽固有缺陷；噪声依赖项有明确契约并可复现；CP→CP→FT 保留 lineage | F03 |
| B07 | P0 / M | 明确 Run/Unit 状态与必需 sink 失败策略 | 无 | 最后一片/盘中途 abort 必须报告 ABORTED；必需 sink 模拟磁盘失败产生 FAILED/DATA_ERROR、非零 CLI 状态和明确事件；可选 sink 失败按配置降级；清理不覆盖原始错误 | F02/F15 |
| B08 | P0 / L | 建立可恢复结果存储及原子 checkpoint；恢复 summary/binmap/输出 | B06、B07 | 在 unit 开始、touchdown 中途/结束、unit 尾分别模拟中止和崩溃；恢复后全部 part 无遗漏无重复、测量/bin 与完整运行一致；checkpoint 必须晚于结果提交；配置或 pattern 变化拒绝续测；已有 STDF 不被截断覆盖 | F02 |
| B09 | P0 / M | Pattern completion 状态、指令预算、协作取消与超时 | B04 | 无 vector 的 jump 环可被预算/Stop 终止；截断的完整 pattern 标 incomplete；显式窗口模式单独表达；搜索/Shmoo/March 可取消；完成/失败/超时/取消分别写入结果 | F07 |

**第一批出口：** B00 建立可运行基线；B01/B02/B04/B05/B06/B07/B08/B09 通过核心回归，B03 与 B12 配合完成后，才重新宣称 STDF、重测和续测已验收。保留独立外部格式样本，避免 writer/reader 同时修改后相互掩盖错误。

## 第二批：补齐配置、运行和阶段流程

| ID | 优先级 / 大小 | 任务与交付物 | 依赖 | 验收标准 | 来源 |
|---|---|---|---|---|---|
| B10 | P1 / M | 编译并静态检查 Flow；集中报告多文件错误 | B04、B05 | 一次报告未知 item、错误 Params、缺 pattern/pin/limit/bin、非法表达式；错误带文件和字段路径；拒绝非正/不收敛的搜索分辨率、非法 sweep/worker/site/概率；运行不再把配置错误伪装为 DUT fail | F06 |
| B11 | P1 / M | 使用带类型的物理资源键；独占/共享资源与冲突策略 | B10、B20 | DPS1 与 Digital1 可独立存在；同一独占 Digital/DPS 被跨 site 重用可检测；允许的共享资源按 ResourceGroup 排队，等待时间进入模型；空 site/禁用 site 不占用资源 | F06/F14/F17 |
| B12 | P1 / M | 统一 RetestPolicy 和不可变 AttemptHistory | B04、B06、B07 | item action=retest、session、batch、CLI、GUI 的 bins/on/max_retries/keep 行为一致；last/best 各保留完整尝试、无自引用；固定好/坏/contact DUT 的最终判定可预期；支持重测耗尽 | F09 |
| B13 | P1 / M | 修复 Batch 的有限派发、fail-fast、取消及最终状态 | B07、B12 | serial/thread/process：首 job 失败、worker 崩溃、用户取消都能收敛；未启动 job 标 ABORTED，已启动 job 按约定完成/取消并回收；最终无 RUNNING/PENDING；job 输出隔离；retest policy 在进程间完整传递 | F08/F09 |
| B14 | P1 / L | 统一 KGD/part 存活集合及跨阶段 lineage；自动 FT 分盘 | B06、B08、B12 | KGD 输入大于 tray 容量时无遗漏；CP1→CP2→FT1→FT2 每阶段输入恰好等于上阶段存活集合；FT fail 不再次进入 FT2；空 KGD 合法且有明确结果；lot_id、part_id、wafer/x/y 可关联 | F10 |
| B15 | P1 / S | 修复 Handler 全部 site 的 contact-fail 采样 | B06 | 1/4/8 sites，概率 0 全部正常、概率 1 全部接触失败；中间概率无固定 site 偏置；同 seed 的接触尝试可重放；接触失败 bin 不硬编码为只适合示例的规则 | F11 |
| B16 | P1 / L | 校准空间单位、温度响应及噪声开关 | B06 | die 网格按尺寸/中心转换至毫米；边缘与簇状缺陷落在预期物理区域；相同物理坐标换 die 尺寸表达仍一致；noise=0 真正无噪声；温漂系数有真值模型与边界案例；温扫不再靠手调 yield 代替温度响应 | F12 |
| B17 | P1 / L | 定义并实现简化 levels/strobe/capture 语义 | B09、B16 | 独立参考 DUT 上，改变比较阈值/采样边沿产生可解释变化；capture 窗口、无 compare 周期、DSL capture 与 API 一致；边界与模型限制写入文档；百万向量 benchmark 记录回归 | F13 |
| B18 | P1 / M | 统一虚拟时钟、test/index/jam/change/retest 时间及 UPH/OEE | B11、B12、B14 | 构造可手算的两次重测、两种长度 unit、wafer change、jam、尾批场景，累计时间与手算一致；run UPH=总器件/总模型时间；明确 sequential 是宿主调度还是物理串行；GUI 与 JSON 值一致；理想周期有明确来源 | F14 |
| B19 | P1 / M | 按可比 population 分组统计，限制跨 job Cpk 合并 | B05、B14 | key 至少包含 test identity/revision、单位、规格和声明的分组条件；不同温区/规格不自动扩大上下限；同条件 pooled mean/sigma 与直接计算一致；无足够样本/无规格给 N/A；明确 FPY/final yield/attempt yield | F14 |
| B20 | P1 / M | 补齐 HAL Protocol/capabilities，收敛直接模拟后端访问 | B04、B09 | TestItem 使用的资源属性/计时/量测接口均有类型契约；模拟专用 SRAM/表征能力显式声明；用最小替代 HAL 跑通基础 TestItem；core strict 类型检查不再因缺少能力接口而报错 | F16 |
| B21 | P1 / L | 将插件生命周期和注册工厂真正接入公开 API | B10、B20 | 仅安装插件、只调用公开 API 就能触发 lot/unit/part/item hooks；自定义 scenario/config/binning/backend 至少有可运行例子；WARN/IGNORE 不阻断其他插件，ABORT 明确失败；serial/thread/process 的加载和隔离行为一致 | F16 |
| B22 | P1 / M | 明确分歧 flow 的多 site 同步与异常隔离；处理 process 能力 | B09、B11 | 分支/循环次数不同、site fail-stop、before-part 异常、暂停/断点/中止、worker<site 均不挂起或错配 item；barrier 超时明确失败而非静默放行；未实现 process 时在校验时拒绝或清楚标记实际执行模式 | F17 |

**第二批出口：** 用真实输入集合和独立 oracle 检查 E2E-CP/FT/FLOW/BATCH/EXT；不能只断言“yield 提升”或“产物存在”。器件身份、尝试历史、最终 bin、输出格式、统计及时间应可相互核对。

## 第三批：规模、GUI 和持续质量

| ID | 优先级 / 大小 | 任务与交付物 | 依赖 | 验收标准 | 来源 |
|---|---|---|---|---|---|
| B23 | P2 / L | GUI 用批量不可变快照/队列；增量表格和在线汇总 | B07、B19 | worker 写入与 UI 遍历隔离；十万 TEST_RESULT 事件真实运行测得 >10 fps（记录硬件和事件速率）；无字典迭代异常、事件漏计、重测重复计数；测量刷新延迟/主线程占用，不能用 timer 间隔替代 FPS | F17 |
| B24 | P2 / L | 流式 touchdown、复用 worker、结果分页与有界统计 | B08、B19 | 十万 part 运行记录峰值内存/吞吐；summary 等价于完整小样本；不要求全部 TestResult 常驻内存；GUI 当前窗口、histogram 样本和历史查询有明确容量；重复运行无持续资源增长 | F17 |
| B25 | P1 / M | CI/版本约束/验收矩阵及可信回归基线 | 可立即建立骨架；最终依赖 B01–B24 对应范围 | 固定支持的 Python/依赖组合；ruff/strict core 类型/单元组件集成/独立 STDF 语义通过；core 正式覆盖率≥85%；8 sites×1000 touchdowns 重复 3 次一致；百万向量解析执行记录耗时（需求参考<30s），十万 part、GUI 十万事件出基线报告；GUI 专项环境安装并执行 PySide6；repro 只屏蔽真实时间戳等必要字段；README/PROGRESS/DECISIONS 与结果一致 | F18 |

**第三批出口：** 发布一份带代码版本、依赖版本、测试输入规模、耗时、峰值内存、GUI FPS 和已知模型边界的验收报告。性能不达标应有具体 profile 和后续任务，而不是直接增加 process 数或更换整个架构。

## 建议执行顺序

1. 立即做 B00 和 B25 的环境/CI 骨架，使当前示例问题与环境问题可重复地分开。
2. B01→B02；同时推进 B04/B05/B06/B07/B09，它们可独立评审。
3. B06/B07 稳定后做 B08、B12；再完成 B03。到这里先做一次正确性验收。
4. B10/B11/B13/B14/B15/B16 补齐流程与执行，再做 B17–B22 的模型与契约。
5. B23/B24 根据 profile 改造，最后完成 B25 的规模与发布门禁。

如果只选择一个近期里程碑，建议范围为 **B00–B09 + B12 + B25 的基础门禁**；其中 B03 必须等重测语义稳定后验收。其目标是“同一输入器件得到可追踪、可恢复、符合标准的结果”，完成后再扩 GUI 功能或真实仪器接入。

## 需要在设计中固定的口径

这些属于实施时必须写入契约的选择，不影响先修复已经确认的问题：

- DUT 级固定 site 偏差是否属于有意仿真因素；若属于，应独立于随机噪声配置，并提供跨 site 对比的基准模式。
- `max_cycles` 表示完整执行预算，还是声明的有限观测窗口；两者必须有不同状态表达。
- 首测良率、最终良率、尝试记录统计、重测后 STDF 汇总之间的对应关系。
- sequential 表示宿主按顺序计算并行 tester 的结果，还是物理 tester 串行；两者的 UPH 不能混用。
- 数字仿真定位是周期级逻辑行为，还是包含简化电压阈值/时序裕量；模型局限应由具体能力声明体现。
- 必需结果输出失败时的终止/恢复策略，以及已有输出目录的覆盖策略。
