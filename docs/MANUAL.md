# PyATE 使用说明书

这份说明面向第一次打开程序的人。它讲三件事：模拟器在做什么、一次测试是怎么跑完的、界面和命令行上具体怎么操作。字段级的默认值和枚举在 [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)。30 分钟的命令行练习在 [QUICKSTART.md](QUICKSTART.md)。

## 1. 它模拟什么

PyATE 是一台离线的 ATE（自动测试设备）。没有真实仪器，也没有真实芯片。

一次运行里，程序按 `session.yaml` 把这些东西装到一起：

- **测试机**：虚拟的 DPS、PMU、继电器和数字通道。电压、电流、pattern 比较都发生在这里。
- **DUT**：数字行为模型（计数器、移位寄存器等）。缺陷和工艺波动按 `seed` 注入，所以同一个 seed 会得到同一批结果。
- **测试程序**：一棵 TestFlow。每个节点是一个 TestItem（漏电、静态电流、functional、shmoo、延时……），带 test number、参数和失败后的动作。
- **场景**：CP 用 prober 在晶圆上逐个 touchdown；FT 用 handler 在 tray 里逐颗上料。可以有多个 site 并行。
- **结果**：每条测量变成 datalog，每颗 die/part 得到 soft bin，最后写出 summary、binmap 和 STDF。

GUI 和命令行都只调用 `pyate.api`。界面上改配置、点 Start，和命令行 `pyate run` 走的是同一条执行路径。

```mermaid
flowchart LR
  session[session.yaml] --> load[加载并校验]
  load --> flow[TestFlow]
  flow --> sites[各 site 执行 TestItem]
  sites --> hal[虚拟仪器 + DUT]
  hal --> events[测量事件]
  events --> gui[GUI datalog / wafer / summary]
  events --> files[summary / STDF / binmap]
```

## 2. 一次运行里发生的事

1. 读 `session.yaml`，再按里面的路径读 tester、channel map、levels、timing、limits、binning、DUT、wafer 或 handler、测试程序。
2. 校验通道是否冲突、pattern 引脚是否存在、test number 是否唯一。失败时指出**文件和字段**，并且不会用坏配置覆盖磁盘。
3. 按 lot 里的 wafer（CP）或 tray（FT）生成待测单元。`run.sites` 决定这次用几个 site。`run.disabled_sites` 里的 site 直接 mask 掉。`run.parallel_mode` 取 `sequential`、`thread`、`process` 或 `async`。`process` 实际退化为线程。
4. 每个 touchdown 上，执行器按 flow 顺序跑 TestItem。某个节点 `enabled: false` 会被跳过。`on_fail.action` 可以是 `continue`（记失败 bin，继续后面的项）、`stop`（这颗不再往下测）或 `retest`。
5. 每个 TestItem 之前都会看一次暂停。界面上的 Pause、断点和 Step 都作用在这里。
6. 测完一颗就发一条 part 结果（坐标或 part id、soft bin、第一个失败项）。CP 把 bin 画到 wafer 上，FT 画到 tray 上。
7. 全部结束后写 `summary.json`、`run_snapshot.yaml`（这次真正生效的配置）和 STDF。

虚拟时间由仪器自己推进（`simulation.sim_time_scale: 0` 时不等真实墙钟），所以一小片 wafer 通常几秒到几十秒就结束。`demo_cp` 是 300 mm、六百多颗 die，用来看完整良率；下面的 `demo_intro` 只有大约二十颗，用来学操作。

## 3. 配置文件各管什么

都从 `session.yaml` 指出去。路径相对于 session 文件所在目录。

