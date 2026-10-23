# 01 总体架构

## 分层
```
GUI / CLI            (pyate.gui, pyate.cli)         只调用 API 层
API 层               (pyate.api)                     Session、RunController
执行层               (pyate.engine)                  Flow 执行器、Site 调度、Lot/Wafer 控制
测试层               (pyate.testitems)               TestItem 基类与内置库
资源层               (pyate.hal, pyate.dut, pyate.pattern)  虚拟仪器、DUT 模型、向量引擎
数据层               (pyate.config, pyate.datalog)   配置、结果、STDF
扩展层               (pyate.plugins)                 entry_points 插件注册
```

## 目录结构(必须创建)
```
pyate/
  pyproject.toml
  src/pyate/
    __init__.py
    config/     models.py loader.py validators.py
    hal/        base.py dps.py pmu.py digital.py timing.py relay.py simulated.py
    dut/        base.py models/{inverter,nand,counter,shiftreg,adder,lfsr,sram}.py
    pattern/    parser.py model.py engine.py
    testitems/  base.py registry.py continuity.py leakage.py dc.py functional.py vdd_sweep.py shmoo.py
    engine/     flow.py executor.py binning.py site.py context.py events.py
    scenario/   cp.py ft.py prober_sim.py handler_sim.py wafer_map.py lot.py
    batch/      queue.py runner.py retest.py
    datalog/    result.py stdf/{records.py writer.py reader_check.py} csv_log.py
    api/        session.py controller.py
    gui/        main_window.py panels/*.py
    cli/        main.py
    plugins/    loader.py hookspec.py
  configs/      (示例配置,见 02)
  tests/
  docs/         PROGRESS.md DECISIONS.md
```

## 核心数据对象(engine/context.py,所有模块共用)
- `TestResult`: test_num, test_name, site, value(float|None), unit, lo_limit, hi_limit, passed, fail_code, pin(optional), timestamp
- `SiteContext`: site_id, dut_handle, pin_map(该 site 的物理通道), results[], soft_bin, hard_bin, x_coord, y_coord, part_id
- `RunContext`: lot_id, wafer_id, sublot, test_stage(CP1/CP2/FT1…), program_name, operator, temperature, sites[], config snapshot
- 事件总线 `EventBus`:`LOT_START, WAFER_START, PART_START, TEST_START, TEST_RESULT, PART_END, WAFER_END, LOT_END, LOG, ERROR`;订阅者:datalog、GUI、插件。

## 任务
- T01.1 创建目录与 pyproject,配置 pytest、ruff。
- T01.2 实现 `EventBus`(线程安全,同步/异步订阅)。
- T01.3 实现上述数据对象(dataclass,可序列化为 dict)。
- T01.4 写 `docs/DECISIONS.md` 初稿。

## 验收标准
- `pip install -e .` 成功;`pytest` 跑通 EventBus 与数据对象单测。
- `import pyate.engine` 不触发任何 GUI 依赖。
