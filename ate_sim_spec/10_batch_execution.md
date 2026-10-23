# 10 批量执行

## 功能
- **Batch 定义**(`batch.yaml`):一组 job,每个 job = session.yaml + 覆盖项(lot_id、temperature、DUT profile、seed、site 数、test program 版本)。
- 参数扫描(sweep):对某参数(如 Vdd corner、温度、defect 率)做笛卡尔积/列表,自动生成 job。
- 执行器(batch/runner.py):
  - 串行/进程池并行(`max_workers`),每 job 独立输出目录 `out/<batch_id>/<job_id>/`。
  - 状态机:PENDING → RUNNING → DONE / FAILED / ABORTED;失败 job 不阻塞其他;可 `--fail-fast`。
  - 进度事件、日志文件、`batch_summary.json/csv`(各 job 的 yield、bin 分布、UPH、STDF 路径)。
- **重测策略**(batch/retest.py):
  - `retest: {bins: [3,4], max_retries: 2, on: [contact_fail]}`:指定 bin 的 part 自动重测(同位置/同 tray),STDF 中以 retest 方式写入(`RETEST_CODE`/多个 PRR 且标记 `PART_FLG` 的 retest bit)。
  - 重测后以最后一次结果覆盖 wafer map(可配置"取最好"或"取最后")。
- 结果聚合:跨 job 合并 yield 报表、bin pareto、Cpk 统计(对每个 test_num 的均值/σ/Cpk/直方图)。

## 任务
- T10.1 batch.yaml schema + sweep 展开器。
- T10.2 BatchRunner(进程池、状态、取消)。
- T10.3 重测策略实现并与 11 的 STDF 重测语义对齐。
- T10.4 聚合报表(summary、pareto、Cpk、CSV 导出)。
- T10.5 CLI:`pyate batch run batch.yaml`、`pyate batch status`、`pyate batch report`。

## 验收标准
- 3 温度 × 2 DUT profile = 6 job 自动生成并全部跑完,各生成独立 STDF。
- 人为让一个 job 抛异常,batch 仍完成其余 job 并在 summary 标记 FAILED。
- 重测:带 contact_fail 的 die 经重测后 yield 提升且 STDF 可正确回读。
