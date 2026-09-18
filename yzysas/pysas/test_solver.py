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
     A  两管串联      3e5->1e5    中点压力 = 二次方程精确根（可压修正）+
                                 流量定点迭代（f(Re) 自洽）
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
def orifice_m_hand(p01, p02, T0, Cd, A, R=287.05, gamma=1.4):
    """孔板流量手算公式（亚/超临界，与 orifice.py docstring 同源）。"""
    beta = min(max(p02 / p01, 1e-8), 1.0)
    crit = (2.0 / (gamma + 1.0)) ** (gamma / (gamma - 1.0))
    factor = Cd * A * p01 / np.sqrt(T0)
    if beta <= crit:
        return factor * np.sqrt(gamma / R) \
            * (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
    return factor * np.sqrt(2.0 * gamma / (R * (gamma - 1.0))) \
        * beta ** (1.0 / gamma) * np.sqrt(1.0 - beta ** ((gamma - 1.0) / gamma))


def pipe_m_hand_full(p_up, p_down, T0, L, D, eps, area, m_guess,
                     mu=1.8e-5, R=287.05, gamma=1.4):
    A, dp = area, p_up - p_down
    if dp <= 0:
        return 0.0
    rho = p_up / (R * T0)
    re_of = lambda m: abs(m) * D / (mu * A)
    m_lam = rho * dp * A * D * D / (32.0 * mu * L)
    re_g = re_of(m_guess)
    if re_g <= 2000:
        return m_lam
    term = eps / (3.7 * D) + 5.74 / re_g ** 0.9
    f = 0.25 / np.log10(term) ** 2
    m_turb = A * np.sqrt(2.0 * rho * dp * D / (f * L))
    w = min(max((re_g - 2000.0) / 2000.0, 0.0), 1.0)
    return (1.0 - w) * m_lam + w * m_turb


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
    "gas": {"R": 287.05, "gamma": 1.4, "T0_default": 600.0, "mu": 1.8e-5},
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
    "gas": {"R": 287.05, "gamma": 1.4, "T0_default": 600.0, "mu": 1.8e-5},
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
    "gas": {"R": 287.05, "gamma": 1.4, "T0_default": 600.0, "mu": 1.8e-5},
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
    "gas": {"R": 287.05, "gamma": 1.4, "T0_default": 600.0, "mu": 1.8e-5},
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
    "gas": {"R": 287.05, "gamma": 1.4, "T0_default": 600.0, "mu": 1.8e-5},
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

CASE_ALL_FLOW = {  # 全流量边界（无压力锚定）-> 组装期应断言报错
    "gas": {"R": 287.05, "gamma": 1.4, "T0_default": 600.0, "mu": 1.8e-5},
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
    x0A = np.array([3.0e5, 2.0e5, 1.0e5, -0.1, -0.1, 0.1, -0.1, 0.1, 0.1])
    scalingA = make_scaling(sysA, ctxA)
    probA = ScaledProblem(sysA, ctxA, scalingA)
    J_raw = fd_jacobian(lambda x: sysA.residual(x, ctxA), x0A)
    J_scl = fd_jacobian(probA.residual, scalingA.to_scaled(x0A))
    cond_raw, cond_scl = np.linalg.cond(J_raw), np.linalg.cond(J_scl)
    print(f"   p_ref={scalingA.p_ref:.3e} Pa  m_ref={scalingA.m_ref:.4f} kg/s"
          f"（壅塞容量自估）")
    print(f"   cond(J)={cond_raw:.3e} -> cond(J~)={cond_scl:.3e}"
          f"（改善 {cond_raw / cond_scl:.1e} 倍）")
    check("缩放把病态雅可比变成良态", cond_scl < 100 and cond_raw / cond_scl > 1e3)

    # ================= ② 手算核对 =================
    print("\n② 手算核对（独立公式）")

    # B 单孔板亚临界
    sysB, ctxB = assemble(CASE_ORIFICE_SUB)
    resB = solve(sysB, None, ctxB)
    m_hand = orifice_m_hand(3.0e5, 2.0e5, 600.0, 0.8, 1.0e-4)
    print(f"   B 单孔板亚临界: m={resB.x[sysB.n_interior + 1]:.8f}"
          f"  手算={m_hand:.8f}  iters={resB.report.iters}")
    check("B 亚临界闭式公式", abs(resB.x[sysB.n_interior + 1] - m_hand) < 1e-9)

    # B2 单孔板壅塞（M0 超临界 bug 回归）
    sysB2, ctxB2 = assemble(CASE_ORIFICE_CHOKED)
    resB2 = solve(sysB2, None, ctxB2)
    m_hand2 = orifice_m_hand(3.0e5, 1.0e5, 600.0, 0.8, 1.0e-4)
    print(f"   B2 单孔板壅塞:  m={resB2.x[sysB2.n_interior + 1]:.8f}"
          f"  手算={m_hand2:.8f}  iters={resB2.report.iters}")
    check("B2 壅塞闭式公式（回归）", abs(resB2.x[sysB2.n_interior + 1] - m_hand2) < 1e-9)

    # A 两管串联：可压修正手算
    #   连续性 m0=m1 且两管同 Re 同 f -> rho0Dp0 = rho1Dp1，rho∝p_up：
    #   3e5(3e5-p) = p(p-1e5) -> p^2 + 2e5·p - 9e10 = 0 -> p = 216227.76 Pa
    resA = solve(sysA, x0A, ctxA)
    p_hand = (-2.0e5 + np.sqrt(4.0e10 + 3.6e11)) / 2.0
    m_fix = 0.1
    for _ in range(60):  # 流量定点迭代：m = Asqrt(2rho0Dp0·D/(f(Re(m))·L))
        m_fix = pipe_m_hand_full(3.0e5, p_hand, 600.0, 0.5, 0.02, 1.0e-5,
                                 3.1416e-4, m_fix)
    print(f"   A 两管串联: p_mid={resA.x[1]:.2f}  手算={p_hand:.2f}")
    print(f"          m={resA.x[sysA.n_interior + 1]:.6f}"
          f"（管0进口）  手算定点={m_fix:.6f}  iters={resA.report.iters}")
    check("A 中点压力二次方程精确根", abs(resA.x[1] - p_hand) < 0.5,
          f"Δ={abs(resA.x[1] - p_hand):.2e} Pa")
    check("A 流量定点迭代",
          abs(abs(resA.x[sysA.n_interior + 1]) - m_fix) < 1e-8)

    # C 孔板+管串联：壅塞孔板闭式 + 管压降定点
    sysC, ctxC = assemble(CASE_SERIES)
    resC = solve(sysC, None, ctxC)
    m_ch = orifice_m_hand(3.0e5, 1.0e5, 600.0, 0.8, 1.0e-4)
    p_c = 1.05e5
    for _ in range(60):  # p_mid = 1e5 + Dp_pipe(m_ch)，rho=rho(p_mid)
        re = m_ch * 0.02 / (1.8e-5 * 3.1416e-4)
        term = 1.0e-5 / (3.7 * 0.02) + 5.74 / re ** 0.9
        f = 0.25 / np.log10(term) ** 2
        p_c = 1.0e5 + m_ch ** 2 * f * 0.5 / (2.0 * (p_c / (287.05 * 600.0))
                                             * 3.1416e-4 ** 2 * 0.02)
    print(f"   C 孔板+管串联: p_mid={resC.x[1]:.2f}  手算定点={p_c:.2f}")
    print(f"          m={resC.x[sysC.n_interior + 1]:.8f}"
          f"  手算壅塞={m_ch:.8f}  iters={resC.report.iters}")
    check("C 壅塞孔板闭式流量", abs(resC.x[sysC.n_interior + 1] - m_ch) < 1e-8)
    check("C 中点压力管压降定点", abs(resC.x[1] - p_c) < 0.5,
          f"Δ={abs(resC.x[1] - p_c):.2e} Pa")

    # H 双孔板近临界：独立公式二分
    sysH, ctxH = assemble(CASE_TWIN_ORIFICE)
    x_refH = np.array([5.0e5, 3.0e5, 1.0e5, -0.05, -0.05, 0.05, -0.05, 0.05, 0.05])
    resH = solve(sysH, x_refH, ctxH)
    g = lambda p: (orifice_m_hand(5.0e5, p, 600.0, 0.8, 3.1416e-4)
                   - orifice_m_hand(p, 1.0e5, 600.0, 0.8, 0.2 * 3.1416e-4))
    p_h = bisect_root(g, 1.0e5, 5.0e5)
    m_h = orifice_m_hand(p_h, 1.0e5, 600.0, 0.8, 0.2 * 3.1416e-4)
    print(f"   H 双孔板近临界: p_mid={resH.x[1]:.2f}  手算二分={p_h:.2f}"
          f"（beta1={p_h / 5e5:.4f}）")
    print(f"          m={resH.x[sysH.n_interior + 1]:.8f}  手算={m_h:.8f}"
          f"  iters={resH.report.iters}")
    check("H 双孔板 1-D 二分", abs(resH.x[1] - p_h) < 0.5
          and abs(resH.x[sysH.n_interior + 1] - m_h) < 1e-8)

    # ================= ③ scipy 同解 =================
    print("\n③ 与 scipy root 同解（同一初值）")
    for tag, system, ctx, x0, res in [
        ("A", sysA, ctxA, x0A, resA),
        ("B", sysB, ctxB, None, resB),
        ("B2", sysB2, ctxB2, None, resB2),
        ("C", sysC, ctxC, None, resC),
        ("H", sysH, ctxH, x_refH, resH),
    ]:
        x0_use = x0 if x0 is not None else default_guess(system, ctx)
        sol = root(system.residual, x0_use, args=(ctx,), method="lm")
        scale = np.maximum(np.abs(res.x), 1.0)
        scale[system.n_interior:] = max(res.scaling.m_ref, 1e-12)
        rel = np.abs(res.x - sol.x) / scale
        print(f"   {tag:3s} scipy conv={sol.success}  "
              f"max 相对差 = {rel.max():.2e}")
        check(f"{tag} 与 scipy 同解", rel.max() < 1e-5)

    # ================= ④ 坏初值：裸牛顿 vs 阻尼牛顿 =================
    print("\n④ 坏初值对比（H 算例: p_mid 钉在出口压力 1e5、m 全 0——")
    print("   孔板2 起点恰在 beta=1 奇异点；边界节点仍钉真值）")
    x_bad = np.array([5.0e5, 1.0e5, 1.0e5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
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
    x_degen = np.array([5.0e5, 0.1, 1.0e5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
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
    # 手算：m=0.1 定流，管 Darcy 反解 p_in = p_out + Dp（f 由 Re 定点）
    m_spec = 0.1
    p_in = 1.05e5
    for _ in range(60):
        re = m_spec * 0.02 / (1.8e-5 * 3.1416e-4)   # Re ≈ 3537（过渡段）
        term = 1.0e-5 / (3.7 * 0.02) + 5.74 / re ** 0.9
        f = 0.25 / np.log10(term) ** 2
        # 湍流式反解 Dp = m^2fL/(2rhoA^2D)，rho 由 p_in 估（上游）
        dp = m_spec ** 2 * f * 0.5 / (2.0 * (p_in / (287.05 * 600.0))
                                      * 3.1416e-4 ** 2 * 0.02)
        p_in = 1.0e5 + dp
    print(f"   S 流量进口: p_in={resS.x[0]:.2f}  手算定点={p_in:.2f}"
          f"  m_spec={m_spec}")
    print(f"          源端口流量={resS.x[sysS.n_interior]:.6f}"
          f"（应为 -0.1 = 注入）  iters={resS.report.iters}")
    check("S 流量边界进口压力 Darcy 定点", abs(resS.x[0] - p_in) < 0.5,
          f"Δ={abs(resS.x[0] - p_in):.2e} Pa")
    check("S 源端口流量 = -m_spec（符号约定）",
          abs(resS.x[sysS.n_interior] - (-m_spec)) < 1e-8)

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