| 文件 | 作用 |
|---|---|
| `tester.yaml` | 通道数量、向量深度、仿真噪声和虚拟时间比例 |
| `channel_map.yaml` | 引脚在每个 site 上占用哪条通道 |
| `loadboard.yaml` | 负载板编号、site 数、继电器名。pin 到通道只写在 `channel_map` |
| `levels.yaml` | 电平集：VIL/VIH/VOL/VOH 和 VDD |
| `timing.yaml` | 时序集：周期和驱动/比较沿 |
| `testprogram.yaml` | 各 stage 的 flow：item、test number、参数、`on_fail`、是否启用 |
| `limits.csv` | 每个 test 的上下限。比较时按 test number 和名字查找 |
| `binning.yaml` | soft/hard bin、pass bin、失败码到 bin 的映射 |
| `dut_profile.yaml` | DUT 模型、良率、缺陷比例、工艺 σ |
| `wafer.yaml` | 直径、die 尺寸、notch 方向、边缘排除、跳过的 die |
| `lot.yaml` | lot id、要测哪些 wafer、阶段顺序 |
| `prober.yaml` / `handler.yaml` | 换片时间、接触不良概率、site 排布 |
| `patterns/*.pat` | 数字向量。functional 和 shmoo 按名字来找 |
| session 里的 `run` | 这次用的 stage、site 数、seed、输出目录、禁用的 site |

在 Config 页改完点 **Save file**。程序先按模型校验，通过才写回。YAML 按模型重写，原来文件里的注释不会保留。

## 4. 安装和启动

需要 Python 3.10+。GUI 还需要 PySide6。

PowerShell，在仓库根目录：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

已经安装过时：

```powershell
pyate gui configs\demo_intro\session.yaml
```

还没安装、直接从源码启动：

```powershell
$env:PYTHONPATH = "src"
python -m pyate.cli.main gui configs\demo_intro\session.yaml
```

默认是浅色主题。`--theme dark` 切到深色，`--theme light` 是显式指定浅色。不写 session 会打开空窗口，再到 Config 页 **Open...** 选择 `session.yaml`，然后 **Load**。

命令行跑同一份示例（不打开窗口）：

```powershell
$env:PYTHONPATH = "src"
python -m pyate.cli.main run configs\demo_intro\session.yaml
```

## 5. 用 demo_intro 走一遍

`configs/demo_intro` 是最短的一套可运行配置。它复用 `demo_cp` 的测试机、通道、电平和 bin 定义，自己只改了四件事：大约 20 颗 die 的小晶圆、1 个 site、一条四步 flow、两个 pattern。

Flow 顺序：

| test number | 项 | 用来看什么 |
|---|---|---|
| 2000 | `idd_static` | 有数值，Summary 里能出直方图、趋势和概率图 |
| 3000 | `functional` | 跑 `patterns/cnt_basic.pat`，这是会通过的向量 |
| 9000 | `delay_ms` | 专门用来下断点，停在这一项前面 |
| 9100 | `shmoo` | 3×3 的 VDD × period 扫描，跑完右侧 Shmoo 页有图 |

另外有一个故意写错的 `patterns/cnt_fail.pat`，不在 flow 里。Config 页的 `patterns` 下面能看到它和会通过的 `cnt_basic.pat`，失败原因写在文件开头的注释里。Pattern 页可以单步调试这份失败向量。

### 5.1 打开并确认配置

启动后状态栏应类似 `demo_intro: stage CP1, 1 site(s)`。

- **Config**：左边树是 session 引用的每个文件，以及 `patterns` 下的向量。`cnt_basic.pat` 是会通过的向量，`cnt_fail.pat` 是故意失败的向量；点失败的那一项，说明页写了原因：复位后第一拍 `SO` 仍是 0，文件里却写成了 `H`。点 `levels` 或 `run`，右边是表单和 YAML。改一个数字后 **Save file**；校验失败时下面的文本会带字段路径，磁盘上的文件不变。向量文件只读，不会被 Save 写成 YAML。
- **TestFlow**：应看到 `idd_static`、`functional`、`delay_ms`、`shmoo`。取消勾选会把 `enabled` 写成 false 并保存 `testprogram.yaml`，执行时跳过该项。选中一项可以改参数和 `on_fail`，再点 **Apply to node**。
- **Limits/Bins**：改 lo/hi 后 **Apply changes**，再在 Config 页 **Save limits** 才写回 CSV。

### 5.2 断点和单步

执行器在**每个测试项开始之前**看暂停。断点按 test number 记，所有 site 共用；命中一次就消费掉（one shot），所以点 **Resume** 会真的继续跑完，而不是在下一颗 die 的同一个测试上再停一次。

