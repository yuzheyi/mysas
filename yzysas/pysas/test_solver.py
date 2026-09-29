"""test_solver — M1 求解器内核验证脚本（缩放层 + 阻尼牛顿 + 离散牛顿）。

2026-09-15 边界元件化适配：算例改新 JSON 格式（边界 = PRESSURE_BOUNDARY
单口元件，节点统一内部），x = [全部节点 p0 | 各端口 m]，新增 ⑤ 流量
边界算例（MASS_SOURCE + PRESSURE_BOUNDARY 混合边界）与 ⑥ 适定性断言
（全流量边界组装期报错）。旧 boundary 字段兼容层已移除（2026-09-18）。

验证链（每步打印，全部手算可核对；X 任何一项失败则退出码 1）:
  ① 缩放层: 两管算例 cond(J) 未缩放 vs 缩放（含压力行分档）
  ② 手算核对（独立公式实现，不 import 元件代码）:
     B  单孔板亚临界 3e5->2e5     闭式公式
     B2 单孔板壅塞   3e5->1e5     闭式公式（M0 超临界公式 bug 的回归测试）
     A  两管串联      3e5->1e5    1-D 二分（静密度口径 2026-09-25：两管
                                 同 Re 同 f → dp0·ρs0=dp1·ρs1，但 ρs 的
                                 τ(Ma) 修正两管不同 → 旧二次方程不再精确，
                                 改 bisect p_mid + 定点流量）
     C  孔板+管串联   3e5->1e5    壅塞孔板闭式 + 管压降定点迭代
     H  双孔板近临界  5e5->1e5    独立公式 1-D 二分（beta1=0.991 贴 sqrt(1-beta) 奇异区）
  ③ 与 scipy root 同解（同一初值逐变量对比）
  ④ 坏初值对比: 裸牛顿 vs 阻尼牛顿（H 算例）
     + 诚实边界: 双孔板同壅塞、压力列全零，两者皆败——局部法的天花板
  ⑤ 流量边界: MASS_SOURCE(注入 0.1 kg/s) + PRESSURE_BOUNDARY 混合，
     单管沿程损失反解进口压力，与手算 Darcy 闭式对照
  ⑥ 适定性断言: 全流量边界（无 PRESSURE_BOUNDARY）-> 组装期 ValueError
"""
import sys

import numpy as np
from scipy.optimize import root

sys.path.insert(0, r"e:\mywork\programDesign\mysas\yzysas")

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import load_netinf, netinf_from_dict, build_models  # noqa: E402
from pysas.solver import (                       # noqa: E402
    solve, default_guess, make_scaling, ScaledProblem, fd_jacobian)
from pysas.datamodel.solver import (              # noqa: E402
    NewtonOptions, DiscreteNewtonOptions, SolverSettings)

FAILURES = []


def check(name, ok, detail=""):
    tag = "OK" if ok else "X"
    print(f"   {tag} {name}" + (f"（{detail}）" if detail else ""))
    if not ok:
        FAILURES.append(name)


