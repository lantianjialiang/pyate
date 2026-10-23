# 06 TestItem 基类与内置库

## 基类(testitems/base.py)
```python
class TestItem(ABC):
    name: str; test_num: int; params: dict
    def setup(self, ctx: SiteContext, hal): ...
    def execute(self, ctx: SiteContext, hal) -> list[TestResult]: ...   # 必须实现
    def cleanup(self, ctx, hal): ...                                    # 保证 finally 调用
```
- 限值比较统一由框架完成:`limits` 查询表(test_num+stage+temp),TestItem 只返回原始值与单位,避免每个 item 重写判定。
- 异常处理:HALError → 该 item fail 且 fail_code=HAL_ERR;`continue_on_fail` 参数由 flow 控制。
- 注册表:`@register_testitem("continuity")` 装饰器,flow 中按名字引用,参数来自 testprogram.yaml。

## 内置 TestItem(各需 YAML 参数示例)
| item | 内容 | 输出 |
|---|---|---|
| continuity | 每 pin FIMV(±100µA)读二极管压降 | PTR 每 pin 1 个 |
| leakage_ih_il | VIH/VIL 下的输入漏电 | PTR |
| idd_static / idd_dynamic | 上电静态/运行时电流 | PTR |
| vol_voh | 输出电平测试 (IOL/IOH) | PTR |
| functional | 执行指定 pattern,返回 pass/fail+fail 信息 | FTR |
| vdd_min_search | 二分搜索最低工作电压 | PTR(vmin) |
| fmax_search | 二分搜索最高频率(周期) | PTR |
| shmoo | Vdd × Period 二维扫描 | 结果矩阵(可导出 csv/png) |
| power_up_down | 上电下电序列与序列时间 | PTR |
| sram_march | March 测试 | FTR |
| delay_ms / user_code | 延时、自定义 Python 回调 | - |

## 任务
- T06.1 基类、注册表、limit 查询服务。
- T06.2 实现 continuity、leakage、idd、vol_voh、functional(5 个基础)。
- T06.3 实现 vdd_min_search、fmax_search、shmoo、power_up_down、sram_march。
- T06.4 各 item 参数的 pydantic schema(便于 GUI 动态表单)。

## 验收标准
- 对"良品 DUT"全部 pass;对分别注入 open/short/stuck/leakage 的 DUT,对应 item 且仅对应 item fail。
- vdd_min_search 结果与 DUT 模型真值误差 < 搜索分辨率。
