# 03 虚拟仪器与硬件抽象层(HAL)

## 设计原则
测试项只面向 HAL 抽象接口编程;`SimulatedBackend` 是默认实现。将来可替换成真实仪器驱动,无需改测试项。

## 抽象接口(hal/base.py,使用 abc)
```python
class DPS:        # 电源
    def set_voltage(pin, v); set_current_limit(pin, i); connect(pin); disconnect(pin)
    def measure_current(pin) -> float; measure_voltage(pin) -> float
class PMU:        # 参数测量单元 (FVMI / FIMV)
    def force_voltage(pin, v, irange); force_current(pin, i, vclamp)
    def measure_current(pin); measure_voltage(pin); disconnect(pin)
class DigitalChannel:  # 数字通道
    def apply_levels(levels); apply_timing(timing); load_pattern(p); run(start_label, stop) -> PatternResult
class Relay:      # 继电器/DIB
    def close(name); open(name)
class Timing:     # 时序集管理
class TesterHAL:  # 聚合,按 site 取资源
    def site(site_id) -> SiteResources
    def reset(); self_check()
```

## 模拟后端(hal/simulated.py)
- 所有仪器读数来自 **DUT 模型**(见 04)的物理响应函数:
  - DPS 电流 = DUT.supply_current(vdd, state) + 噪声
  - PMU 在 FVMI 下读 DUT 的 pin 漏电/输入电流;连续性测试读保护二极管压降(~0.6V,可注入 open/short)
  - 数字通道执行 pattern 时逐周期驱动 DUT 并比较输出
- 噪声模型:高斯噪声(sigma 可配)、温漂系数、可重复随机种子 `seed`。
- 资源冲突检测:同一通道被两个 pin 使用、未 connect 就测量、超过电流限制等 → 抛出 `HALError` 并带 site/pin。
- 耗时模拟:可选 `sim_time_scale`(0=不 sleep,1=真实时间),各操作有基准耗时(配置),用于测试时间估算 `test_time`。
- 每个 site 独立资源实例,保证 multi-site 并行安全。

## 任务
- T03.1 定义抽象接口与异常体系(`HALError`, `OverCurrent`, `NotConnected`)。
- T03.2 实现 SimulatedDPS / PMU / Relay / Timing。
- T03.3 实现 SimulatedDigitalChannel(依赖 05 的引擎,先留接口桩)。
- T03.4 TesterHAL 聚合与 site 资源分配(基于 channel_map/loadboard)。
- T03.5 噪声/种子/时间模拟。

## 验收标准
- 单测:FVMI 对理想电阻性 DUT 读数误差 < 噪声 3σ;未 connect 测量必抛 NotConnected。
- 固定 seed 两次运行读数完全一致。
- 4 个 site 并行调用互不串扰(线程测试)。
