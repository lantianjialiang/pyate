# 08 Multi-DUT / Multi-Site 并行

## 概念
- Site = 一个并行测试单元(含自己的 pin→channel 映射、HAL 资源、DUT 实例)。
- `site_count` 来自 loadboard/prober/handler 配置;同一时间 N 个 DUT 同时测试。

## 调度模型(engine/site.py)
```python
class SiteScheduler:
    def run_touchdown(sites: list[SiteContext], flow) -> list[PartResult]
```
- 并行方式可配置:`parallel_mode: thread | sequential | process`(默认 thread)。
- 同步点:flow 的每个 item 默认 **site 并行、item 间同步**(类似真实 ATE:每 test 并行,之后统一 barrier);另提供 `async_flow` 模式(各 site 独立跑完整 flow,更快)。
- 某 site fail-stop 后:该 site 的后续 item 被 mask(不执行,结果标记 skipped),其他 site 继续;全部 site 结束才结束 touchdown。
- 共享资源冲突(如共享 DPS 通道)用 `ResourceGroup` 串行化并计入耗时。
- Site 禁用:`disabled_sites`(缺 die/空 socket 时不测);Site 之间结果隔离,`site_num` 在 PTR/PRR 中正确填写。
- Test time 模型:并行 touchdown 的 test_time = max(site 耗时) + index time;可输出 UPH 估计。

## 任务
- T08.1 SiteContext 工厂(按配置创建 N 个 site 及各自 HAL、DUT)。
- T08.2 SiteScheduler(thread 模式 + barrier 同步 / async 两种)。
- T08.3 site mask 与 skipped 结果语义。
- T08.4 UPH/test_time 统计模块。
- T08.5 并发安全审计:共享对象加锁清单写入 DECISIONS.md。

## 验收标准
- 4 site 与 1 site 对同一批 DUT 结果(bin、测量值在固定 seed 下)一致。
- 一个 site fail-stop 不影响其他 site;日志中 site 标识完全正确。
- 压力测试:8 site × 1000 touchdown 无死锁、无数据竞争(重复 3 次一致)。