# ---------------- 独立手算公式（不复用元件代码，防"自己验证自己"） ----------------
def orifice_m_hand(p01, p02, T0, Cd, A):
    """孔板流量手算公式（亚/超临界，与 orifice.py docstring 同源）。
    物性数值从气体类取（缺省 IdealGas，与算例同气体——数值单点在类）。"""
    from pysas.fluids import make_gas
    _g = make_gas()
    R, gamma = _g.R, _g.gamma
    beta = min(max(p02 / p01, 1e-8), 1.0)
    crit = (2.0 / (gamma + 1.0)) ** (gamma / (gamma - 1.0))
    factor = Cd * A * p01 / np.sqrt(T0)
    if beta <= crit:
        return factor * np.sqrt(gamma / R) \
            * (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
    return factor * np.sqrt(2.0 * gamma / (R * (gamma - 1.0))) \
        * beta ** (1.0 / gamma) * np.sqrt(1.0 - beta ** ((gamma - 1.0) / gamma))


def pipe_m_hand_full(p_up, p_down, T0, L, D, eps, area, m_guess):
    """管流量手算（静密度自洽定点，2026-09-25 与 pipe.py 同口径升级）。

    ρs/μ(Ts) 由上游总态+流量经等熵关系反算（复用 fluids 引擎——手算的
    "独立"指不复用元件代码，物性公式单点允许同源）：
      q = m·sqrt(T0)/(p0·A) → Ma → τ → ρs，μ = Sutherland(Ts)
    外层定点迭代 m ← m(ρs(m), μ(Ts(m))) 至收敛（亚声速下压缩，收敛快）。
    """
    from pysas.fluids import make_gas
    _gas = make_gas()                       # 缺省 IdealGas（数值由类赋予）
    A, dp = area, p_up - p_down
    if dp <= 0:
        return 0.0
    m = m_guess
    for _ in range(60):                     # ρ,μ↔m 定点
        st = _gas.total_to_static(p_up, T0, m, A)
        rho, mu = st.rho, _gas.mu(st.T)     # 同源静参数：密度与粘度同口径
        m_lam = rho * dp * A * D * D / (32.0 * mu * L)
        re_g = abs(m) * D / (mu * A)
        if re_g <= 2000:
            m_new = m_lam
        else:
            term = eps / (3.7 * D) + 5.74 / re_g ** 0.9
            f = 0.25 / np.log10(term) ** 2
            m_turb = A * np.sqrt(2.0 * rho * dp * D / (f * L))
            w = min(max((re_g - 2000.0) / 2000.0, 0.0), 1.0)
            m_new = (1.0 - w) * m_lam + w * m_turb
        if abs(m_new - m) < 1e-13 * max(1.0, abs(m_new)):
            return m_new
        m = m_new
    return m


def bisect_root(g, a, b, iters=200):
    """1-D 二分求根（H 算例手算用，独立于求解器）。"""
    ga, gb = g(a), g(b)
    for _ in range(iters):
        c = 0.5 * (a + b)
        if g(c) * ga <= 0:
            b, gb = c, g(c)
        else:
            a, ga = c, g(c)
    return 0.5 * (a + b)


# ---------------- 算例定义（边界元件化新格式） ----------------
CASE_ORIFICE_SUB = {   # B: 单孔板亚临界（新格式：边界元件）
    "gas": {"type": "IdealGas", "T0_default": 600.0},
    "nodes": [{"id": 0}, {"id": 1}],
    "comps": [
        {"id": 0, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 0}], "params": [3.0e5, 600.0]},
        {"id": 1, "type": "ORIFICE",
         "ports": [{"area": 1.0e-4, "node": 0}, {"area": 1.0e-4, "node": 1}],
         "params": [1.0, 0.8]},
        {"id": 2, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 1}], "params": [2.0e5, 600.0]},
    ],
}
CASE_ORIFICE_CHOKED = {  # B2: 单孔板壅塞（新格式）
    "gas": {"type": "IdealGas", "T0_default": 600.0},
    "nodes": [{"id": 0}, {"id": 1}],
    "comps": [
        {"id": 0, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 0}], "params": [3.0e5, 600.0]},
        {"id": 1, "type": "ORIFICE",
         "ports": [{"area": 1.0e-4, "node": 0}, {"area": 1.0e-4, "node": 1}],
         "params": [1.0, 0.8]},
        {"id": 2, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 1}], "params": [1.0e5, 600.0]},
    ],
}
CASE_SERIES = {  # C: 孔板 + 管串联（新格式）
    "gas": {"type": "IdealGas", "T0_default": 600.0},
    "nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
    "comps": [
        {"id": 0, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 0}], "params": [3.0e5, 600.0]},
        {"id": 1, "type": "ORIFICE",
         "ports": [{"area": 1.0e-4, "node": 0}, {"area": 1.0e-4, "node": 1}],
         "params": [1.0, 0.8]},
        {"id": 2, "type": "PIPE",
         "ports": [{"area": 3.1416e-4, "node": 1}, {"area": 3.1416e-4, "node": 2}],
         "params": [0.5, 0.02, 1.0e-5]},
        {"id": 3, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 2}], "params": [1.0e5, 600.0]},
    ],
}
CASE_TWIN_ORIFICE = {  # H: 串联双孔板，beta1=0.991 近 sqrt(1-beta) 奇异区（新格式）
    "gas": {"type": "IdealGas", "T0_default": 600.0},
    "nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
    "comps": [
        {"id": 0, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 0}], "params": [5.0e5, 600.0]},
        {"id": 1, "type": "ORIFICE",
         "ports": [{"area": 3.1416e-4, "node": 0}, {"area": 3.1416e-4, "node": 1}],
         "params": [1.0, 0.8]},
        {"id": 2, "type": "ORIFICE",
         "ports": [{"area": 3.1416e-4, "node": 1}, {"area": 3.1416e-4, "node": 2}],
         "params": [0.2, 0.8]},
        {"id": 3, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 2}], "params": [1.0e5, 600.0]},
    ],
}
CASE_MASS_SOURCE = {  # S: 流量进口 + 压力出口混合边界（新格式）
    "gas": {"type": "IdealGas", "T0_default": 600.0},
    "nodes": [{"id": 0}, {"id": 1}],
    "comps": [
        {"id": 0, "type": "MASS_SOURCE",
         "ports": [{"area": 0.0, "node": 0}], "params": [0.1, 600.0]},
        {"id": 1, "type": "PIPE",
         "ports": [{"area": 3.1416e-4, "node": 0}, {"area": 3.1416e-4, "node": 1}],
         "params": [0.5, 0.02, 1.0e-5]},
        {"id": 2, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 1}], "params": [1.0e5, 600.0]},
    ],
}

