"""验证算例 V2: 管件沿程损失与分流网络（pysas PipeModel vs Darcy 手算）。

基准（每条都可独立手算核对，公式为教科书级）:
  [D] Darcy-Weisbach 沿程损失 Δp = f·(L/D)·ρv²/2，湍流摩擦系数 Swanee-Jain
      显式式（与 pysas pipe.py 同源但独立重写实现）
  [K] Kirchhoff 节点律: 网络任一节点 Σṁ = 0（assembly 层连续性方程的直接
      物理检验）；并联支路压降相等

验证内容:
  算例 T（串联回归）: 两管串联 3e5→1e5 Pa，中点压力 = 二次方程精确根
      （可压修正 ρ∝p_up），流量 = 定点迭代 f(Re) 自洽 —— M0 老朋友
  算例 P（并联新增）: 双管并联（不等长不等径）3e5→1e5 Pa，
      核对 ① 每支路 Darcy 压降相等且等于两端压差
             ② Kirchhoff: ṁ_1 + ṁ_2 = 总流量
             ③ 各支路流量与独立 Darcy 定点迭代解一致

判定标准（文档 §5）:
  ① T: |p_mid − p_exact| < 0.5 Pa（收敛噪声水平）
  ② P: 两支路压降差 < 0.5 Pa
  ③ P: Kirchhoff 残差 |ṁ₁+ṁ₂−ṁ_total| < 1e-9
  ④ P: 各支路流量 vs 独立 Darcy 定点解 |Δṁ| < 1e-8

运行: python run_v2_pipe_network.py → v2_results.json + fig_v2_network.png
"""
from __future__ import annotations

import json
import sys

import numpy as np

sys.path.insert(0, r"E:\mywork\programDesign\mysas_dev\yzysas")
sys.path.insert(0, r"e:\mywork\programDesign\mysas\papers\sas-validation-benchmark")

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import netinf_from_dict, build_models  # noqa: E402
from pysas.solver import solve                    # noqa: E402

R, GAMMA, T0, MU = 287.05, 1.4, 600.0, 1.8e-5
AREA = 3.1416e-4          # 与 netinf.json 两管算例一致的流通面积


# ---------- 独立 Darcy 实现（不复用 pysas pipe.py，防自己验证自己） ----------
def pipe_m(p_up: float, p_down: float, L: float, D: float, eps: float,
           area: float, m_guess: float) -> float:
    """Darcy-Weisbach + Swanee-Jain，三段（层流线性/过渡插值/湍流）。
    与 pipe.py 文档公式同源、代码独立重写。返回流量 ≥0（高压→低压）。"""
    dp = p_up - p_down
    if dp <= 0:
        return 0.0
    rho = p_up / (R * T0)
    m_lam = rho * dp * area * D * D / (32.0 * MU * L)      # Hagen-Poiseuille
    re = abs(m_guess) * D / (MU * area)
    if re <= 2000:
        return m_lam
    f = 0.25 / np.log10(eps / (3.7 * D) + 5.74 / re ** 0.9) ** 2
    m_turb = area * np.sqrt(2.0 * rho * dp * D / (f * L))
    w = min(max((re - 2000.0) / 2000.0, 0.0), 1.0)
    return (1.0 - w) * m_lam + w * m_turb


def darcy_dp(m: float, p_up: float, L: float, D: float, eps: float,
             area: float) -> float:
    """给定流量正算压降（核对并联支路压降用）。"""
    rho = p_up / (R * T0)
    re = abs(m) * D / (MU * area)
    if re <= 2000:
        f = 64.0 / max(re, 1e-12)
    else:
        f = 0.25 / np.log10(eps / (3.7 * D) + 5.74 / re ** 0.9) ** 2
    v = abs(m) / (rho * area)
    return f * (L / D) * rho * v * v / 2.0


# ---------- 网络构造 ----------
def case_series() -> dict:
    """T: 两管串联（同 netinf.json 老算例，新边界元件格式）。"""
    return {
        "gas": {"R": R, "gamma": GAMMA, "T0_default": T0, "mu": MU},
        "nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
        "comps": [
            {"id": 0, "type": "PRESSURE_BOUNDARY",
             "ports": [{"area": 0.0, "node": 0}], "params": [3.0e5, T0]},
            {"id": 1, "type": "PIPE",
             "ports": [{"area": AREA, "node": 0}, {"area": AREA, "node": 1}],
             "params": [0.5, 0.02, 1.0e-5]},
            {"id": 2, "type": "PIPE",
             "ports": [{"area": AREA, "node": 1}, {"area": AREA, "node": 2}],
             "params": [0.5, 0.02, 1.0e-5]},
            {"id": 3, "type": "PRESSURE_BOUNDARY",
             "ports": [{"area": 0.0, "node": 2}], "params": [1.0e5, T0]},
        ],
    }


