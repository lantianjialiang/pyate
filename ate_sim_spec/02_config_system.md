# 02 配置系统

## 配置文件清单(configs/ 下提供全部示例)
| 文件 | 作用 | 关键字段 |
|---|---|---|
| tester.yaml | ATE 机台配置 | name, model, num_sites_max, digital_channels, dps_channels, pmu_channels, max_vector_rate_mhz, vector_depth |
| channel_map.yaml | 逻辑 pin ↔ 物理通道(按 site) | pin, type(io/in/out/power), site→channel |
| loadboard.yaml | 负载板/Socket/Probecard | board_id, site_count, relay_map |
| levels.yaml | 电平集 | name, vil, vih, vol, voh, vterm, iol, ioh, vdd(nom/min/max) |
| timing.yaml | 时序集 | period_ns, edges(drive_on/data/return/compare strobe)、timing set 名 |
| patterns/*.pat | 向量文件 | 见 05 |
| testprogram.yaml | 测试程序(flow+items 引用) | 见 07 |
| limits.csv / limits.yaml | 限值表(按 test_num/stage/temp) | test_num, name, lo, hi, unit, scale |
| binning.yaml | Soft/Hard Bin 定义 | sbin, hbin, name, pass_fail, 映射规则 |
| dut_profile.yaml | 选用的 DUT 模型与注入缺陷 | model, params, defects(stuck_at/open/short/leakage) |
| wafer.yaml | 晶圆图 | diameter, die_size, grid, flat/notch, 屏蔽 die、中心坐标 |
| lot.yaml | Lot 信息 | lot_id, wafers[], 流向(CP1→CP2→FT) |
| prober.yaml / handler.yaml | 设备仿真参数 | index_time_ms, site 布局、多 site 步进、jam 概率 |
| stdf_options.yaml | STDF 输出 | 输出目录、命名规则、是否记录 PTR/MPR/FTR、cpu_type, 字节序 |
| session.yaml | 一次运行汇总 | 引用上面各文件路径 + 覆盖项 |

## 实现要求
- 用 pydantic v2 定义每个文件对应的 model(`config/models.py`)。
- `loader.py`:`load_session(path) -> SessionConfig`;支持相对路径、`include:`、`${ENV}` 与命令行 `--set a.b.c=1` 覆盖。
- `validators.py` 做跨文件校验:pin 是否在 channel_map 中存在、pattern 引用的 pin 是否已声明、limit 的 test_num 唯一、bin 号唯一、site 数 ≤ tester.num_sites_max。
- 校验失败给出 文件名+行/字段路径 的可读错误,集中返回全部错误而非首个。
- 配置快照:每次运行保存 `run_snapshot.yaml` 到输出目录,便于复现。

## 任务
- T02.1 定义全部 pydantic model。
- T02.2 实现 loader(include/env/override)。
- T02.3 实现跨文件校验器。
- T02.4 在 `configs/` 提供两套完整示例:`demo_cp/`(晶圆 CP, 4 site)与 `demo_ft/`(FT, 8 site),DUT 为 8 位计数器+移位寄存器组合。
- T02.5 `pyate config validate <session.yaml>` CLI 命令。

## 验收标准
- 两套示例均通过校验;故意破坏(缺 pin、bin 重复)时错误信息准确,且一次报全。
- 加载→dump→再加载,对象相等。