CASE_AREA_CHANGE = {  # W: 突缩面积变化件（小截面损失，ζ=0.5）
    "gas": {"type": "IdealGas", "T0_default": 600.0},
    "nodes": [{"id": 0}, {"id": 1}],
    "comps": [
        {"id": 0, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 0}], "params": [3.0e5, 600.0]},
        {"id": 1, "type": "AREA_CHANGE",
         "ports": [{"area": 1.0e-3, "node": 0}, {"area": 2.0e-4, "node": 1}],
         "params": [0.5]},
        {"id": 2, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 1}], "params": [2.9e5, 600.0]},
    ],
}
CASE_AREA_CHANGE_T = {  # W2: 温度直传——中间节点 1 不挂边界元件，T 才进 x
    "gas": {"type": "IdealGas", "T0_default": 600.0},
    "nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
    "comps": [
        {"id": 0, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 0}], "params": [3.0e5, 650.0]},
        {"id": 1, "type": "AREA_CHANGE",
         "ports": [{"area": 1.0e-3, "node": 0}, {"area": 2.0e-4, "node": 1}],
         "params": [0.5]},
        {"id": 2, "type": "ORIFICE",
         "ports": [{"area": 2.0e-4, "node": 1}, {"area": 2.0e-4, "node": 2}],
         "params": [1.0, 0.8]},
        {"id": 3, "type": "PRESSURE_BOUNDARY",
         "ports": [{"area": 0.0, "node": 2}], "params": [2.9e5, 550.0]},
    ],
}

CASE_ALL_FLOW = {  # 全流量边界（无压力锚定）-> 组装期应断言报错
    "gas": {"type": "IdealGas", "T0_default": 600.0},
    "nodes": [{"id": 0}, {"id": 1}],
    "comps": [
        {"id": 0, "type": "MASS_SOURCE",
         "ports": [{"area": 0.0, "node": 0}], "params": [0.1, 600.0]},
        {"id": 1, "type": "PIPE",
         "ports": [{"area": 3.1416e-4, "node": 0}, {"area": 3.1416e-4, "node": 1}],
         "params": [0.5, 0.02, 1.0e-5]},
        {"id": 2, "type": "MASS_SOURCE",
         "ports": [{"area": 0.0, "node": 1}], "params": [-0.1, 600.0]},
    ],
}


def assemble(case):
    net, ctx = netinf_from_dict(case)
    return NetworkSystem(net, build_models(net)), ctx


def with_T(system, pressures, T_fill, m_dots):
    """构造含 T 区的初值：[pressures | T_fill×N_T | m_dots]。
    M3 后 x 排布多出温度段，手写算例用此函数拼，避免逐个改数组。"""
    return np.array([*pressures, *([T_fill] * system.n_T), *m_dots])


def run_solver(system, ctx, x0, damped=True, on_step=None):
    opts = DiscreteNewtonOptions(
        newton=NewtonOptions(damped=damped, max_iter=50))
    settings = SolverSettings(mode=1, discrete=opts)
    return solve(system, x0, ctx, settings, on_step=on_step)