1. 打开 **Run**。
2. **break at** 下拉框列出当前 stage 的所有测试项（显示成 `9000  shmoo`，前一位是 test number），选中后点 **Arm**。标签变成 `breakpoints: 9000 (one shot)`。想用列表里没有的编号，也可以直接在框里输入数字再 Arm。
3. 点 **Start**。上一轮的 datalog、wafer、summary 和 shmoo 会先清掉。flow 跑完 2000 和 3000 后，在 9000 之前停下：右边状态变成 `state: paused`，日志里有一行 `breakpoint at test 9000 shmoo - paused, press Resume to continue`，断点标签变成 `hit 9000 shmoo (site 1) - Resume to continue`。
4. 点 **Step**：只执行接下来的一项（9000），然后再次暂停。再 Step 一次会跑后面的项。
5. 点 **Resume** 继续，直到这一轮结束。**Pause** 是在下一项之前停住。**Stop** 放弃这一轮。**Clear data** 丢掉当前结果并停掉正在跑的那一轮；断点和 site 勾选不动。
6. **Clear** 去掉全部断点（包括还没命中的）。

停着的时候按钮会自动切成可用状态：**Resume** / **Step** 只在 `state: paused` 时可点，**Pause** 只在 running 时可点，**Start** 在跑的时候禁用。日志级别选 ALL（默认）才看得到断点那几行。

想“跑一项停一下”，也可以不设断点：Start 之后马上 Pause，然后反复 Step。

取消某个 site 的勾选（`S1`、`S2`……）再 Start，这些 site 不会测。这写入已有的 `run.disabled_sites`。`demo_intro` 只有 1 个 site。

### 5.3 看 wafer、summary 和 shmoo

Start 跑完后（大约几十秒，视机器而定）：

- **Wafer**：每颗 die 的颜色是 soft bin，边缘的 die 也是完整的方块，不会被圆边裁成一块。鼠标停在 die 上可以看到坐标、bin，以及这颗的第一个失败测试和值。圆片底部只有一个很小的定位豁口。下面一条是 bin 比例。**Export wafer map PNG** 把当前图存成图片。
- **Summary**：默认 **group by = test**，每个测试一行。改成 **site** 按 site 拆开，改成 **wafer** 按晶圆拆开（多片 wafer 时用 `demo_cp` 的 lot）。有数值的测试会列出 mean、stdev、min、max，以及规格 lo/hi、Cp、Cpk（Cp 要上下限都有，Cpk 有一边即可；σ 用样本标准差 n−1）。点一行，下面同时画该组的 histogram、trend 和 probability。三张图上的橙色虚线是 lower limit，红色虚线是 high limit；鼠标停在柱、点或限值线上会显示对应的值。probability 的横轴是测量值，纵轴是标准正态分位；灰色虚线是这组数据的正态参考线。
- **Shmoo**：flow 里的 9100 跑完后这里出现 pass/fail 色块。横轴是 period（ns），纵轴是 VDD。悬停一个格子可以看到电平和周期。**PNG** / **CSV** 导出。想看一张真正的 shmoo（有速度边界，而不是一片同色），用 `configs\demo_shmoo\session.yaml`：它是 1 site、小晶圆、慢速 DUT，21×31 的扫描在 3.6 V 只需要 21 ns，到 2.75 V 却要 119 ns，所以边界斜着穿过整张图，2.7 V 以下是全红（供电不足）。

不想把 shmoo 放进每一颗 die 时，从 flow 里取消勾选 9100，改用 Shmoo 页的 **Run shmoo**。它只用当前 session 的 DUT、电平和 pattern，在一个 site 上扫一次，不走整片晶圆。

- **跑的是哪份配置**：当前 session、当前 stage 里第一个 `shmoo` 项。VDD/period 范围、pattern、cycle 数都取自那一项，色块上方那行灰字写明用的是哪套配置、哪个 stage、多大网格（例如 `demo_shmoo · CP1 · 21×31 grid · VDD 2.6–3.6 V · 8–128 ns · 32 cycles · nominal die`）。flow 里没有 shmoo 项时（`demo_cp`、`demo_intro`）才退回 items 的默认范围 2.7–3.6 V、10–120 ns。
- **网格默认跟 flow 一致**：V / T / cyc 三个框在打开 session 时就填成那一项的值，点一下得到的图和整轮跑出来的分辨率相同；改这三个数才是自己缩小或放大网格。
- **和整轮为什么可能不一样**：Run 页的图来自**最后一颗 die**，每颗 die 有自己的 `vth_shift`（按 `dut_profile.process.vth_sigma` 采样），边界会左右偏一点，`demo_shmoo` 的 21 颗落在 57.8–59.3% 之间；**Run shmoo** 用的是**标称 die**（不做 die-to-die 抖动），所以在同一个网格下它等于"中间那颗"，和具体某一颗略有差别。想看某一颗，就在 TestFlow 里选中 9100 用 **Run selected item**（见第 6 节），它和这个按钮用同一个标称 die，因此两者在相同网格下结果完全一致。

