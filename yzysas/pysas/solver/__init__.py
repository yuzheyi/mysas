"""solver — 自研非线性求解器（M1：缩放层 + 阻尼牛顿 + 离散牛顿）。

模块分层（与 datamodel/solver.py 设置类一一对应，同构 C++ sas_solver.h）:
  scaling   缩放层: x̃=S⁻¹x, F̃=R·F → 无量纲 O(1) 方程组（先缩放，后一切）
  newton    阻尼牛顿内核: 线搜索 α 回退 + 稠密 LU（最内层）
  discrete  离散牛顿: 差分雅可比（当前唯一雅可比来源；距离-2 染色悬置）
  同伦延拓（M2）届时作为最外层包住 discrete_newton。

用法:
  net, ctx = load_netinf(...)
  models = build_models(net)
  sys_ = NetworkSystem(net, models)
  res = solve(sys_, x0, ctx)
  res.x  res.report.converged  res.report.final_residual
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pysas.datamodel.solver import (
    DiscreteNewtonOptions, NewtonReport, SolverSettings,
)
from pysas.solver.discrete import discrete_newton, fd_jacobian
from pysas.solver.newton import damped_newton
from pysas.solver.scaling import Scaling, ScaledProblem, make_scaling

__all__ = [
    "solve", "SolveResult", "default_guess",
    "Scaling", "ScaledProblem", "make_scaling",
    "damped_newton", "discrete_newton", "fd_jacobian",
]


@dataclass
class SolveResult:
    """solve() 返回：解 + 报告 + 缩放层（诊断/复算用）。"""

    x: np.ndarray           # 原始坐标解 [p0 内部节点 | ṁ 各端口]
    x_scaled: np.ndarray    # 缩放坐标解（量级 O(1)，sanity check 用）
    report: NewtonReport    # 迭代报告（判据在缩放坐标上）
    scaling: Scaling        # 本轮缩放层
    max_F_raw: float        # 原始量纲 max|F|（物理单位核对用）

    @property
    def converged(self) -> bool:
        return self.report.converged


def default_guess(system, ctx) -> np.ndarray:
    """缺省初值：锚定节点 p0/T0 = 自报锚定值，其余取均值（压力无锚 →
    1e5 Pa，温度无锚 → T0_default），各口 ṁ = 0。

    锚定值由元件自报（anchor_P_values/anchor_T_values，见 base.py）——
    新元件实现接口即可，此处零改动。为什么边界节点要钉真值：
    若也取均值，孔板起点恰在 β=1、管在 Δp=0 的导数奇异点，牛顿方向
    失真（C 算例迭代 0 步即死的实证）。
    """
    x0 = np.zeros(system.n)
    specs = {}
    for model in system.models.values():
        specs.update(model.anchor_P_values())
    p0s = [v for v in specs.values() if v > 0.0]
    mean_p = float(np.mean(p0s)) if p0s else 1.0e5
    for i, nid in enumerate(system.interior_ids):
        x0[i] = specs.get(nid, mean_p)
    # T 区：未锚温节点取锚温均值（无锚 → T0_default）
    T_specs = {}
    for model in system.models.values():
        T_specs.update(model.anchor_T_values())
    mean_T = (float(np.mean(list(T_specs.values())))
              if T_specs else ctx.T0_default)
    for nid in system.T_ids:
        x0[system.T_idx_of_node[nid]] = mean_T
    return x0


def solve(system, x0, ctx, settings: SolverSettings | None = None, *,
          p_ref: float | None = None, m_ref: float | None = None,
          on_step=None) -> SolveResult:
    """统一入口：NetworkSystem + 初值（原始坐标）→ SolveResult。

    settings=None 用离散牛顿默认设置；mode=0 纯牛顿需要解析雅可比（悬置）、
    mode=2 同伦延拓是 M2 内容——两者均未实现，显式报错。
    p_ref/m_ref 可手动覆盖缩放参考量（默认 make_scaling 自估）。
    on_step(it, alpha, resid) 每接受一步回调一次（轨迹诊断用）。
    """
    mode = settings.mode if settings is not None else 1
    if mode == 0:
        raise NotImplementedError(
            "纯牛顿（mode=0）需要解析雅可比，尚未实现（悬置项，见 待定问题.md）；"
            "现用 mode=1 离散牛顿")
    if mode == 2:
        raise NotImplementedError("同伦延拓（mode=2）是 M2 内容")

    opts = settings.discrete if settings is not None else DiscreteNewtonOptions()

    scaling = make_scaling(system, ctx, p_ref=p_ref, m_ref=m_ref)
    problem = ScaledProblem(system, ctx, scaling)

    if x0 is None:
        x0 = default_guess(system, ctx)
    x0_t = scaling.to_scaled(np.asarray(x0, dtype=float))

    x_t, report = discrete_newton(problem.residual, x0_t, opts, on_step=on_step)

    x = scaling.to_raw(x_t)
    F_raw = system.residual(x, ctx)
    return SolveResult(
        x=x, x_scaled=x_t, report=report, scaling=scaling,
        max_F_raw=float(np.max(np.abs(F_raw))) if F_raw.size else 0.0)
