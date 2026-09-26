"""general — 全局求解设置（对应 sas_general.h）。"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class SolveMode(enum.IntEnum):
    STEADY = 0      # 稳态：解一次非线性方程组
    TRANSIENT = 1   # 非稳态：时间推进，每步内解一次


@dataclass
class General:
    solve_mode: SolveMode = SolveMode.STEADY
    dt: float = 1.0e-4            # 时间步长 s
    n_steps: int = 1000
    max_iterations: int = 50      # 非线性迭代上限
    tolerance: float = 1.0e-6     # 节点质量不平衡收敛容差 kg/s
    relaxation: float = 1.0       # 亚松弛因子
    # （气体参数字段已删，2026-09-26：物性归 fluids 家族，JSON 只选
    #   gas.type，数值由气体类构造器赋予——General 只留求解设置）
