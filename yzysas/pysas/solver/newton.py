"""newton — 阻尼牛顿内核（三层联合算法的最内层，局部收敛）。

迭代（全程缩放坐标 x̃，见 scaling.py）:
  ① 收敛判据  max|F̃| < tol          无量纲"相对判据"，不再混单位
  ② 线性求解  J̃·Δx̃ = −F̃            稠密 LU（np.linalg.solve，LAPACK 部分主元）
                                     ——"线性矩阵求解随 M1 顺带"的落点，
                                     小规模稠密够用，大规模再上稀疏/Krylov
  ③ 线搜索    α 从 relax_init 起步，max|F̃| 不降则减半回退；
              降到 relax_min 仍不降 → 判失败（牛顿方向不可信）
  ④ 接受 x̃ ← x̃ + α·Δx̃，回到 ①
damped=False 跳过 ③（α≡1 裸牛顿），仅供"阻尼 vs 裸"对比实验/调试。

线搜索准则 = 纯残差下降（计划规定），未用 Armijo 充分下降：
  远离解处牛顿方向本就可能只缓慢下降，纯下降 + α 减半已足够稳；
  Armijo/信任域列入悬置（见 待定问题.md），等真实算例暴露需要再上。
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from pysas.datamodel.solver import NewtonOptions, NewtonReport


def _res_norm(F: np.ndarray) -> float:
    """残差 ∞ 范数；NaN/Inf 视为 +inf（线搜索自动拒绝坏试探点）。"""
    n = float(np.max(np.abs(F))) if F.size else 0.0
    return n if np.isfinite(n) else np.inf


def damped_newton(f, jac, x0: np.ndarray, opts: NewtonOptions,
                  on_step: Optional[Callable[[int, float, float], None]] = None,
                  ) -> tuple[np.ndarray, NewtonReport]:
    """阻尼牛顿主循环。

    f   : x̃ → F̃   缩放坐标残差（纯函数，见 ScaledProblem）
    jac : x̃ → J̃   缩放坐标雅可比（差分由 discrete.py 提供）
    on_step(it, alpha, resid)  每接受一步回调一次（诊断/轨迹打印用）

    返回 (x̃, report)。report.final_residual 是缩放坐标 max|F̃|（判据本身）。
    """
    x = np.asarray(x0, dtype=float).copy()
    report = NewtonReport(final_relax=opts.relax_init)

    F = f(x)
    r = _res_norm(F)
    report.final_residual = r
    if not np.isfinite(r):
        return x, report  # 初值处残差即非有限，直接判失败

    for _ in range(opts.max_iter):
        if r < opts.tol:
            report.converged = True
            return x, report

        # ② 线性求解（奇异/非有限雅可比 → 失败退出）
        try:
            dx = np.linalg.solve(jac(x), -F)
        except np.linalg.LinAlgError:
            return x, report
        if not np.all(np.isfinite(dx)):
            return x, report

        # ③ 线搜索：残差下降准则，α 减半回退（裸牛顿 α≡1 直接接受）
        alpha = opts.relax_init
        F_new, r_new, accept = None, np.inf, False
        while alpha >= opts.relax_min:
            F_new = f(x + alpha * dx)
            r_new = _res_norm(F_new)
            if (not opts.damped) or r_new < r:
                accept = True
                break
            alpha *= 0.5
        if not accept:
            report.final_relax = alpha
            return x, report  # α 触底仍不降 → 牛顿方向不可信

        # ④ 接受
        x = x + alpha * dx
        F, r = F_new, r_new
        report.iters += 1
        report.final_residual = r
        report.final_relax = alpha
        if on_step is not None:
            on_step(report.iters, alpha, r)

    report.converged = r < opts.tol  # 迭代耗尽：最后一步可能恰好达标
    return x, report
