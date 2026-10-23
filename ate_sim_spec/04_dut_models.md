# 04 DUT 行为模型(数字电路)

## 基类(dut/base.py)
```python
class DUTModel(ABC):
    pins: dict[str, PinSpec]          # 方向、类型
    def reset(self); def power(vdd: float)
    def clock_cycle(inputs: dict[str,int]) -> dict[str,int]   # 周期级行为
    def pin_leakage(pin, v) -> float; def supply_current(vdd, state) -> float
    def continuity_vdiode(pin, polarity) -> float
    def inject(defect: Defect)
```
- `power()` 低于 `vdd_min` 时输出未知(X)或全 0,模拟欠压失效;`vdd` 越低 `fmax` 越低(用于 shmoo)。

## 内置模型(dut/models/,各自带参数化宽度)
| 模型 | 说明 |
|---|---|
| inverter / nand / nor / xor | 组合逻辑门阵列(多路) |
| adder (N bit) | 组合加法器 |
| counter (N bit, 同步复位/使能) | 时序 |
| shiftreg (N bit, SI/SO) | 时序,扫描链雏形 |
| lfsr | 伪随机序列 |
| sram_simple (深度×位宽) | 读写 March 测试 |
| composite | 组合以上模块成一个 DUT(用于 demo) |

## 缺陷注入(Defect)
- `stuck_at(pin|node, 0/1)`、`open(pin)`、`short(pinA,pinB)`、`leakage(pin, nA)`、`high_idd(factor)`、`slow(vdd_threshold/fmax_derate)`、`random_bitflip(prob)`。
- 良率仿真:`dut_profile.yaml` 中 `yield: {pass: 0.9, defect_mix: {...}}`,每个 DUT 按 seed 采样决定是否带缺陷;CP 时可叠加**空间分布**(边缘 die 失效概率更高、中心聚集簇)。

## 工艺波动
每个 DUT 采样:vth 偏移、idd 缩放、leakage 缩放(正态/对数正态),让量测值呈分布,便于生成 Cpk/直方图。

## 任务
- T04.1 基类与 PinSpec。
- T04.2 实现上述 7 类模型 + composite。
- T04.3 缺陷注入框架与 yield 采样器(支持 wafer 空间分布回调)。
- T04.4 工艺波动采样。

## 验收标准
- 每个模型有周期级真值表/序列单测;注入 stuck_at 后输出确定性错误。
- 1000 次采样良率与配置偏差 < 3%。
