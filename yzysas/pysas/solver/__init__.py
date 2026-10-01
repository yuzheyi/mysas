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

import warnings
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
    # T 区初值（想法 22 完成态：全部节点 T 是未知量）：取源元件
    # T_supply 均值（PB/MASS_SOURCE/booster 供温），无源 = T0_default。
    # 关键：零流量初值点上能量行对 T 的灵敏度只剩 ε 正则（1e-8）
    # → J 在初值点结构性病态（cond~1e16，边界节点 T 物理无约束是
    # 出流方向的真实性质）——但流起来后 mp·T_n 项生效行即良性，自研
    # 牛顿照常收敛（实证 4 步）。给接近物理的 T 初值让初值敏感法能走。
    supplies = [m.T_supply(ctx) for m in system.models.values()
                if len(m.comp.ports) == 1]
    mean_T = (float(np.mean(supplies)) if supplies else ctx.T0_default)
    for nid in system.T_ids:
        x0[system.T_idx_of_node[nid]] = mean_T

    # 容量初值（2026-10-01，软壅塞配套——头脑风暴三路审查定稿）：
    # 零压降元件（软壅塞守卫的对象）在 m=0 起步时压力行对流量全盲
    # （死区内项恒 +0.0）→ 纯零压降链路的 J 结构性奇异（实测
    # cond 1.8e17、iters=0 冻结）。把非锚定元件的端口流量初始化为
    # 元件声速容量的 ±1.1 倍（同一幅值、连续性精确对称，略越 kink
    # 让 FD 看见陡坡斜率 K），方向按锚定压力梯度取（ṁ>0=流入，
    # 高压侧口为正）。正常网络：初值只影响路径不影响根，全量回归
    # 护栏守住逐位不变。
    anchors2 = {}
    for model in system.models.values():
        anchors2.update(model.anchor_P_values())

    def _p_near(nid: int) -> float:
        """节点 nid 的压力初值（锚定值或网络均值）——方向判别用。"""
        return anchors2.get(nid, mean_p)

    for comp in sorted(system.net.comps, key=lambda c: c.comp_id):
        model = system.models[comp.comp_id]
        if model.anchor_P_values():
            continue
        if 1 not in model.row_units:
            continue      # 无压力行（孔板/管/面积件）：特性行自定流量，
                          # 零流量初值有层流线性律保底——容量初值只服务
                          # 软壅塞守卫对象（压力行在死区内对流量盲的件）
        # 元件级容量（同一幅值；多口件按进/出口分组对称分摊，
        # 连续性初值精确为零）：
        # cap 取上风侧节点滞止态（与 _soft_choke_guard 的 upwind 同口径）
        p_ports = [_p_near(p.node_id) for p in comp.ports]
        k_up = int(np.argmax(p_ports))
        p_max, p_min = max(p_ports), min(p_ports)
        if p_max <= p_min:
            continue                      # 无梯度（等压环）：保持 0
        # 初值容量用【全网锚定压力上限】估（初值目的只是让 FD 看见
        # 陡坡斜率，幅值宁大勿小——非锚定节点初值=均值会低估容量，
        # 初值流量超初值容量、陡坡大幅激活、首步不降）。**两侧同幅值
        # （单一 cap）**：守卫阈值按 upwind 高压侧容量判，若低压组用
        # 自身节点容量估（旧 cap_lo 口径），1.1·cap_lo < cap_up 落回
        # 死区内 → 压力行重新变盲 → iters=0 冻结（PB-PB 零压降链
        # 实证，2026-10-02 告警验证抓出）。元件内 Σṁ 初值精确为零。
        p_hi_ref = max(list(anchors2.values()) + [mean_p])
        cap = model.choke_capacity(max(p_hi_ref, 1.0), mean_T, ctx, k_up)
        if cap <= 0.0:
            continue
        hi = [j for j, p in enumerate(p_ports) if p >= 0.5 * (p_max + p_min)]
        n_lo = max(len(comp.ports) - len(hi), 1)
        for j, port in enumerate(comp.ports):
            if port.area <= 0.0:
                continue
            if j in hi:
                x0[system.m_idx_of_port[(comp.comp_id, j)]] = 1.1 * cap / len(hi)
            else:                          # 低压侧组：流出（−）
                x0[system.m_idx_of_port[(comp.comp_id, j)]] = -1.1 * cap / n_lo
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

    # ---------- 投影牛顿：压力可行域下界（2026-09-26） ----------
    # 物理依据（被动网络极大值原理）：无抽出源时，稳态解的全局总压
    # 不低于最小锚定压力——双壅塞陷阱点 p*=p_up·A_min/A_max 恰落在
    # 盒外（面积悬殊时被拉到低于下游锚点），投影把选代点按在可行域
    # 边界滑行：盒边界处 dp=0 → 钳位元件回 Darcy 支 → 死列复活
    # （tmp_diag2 实证：冻结点 J 的 p_mid 列恒零）。保险丝：抽出型
    # MASS_SOURCE（ṁ_spec<0，泵抽真空）可合法把节点拉到盒外——
    # 检测到则摘掉盒子。投影只夹压力段，且不进残差（否则重造死列，
    # 见 newton.py docstring）。
    project = None
    from pysas.datamodel import ElemType   # 保险丝判据用（抽出源检测）
    anchors = {}
    for model in system.models.values():
        anchors.update(model.anchor_P_values())
    p_anchors = [v for v in anchors.values() if v > 0.0]
    has_extract = any(
        c.elem_type == ElemType.MASS_SOURCE and c.params[0] < 0.0
        for c in system.net.comps)
    if p_anchors and not has_extract:
        p_lo = min(p_anchors)
        lo_t = p_lo / scaling.p_ref    # 缩放坐标下界（压力列刻度 p_ref）
        n_p = system.n_interior

        def project(xt: np.ndarray) -> np.ndarray:
            xt[:n_p] = np.maximum(xt[:n_p], lo_t)
            return xt

    x_t, report = discrete_newton(problem.residual, x0_t, opts,
                                  on_step=on_step, project=project)

    x = scaling.to_raw(x_t)
    F_raw = system.residual(x, ctx)

    # ---------- 软壅塞终态告警（2026-10-02 用户裁决） ----------
    # 解上守卫仍激活 = 网络物理上要求某口流量超过声速容量（非物理），
    # 兜底陡坡把它钉在 cap+Δp/K 让计算走得下去——使用者应回头修网络
    # （面积/压差/拓扑）。只在解处查一次且仅收敛时报：residual 里报
    # 会被 FD 雅可比 n+1 次调用刷屏，且容量初值故意 1.1×cap 越 kink、
    # 首残差必然"激活"——那是机制不是病；判定与方程侧共用
    # _comp_overloads（含全部豁免规则），两者永远同条件。
    if report.converged:
        hits = []
        for comp in sorted(system.net.comps, key=lambda c: c.comp_id):
            model = system.models[comp.comp_id]
            for i, cap, m_a in system._comp_overloads(model, comp, x, ctx):
                hits.append((comp.comp_id, i, m_a, cap))
        if hits:
            detail = "; ".join(
                f"c{cid}口{i}: ṁ={m_a:.3g} > cap={cap:.3g} kg/s"
                for cid, i, m_a, cap in hits)
            warnings.warn(
                "软壅塞兜底在解处仍激活（非物理解——某口流量被钉在声速"
                f"容量附近）：{detail}。网络物理容量不足，请检查元件"
                "面积/压差设置（求解器已强行收敛以便调试）",
                RuntimeWarning)
    return SolveResult(
        x=x, x_scaled=x_t, report=report, scaling=scaling,
        max_F_raw=float(np.max(np.abs(F_raw))) if F_raw.size else 0.0)