def case_parallel() -> dict:
    """P: 双管并联。支路 1: L=0.5 m D=0.02 m；支路 2: L=1.2 m D=0.015 m。"""
    return {
        "gas": {"R": R, "gamma": GAMMA, "T0_default": T0, "mu": MU},
        "nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
        "comps": [
            {"id": 0, "type": "PRESSURE_BOUNDARY",
             "ports": [{"area": 0.0, "node": 0}], "params": [3.0e5, T0]},
            {"id": 1, "type": "PIPE",                    # 支路 1（短粗）
             "ports": [{"area": AREA, "node": 0}, {"area": AREA, "node": 1}],
             "params": [0.5, 0.02, 1.0e-5]},
            {"id": 2, "type": "PIPE",                    # 支路 2（长细）
             "ports": [{"area": AREA, "node": 0}, {"area": AREA, "node": 1}],
             "params": [1.2, 0.015, 1.0e-5]},
            {"id": 3, "type": "PIPE",                    # 汇合后出口段
             "ports": [{"area": AREA, "node": 1}, {"area": AREA, "node": 2}],
             "params": [0.3, 0.025, 1.0e-5]},
            {"id": 4, "type": "PRESSURE_BOUNDARY",
             "ports": [{"area": 0.0, "node": 2}], "params": [1.0e5, T0]},
        ],
    }


def assemble(case_dict):
    net, ctx = netinf_from_dict(case_dict)
    return NetworkSystem(net, build_models(net)), ctx