### 5.4 调试一条 pattern

Pattern 页不跑 flow，只对一个 `.pat` 做向量比较。

1. 打开 **Pattern**。
2. **Open...** 选择 `configs\demo_intro\patterns\cnt_fail.pat`。DUT 保持 `composite`，VDD 3.3 V，period 20 ns 即可。
3. 点 **Run pattern**。结果应是 FAIL。文字区给出第一个失败周期、期望值和实际值。
4. 下面的比较条按 pin 画出 expected 和 actual，不一致的 pin 是红色。

把文件换成 `cnt_basic.pat` 再跑一次，应是 PASS，比较条清空。flow 里的 functional 用的就是这份会通过的 pattern。

改向量的方法：`L`/`H` 是比较低/高，`0`/`1` 是驱动。`cnt_fail.pat` 只把第二拍的 `SO=L` 改成了 `SO=H`，所以比较会在 SO 上失败。

## 6. 界面上的其他页

**TestFlow 的排序。** 选中一个节点后点 **Move up** / **Move down**，节点移动后仍然保持选中（焦点也回到树上），可以连点，不用每次重新选。也可以直接拖拽，落点后的节点同样保持选中。

**TestFlow 里单独跑一项。** 选中一个测试项，右侧 **run one item** 里选 site（默认 site 1，也可以选 all sites 一次跑完所有 site），点 **Run selected item**。它只执行这一项：不勾 wafer、不 bin、不写 STDF，结果显示在 Run 页的 datalog 和 Summary（页签会自动切到 Run）。参数直接取右边表单里的**当前值**，不用先按 **Apply to node**，所以可以先改一个 cycle 数再点运行；文件不会被改写。默认勾着 **clear Run data first**：单跑一项的 samples 和整轮混在一起会把 Cpk 算错，想保留上一轮就取消勾选。跑完状态栏和 Run 页日志各有一行结果，右侧框里也留着，方便重复点。**Stop the run first**：整轮还在跑时这个按钮会拒绝执行。它用的也是标称 die，和 Shmoo 页的 **Run shmoo** 完全一致。

**STDF。** **Open STDF...** 选择一次运行写出的 `.stdf`。最上面是一张统计卡片：文件路径、`lot / part / job / node / stage / temp` 一行、`parts / good / yield / retests / PTR / FTR` 六个大数字、hard/soft bin 芯片行，以及各记录条数。下面的表和三张图与 Run 的 Summary 相同，也可以按 test、site、wafer 分组，数据来自文件里的 PTR/FTR，不是当前这次 GUI 运行。good 与 yield 按 PRR 的 `PART_FLG`（0x08 = fail、0x10 = 未测）统计，和 run 汇总的口径一致。

**插件。** 最后一个页签 **Plugins**（或菜单 Tools → Plugins...）。顶部一行是搜索路径：`PYATE_PLUGIN_PATH` 加上仓库（打包版是 exe 同级）的 `examples/plugins`。

- **installed plugins** 表：plugin / status / api / contributes / source，contributes 是它注册的东西（`dut:adc`、`testitem:adc_inl_dnl`、`sink:json`）。选中一行，表下灰字显示这个插件的说明和注册清单。
- **Disable / Enable**（右上）：立即把插件停用或启用。停用不只是不跑钩子，它注册的 DUT 模型、test item、sink 也一起从注册表撤掉（会顺带删掉全局注册，重新启用再加回来），所以 Run 页下一次跑就用新注册表；正在跑的那一轮不受影响。
- **available in the search paths**：磁盘上有、还没加载的插件。双击一行或点 **Load selected** 加载；已经加载的显示 `loaded`，按钮变灰。
- **Load file... / Load folder...**：加载任意 `.py` 或包目录（`src` 布局的插件项目目录也认）。**Reload all** 丢弃现有 manager 重新扫描，手工加载过的插件会被记住并重新加载。
- 底部日志是 registry 的 note / warning / error；比如钩子方法忘了 `@hookimpl`，这里会直接告诉你它不会执行。

