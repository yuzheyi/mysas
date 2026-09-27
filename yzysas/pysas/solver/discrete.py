"""discrete — 离散牛顿：差分雅可比替代解析导数（M1 唯一的雅可比来源）。

前向差分（每轮 n+1 次残差评估）:
  J̃[:,j] = (F̃(x̃ + h_j·e_j) − F̃(x̃)) / h_j,   h_j = fd_eps_rel·max(1, |x̃_j|)
  缩放后 x̃ 各分量 O(1) → max(1,|x̃|) 的 1 兜底零分量，步长自愈（计划原文）。
  顺序不能反：未缩放时差分步长对小流量变量是绝对步长，1e-7 kg/s 的扰动
  会被 ~1e-16 的浮点噪声淹没——"先缩放、再差分"正是为此。

精度账：前向差分最优步长 ~√ε_mach ≈ 1.5e-8（截断/舍入平衡），
fd_eps_rel=1e-7 略保守，雅可比相对误差 ~1e-8 量级——远小于牛顿
收敛所需，不破坏收敛速度（差分雅可比自动包含元件内部派生量
（如 Re→f）的链式贡献，这正是 M1 优先差分的理由，见开发日志·想法 5）。

距离-2 染色并行扰动（opts.use_coloring）【悬置，见 待定问题.md】:
  稀疏网络按"共享行的列不相交"分组染色，整组一次扰动同时回填，
  残差评估从 n+1 次降到色数次。需先从装配层索引推导稀疏模式
  （连续性行↔该节点各口流量列；元件行↔本元件流量列+内部压力列）。
  M1 先逐列——正确性优先，等真实规模算例暴露评估次数是瓶颈再做。
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from pysas.datamodel.solver import DiscreteNewtonOptions, NewtonReport
from pysas.solver.newton import damped_newton


def fd_jacobian(f, x: np.ndarray, eps_rel: float = 1.0e-7) -> np.ndarray:
    """前向差分雅可比（在缩放坐标内调用；n 次额外残差评估）。"""
    x = np.asarray(x, dtype=float)
    F0 = f(x)
    J = np.empty((x.size, x.size))
    for j in range(x.size):
        h = eps_rel * max(1.0, abs(x[j]))
        xp = x.copy()
        xp[j] += h
        J[:, j] = (f(xp) - F0) / h
    return J


def discrete_newton(f, x0: np.ndarray,
                    opts: Optional[DiscreteNewtonOptions] = None,
                    on_step: Optional[Callable[[int, float, float], None]] = None,
                    project: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                    ) -> tuple[np.ndarray, NewtonReport]:
    """离散牛顿 = 差分雅可比 + 阻尼牛顿内核。f: 缩放坐标残差。
    project: 可选投影（透传给内核，见 newton.damped_newton）。"""
    if opts is None:
        opts = DiscreteNewtonOptions()
    if opts.use_coloring:
        raise NotImplementedError(
            "距离-2 染色差分未实现（悬置项，见 待定问题.md），用 use_coloring=False")

    def jac(x: np.ndarray) -> np.ndarray:
        return fd_jacobian(f, x, opts.fd_eps_rel)

    return damped_newton(f, jac, x0, opts.newton, on_step=on_step,
                         project=project)
