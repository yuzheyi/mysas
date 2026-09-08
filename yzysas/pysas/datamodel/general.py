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
    gas_constant: float = 287.05  # R，J/(kg·K)，默认空气
    gamma: float = 1.4            # 比热比 γ
