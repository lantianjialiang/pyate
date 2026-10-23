# 快速上手（约 30 分钟）

目标：从零跑完一次 CP、看懂产物、再跑完整条 lot（CP → FT），最后打开 GUI 看直观结果。

## 0. 准备

```bash
python -m venv .venv
. .venv/Scripts/activate          # Windows
pip install -e ".[dev]"           # dev 里含 PySide6，GUI 测试也能跑
pyate version
```

仓库自带两套示例配置，不需要额外文件：

- `configs/demo_cp/`：300 mm wafer、4 sites、counter + shift register DUT
- `configs/demo_ft/`：Handler、tray、重测、contact fail
- `configs/demo_batch/`：温度 × DUT profile 的笛卡尔积扫参

## 1. 先校验配置

```bash
pyate config validate configs/demo_cp/session.yaml
```

输出会列出各文件是否解析成功、site 数、stage、DUT 模型。改错一个字段（例如把
`run.sites` 写成字符串），错误信息会指出 **文件 + 字段路径 + 期望类型**，这是后续
调参最快的手段。

## 2. 跑一次 CP

```bash
pyate run configs/demo_cp/session.yaml --set lot.wafers='["W01"]' --sites 2 --seed 42
```

会看到类似：

```
running demo_cp stage CP1 with 2 site(s)...
lot      : LOT-CP-0001  stage CP1  program DEMO_CNT8
parts    : 673 tested, 622 passed (92.422 % yield)
bins     : {'1': 622, '2': 18, '3': 5, '4': 5, '6': 10, '7': 13}
uph      : 26255.44   duration: 25.142 s
summary  : out/cli_run/summary.json
snapshot : out/cli_run/run_snapshot.yaml
  wafer  W01        parts=  673 pass=  622 yield=  92.42% uph=  26255.4
```

同一条命令重复跑，除了 STDF 里的时间字段（见 `docs/STDF_MAPPING.md`），结果逐条一致。

要点：

- `--set K=V` 直接覆盖任意配置项，`K` 用点号路径，值为 YAML 字面量。多次 `--set` 可叠加。
- `--seed` 决定缺陷注入与量测噪声，**同 seed + 同配置 = 同结果**（E2E-REPRO 保证）。
- `--units N` 控制打印多少个 unit，`--json` 给出机器可读汇总。

## 3. 看产物

`out/cli_run/` 下：

| 文件 | 用途 |
|---|---|
| `summary.json` | 整轮汇总：yield、bin 分布、UPH、每个 unit 一行 |
| `run_snapshot.yaml` | 本次运行生效的完整配置（含 --set 覆盖），复现问题的第一手材料 |
| `LOT-CP-0001_W01_CP1.stdf` | STDF V4，任何 ATE 工具链都能读（文件名不含时间戳，重跑覆盖） |
| `W01_binmap.csv` | die 级 binmap，可直接画图（`svg`/`txt` 同目录） |
| `stats.json` | 每项测试的均值/σ/Cpk |

校验 STDF：

```bash
pyate stdf verify out/cli_run/LOT-CP-0001_W01_CP1.stdf
# => PASS   records: FAR=1 MIR=1 PIR=673 PRR=673 PTR=... 
```

`verify` 会检查记录顺序、长度头、必填字段，并在装了 pystdf 时做交叉回读。

## 4. 改测试流程（TestFlow）

打开 `configs/demo_cp/testprogram.yaml`，找到 `flows.CP1`：

```yaml
CP1:
  - item: continuity
  - item: leakage
  - item: functional
    params: {pattern: cnt_basic.pat}
```

试三件事：

1. 加 `enabled: false` 关掉某项 → 该项不执行，PRR 里不会有它的 PTR。
2. 加分支：

   ```yaml
   - branch:
       if: 'value("idd_static") > 2000'
       then: [{item: leakage}]
       else: [{item: delay_ms, params: {ms: 1}}]
   ```
3. 加 `goto_bin: 9` 强制分到工程 bin（即使全部 pass）。

改完直接 `pyate run ...`，不必重新安装。

## 5. 跨阶段跑完整条 lot

CP 之后通常先修修补补再 FT。`lot run` 会把上一阶段的良品（KGD）喂给下一阶段：

```bash
pyate lot run configs/demo_cp/session.yaml \
  --stages CP1,CP2 \
  --stage-session FT1=configs/demo_ft/session.yaml \
  --out out/lot_demo
```

输出：

```
CP1  tested 673  passed 610  yield 90.64%  kgd 610
CP2  tested 610  passed 601  yield 98.52%  kgd 601
FT1  tested 601  passed 585  yield 97.34%  kgd 585
first pass 90.64%  final 86.93%  cumulative 86.93%
report: out/lot_demo/lot_report.json
```

- `--stages` 省略时取 `lot.flow`（在 `lot.yaml` 里配）。
- 每个 stage 可以用不同 session：`--stage-session FT1=...`。省略则沿用主 session。
- `lot_report.json` / `lot_report.md` 记录每阶段 tested/passed/yield/KGD，`first_pass_yield`
  与 `cumulative_yield` 是绩效报表最常用的两个数。

## 6. 批量扫参

```bash
pyate batch run configs/demo_batch/batch.yaml --workers 2
pyate batch report configs/demo_batch/out/TEMP_X_PROFILE --cpk 10
```

`batch.yaml` 里 `overrides` + `sweep` 会展开成笛卡尔积（示例是 3 个温度 × 2 个 DUT
profile = 6 个 job），产物落在 `output_dir` 下：每个 job 一个子目录，外加
`batch_summary.json/csv` 与 `batch_report.md`。`batch report` 输出 aggregate yield、
bin pareto 与 Cpk 表，比逐个跑再手工汇总省事。

## 7. GUI

```bash
pyate gui --theme dark
```

页签顺序就是工作流：Config（打开/校验）→ TestFlow（勾选启停）→ Limits/Bins（改 limit）
→ Run（跑、看 datalog / wafer map / bin bar）→ Batch → Pattern。

几个常用操作：

- 打开 `configs/demo_cp/session.yaml`，Run 页签点 **Start**，右侧 wafer map 实时按 bin 上色。
- **Export wafer map PNG** 导出图片（自动按 wafer 直径与 die 尺寸缩放）。
- View 菜单切换 dark/light。
- Pattern 页签可以不改配置直接跑 `.pat`，用来调向量。

## 8. 下一步

- 想加测试项 → [EXTENDING.md](EXTENDING.md)
- 想查某个 YAML 字段 → [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)
- 想看 STDF 字段怎么来的 → [STDF_MAPPING.md](STDF_MAPPING.md)