插件的钩子输出（例如 `failwatch` 的失败清单）仍会写到 `<out>` 目录并出现在 Run 页日志。写法和完整例子见 README 的「插件」一节与 `docs/EXTENDING.md`。

**日志。** Run 页最下面。级别可选 ALL、INFO、ERROR。断点命中、shmoo 开始和结束、运行结束都写在这里。

**主题。** 打开时是浅色（默认），菜单 View 里可以在浅色/深色之间切换，当前主题前面带勾。状态栏右侧和 Help → About 是版权信息 © lantianjialiang。页签顺序是 Config、TestFlow、Limits/Bins、Run、Pattern、STDF、Plugins。批量扫参（`batch.yaml`）没有界面，用命令行 `pyate batch`。

多 site 的完整 CP 用 `configs\demo_cp\session.yaml`（300 mm、4 site）。FT、tray、卡料和重测用 `configs\demo_ft\session.yaml`，Run 页的 **Tray** 每个 site 一格，颜色是最近一颗的 bin，jam 会标出来。

## 7. 命令行

和 GUI 同一套配置。

```powershell
pyate config validate configs\demo_intro\session.yaml
pyate run configs\demo_intro\session.yaml --sites 1 --seed 7
pyate run configs\demo_cp\session.yaml --set lot.wafers='["W01"]' --sites 2
pyate pattern run configs\demo_intro\patterns\cnt_fail.pat --dut composite --cycles 64
pyate stdf verify out\某个文件.stdf
pyate batch run configs\demo_batch\batch.yaml
pyate lot run configs\demo_cp\session.yaml --stages CP1 --stage-session FT1=configs\demo_ft\session.yaml
```

`--set 点号路径=YAML值` 覆盖一项配置，不改文件。`--json` 打印机器可读的 summary。`--seed` 固定缺陷和噪声：同配置同 seed，测量结果一致。

`pattern run` 就是 Pattern 页的命令行形式，用来在不打开 flow 的情况下看第一个失败周期。

## 8. 产物怎么读

默认写到 session 里 `run.output_dir`（示例是仓库下的 `out/`，GUI 里也可以在运行前改）。

| 文件 | 内容 |
|---|---|
| `summary.json` | 这轮的 tested、passed、yield、UPH、每个 wafer 或 tray 一行 |
| `run_snapshot.yaml` | 含覆盖项在内的完整生效配置，复现问题时先看它 |
| `*_binmap.csv` | 每颗 die 的 x、y、bin |
| `stats.json` | 每个测试的均值、σ、Cpk |
| `*.stdf` | STDF V4，可用 `pyate stdf verify` 检查 |

GUI 的 Summary 是**这一轮已经收到的测量**的在线统计，按测试（以及可选的 site 或 wafer）汇总 mean、stdev 和 Cpk。它和 `summary.json` 里的 lot 级良率不是同一张表：lot 级看有多少颗 pass，测试级看某一个 test number 的分布。`stats.json` 是同一套 Cp/Cpk 写到文件里的版本。

## 9. 四套示例怎么选

| 目录 | 什么时候用 |
|---|---|
| `configs/demo_intro` | 学 GUI：断点、pattern 调试、shmoo、summary。晶圆很小 |
| `configs/demo_cp` | 看一片真实规模的 CP、wafer map 和 STDF。flow 里没有 shmoo |
| `configs/demo_ft` | Handler、tray、contact fail 和重测 |
| `configs/demo_shmoo` | 专门演示 shmoo：1 site、小晶圆、慢速 DUT，21×31 的 VDD × period 有一条斜的速度边界 |
| `configs/demo_batch` | 温度等参数的批量扫描（命令行） |

更长的字段说明、插件写法和 STDF 字段对照见 `docs/` 里的其他文档。