def main() -> None:
    results = {}

    # ================= 算例 T: 串联（回归） =================
    sysT, ctxT = assemble(case_series())
    resT = solve(sysT, None, ctxT)
    assert resT.converged
    p_mid = resT.x[1]
    m_tot_T = abs(resT.x[sysT.n_interior + 1])

    # 手算: 可压修正二次方程根 + 流量定点迭代
    p_exact = (-2.0e5 + np.sqrt(4.0e10 + 3.6e11)) / 2.0
    m_fix = 0.1
    for _ in range(60):
        m_fix = pipe_m(3.0e5, p_exact, 0.5, 0.02, 1.0e-5, AREA, m_fix)
    results["T_series"] = {
        "p_mid_solver": float(p_mid), "p_mid_exact": float(p_exact),
        "mdot_solver": float(m_tot_T), "mdot_fixed_point": float(m_fix),
    }
    checks_T = {
        "① T: |p_mid−p_exact|<0.5 Pa": abs(p_mid - p_exact) < 0.5,
        "② T: |ṁ−定点解|<1e-8": abs(m_tot_T - m_fix) < 1e-8,
    }

    # ================= 算例 P: 并联 =================
    sysP, ctxP = assemble(case_parallel())
    resP = solve(sysP, None, ctxP)
    assert resP.converged
    # 端口排布: 按 comp_id 展平。P 网络 N=3 节点 + 10 口
    # comp1(2口) comp2(2口) comp3(2口) comp4边界(1口) comp0边界(1口)
    # 流量下标从 n_interior=3 起，按 (comp_id, port) 排列:
    #   comp0 边界: [3]      comp1 支路1: [4,5]   comp2 支路2: [6,7]
    #   comp3 出口段: [8,9]  comp4 边界: [10]
    m_br1 = abs(resP.x[4])     # 支路 1 高压口
    m_br2 = abs(resP.x[6])     # 支路 2 高压口
    m_tot_P = abs(resP.x[8])   # 出口段流量（= 汇总流量）
    p_plenum = resP.x[1]       # 并联汇合节点压力

    # ② 每支路 Darcy 压降应相等（都等于 p0 − p_plenum）
    dp1 = darcy_dp(m_br1, 3.0e5, 0.5, 0.02, 1.0e-5, AREA)
    dp2 = darcy_dp(m_br2, 3.0e5, 1.2, 0.015, 1.0e-5, AREA)
    dp_net = 3.0e5 - p_plenum

    # ④ 各支路独立定点解
    def fixed_point_branch(L, D):
        m = 0.05
        for _ in range(200):
            m = pipe_m(3.0e5, p_plenum, L, D, 1.0e-5, AREA, m)
        return m

    m1_hand = fixed_point_branch(0.5, 0.02)
    m2_hand = fixed_point_branch(1.2, 0.015)

    results["P_parallel"] = {
        "p_plenum": float(p_plenum),
        "mdot_branch1": float(m_br1), "mdot_branch2": float(m_br2),
        "mdot_total": float(m_tot_P),
        "darcy_dp_branch1": float(dp1), "darcy_dp_branch2": float(dp2),
        "dp_network": float(dp_net),
        "mdot_branch1_hand": float(m1_hand), "mdot_branch2_hand": float(m2_hand),
    }
    checks_P = {
        "③ P: 支路压降相等 |Δ|<0.5 Pa":
            abs(dp1 - dp_net) < 0.5 and abs(dp2 - dp_net) < 0.5,
        "④ P: Kirchhoff |ṁ₁+ṁ₂−ṁ_tot|<1e-9":
            abs(m_br1 + m_br2 - m_tot_P) < 1e-9,
        "⑤ P: 支路流量=独立 Darcy 定点解":
            abs(m_br1 - m1_hand) < 1e-8 and abs(m_br2 - m2_hand) < 1e-8,
    }

    all_checks = {**checks_T, **checks_P}

    # ================= 出图 =================
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))

    # 图 1: 串联沿程压力分布（手算根对照）
    axes[0].plot([0, 0.5, 1.0], [3.0e5, p_mid, 1.0e5], "o-", ms=5,
                 color="tab:blue", label="pysas")
    axes[0].plot([0.5], [p_exact], "x", ms=10, color="red",
                 label=f"exact root {p_exact:.0f} Pa")
    axes[0].set_xlabel("normalized axial position")
    axes[0].set_ylabel("p₀ [Pa]")
    axes[0].set_title(f"T: series pipes — p_mid\n(ṁ={m_tot_T:.4f} kg/s)")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)

    # 图 2: 并联网络示意（用条形图表示各支路流量）
    names = ["branch 1\nL=0.5 D=0.02", "branch 2\nL=1.2 D=0.015", "total\n(exit)"]
    vals = [m_br1, m_br2, m_tot_P]
    bars = axes[1].bar(names, vals, color=["tab:orange", "tab:green", "tab:gray"])
    for b, v in zip(bars, vals):
        axes[1].text(b.get_x() + b.get_width() / 2, v + 2e-4, f"{v:.4f}",
                     ha="center", fontsize=9)
    axes[1].set_ylabel(r"$\dot m$ [kg/s]")
    axes[1].set_title(f"P: parallel branches — Kirchhoff check\n"
                      f"(p_plenum = {p_plenum:.0f} Pa)")
    axes[1].grid(alpha=0.3, axis="y")

    # 图 3: 支路压降一致性
    labels = ["branch 1\n(Darcy)", "branch 2\n(Darcy)", "network\n(p₀−p_plenum)"]
    dps = [dp1, dp2, dp_net]
    bars = axes[2].bar(labels, dps, color=["tab:orange", "tab:green", "tab:blue"])
    for b, v in zip(bars, dps):
        axes[2].text(b.get_x() + b.get_width() / 2, v + 200, f"{v:.0f}",
                     ha="center", fontsize=9)
    axes[2].set_ylabel(r"$\Delta p$ [Pa]")
    axes[2].set_title("P: branch Δp equality (Darcy vs network)")
    axes[2].grid(alpha=0.3, axis="y")

    plt.tight_layout()
    fig.savefig(r"E:\mywork\programDesign\mysas_dev\yzysas\fuzz\results\bench"
                r"\fig_v2_network.png", dpi=130)

    # ================= 落盘 =================
    out = {
        "benchmark": "V2 Darcy pipe friction + parallel network",
        "solver": "pysas M1 (damped discrete Newton, scaled)",
        "results": results,
        "checks": {k: bool(v) for k, v in all_checks.items()},
    }
    with open(r"E:\mywork\programDesign\mysas_dev\yzysas\fuzz\results\bench"
              r"\v2_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    for k, v in all_checks.items():
        print(("  ✓ " if v else "  ✗ ") + k)
    print(f"  T: p_mid={p_mid:.2f} (exact {p_exact:.2f})  ṁ={m_tot_T:.6f} kg/s")
    print(f"  P: p_plenum={p_plenum:.0f} Pa  ṁ₁={m_br1:.6f}  ṁ₂={m_br2:.6f}"
          f"  ṁ_tot={m_tot_P:.6f} kg/s")
    print("图: fig_v2_network.png   数据: v2_results.json")
    if not all(all_checks.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
