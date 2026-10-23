# 配置参考

所有配置都是 YAML，由 pydantic v2 严格校验（未知字段直接报错）。一份 `session.yaml`
把各文件串起来：

```yaml
# configs/demo_cp/session.yaml
name: demo_cp
program: testprogram.yaml
tester: tester.yaml
channel_map: channel_map.yaml
loadboard: loadboard.yaml
levels: levels.yaml
timing: timing.yaml
limits: limits.csv
binning: binning.yaml
dut_profile: dut_profile.yaml
wafer: wafer.yaml        # CP 才需要
lot: lot.yaml
prober: prober.yaml      # CP 才需要
handler: handler.yaml    # FT 才需要
stdf: stdf_options.yaml
run:
  stage: CP1
  sites: 4
  seed: 20261021
```

相对路径一律相对于 **session.yaml 所在目录**。`--set a.b=c` 可以在命令行覆盖任意字段。

---

## run — 运行控制

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `stage` | str | 程序第一个 flow | 要跑的 flow 名（CP1/CP2/FT1…） |
| `sites` | int | 自动（布局容量） | 并行 site 数，不能超过 `tester.num_sites_max` 与布局容量 |
| `seed` | int | 20260101 | 全局随机种子。同 seed + 同配置 = 逐记录一致 |
| `temperature` | float | 继承 prober/handler | 影响 limit 的温度窗匹配与 STDF |
| `output_dir` | str | `out` | 产物目录 |
| `parallel_mode` | `thread\|sequential\|async\|process` | thread | `process` 会退化为线程并告警 |
| `disabled_sites` | list[int] | `[]` | 从 1 开始，禁用后该 site 不参与调度 |
| `stop_on_fail` | bool | false | 任一 part fail 立即停止整轮 |
| `retest_bins` | list[int] | `[]` | 需要重测的 soft bin |
| `max_retries` | int | 0 | 重测次数上限 |
| `only` / `skip` | list[str] | `[]` | 只跑/跳过这些 test item（**不补前序物理条件**，见 D16） |

## tester — 机台能力

| 字段 | 默认 | 说明 |
|---|---|---|
| `name` / `model` | `PYATE` | 写入 STDF MIR 的 `SERL_NUM`（保持可复现） |
| `num_sites_max` | 8 | `run.sites` 上限 |
| `digital_channels` | 256 | Digital 通道数 |
| `dps_channels` / `pmu_channels` | 16 / 64 | 电源与参数测量通道数 |
| `max_vector_rate_mhz` | 200 | 向量速率上限，pattern 的 period 会校验 |
| `vector_depth` | 1M | 单 pattern 最大向量数 |
| `overhead_ms` | 0.4 | 每次 item 的固定开销（影响 UPH） |
| `simulation.noise_sigma` | 0.002 | 量测高斯噪声（相对值），0 表示理想 |
| `simulation.sim_time_scale` | 0.0 | 虚拟时间缩放；0 表示不额外缩放 delay/pattern |
| `simulation.seed` | 20260101 | 噪声种子 |

## channel_map — pin 到通道

```yaml
pins:
  - {pin: VDD, type: dps,   channel: 1}
  - {pin: CLK, type: digital, channel: 3}
  - {pin: Q[0:7], type: digital, channel: 10, width: 8}   # 总线一次声明 8 位
```

`type` ∈ `dps|pmu|digital|relay`。总线写法 `NAME[0:N]` 会展开成 `N+1` 个 pin，**bit0 在最左**；
`width` 必须与范围一致。通道号在同一类型内不可重复（资源冲突直接报错）。

## loadboard / levels / timing

- `loadboard`：`board_id`、`site_count`、`pin_to_channel`（按 site 复用通道）、`relay_map`。
- `levels`：`default_set` + `sets[]`，每个 set 含 `vil/vih/vol/voh/vterm/iol/ioh/vdd`。
- `timing`：`default_set` + `sets[]`，每个 set 含 `period_ns` 与 `edges`（`drive_on/data/return_/compare`，单位 ns）。

## limits — 测试项上下限

CSV 列：`test_num,name,lo,hi,unit,scale,stage,temp_min,temp_max`。

- `lo`/`hi` 留空表示单边或无限制。
- `scale` 用于显示单位换算（例如 A → mA）。
- `stage` 为空表示适用所有 stage；`temp_min/temp_max` 为空表示全温度区间。
- 同一 `test_num` 在不同 stage/温度窗可以有多行，查找顺序是 **stage 精确匹配优先，再按温度窗收窄**。

## binning — 分 bin

```yaml
pass_bin: 1
default_fail_bin: 2
soft_bins: [{sbin: 1, name: pass, pass_fail: pass}, {sbin: 2, name: cont, pass_fail: fail}]
hard_bins: [{hbin: 1, name: good}, {hbin: 2, name: bad}]
rules: [{sbin: 2, hbin: 2}]
bin_priority: [3, 2]          # 多个 fail_code 竞争时的优先级
fail_code_bins: {1001: 2, 2001: 3}
```

判定顺序（见 D03/D09）：`goto_bin`/forced bin → `fail_code_bins` + `bin_priority` →
`defect_bin` → `pass_bin`/`default_fail_bin` → `rules` 映射 hard bin。

## dut_profile — DUT 行为与缺陷