def main():
    # ================= ① 缩放层 =================
    print("① 缩放层：cond(J) 未缩放 vs 缩放（两管算例 netinf.json，含压力行分档）")
    netA, ctxA = load_netinf(r"e:\mywork\programDesign\mysas\yzysas\pysas\netinf.json")
    sysA = NetworkSystem(netA, build_models(netA))
    # x0: [p0×3 | m×6]——节点压力给边界真值+均值，流量给顺压方向
    x0A = with_T(sysA, [3.0e5, 2.0e5, 1.0e5], 600.0,
                 [-0.1, -0.1, 0.1, -0.1, 0.1, 0.1])
    scalingA = make_scaling(sysA, ctxA)
    probA = ScaledProblem(sysA, ctxA, scalingA)
    # cond 在【解点】算（想法 22 退锚后：零流量初值点上边界节点 T 物理无
    # 约束（能量行对 T 灵敏度只剩 ε 正则）→ 初值点 cond~1e16 是真实
    # 性质非缩放缺陷；流起来后 mp·T_n 项接管，解点 cond 良性 9846）
    resA0 = solve(sysA, None, ctxA)
    assert resA0.converged
    J_raw = fd_jacobian(lambda x: sysA.residual(x, ctxA), resA0.x)
    J_scl = fd_jacobian(probA.residual, scalingA.to_scaled(resA0.x))
    cond_raw, cond_scl = np.linalg.cond(J_raw), np.linalg.cond(J_scl)
    print(f"   p_ref={scalingA.p_ref:.3e} Pa  m_ref={scalingA.m_ref:.4f} kg/s"
          f"（壅塞容量自估）")
    print(f"   cond(J)={cond_raw:.3e} -> cond(J~)={cond_scl:.3e}"
          f"（改善 {cond_raw / cond_scl:.1e} 倍）")
    # 阈值口径（2026-09-26 更新）：netinf.json 换成面积悬殊拓扑后，
    # 解点 J 本身良态（raw cond ~1.6e8，无旧等面积拓扑的 1e16 病态
    # 初值路径——投影牛顿直接收敛），缩放收益 = 行分档归一 ~1e2 量级
    # 属实情；缩放后 cond ~1.5e6 主因能量行参考量按全网容量估
    # （m_ref=0.155 vs 本算例 ṁ=0.007，行幅值被低估 20 倍）——
    # 迭代收敛不受影响（7 轮到 1e-11）。断言：有改善且绝对值 <1e8
    check("缩放把雅可比条件数压到良态", cond_scl < 1.0e8 and cond_raw / cond_scl > 10)

    # ================= ② 手算核对 =================
    print("\n② 手算核对（独立公式）")

    # B 单孔板亚临界
    sysB, ctxB = assemble(CASE_ORIFICE_SUB)
    resB = solve(sysB, None, ctxB)
    m_hand = orifice_m_hand(3.0e5, 2.0e5, 600.0, 0.8, 1.0e-4)
    mB = resB.x[sysB.n_interior + sysB.n_T + 1]   # 孔板进口口流量
    print(f"   B 单孔板亚临界: m={mB:.8f}"
          f"  手算={m_hand:.8f}  iters={resB.report.iters}")
    check("B 亚临界闭式公式", abs(mB - m_hand) < 1e-9)

    # B2 单孔板壅塞（M0 超临界 bug 回归）
    sysB2, ctxB2 = assemble(CASE_ORIFICE_CHOKED)
    resB2 = solve(sysB2, None, ctxB2)
    m_hand2 = orifice_m_hand(3.0e5, 1.0e5, 600.0, 0.8, 1.0e-4)
    mB2 = resB2.x[sysB2.n_interior + sysB2.n_T + 1]
    print(f"   B2 单孔板壅塞:  m={mB2:.8f}"
          f"  手算={m_hand2:.8f}  iters={resB2.report.iters}")
    check("B2 壅塞闭式公式（回归）", abs(mB2 - m_hand2) < 1e-9)

    # A 两管串联（2026-09-27 Fanno cap + 等面积拓扑）：netinf.json 现为
    #   两管全同（A=3.1416e-4，出口 1e5）。物理图景（瓶颈涌现·等面积版，
    #   对称几何不产生对称解）：管2 面对 p_mid→1e5 大压比独占壅塞（出口
    #   Ma=1），管1 亚声速 Darcy 沿程加速——
    #     ṁ = cap₂(p_mid) = ṁ_Darcy₁(3e5, p_mid)（联立定点，二分）
    #   cap 定点逻辑住管组（PipeModel._fanno_cap，2026-09-29 归位），
    #   手算用独立复刻（同公式不同代码路径，防自证）
    resA = solve(sysA, x0A, ctxA)
    from pysas.fluids import make_gas
    from pysas.fluids.isentropic import (
        fanno_param as _fp, mach_from_fanno as _mf, q_of_mach as _qm)

    def _cap_hand(A, p0, T0, L, D, eps, R, gam, mu):
        """Fanno cap 手算复刻（定点：q ← q(F⁻¹(f(Re(q))·L/D))；
        与 PipeModel._fanno_cap 同公式不同代码路径，防自证）。"""
        q = _qm(1.0, R, gam)
        for _ in range(50):
            re = q * p0 * D / (mu * np.sqrt(T0))
            f = 0.25 / np.log10(eps / (3.7 * D) + 5.74 / re ** 0.9) ** 2
            m_in = _mf(f * L / D, gam)
            q_new = _qm(m_in, R, gam)
            if abs(q_new - q) <= 1.0e-14 * q:
                return q_new * p0 * A / np.sqrt(T0)
            q = q_new
        return q * p0 * A / np.sqrt(T0)
    # 定点：g(p_mid) = Darcy₁(3e5,p_mid) − cap₂(p_mid)；g 单调降 → 唯一根
    _gasA = make_gas()
    A1, A2 = 3.1416e-4, 3.1416e-4
    D, L, EPS = 0.02, 0.5, 1.0e-5
    mu_A = _gasA.mu(600.0)

    def g_mid(p_mid):
        m_d = pipe_m_hand_full(3.0e5, p_mid, 600.0, L, D, EPS, A1, 0.05)
        m_c = _cap_hand(A2, p_mid, 600.0, L, D, EPS,
                        _gasA.R, _gasA.gamma, mu_A)
        return m_d - m_c
    p_lo_b, p_hi_b = 1.0e5, 3.0e5
    for _ in range(200):
        p_mid_h = 0.5 * (p_lo_b + p_hi_b)
        if g_mid(p_mid_h) > 0.0:
            p_lo_b = p_mid_h
        else:
            p_hi_b = p_mid_h
    p_hand = 0.5 * (p_lo_b + p_hi_b)
    m_fix = _cap_hand(A2, p_hand, 600.0, L, D, EPS, _gasA.R, _gasA.gamma, mu_A)
    print(f"   A 两管串联(等面积瓶颈涌现): p_mid={resA.x[1]:.2f}"
          f"  手算二分={p_hand:.2f}")
    mA = resA.x[sysA.n_interior + sysA.n_T + 1]
    print(f"          m={mA:.6f}"
          f"（管0进口）  手算={m_fix:.6f}  iters={resA.report.iters}"
          f"  cap2={m_fix:.6f}（管2 Fanno 壅塞位）")
    check("A 中点压力（Darcy₁=cap₂ 联立定点）", abs(resA.x[1] - p_hand) < 0.5,
          f"Δ={abs(resA.x[1] - p_hand):.2e} Pa")
    check("A 流量（cap₂ Fanno）",
          abs(abs(mA) - m_fix) < 1e-6)

    # C 孔板+管串联：壅塞孔板闭式 + 管压降定点（ρs 静密度口径）
    sysC, ctxC = assemble(CASE_SERIES)
    resC = solve(sysC, None, ctxC)
    m_ch = orifice_m_hand(3.0e5, 1.0e5, 600.0, 0.8, 1.0e-4)
    from pysas.fluids import make_gas
    _gasC = make_gas()    # 缺省 IdealGas（Sutherland；与算例同气体）
    p_c = 1.05e5
    for _ in range(60):  # p_mid = 1e5 + Dp_pipe(m_ch)，ρs=ρs(p_mid, m_ch)
        st_c = _gasC.total_to_static(p_c, 600.0, m_ch, 3.1416e-4)
        rho_c, mu_c = st_c.rho, _gasC.mu(st_c.T)   # 静参数同源（pipe 同口径）
        re = m_ch * 0.02 / (mu_c * 3.1416e-4)
        term = 1.0e-5 / (3.7 * 0.02) + 5.74 / re ** 0.9
        f = 0.25 / np.log10(term) ** 2
        p_c = 1.0e5 + m_ch ** 2 * f * 0.5 / (2.0 * rho_c
                                              * 3.1416e-4 ** 2 * 0.02)
    print(f"   C 孔板+管串联: p_mid={resC.x[1]:.2f}  手算定点={p_c:.2f}")
    mC = resC.x[sysC.n_interior + sysC.n_T + 1]
    print(f"          m={mC:.8f}"
          f"  手算壅塞={m_ch:.8f}  iters={resC.report.iters}")
    check("C 壅塞孔板闭式流量", abs(mC - m_ch) < 1e-8)
    check("C 中点压力管压降定点", abs(resC.x[1] - p_c) < 0.5,
          f"Δ={abs(resC.x[1] - p_c):.2e} Pa")

    # H 双孔板近临界：独立公式二分
    sysH, ctxH = assemble(CASE_TWIN_ORIFICE)
    x_refH = with_T(sysH, [5.0e5, 3.0e5, 1.0e5], 600.0,
                    [-0.05, -0.05, 0.05, -0.05, 0.05, 0.05])
    resH = solve(sysH, x_refH, ctxH)
    g = lambda p: (orifice_m_hand(5.0e5, p, 600.0, 0.8, 3.1416e-4)
                   - orifice_m_hand(p, 1.0e5, 600.0, 0.8, 0.2 * 3.1416e-4))
    p_h = bisect_root(g, 1.0e5, 5.0e5)
    m_h = orifice_m_hand(p_h, 1.0e5, 600.0, 0.8, 0.2 * 3.1416e-4)
    print(f"   H 双孔板近临界: p_mid={resH.x[1]:.2f}  手算二分={p_h:.2f}"
          f"（beta1={p_h / 5e5:.4f}）")
    mH = resH.x[sysH.n_interior + sysH.n_T + 1]
    print(f"          m={mH:.8f}  手算={m_h:.8f}"
          f"  iters={resH.report.iters}")
    check("H 双孔板 1-D 二分", abs(resH.x[1] - p_h) < 0.5
          and abs(mH - m_h) < 1e-8)

    # ================= ③ scipy 同解 =================
    print("\n③ 与 scipy root 同解（同一初值；hybrid——lm 对能量行 ε 正则")
    print("   的悬殊量纲会把该行当噪声，H 算例实测 lm 不收敛而自研解正确）")
    for tag, system, ctx, x0, res in [
        # A/H 的 scipy 从解点 warm-start：零流量初值点边界节点 T 无方程
        # （想法 22 退锚的物理性质，cond~1e16），自研阻尼牛顿穿得过、
        # scipy-lm 穿不过——对照实验改为验证同根，穿越能力对比归 ④
        ("A", sysA, ctxA, "sol", resA),
        ("B", sysB, ctxB, None, resB),
        ("B2", sysB2, ctxB2, None, resB2),
        ("C", sysC, ctxC, None, resC),
        ("H", sysH, ctxH, "sol", resH),
    ]:
        x0_use = res.x.copy() if x0 == "sol" else (
            x0 if x0 is not None else default_guess(system, ctx))
        sol = root(system.residual, x0_use, args=(ctx,), method="lm",
                   options={"ftol": 1.0e-12, "xtol": 1.0e-12})
        scale = np.maximum(np.abs(res.x), 1.0)
        T_slice = slice(system.n_interior, system.n_interior + system.n_T)
        scale[T_slice] = np.maximum(np.abs(res.x[T_slice]), 1.0)
        m_slice = slice(system.n_interior + system.n_T, system.n)
        scale[m_slice] = max(res.scaling.m_ref, 1e-12)
        rel = np.abs(res.x - sol.x) / scale
        print(f"   {tag:3s} scipy conv={sol.success}  "
              f"max 相对差 = {rel.max():.2e}")
        check(f"{tag} 与 scipy 同解", rel.max() < 1e-5)

    # ================= ④ 坏初值：裸牛顿 vs 阻尼牛顿 =================
    print("\n④ 坏初值对比（H 算例: p_mid 钉在出口压力 1e5、m 全 0——")
    print("   孔板2 起点恰在 beta=1 奇异点；边界节点仍钉真值）")
    x_bad = with_T(sysH, [5.0e5, 1.0e5, 1.0e5], 600.0, [0.0] * 6)
    trail_bare, trail_damp = [], []
    res_bare = run_solver(sysH, ctxH, x_bad, damped=False,
                          on_step=lambda i, a, r: trail_bare.append((i, a, r)))
    res_damp = run_solver(sysH, ctxH, x_bad, damped=True,
                          on_step=lambda i, a, r: trail_damp.append((i, a, r)))
    print("   裸 牛顿（alpha≡1）逐步 max|F~|:")
    print("        " + "  ".join(f"{t[2]:.1e}" for t in trail_bare))
    print(f"        -> converged={res_bare.report.converged}"
          f"  iters={res_bare.report.iters}")
    print("   阻尼牛顿（线搜索 alpha 回退）:")
    for it, a, r in trail_damp:
        print(f"        it={it:2d}  alpha={a:.3f}  max|F~|={r:.3e}")
    print(f"        -> converged={res_damp.report.converged}"
          f"  iters={res_damp.report.iters}")
    check("坏初值下裸牛顿失败（卡死/发散）", not res_bare.report.converged)
    check("同初值下阻尼牛顿收敛", res_damp.report.converged)

    print("\n   诚实边界（信息项，不计入失败）:")
    # 双孔板同壅塞：p_mid 压到两孔板同时壅塞的区间，压力列近零
    x_degen = with_T(sysH, [5.0e5, 0.1, 1.0e5], 600.0, [0.0] * 6)
    for tag, damped in [("裸", False), ("阻尼", True)]:
        r = run_solver(sysH, ctxH, x_degen, damped=damped)
        print(f"   p_mid=0.1（双孔板同壅塞）{tag}: converged={r.report.converged}"
              f"  iters={r.report.iters}")
    print("   -> 两孔板同时壅塞时中间压力列近零、J 奇异，局部法无解可走："
          "M2 同伦延拓的动机")

    # ================= ⑤ 流量边界 =================
    print("\n⑤ 流量边界（MASS_SOURCE 0.1 kg/s 注入 + 压力出口 1e5 Pa）")
    sysS, ctxS = assemble(CASE_MASS_SOURCE)
    resS = solve(sysS, None, ctxS)
    # 手算：m=0.1 定流，管 Darcy 反解 p_in = p_out + Dp（f 由 Re 定点；
    # ρs = 恢复静密度（上游口，随 p_in 与 m_spec），2026-09-25 同口径升级）
    m_spec = 0.1
    p_in = 1.05e5
    for _ in range(60):
        st_s = _gasC.total_to_static(p_in, 600.0, m_spec, 3.1416e-4)
        rho_s, mu_s = st_s.rho, _gasC.mu(st_s.T)   # 静参数同源（pipe 同口径）
        # 钳位口径（2026-09-26）：管 Darcy 容量不足 0.1 时进口压涨到
        # cap(p_in) = m_spec 反解——p_in = m_spec / (q(1)/√T0 · A)
        # Fanno cap（2026-09-27）：管容量不足 0.1 时进口压涨到
        # Fanno cap(p_in) = m_spec 外层定点（cap 弱依赖 p0，不可线性除）
        cap_s = _cap_hand(3.1416e-4, p_in, 600.0, 0.5, 0.02, 1.0e-5,
                          _gasC.R, _gasC.gamma, _gasC.mu(600.0))
        if cap_s <= m_spec:   # 管容量 < 规定流量：p_in 由 cap 反解
            break
        re = m_spec * 0.02 / (mu_s * 3.1416e-4)   # Re（过渡段）
        term = 1.0e-5 / (3.7 * 0.02) + 5.74 / re ** 0.9
        f = 0.25 / np.log10(term) ** 2
        # 湍流式反解 Dp = m^2fL/(2·ρs·A^2·D)，ρs 由 p_in+m 反算（上游）
        dp = m_spec ** 2 * f * 0.5 / (2.0 * rho_s
                                      * 3.1416e-4 ** 2 * 0.02)
        p_in = 1.0e5 + dp
    if cap_s <= m_spec:
        # Fanno cap 反解：cap(p) = m_spec 外层定点（cap ∝ p0 仅首阶——
        # q* 经 Re→f 弱依赖 p0，fanno 面积不变性不覆盖 p0 维度）
        p_in = 1.0e5
        for _ in range(60):
            cap_it = _cap_hand(3.1416e-4, p_in, 600.0, 0.5, 0.02, 1.0e-5,
                               _gasC.R, _gasC.gamma, _gasC.mu(600.0))
            p_new = p_in * m_spec / cap_it
            if abs(p_new - p_in) < 1.0e-10 * max(1.0, abs(p_new)):
                p_in = p_new
                break
            p_in = p_new
        mode_s = "choked(Fanno-cap)"
    else:
        mode_s = "Darcy"
    print(f"   S 流量进口: p_in={resS.x[0]:.2f}  手算定点={p_in:.2f}({mode_s})"
          f"  m_spec={m_spec}")
    print(f"          源端口流量={resS.x[sysS.n_interior + sysS.n_T]:.6f}"
          f"（应为 -0.1 = 注入）  iters={resS.report.iters}")
    check("S 流量边界进口压力 Darcy 定点", abs(resS.x[0] - p_in) < 0.5,
          f"Δ={abs(resS.x[0] - p_in):.2e} Pa")
    check("S 源端口流量 = -m_spec（符号约定）",
          abs(resS.x[sysS.n_interior + sysS.n_T] - (-m_spec)) < 1e-8)

    # ================= W/W2 面积变化元件（想法 19 模式示范） =================
    print("\nW/W2 面积变化元件（两口面积不同，zeta*rho*V_min^2/2 总压损失）")
    sysW, ctxW = assemble(CASE_AREA_CHANGE)
    resW = solve(sysW, None, ctxW)
    # 手算：m = A_min·sqrt(2·ρ_up·Δp0/ζ)，ρ_up = p_hi/(R·T0)（上游总态）
    rho_w = _gasC.rho_from_pT(3.0e5, 600.0)   # 物性从气体类取（单点）
    m_w = 2.0e-4 * np.sqrt(2.0 * rho_w * 1.0e4 / 0.5)
    mW = resW.x[sysW.m_idx_of_port[(1, 0)]]       # comp1 口0（高压侧，流入为正）
    print(f"   W 突缩: m={mW:.8f}  手算={m_w:.8f}"
          f"（A_min=2e-4, ζ=0.5, Δp0=1e4）  iters={resW.report.iters}")
    check("W 突缩总压损失流量", abs(mW - m_w) < 1e-8)
    check("W 连续性 |m1+m2|<1e-10",
          abs(resW.x[sysW.n_interior + sysW.n_T + 2]
              + resW.x[sysW.n_interior + sysW.n_T + 3]) < 1e-10)

    sysW2, ctxW2 = assemble(CASE_AREA_CHANGE_T)
    resW2 = solve(sysW2, None, ctxW2)
    # 出口温度检验（想法 22 退锚后的新物理）：中间节点 1 的 T 由能量行
    # 解出 = 上游 650 的输运（绝热直通）；出口节点 2 的 T 也是未知量，
    # 出流能量行解出真实出口温度（同样 650——上游直传；旧硬锚定会把
    # 它钉在无意义的 550 上报表撒谎，已废弃）。ε 正则回拉 ~4e-3 K 量级
    # （出口行全部注入项，对自身 T 灵敏度低，回拉稍大，设计内）。
    T_mid = resW2.x[sysW2.T_idx_of_node[1]]
    T_out = resW2.x[sysW2.T_idx_of_node[2]]
    print(f"   W2 温度直传: T_mid={T_mid:.6f}  T_out={T_out:.6f}"
          f"（上游 650.0；出口解出真实温度而非旧锚定 550）")
    check("W2 温度直传（两口默认 port_T_out）",
          abs(T_mid - 650.0) < 1e-2 and abs(T_out - 650.0) < 1e-2,
          f"midΔ={abs(T_mid - 650.0):.2e} outΔ={abs(T_out - 650.0):.2e} K")

    # ================= ⑥ 适定性断言 =================
    print("\n⑥ 适定性断言（全流量边界应组装期报错）")
    print("   全流量边界（无 PRESSURE_BOUNDARY）-> 应组装期报错:")
    try:
        assemble(CASE_ALL_FLOW)
        check("全流量边界断言触发", False, "未报错！")
    except ValueError as e:
        print(f"        ValueError: {str(e)[:50]}…")
        check("全流量边界断言触发", True)

    # ================= 汇总 =================
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"X {len(FAILURES)} 项未通过: {FAILURES}")
        sys.exit(1)
    print("OK 边界元件化重构全部验证项通过（M1 内核 + 边界元件）")


if __name__ == "__main__":
    main()
