# Review 修复进度

更新：2026-11-04。需求来源：`REVIEW_BACKLOG_2026-11-03.md`。本文件区分已有实现、已执行验证与剩余验收，不能将“已实现一部分”视为整个任务完成。

已执行验证：

- 第一组判定、限值、STDF codec 和配置回归：74 项通过。
- 恢复、多阶段、批量和 Pattern 组合回归：66 项通过。
- HAL、恢复、插件、离屏 GUI 回归：84 项通过。
- 最新原子输出、指令预算、batch fail-fast 和恢复回归：19 项通过。
- core strict mypy：51 个源文件无错误。
- 最终全量测试（含离屏 GUI）：**347 项全部通过，384.14 秒**，原始日志为 `out/review_20261103/final_all.txt`。
- 收紧后的可复现专项：5 项通过；PRR 模型时间和 TSR 统计值不再屏蔽。
- 最终 core strict mypy：51 文件无错误，日志 `out/review_20261103/final_mypy.txt`。

| 任务 | 当前状态 | 已实现及剩余内容 |
|---|---|---|
| B00 | 已实现，回归通过 | CP2 供电前提、ADC DPS 通道修复；生成器同步修复 |
| B01/B02 | 核心实现与字节级回归通过 | PRR/PTR flags、U2 Dn、大小端、CPU 一致性；更广泛外部工具样本仍待补充 |
| B03 | 部分实现 | 毫秒、WCR 几何/单位/朝向、PMR 和真实 pin 索引、旧尝试标志修复；完整重测汇总标准样本仍需扩充 |
| B04 | 已实现，回归通过 | 执行状态、测量有效性、功能判定、限值判定分离；显式失败及非有限数不能变 pass |
| B05 | 部分实现，现有回归通过 | 专用 stage 优先、非重叠温区、scale 比较；单位显示及全部边界规则仍需扩展验收 |
| B06 | 核心实现与回归通过 | PhysicalPartId/DUTInstanceSpec，独立缺陷采样和物理件量测种子；1/2/4 site 逐器件等价；8 site、扫描顺序及跨进程专项待补 |
| B07 | 核心实现与回归通过 | DONE/ABORTED/FAILED、必需 sink 失败传播和清理；可选 sink 的完整降级报告仍待增强 |
| B08 | 核心实现与回归通过 | SQLite touchdown 原子提交、结果重放、输入/pattern 指纹、原子 checkpoint；STDF 先写 partial 再替换；更多崩溃注入点待验收 |
| B09 | 核心实现与回归通过 | instruction budget、complete/window 区分、Pattern 取消回调、HAL 延时取消、March 取消检查；全部长操作的延迟专项待测 |
| B10 | 部分实现 | TestItem/Params/必需限值检查、搜索分辨率约束；多文件集中错误和完整 Flow 编译尚未完成 |
| B11 | 部分实现 | 资源键区分 DPS/digital、跨 site 独占冲突；共享 ResourceGroup 排队和耗时尚未完成 |
| B12 | 部分实现 | policy 传递、无自引用历史、on_fail 请求整颗重测；全部 keep/on/耗尽组合仍需专项测试与文档 |
| B13 | 核心修复回归通过 | 限制 in-flight、回收 futures、未启动 job 中止、policy 进程传递；运行中 job 当前仍采用“完成后回收”，进程崩溃专项待补 |
| B14 | 部分实现 | KGD 自动扩盘、统一 lot 身份、共享 population、FT 存活集合传给下一 FT；完整四阶段 lineage 专项待补 |
| B15 | 已修复，待扩展概率专项 | 去除 site1 不参与 contact-fail 的特殊条件 |
| B16 | 部分实现 | mm 坐标、center_die、noise=0 生效；温度物理响应尚未实现 |
| B17 | 部分实现 | capture 在无 compare 周期和窗口中可收集；levels/strobe 模型与 DSL capture 尚未完成 |
| B18 | 部分实现 | 重测 wave 累加、wafer change 时间、run UPH 按总量/总时间；OEE 基准和共享等待专项尚未完成 |
| B19 | 部分实现 | 不同测试名/规格分组、batch 不再扩张规格窗口；stage/temp/unit/revision 元数据贯穿及在线统计待完成 |
| B20 | 部分实现 | HAL 时钟/取消/DUT 能力和 digital levels/timing 契约补齐，core strict 无错误；替代 HAL 与 SRAM capability 专项待完成 |
| B21 | 部分实现 | 默认生命周期 hooks、逐插件隔离和 ABORT 传播；scenario/backend/binning/config 注册入口接入；参考插件和进程专项待完成 |
| B22 | 部分实现 | worker<active-site 明确拒绝，异常路径释放 barrier；复杂分歧 flow 同步、真实 process 模式尚未完成 |
| B23 | 部分实现 | VM 更新、统计遍历和 GUI 渲染加锁，GUI 冒烟通过；增量渲染及十万事件实际 FPS 尚未验收 |
| B24 | 待实施 | 流式 touchdown、复用 worker、历史分页、有界在线统计和十万 part 内存验证 |
| B25 | 部分实施 | 测试依赖已可用，GUI 已实际运行，core strict 已通过；ruff 清理、CI/锁定环境、正式覆盖率及大规模基线待完成 |

后续顺序：先完成 B03/B08/B12 的边界回归，再补 B10/B11/B14/B16–B22 的完整验收，最后做 B23/B24 的规模改造及 B25 的持续质量门禁。所有任务保持用户已授权的实施范围，无需重新确认。
