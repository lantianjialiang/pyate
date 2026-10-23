# 14 测试策略与总验收

## 测试分层
| 层 | 内容 | 工具 |
|---|---|---|
| 单元 | config、pattern parser、DUT 模型、STDF 编码器 | pytest |
| 组件 | HAL+DUT、FlowExecutor、SiteScheduler | pytest |
| 集成 | 完整 CP 与 FT 跑通并校验 STDF | pytest + pystdf |
| 回归 | golden 文件:固定 seed 下 STDF/summary 与基线 diff | pytest + hash/semantic diff |
| 并发 | 多 site、批量并行压测 | pytest -n / 重复运行 |
| GUI | ViewModel 单测 + pytest-qt 冒烟 | pytest-qt |
| 性能 | 100 万向量、10 万 part | pytest-benchmark(记录,不强制阈值) |

## 端到端验收场景(必须全部通过)
1. **E2E-CP**:demo_cp,4 site,300mm wafer,CP1 流程 8 个 item,yield 约 90%,输出 STDF,wafer map 与 STDF 的 bin 一致。
2. **E2E-FT**:demo_ft,8 site,2 个 tray(共 1000 part),jam 与 contact_fail 注入,重测后 yield 提升,STDF 校验通过。
3. **E2E-FLOW**:CP1→CP2→FT1 的 lot 流转,CP fail 的 die 不进 FT(KGD)。
4. **E2E-BATCH**:3 温度 × 2 profile 的 batch,聚合报表含 Cpk、bin pareto。
5. **E2E-EXT**:装入参考插件后测试 ADC DUT。
6. **E2E-REPRO**:同一 session+seed 两次运行,STDF 除时间戳字段外完全一致。
7. **E2E-GUI**:GUI 驱动 E2E-CP 并导出 wafer map。

## 质量门槛
- 覆盖率:core(config/hal/dut/pattern/engine/datalog)≥ 85%。
- `ruff`、`mypy --strict`(core 包)无错误。
- 所有示例配置 `pyate config validate` 通过。
- 文档齐全:README、QUICKSTART、CONFIG_REFERENCE、EXTENDING、STDF_MAPPING。

## 交付物清单
源码、configs 示例、tests、docs、`pyproject.toml`、示例输出(`examples/out/` 含 STDF 与 wafer map 图)。
