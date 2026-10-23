# 配置 / 测试项 → STDF 字段映射

STDF V4 由自研 writer 写出（`pyate/datalog/stdf/writer.py`，纯 `struct`），
`pyate/stdf/reader_check.py` 做结构校验，pystdf 仅用于测试期交叉回读。

## 记录顺序

一次 CP 运行（两片 wafer）：

```
FAR → ATR → MIR → SDR
  → WIR → [WCR] → {PIR → PTR/FTR... → PRR}
  → TSR → HBR → SBR → PCR
  → WRR
  ...
MRR
```

- FAR/ATR 全局一次；MIR/SDR 全局一次；WIR/WRR 每片 wafer 一对。
- FT 没有 WIR/WRR，只有 PIR/PRR 循环 + 汇总记录。
- `stdf.include_pir_prr: false` 时只写 PTR/FTR（便于只关心量测的场合）。

## 全局记录

| 记录 | 字段 | 来源 |
|---|---|---|
| FAR | `CPU_TYPE` | `stdf.cpu_type`（默认 2），`stdf.byte_order` 决定实际字节序 |
| FAR | `STDF_VER` | 固定 4 |
| ATR | `MOD_TIM` / `CMD_LINE` | 写文件时刻 / 实际命令行（便于审计，属于时间字段） |
| MIR | `LOT_ID` / `SBLOT_ID` | `lot.lot_id` / `lot.sublot` |
| MIR | `JOB_NAM` | `testprogram.program` |
| MIR | `TEST_COD` / `FLOW_ID` | `run.stage` |
| MIR | `TSTR_TYP` / `NODE_NAM` | `tester.model` / `tester.name` |
| MIR | `SERL_NUM` | 固定为 `tester.model`（**刻意不用时间戳**，否则破坏可复现性，见 D18） |
| MIR | `TST_TEMP` | `run.temperature`，格式 `%.1f` |
| MIR | `DATE_COD` | 当天日期 `YYYYMMDD`（时间字段） |
| SDR | `SITE_CNT` / `SITE_NUM` | `run.sites`（1..N） |
| SDR | `HAND_TYP` | CP → `PROBER`，FT → `HANDLER` |
| SDR | `CARD_TYP` | CP → `PROBE_CARD`，FT → `DIB` |
| SDR | `LOAD_ID` / `CARD_ID` | `loadboard.board_id` |
| WIR | `WAFER_ID` | `lot.wafers[i]` |
| WCR | `WAFR_SIZ` / `DIE_WID` / `DIE_HT` / `CENTER_X/Y` | `wafer.diameter_mm` / `wafer.die_size_mm` / `wafer.center_die`（仅在 `stdf.write_wcr: true` 时写） |
| WRR | `PART_CNT` / `GOOD_CNT` / `WAFER_ID` | 该 wafer 的统计 |
| MRR | `DISP_COD` | 正常结束为空格 |

## 每个 part

| 记录 | 字段 | 来源 |
|---|---|---|
| PIR | `SITE_NUM` | `PartResult.site` |
| PRR | `HARD_BIN` / `SOFT_BIN` | `BinningEngine` 的判定结果 |
| PRR | `X_COORD` / `Y_COORD` | die 坐标（FT 为 0） |
| PRR | `NUM_TEST` | 该 part 实际写入的 PTR+FTR 个数 |
| PRR | `PART_ID` | CP：`<wafer>-X<x>Y<y>`；FT：`<lot>-T<tray>-P<n>` |
| PRR | `PART_TXT` | 第一个失败项的描述（最多 64 字符） |
| PRR | `TEST_T` | 该 part 的测试耗时（秒，**取整**） |
| PRR | `PART_FLG` | 见下 |

`PART_FLG` 位：

| 位 | 含义 | 何时置位 |
|---|---|---|
| 0x01 | not tested | `part.skipped`（site 禁用、prober 跳过该 die） |
| 0x02 | fail | 非 pass bin |
| 0x04 | retest | `part.retest`（重测产生的 PRR） |
| 0x08 | abnormal | part 上带 `error`（site 崩溃、仪器异常） |

## 每个测试项

