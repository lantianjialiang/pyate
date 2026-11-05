# Review 修复进度

更新：2026-11-05。需求来源：`REVIEW_BACKLOG_2026-11-03.md`。本文件区分已有实现、已执行验证与剩余验收，不能将“已实现一部分”视为整个任务完成。

已执行验证（347 项是上一轮基线，本轮结果追加在文末）：

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
| B07 | 核心实现与回归通过 | DONE/ABORTED/FAILED、必需 sink 失败传播；可选 sink 错误进入 summary，清理异常保留主错误；CLI 失败出口仍待完整专项 |
| B08 | 核心实现与回归通过 | SQLite touchdown 原子提交、结果重放、输入/pattern 指纹、原子 checkpoint；STDF 先写 partial 再替换；更多崩溃注入点待验收 |
| B09 | 核心实现与回归通过 | instruction budget、complete/window 区分、Pattern 取消回调、HAL 延时取消、March 取消检查；全部长操作的延迟专项待测 |
| B10 | 部分实现 | 多文件 schema 集中报错、未选择分支中的表达式/loop/bin 预检、TestItem/Params/必需限值检查；全部参数边界仍待扩展 |
| B11 | 部分实现 | 资源键区分 DPS/digital、跨 site 独占冲突；共享 ResourceGroup 排队和耗时尚未完成 |
| B12 | 部分实现 | policy 传递、无自引用历史、on_fail 请求整颗重测；全部 keep/on/耗尽组合仍需专项测试与文档 |
| B13 | 核心修复回归通过 | 限制 in-flight、回收 futures、未启动 job 中止、policy 进程传递；运行中 job 当前仍采用“完成后回收”，进程崩溃专项待补 |
| B14 | 部分实现 | KGD 自动扩盘、统一 lot 身份、共享 population、FT 存活集合传给下一 FT；完整四阶段 lineage 专项待补 |
| B15 | 已修复，待扩展概率专项 | 去除 site1 不参与 contact-fail 的特殊条件 |
| B16 | 核心模型与专项通过 | mm 坐标、center_die、noise=0；温度影响 IDD/漏电/最小周期/Vmin/二极管压降，Composite 传播；系数和模型边界见 SIMULATION_CONTRACT.md |
| B17 | 核心模型与专项通过 | VOH/VOL 电压比较、strobe 稳定窗口、未知值及 DSL capture/API 覆盖语义；百万向量性能待记录 |
| B18 | 部分实现 | 重测 wave 累加、wafer change、run UPH 按总量/总时间；ideal_test_ms 固定基准和来源标记；共享等待专项尚未完成 |
| B19 | 后端在线统计专项通过 | unit/stage/temp/revision/规格分组与 Welford 在线统计，TSR 不存原始样本；N/A 与批量报告接通；GUI 多规格分组及良率口径仍待完善 |
| B20 | 部分实现 | HAL 时钟/取消/DUT 能力和 digital levels/timing 契约补齐，core strict 无错误；替代 HAL 与 SRAM capability 专项待完成 |
| B21 | 部分实现 | 默认生命周期 hooks、逐插件隔离和 ABORT 传播；scenario/backend/binning/config 注册入口接入；参考插件和进程专项待完成 |
| B22 | 核心同步专项通过 | 相反分支/嵌套 loop 静态阶段同步、goto 提前离场、全体超时失败、worker 数约束；未实现的站点 process 在预检和入口明确拒绝；暂停/断点压力矩阵待扩展 |
| B23 | 有界队列与绘制探针通过 | 锁隔离、窗口有界、全程汇总在线累计；减少重复 Qt 调用并清除 FAIL→PASS 残色；十万事件实测 12.74 FPS；多规格分组/完整页面矩阵仍待扩展 |
| B24 | 核心改造与规模探针通过 | 流式 touchdown、线程池复用、SQLite 分页、有界统计；十万 part 峰值 93.21 MiB；population/bin 元数据仍随规模增长 |
| B25 | 本地质量与规模探针通过，远程待验证 | 固定 Python 3.12 依赖、Windows CI 和核心 85% 门禁；Ruff 清零，strict core 51 文件通过，正式覆盖率 86.33%；8×1000 TD 重复三轮一致；百万行解析+执行 30.26 秒，略超参考目标，仍待优化 |

后续顺序：先完成 B03/B08/B12 的边界回归，再补 B10/B11/B14/B16–B22 的完整验收，最后做 B23/B24 的规模改造及 B25 的持续质量门禁。所有任务保持用户已授权的实施范围，无需重新确认。

本轮实际全量 431 项通过；最后 GUI 缓存修改后的专项 51 项通过；Ruff 和 strict core 通过。覆盖率、性能、代码状态及未完成范围的完整证据见 `VALIDATION_2026-11-05.md`，不能把上方上一轮 347 项基线当成本轮结果。