```yaml
model: counter
params: {bits: 8}
defects:
  - {kind: stuck_at, pin: Q0, value: 0, prob: 0.05}
  - {kind: leakage,  pin: IO1, nA: 800, prob: 0.02}
  - {kind: slow,     factor: 1.8, prob: 0.03}
yield: {pass_rate: 0.9, edge_fail_boost: 1.8, cluster_prob: 0.15}
process: {vth_sigma: 0.03, idd_sigma: 0.05, leak_sigma: 0.08}
```

- `defects[].kind` ∈ `stuck_at|open|short|leakage|high_idd|slow|random_bitflip`。
- `short` 需要 `pin2`；`value` 是卡死电平；`factor` 是 slow 的倍数。
- `yield.pass_rate` 是名义良率，`edge_fail_boost` 让边缘 die 更易失效，`cluster_prob`
  产生簇状失效。**采样按 die 坐标独立**，所以同一 profile 在不同坐标得到不同结论。
- `process.*_sigma` 是工艺波动（相对 1σ），会叠加到量测值上。

## wafer / prober — CP

| wafer 字段 | 默认 | 说明 |
|---|---|---|
| `diameter_mm` | 300 | 圆片直径，决定 die 网格覆盖 |
| `die_size_mm` | (5, 5) | die 尺寸 |
| `edge_exclusion_mm` | 3.0 | 边缘禁测带 |
| `notch` | bottom | 缺口方向 |
| `center_die` | (0, 0) | 坐标原点对应的 die |
| `skip_list` | [] | 显式跳过的 die 坐标 |

| prober 字段 | 默认 | 说明 |
|---|---|---|
| `index_time_ms` | 250 | 每次 touchdown 的步进时间 |
| `layout.cols/rows` | 2/2 | probe card 布局。`run.sites` 小于容量时 footprint 自动收窄 |
| `wafer_change_time_s` | 12 | 换片时间 |
| `contact_fail_prob` | 0.0 | 探针接触不良概率（产生 bin 2） |
| `temperature` | 25.0 | 写入 STDF |
| `scan` | `serpentine` | 扫描路径（`serpentine`/`raster`/…） |

## handler / lot — FT

| handler 字段 | 默认 | 说明 |
|---|---|---|
| `layout.cols/rows` | 2/2 | 同测 socket 布局 |
| `cycle_time_ms` | 180 | 每个 part 的机械周期 |
| `index_time_ms` | 0 | 换 tray 索引 |
| `jam_prob` | 0.0 | 卡料概率 |
| `contact_fail_prob` | 0.002 | 接触不良概率 |
| `tray_capacity` | 500 | 每 tray 容量 |
| `jam_recovery_ms` | 30000 | 卡料恢复耗时 |

| lot 字段 | 默认 | 说明 |
|---|---|---|
| `lot_id` / `sublot` | LOT0001 / "" | 写入 STDF MIR |
| `wafers` | [W01] | CP 的 wafer 列表 |
| `flow` | [CP1] | `lot run` 的默认 stage 序列 |
| `trays` / `parts_per_tray` | 0 / 500 | FT 的 tray 与每 tray part 数（`trays=0` 表示按 `flow` 推断） |

## stdf — 输出

| 字段 | 默认 | 说明 |
|---|---|---|
| `output_dir` | `out` | 产物目录 |
| `gzip` | false | 是否压缩 `.stdf.gz` |
| `byte_order` | little | `little`/`big`（FAR `CPU_TYPE` 随之变化） |
| `cpu_type` | 2 | 写入 FAR |
| `write_wcr` | false | 是否写 WCR |
| `write_csv` / `write_datalog` | true / true | 是否额外写 CSV / 文本 datalog |
| `include_pir_prr` | true | 是否写 PIR/PRR（关闭后只有 PTR/FTR） |

## testprogram — 流程

```yaml
program: DEMO_CNT8
revision: '1.0'
flows:
  CP1:
    - item: continuity
      test_num: 1000
      params: {pins: [RSTN, EN]}
      on_fail: {action: stop}          # continue | stop | retest
    - group: dc
      items: [{item: idd_static, test_num: 2000}]
    - branch:
        if: 'value("idd_static") > 2000'
        then: [{item: leakage}]
        else: [{item: delay_ms, params: {ms: 0.5}}]
    - loop: {count: 3, items: [{item: functional, params: {pattern: cnt_basic.pat}}]}
    - item: functional
      enabled: false                   # 关掉但不删
    - goto_bin: 9                      # 强制 bin
```

表达式（`if`）只允许安全白名单：`value("name")`、`passed("name")`、`var("k", default)`、
`get()` 以及 `abs/min/max/round/len/int/float/bool/str`。属性访问、下标、lambda、
导入等一律拒绝（`tests/component/test_executor_flow.py` 覆盖了危险表达式）。

## batch — 批量扫参

```yaml
base:
  session: ../demo_cp/session.yaml
  overrides: {run.sites: 2}
sweep:
  run.temperature: [0, 25, 85]
  dut_profile.yield.pass_rate: [0.85, 0.95]
workers: 4
executor: thread
fail_fast: false
```

`sweep` 做笛卡尔积展开（上面是 3 × 2 = 6 个 job），每个 job 独立输出目录，
`batch report` 汇总 yield/pareto/Cpk。
