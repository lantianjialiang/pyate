# 09 CP(晶圆测试)与 FT(成品测试)场景

## 公共:Lot / 层次
Lot → (Sublot) → Wafer(CP)或 Tray/Reel(FT) → Touchdown → Part。

## CP 场景(scenario/cp.py, prober_sim.py, wafer_map.py)
- WaferMap:圆形晶圆栅格生成(直径、die 尺寸、中心 die、notch 方向、edge exclusion),die 坐标 (x,y),屏蔽 die(`skip_list`)。
- ProberSimulator:
  - 按蛇形/行扫/自定义顺序步进;多 site 布局(如 2×2)时按 touchdown 覆盖多个 die,边缘不足时自动 mask site。
  - 事件:`WAFER_LOAD → ALIGN → STEP → TEST → INK(可选) → WAFER_UNLOAD`。
  - 仿真参数:index_time、wafer_change_time、探针接触失败概率(contact_fail_prob → 触发 continuity fail)、温度(-40/25/125 度,影响 DUT 参数)。
- CP 多阶段:CP1/CP2 流转,CP2 仅测 CP1 pass 的 die(读取上一阶段 wafer map/STDF 或内部 map)。
- 输出 WaferMap 汇总:bin map(文本 + 图片)、良率、各 bin 计数。

## FT 场景(scenario/ft.py, handler_sim.py)
- HandlerSimulator:Tray/Tube/Reel 输入、多 site(如 8)取放、`handler_cycle_time`、卡料 jam 概率、分 bin 到 hard-bin 槽位(sorting)。
- Part 无坐标,使用 part_id 序号;FT 可包含 FT1(常温)/FT2(高温)阶段、burn-in 后重测。
- 最后一个 touchdown 不足 site 数时自动 mask。
- 支持**读取 CP 结果**(可选):CP fail 的 die 不进入 FT(模拟 KGD 流程)。

## 统一控制器(engine/controller.py)
`RunController.run(session)`:
```
for lot: LOT_START
  for unit in (wafers | trays): UNIT_START
     for td in scenario.touchdowns(): run SiteScheduler → PartResults → bin/handler 分选
  UNIT_END
LOT_END → 汇总(yield、bin 分布、UPH)
```
支持:暂停/继续/中止、断点续测(记录已测位置到 `resume.json`)。

## 任务
- T09.1 WaferMap 生成器 + 可视化数据结构。
- T09.2 ProberSimulator + site 布局与边缘 mask。
- T09.3 HandlerSimulator + 分 bin 槽位 + jam。
- T09.4 CP/FT 场景类,多阶段/温度/KGD 流转。
- T09.5 RunController(暂停、中止、续测)。
- T09.6 汇总报表(yield、bin summary、wafer bin map 文本)。

## 验收标准
- 300mm 参考晶圆生成 die 数与独立公式估计偏差 < 2%。
- 2×2 site 的 CP 覆盖全部可测 die 无重复无遗漏。
- 中途中止再续测,最终结果与一次性跑完一致。
- FT 8 site 不满批尾 tray 正确 mask。
