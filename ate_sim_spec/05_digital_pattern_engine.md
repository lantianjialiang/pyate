# 05 数字 Pattern 与向量执行引擎

## Pattern 文件格式(`.pat`,文本,自定义,易解析)
```
pattern  cnt_basic;
pins     CLK, RSTN, EN, Q[0:7];        # 总线展开
timing   tset_fast;                    # 引用 timing.yaml
levels   lv_nominal;
label start:
  # vector: [tset] [opcode] pins_state ; 状态字符: 0 1 L H X Z C(时钟脉冲)
  [tset_fast]                 CLK=C RSTN=0 EN=0 Q=XXXXXXXX ;
  repeat 3  [tset_fast]       CLK=C RSTN=1 EN=1 Q=XXXXXXXX ;
  [tset_fast]                 CLK=C RSTN=1 EN=1 Q=LLLLLLLH ;   # L/H=比较期望
  loop 10 { ... }
  jump start; halt;
```
- 支持:`repeat`、`loop {}` 嵌套、`label`、`jump`、`halt`、`call/return`(可选)、`capture` 存储位。
- 总线展开、注释、include 其他 .pat。

## 解析与模型(pattern/)
- `parser.py`:词法+语法分析,返回 `Pattern(vectors, labels, pins, meta)`;错误带行号。
- 向量以压缩形式存储(opcode 展开在执行期),支持 100 万向量规模。
- `engine.py: PatternEngine.run(pattern, dut, levels, timing, site_hal) -> PatternResult`
  - 逐周期:驱动输入 → `dut.clock_cycle()` → 在 strobe 处比较 H/L/X。
  - 比较用 levels 中 vol/voh 阈值(把 DUT 数字输出映射到电压)。
  - 记录:fail_count、fail_cycles(上限 N,默认 1000)、fail_pins、首次失败周期、capture 数据、执行周期数。
  - 支持 `stop_on_first_fail`、`max_fail_log`。
- 时序仿真(简化):若 `period < dut.min_period(vdd)` 则输出采样错位 → 产生真实的 fmax/shmoo 行为。

## Pattern 生成器(pattern/generators.py)
- 提供脚本化生成:计数器全覆盖、移位寄存器 walking-1、加法器随机、SRAM March C-、LFSR;输出 `.pat` 到 `configs/patterns/`。

## 任务
- T05.1 语法定义与 parser(含总线展开、loop/repeat/label/jump)。
- T05.2 PatternEngine 周期执行与比较。
- T05.3 失败日志结构(供 STDF FTR 使用:cycle_cnt, rel_vadr, rpt_cnt, num_fail, fail_pin bitfield)。
- T05.4 生成器 5 个。
- T05.5 `pyate pattern run <pat> --dut counter8` CLI 调试命令。

## 验收标准
- 对 counter8 正确 pattern 零失败;注入 stuck_at 后精确定位首个失败周期与 pin。
- parser 对语法错误报行号;性能:100 万向量解析+执行 < 30 秒(CPython 可放宽并记录)。