| 记录 | 字段 | 来源 |
|---|---|---|
| PTR | `TEST_NUM` | flow 节点的 `test_num`（或测试项自报） |
| PTR | `TEST_TXT` | `TestResult.test_name` |
| PTR | `RESULT` | `TestResult.value` |
| PTR | `LO_LIMIT` / `HI_LIMIT` | 见「limit 取值」 |
| PTR | `UNITS` | `TestResult.unit` |
| PTR | `TEST_FLG` | pass/fail（0x40/0x80）+ 超时置 incomplete |
| PTR | `LO_SPEC` / `HI_SPEC` | 与 limit 相同（未单独配置 spec 时） |
| FTR | `TEST_NUM` / `TEST_TXT` | 同上 |
| FTR | `CYCL_CNT` / `REL_VADR` / `REPT_CNT` | `TestResult.cycle_cnt` / `rel_vadr` / 1 |
| FTR | `NUM_FAIL` | `TestResult.num_fail`（比较失配的 pin 数） |
| FTR | `FAIL_PIN` | `TestResult.fail_pins` 的位图（1 字节 bit 数 + `ceil(bits/8)` 字节） |
| FTR | `VECT_NAM` | `TestResult.vector_name` |
| FTR | `RSLT_TXT` | `FAIL@cycle N pins=...` 摘要 |

哪些项写 PTR、哪些写 FTR：由测试项返回的 `TestResult.kind` 决定
（`PTR` 数字量测、`FTR` 功能/向量、`MPR` 多结果——`MPR` 目前落到 PTR 形态，见 D12）。

> `make_result()` 会把 `cycle_cnt` / `num_fail` / `fail_pins` / `rel_vadr` /
> `vector_name` 这些**真实字段**填进 `TestResult`，其余键进 `extra`。如果自定义测试项
> 只用 `extra={"cycle_cnt": ...}`，FTR 的 `CYCL_CNT` 会是 0——这是曾经的 bug，已修。

## limit 取值

1. `limits.csv` 按 `test_num` + `stage` + 温度窗匹配：stage 精确匹配优先于 `*`，
   温度窗越窄越优先；
2. 找不到时回落到测试项参数里的 `lo`/`hi`；
3. 都没有时只写前 9 个字段（`TEST_NUM..OPT_FLAG`），不写 limit 字段——
   这是 STDF 允许的「无 limit」形态，`reader_check` 能正确识别。

## 汇总记录

| 记录 | 内容 |
|---|---|
| TSR | 每个 `(site, test_num, kind)` 一条：`EXEC_CNT`、`FAIL_CNT`、`TEST_MIN/MAX`、`TST_SUMS`、`TST_SQRS`（后两者用于算 σ/Cpk） |
| HBR | 每个 hard bin 一条（`HBIN_CNT`、`HBIN_PF`、`HBIN_NAM`） |
| SBR | 每个 soft bin 一条 |
| PCR | 每 site 一条 + 一条 `HEAD_NUM=255` 的总计（`PART_CNT`/`RTST_CNT`/`GOOD_CNT`） |

`teststats.json` / `batch report` 里的 Cpk 就是从 TSR 的 `TST_SUMS`/`TST_SQRS` 反推的
（也可直接从 `stats.json` 读，无需解析 STDF）。

## 可以预期的「不稳定字段」

做字节级回归对比时，只有这些字段会变，其余必须完全一致：

| 记录 | 字段 |
|---|---|
| MIR | `SETUP_T`、`START_T`、`DATE_COD` |
| ATR | `MOD_TIM` |
| WIR | `START_T` |
| WRR | `FINISH_T` |
| PRR | `TEST_T` |
| MRR | `FINISH_T` |

`tests/integration/test_reproducibility.py` 就是按这张表剔字段后逐记录比较的：
同 seed + 同配置 → CP/FT 两次运行除上述字段外**逐条一致**；换 seed 则必须不同。

## 校验与回读

```bash
pyate stdf verify out/cli_run/LOT-CP-0001_W01_CP1.stdf          # 结构 + 必填字段 + pystdf 交叉回读
pyate stdf verify out/cli_run/LOT-CP-0001_W01_CP1.stdf --json   # 机器可读
pyate stdf dump out/cli_run/LOT-CP-0001_W01_CP1.stdf --type PRR --limit 5
```

`VerifyReport` 提供 `parts`、`ptr_count`、`ftr_count`、`retest_parts`、`record_counts`
与 `pystdf_ok`，测试里直接断言这些字段比解析文本更稳。
