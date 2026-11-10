"""Long-running benchmarks, outside the default pytest collection [TS-0].

`testpaths = ["tests"]` keeps these modules out of default runs. Run one
explicitly with `pytest benchmarks/<file> -m ""` or
`bin/pytest-pg benchmarks/<file>`.

This file makes `benchmarks` a regular package: pytest's prepend import mode
then puts the repository root on `sys.path`, so modules here import
`tests.helpers.*`, and function targets such as
`benchmarks.admission_workload:admission_work` resolve in spawned task
processes through the editable install, as `tests.*` targets do.
"""
