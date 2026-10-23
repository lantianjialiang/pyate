# 12 GUI 与 CLI

## CLI(cli/main.py,基于 typer 或 argparse)
```
pyate config validate <session.yaml>
pyate run <session.yaml> [--set k=v] [--sites N] [--only item] [--seed S]
pyate batch run|status|report <batch.yaml>
pyate pattern run <pat> --dut <model>
pyate stdf verify|dump <file>
pyate gui
```
## GUI(PySide6,gui/)—— 只通过 api.Session / RunController / EventBus 交互
| 面板 | 功能 |
|---|---|
| 工程/配置面板 | 打开 session.yaml,树形显示各配置,表单/YAML 编辑,保存前校验并定位错误 |
| TestFlow 编辑器 | 树形显示 flow,拖拽排序、启用/禁用、改 params(由 pydantic schema 动态生成表单)、on_fail 设置 |
| Limits/Bin 表格 | 可编辑表格,导入导出 CSV |
| 运行控制 | Start/Pause/Resume/Stop、单步、断点、选择 stage(CP1/FT1)、选择 site 数/禁用 site |
| 实时 Datalog | 表格实时刷新 test_num/name/site/value/limits/pass-fail,fail 高亮,可按 site 过滤 |
| Wafer Map(CP) | 实时绘制 die 颜色=bin,hover 显示坐标/bin/值,可导出 PNG |
| Handler/Tray 视图(FT) | 显示 site 状态、tray 进度 |
| 统计面板 | 实时 yield、bin pareto、UPH、选定 test 的直方图/趋势图(pyqtgraph 或 matplotlib) |
| Shmoo 视图 | 二维结果色图 |
| Pattern 调试器 | 打开 .pat、选 DUT 执行、波形/失败周期查看 |
| Batch 面板 | 管理 job 列表、进度、汇总、打开输出目录 |
| 日志面板 | LOG/ERROR 事件,级别过滤 |

## 技术要求
- GUI 线程只做渲染;RunController 在工作线程,事件经 Qt signal 转发(限频刷新,避免 UI 卡死:批量合并 50ms)。
- 暗/亮主题可选;所有长操作可取消。
- 提供 `--headless` 测试路径:无显示环境下 GUI 逻辑(ViewModel)可单测。采用 MVVM:`viewmodels/` 无 Qt 依赖。

## 任务
- T12.1 CLI 全部命令。
- T12.2 ViewModel 层(无 Qt)+ 事件节流。
- T12.3 主窗口骨架、配置/Flow/Limits 面板。
- T12.4 运行控制 + 实时 Datalog + 统计。
- T12.5 Wafer Map / Handler 视图 / Shmoo。
- T12.6 Pattern 调试器、Batch 面板。

## 验收标准
- GUI 内完成:打开 demo_cp → 修改一个 limit → 运行 → 实时看到 wafer map → 生成 STDF,全程无需编辑文件。
- 10 万条 TEST_RESULT 事件下 GUI 不卡死(刷新帧率 > 10fps)。
- pytest-qt 冒烟测试通过(offscreen 平台)。
