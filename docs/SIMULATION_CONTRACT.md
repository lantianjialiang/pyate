# 仿真模型与容量契约

更新：2026-11-04。本文记录已经实现的模型边界，不代表全部 Review Backlog 已验收。

## 物理件、温度与随机流

物理件以 lot_id 和 part_id 标识，DUTInstanceSpec 固定工艺/缺陷种子、物理位置与创建时的 profile。跨 stage 复用实例规格；温度、供电、数字状态与接触条件属于测试条件，不重新抽样固有缺陷。器件级量测噪声由物理身份派生，noise_sigma=0 完全关闭噪声。

温度单位为摄氏度；默认参考温度为 25°C，拒绝非有限数及低于绝对零度的输入。默认系数可在 DUT profile.params 中覆盖：

| 参数 | 默认值 | 模型 |
| --- | --- | --- |
| temperature_ref_c | 25 | 参考温度 |
| idd_tempco_per_c | 0.002 | 静态 IDD 乘 exp(k·ΔT) |
| leak_tempco_per_c | ln(2)/20 | 引脚及缺陷漏电乘 exp(k·ΔT) |
| period_tempco_per_c | 0.001 | 最小周期乘 exp(k·ΔT) |
| vmin_tempco_v_per_c | 0.0005 | 最小工作电压加 k·ΔT |
| diode_tempco_v_per_c | -0.002 | 导通二极管压降加 k·ΔT，最低为 0 V |

指数参数限制在 [-50, 50]，用于避免数值溢出。此模型用于可解释的趋势与回归，并非器件数据手册或晶体管级热模型；动态电流尚无独立温漂项。Composite 子模型继承测试温度与这些系数，父模型的 slow_derate 也计入最小周期。

## 数字比较与采集

引擎执行周期级逻辑，再检查比较窗口与输出电压。输出高电压以实际供电为基础；期望 L 必须满足 output_voltage <= VOL，期望 H 必须满足 output_voltage >= VOH。

简化输出稳定时间为 `max(drive_on, data) + output_delay_fraction × DUT.min_period_ns()`，默认 output_delay_fraction=0.2。有效采样点为 compare strobe 与周期结束时刻的较小值。过早采样的输出标为未知，capture 用 -1 表示；有比较条件时判失败。该模型不模拟亚稳态概率、模拟波形、板级传输线或负载瞬态。

DSL 的 `capture Q0, Q1;` 声明对整个 Pattern 生效，即使该周期没有 L/H 比较条件也会采集。API capture_pins=None 采用 DSL；显式 [] 关闭采集。采集窗口按周期筛选，只允许 DUT 输出引脚。

complete 模式需要正常执行到结束，预算截断不能判成功；window 模式表示显式有限观测，返回 window 状态并保留 completed=false。只有控制指令的无限循环也受 instruction budget 限制。长操作通过取消回调协作退出。

## 多站点与时间

thread 模式按 Flow 的静态 item 阶段同步。各 site 访问未选择分支中的同步槽位，但不执行 DUT 操作、不添加结果、不增加模型测试时间。已提前结束的 site 离开 barrier。超时会使所有等待方收到失败，不能继续作为成功的同步阶段运行。

sequential 表示宿主顺序计算同一并行 tester 的各 site 结果，不表示物理 tester 串行。touchdown 的模型测试时间取 site 时间最大值，重测 wave 的最大值逐次相加，再计 index/jam/change。站点 process 模式尚未实现，配置校验和调度入口明确拒绝；batch 的 process worker 是独立能力。

`run.ideal_test_ms` 可配置固定的理想 touchdown 测试时间，用于 OEE/理论 UPH 的基准。未设置时兼容使用观测到的最短测试时间；summary.equipment.ideal_source 明确区分 configured 和 observed_minimum。不能将观测最小值当成设备认证的额定节拍。

## 历史、统计与 GUI

控制器逐个消费 touchdown，跨 touchdown 复用线程池。完成的 touchdown 以 SQLite 事务提交；只有已提交历史可用于续测与分页查询。`iter_part_results(unit_id, offset=0, limit=None)` 流式返回历史；`part_results(unit_id)` 是显式完整物化的兼容接口。

控制器不常驻全部 TestResult，但物理 population、当前 unit 的 bin map 等身份/位置元数据仍随器件数增加。不得将其描述为整个系统常量内存。

后端 Cpk 使用在线矩统计，不保留全部数值。分组包含测试号/名称、单位、上下限、stage、温度和程序 revision；不同组不自动扩大规格范围。少于两个样本、零方差或没有适用规格时，相应能力指数输出 null/N/A。

GUI 默认保留最近 500 条 datalog、500 条日志、1000 个器件的明细图表样本。单器件单测试最多 4000 个最近数值，趋势窗口 80 个数值。待渲染队列也受上述行数限制。无 part 分组的计数、均值、方差继续累计全程数据；part 分组与直方图/概率图只显示保留窗口。snapshot.history_evictions 标记历史窗口淘汰数量。完整结果通过 SQLite 历史读取，不能从 GUI 窗口反推完整数据集。

## 验证与性能工具

安装固定开发组合：`python -m pip install -r requirements-dev-py312.txt`，再执行 `python -m pip install --no-deps -e .`。当前实际验证平台为 Windows / CPython 3.12；不能据此宣称其他 Python 组合已验证。

`tools/benchmark_sim.py` 提供独立进程性能探针，报告保存代码 HEAD、dirty 状态、依赖、操作系统、CPU、宿主耗时及峰值 RSS：

```powershell
python tools/benchmark_sim.py parts --parts 100000 --sites 8 --output out/bench-parts
python tools/benchmark_sim.py sites --touchdowns 1000 --repeats 3 --output out/bench-sites
python tools/benchmark_sim.py pattern --vectors 1000000 --output out/bench-pattern
python tools/benchmark_sim.py pattern --vectors 1000000 --expanded --output out/bench-parser
python tools/benchmark_sim.py gui --events 100000 --rate 10000 --output out/bench-gui
```

parts/sites 使用单个 delay_ms item 和 SQLite journal，不启用可选 CSV/STDF sink；用于测量调度/身份/恢复存储规模，不代表完整生产 Flow 的吞吐。sites 重复比较物理实例及结果摘要。Pattern 默认使用压缩 repeat 源码，--expanded 才真正解析百万行。GUI 运行真实 RunPanel，统计 datalog viewport 的 Paint 事件及刷新间隔，离屏结果不等于显示器的端到端呈现帧率。
