# PyATE 离线 ATE 模拟器 —— 任务拆解总览

## 目标
纯 Python 实现的离线 ATE(Automatic Test Equipment)模拟环境:
配置文件驱动、虚拟仪器/DUT、TestFlow/TestItem 执行、Multi-DUT(多 Site)、CP(晶圆)与 FT(成品)测试、批量执行、STDF V4 输出、GUI + CLI、核心可插件扩展。

## 技术选型(默认,AI 不得随意更换)
| 项 | 选择 |
|---|---|
| Python | 3.10+ |
| 配置 | YAML (PyYAML) + pydantic v2 校验 |
| GUI | PySide6 (Qt) ,GUI 只调用 core 的公开 API |
| 并发 | concurrent.futures / threading(Site 并行),事件用 queue |
| STDF | 自研 STDF V4 writer(struct),pystdf 仅用于测试回读校验 |
| 测试 | pytest |
| 打包 | pyproject.toml,包名 `pyate` |

## 文件索引与执行顺序(依赖关系即顺序)
| 序号 | 文件 | 内容 | 依赖 |
|---|---|---|---|
| 01 | 01_architecture.md | 总体架构、目录、分层、事件总线 | - |
| 02 | 02_config_system.md | 全部配置文件 schema 与加载器 | 01 |
| 03 | 03_virtual_instruments_hal.md | 虚拟 ATE 硬件抽象层 | 01,02 |
| 04 | 04_dut_models.md | 数字电路 DUT 行为模型 | 03 |
| 05 | 05_digital_pattern_engine.md | Pattern 格式与向量执行 | 03,04 |
| 06 | 06_test_items_library.md | 基础 TestItem 库 | 05 |
| 07 | 07_testflow_engine.md | TestFlow/Binning/执行引擎 | 02,06 |
| 08 | 08_multi_dut_site.md | 多 DUT/Site 并行 | 07 |
| 09 | 09_cp_ft_scenarios.md | CP(Prober/Wafer)与 FT(Handler/Lot) | 08 |
| 10 | 10_batch_execution.md | 批量/Lot 队列/重测 | 09 |
| 11 | 11_stdf_output.md | STDF 生成 | 07,09 |
| 12 | 12_gui_cli.md | GUI 与 CLI | 10,11 |
| 13 | 13_extension_plugin.md | 插件扩展机制 | 全部 |
| 14 | 14_testing_acceptance.md | 测试策略与总验收 | 全部 |

## 给 AI 的通用执行规则
1. 严格一次只做一个文件对应的任务,完成后跑该文件末尾的"验收标准"。
2. 所有公开接口必须有类型标注与 docstring;不得在 core 中 import GUI 模块。
3. 新增代码必须带 pytest 用例;未通过不得进入下一个文件。
4. 不得硬编码路径;所有可变参数来自配置文件。
5. 每个任务完成后在 `docs/PROGRESS.md` 追加一行:任务ID / 状态 / 备注。
6. 遇到规格歧义:选最简单且可扩展的方案,并在 `docs/DECISIONS.md` 记录。
