# 11 STDF V4 输出

## 范围
自研 writer(`datalog/stdf/records.py`, `writer.py`),严格遵循 STDF V4 规范;字节序可配(默认小端,CPU_TYPE=2 与之匹配)。

## 记录与写入顺序
```
FAR → ATR(可选) → MIR → [RDR] → SDR(每组 site) →
  [WCR → WIR → { PIR(每 site 每 part) → PTR/MPR/FTR... → PRR } → WRR]  (CP)
  或 { PIR → PTR/FTR... → PRR }                                          (FT)
→ TSR(可选) → HBR(每 hard bin) → SBR(每 soft bin) → PCR(每 site + 汇总 head=255) → MRR
```
## 字段映射要点
| 数据 | STDF |
|---|---|
| lot_id/job/operator/test stage/temp/tester name | MIR(LOT_ID, JOB_NAM, OPER_NAM, TSTR_TYP, TEST_TEMP, NODE_NAM, STAT_NUM, MODE_COD='P', RTST_COD, PART_TYP, FLOW_ID, SETUP_T, START_T …) |
| 站点/负载板/探针卡 | SDR(HEAD_NUM, SITE_GRP, SITE_CNT, SITE_NUM[], HAND_TYP, LOAD_ID, CARD_ID …) |
| Wafer | WIR/WRR/WCR(WAF_SIZ, DIE_HT/WID, WF_FLAT, CENTER_X/Y, POS_X/Y) |
| Part | PIR/PRR(HEAD_NUM, SITE_NUM, PART_FLG(含 retest/abnormal/fail 位), NUM_TEST, HARD_BIN, SOFT_BIN, X_COORD, Y_COORD, TEST_T, PART_ID) |
| 参数测试 | PTR(TEST_NUM, HEAD, SITE, TEST_FLG, PARM_FLG, RESULT, TEST_TXT, LO/HI_LIMIT, UNITS, C_RESFMT, LO_SPEC, HI_SPEC, SCAL 等;**首次出现写完整限值,之后可省略限值** 以符合规范的 optional 机制) |
| 功能测试 | FTR(TEST_NUM, TEST_FLG, CYCL_CNT, REL_VADR, RTN_ICNT, PGM_ICNT, FAIL_PIN, PATG_NUM, VECT_NAM, TIME_SET, NUM_FAIL …) |
| 多针 | MPR(可选) |
| Bin 汇总 | HBR/SBR(PASS/FAIL 标志 'P'/'F'), PCR(PART_CNT, RTST_CNT, ABRT_CNT, GOOD_CNT) |

## 实现要求
- 基础类型编码器:U1/U2/U4/I1/I2/R4/Cn/Bn/Dn/kxUn 等,可选字段(不写)与"缺省值"处理按规范。
- 监听 EventBus 实时写文件(流式写入,崩溃时文件尾部可被识别为未完整;MRR 在 `finally` 写出)。
- Multi-site:各 site 的 PIR/PRR 成对且 head/site 正确;同一 touchdown 内 site 可交错写但每个 part 的 PIR→…→PRR 顺序不乱。
- 输出命名:`{lot}_{wafer|trayid}_{stage}_{timestamp}.stdf`,支持 `.gz`。
- 可选附带 `csv_log` 与 `datalog.txt`(ATE 风格人读 datalog)。
- 回读校验(`reader_check.py`):使用 pystdf 或自写 reader 校验记录顺序、PIR/PRR 配对、PCR 计数与实际 part 数一致、PTR 数量与 NUM_TEST 对齐。

## 任务
- T11.1 类型编码器与 record 定义(FAR/ATR/MIR/MRR/PCR/HBR/SBR/SDR/WCR/WIR/WRR/PIR/PRR/PTR/FTR/MPR/TSR/DTR/GDR)。
- T11.2 StdfWriter(流式、gz、字节序)。
- T11.3 EventBus→STDF 适配器(含首次 PTR 限值逻辑、重测标志)。
- T11.4 reader_check 校验工具与 `pyate stdf verify <file>`。
- T11.5 datalog.txt / csv 同步输出。

## 验收标准
- 生成的 CP / FT STDF 能被 pystdf 与主流工具(如 stdf viewer)解析无错误。
- 回读后 part 数、bin 计数、PTR 值与内存结果逐项一致。
- 4 site 并行产生的 STDF 中 PIR/PRR 配对 100% 正确。
