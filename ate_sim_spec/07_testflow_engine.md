# 07 TestFlow / TestItem 执行引擎

## testprogram.yaml 结构
```yaml
program: DEMO_CNT8
revision: 1.0
flows:
  CP1:
    - {item: continuity, test_num: 1000, params: {...}, on_fail: {bin: 2, action: stop}}
    - {item: idd_static, test_num: 2000, on_fail: {bin: 3, action: continue}}
    - group: functional_suite
      items:
        - {item: functional, test_num: 3000, params: {pattern: cnt_basic.pat}}
    - branch:                          # 条件分支
        if: "result('idd_static').value > 5e-3"
        then: [ {item: delay_ms, params: {ms: 5}} ]
    - {item: fmax_search, enabled: false}
  FT1: [ ... ]
```
## 引擎能力
- 节点类型:item / group / branch(表达式,安全 eval 白名单)/ loop(retry N)/ goto-bin。
- `on_fail`:stop(终止该 DUT 流程并 bin)、continue(记录 fail,最后 bin 取首个 fail 或优先级最高)、retest(见 10)。
- `enabled`、`stage` 过滤、`--only/--skip item` 运行时选项、单步/断点(供 GUI 调试)。
- Binning(`binning.yaml`):规则 → soft_bin;soft_bin→hard_bin 映射;全 pass → bin1;支持 `bin_priority`。
- 每个 DUT 的执行流程:`PART_START → items... → binning → PART_END`,生成 `PartResult`(part_id, x, y, soft_bin, hard_bin, num_tests, test_time_ms, passfail 标志)。
- 测试时间:累加仿真耗时 + 可配置固定 overhead。
- `executor.py: FlowExecutor.run_part(site_ctx) -> PartResult`(单 site 单 DUT,同步、无全局状态,便于并行调用)。
- 流程静态校验:引用的 item/pattern/test_num 唯一性/limit 存在性。

## 任务
- T07.1 testprogram pydantic schema + 表达式安全求值器。
- T07.2 Flow 树构建与静态校验。
- T07.3 FlowExecutor(stop/continue/branch/loop)。
- T07.4 BinningEngine。
- T07.5 事件发布(TEST_START/TEST_RESULT/PART_END)。
- T07.6 调试模式(单步、断点、item 参数运行时覆盖)。

## 验收标准
- 构造 3 种 DUT(good / continuity fail / functional fail),bin 与预期完全一致。
- continue 模式下收集全部 fail,stop 模式下 fail 后的 item 不被执行。
- 非法表达式(如 `__import__`)被拒绝。
