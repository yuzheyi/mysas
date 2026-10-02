"""验证算例 V1: 锐边孔板流量特性（pysas OrificeModel vs 文献）。

基准与定位（证据链全部在 reference_data.py 带原文出处）:
  [J] Jobson (1960) Table 1 理论收缩系数 C(β)（γ=1.4）；×Cv=0.982 为实验带下沿
  [V] VTechWorks 1987 论文引述: 锐边孔板 Cd 0.61(β→1) → 0.82(β=0.27)
  [H] 手册带: 亚临界低马赫 Cd ≈ 0.60–0.65

验证逻辑（诚实定位——Cd 是元件的输入参数，关联式来自文献，不循环自证）:
  扫描 A: Cd_spec = 1.0      → 求解器必须精确复现理想喷嘴律 Φ(β)（含壅塞转折）
  扫描 B: Cd_spec = 0.61     → 流量 = 0.61·Φ(β)，落在手册亚临界带内
  扫描 C: Cd_spec = C_J(β)   → 流量随压比的变化复现 Jobson 理论带（注入文献
           关联式后，等效 Cd 曲线应与 C_J(β) 重合）

判定标准（文档 §5）:
  ① A: |ṁ/Φ − 1| < 1e-9（全压比段，公式实现的精确性）
  ② A: 壅塞平台 = 闭式 Γ·A·p0/√T0，转折发生在 β_crit=0.5283 ± 0.025
  ③ B: ṁ_B/Φ = 0.61 ± 1e-9（Cd 线性注入正确）
  ④ C: 等效 Cd 全程落在 Jobson 带内（|Cd_eff − C_J| < 2e-3）

运行: python run_v1_orifice.py  → v1_results.json + fig_v1_orifice.png
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
from reference_data import (                      # noqa: E402
    ANCHOR_VT1987, BETA_CRIT, JOBSON_CV, JOBSON_TABLE1_C, psi_nozzle)

R, GAMMA, T0 = 287.05, 1.4, 600.0
P_UP, A_ORIF = 3.0e5, 1.0e-4

_BJ = [r for r, _ in JOBSON_TABLE1_C]
_CJ = [c for _, c in JOBSON_TABLE1_C]


def jobson_c(beta: float) -> float:
    """Jobson Table1 C(β) 线性插值；带外常数延拓（壅塞段 C 冻结在 0.741）。"""
    return float(np.interp(beta, _BJ, _CJ))


def case(beta_target: float, cd_spec: float) -> dict:
    """单孔板网络（新边界元件格式，同 test_solver.py CASE_ORIFICE_SUB）。"""
    return {
        "gas": {"R": R, "gamma": GAMMA, "T0_default": T0, "mu": 1.8e-5},
        "nodes": [{"id": 0}, {"id": 1}],
        "comps": [
            {"id": 0, "type": "PRESSURE_BOUNDARY",
             "ports": [{"area": 0.0, "node": 0}], "params": [P_UP, T0]},
            {"id": 1, "type": "ORIFICE",
             "ports": [{"area": A_ORIF, "node": 0}, {"area": A_ORIF, "node": 1}],
             "params": [1.0, cd_spec]},
            {"id": 2, "type": "PRESSURE_BOUNDARY",
             "ports": [{"area": 0.0, "node": 1}],
             "params": [P_UP * max(beta_target, 1.5e-6), T0]},
        ],
    }


def run_point(beta: float, cd_spec: float) -> tuple[float, int]:
    """求一个工作点 → (孔板高压口流量 kg/s, 迭代步数)。"""
    net, ctx = netinf_from_dict(case(beta, cd_spec))
    system = NetworkSystem(net, build_models(net))
    res = solve(system, None, ctx)
    if not res.converged:
        raise RuntimeError(f"β={beta}, Cd={cd_spec} 未收敛")
    return abs(res.x[system.n_interior + 1]), res.report.iters


def main() -> None:
    betas = np.concatenate([
        np.linspace(0.55, 0.98, 22),          # 亚临界段
        np.linspace(0.02, 0.55, 12),          # 壅塞段
    ])

    sweeps = {"A_cd1": [], "B_cd061": [], "C_jobson": []}
    for b in betas:
        b = float(b)
        mA, itA = run_point(b, 1.0)
        mB, _ = run_point(b, ANCHOR_VT1987["cd_near_1"])
        mC, _ = run_point(b, jobson_c(b))
        phi = psi_nozzle(b)
        sweeps["A_cd1"].append(
            {"beta": round(b, 4), "mdot": mA, "iters": itA,
             "cd_eff": mA / (A_ORIF * P_UP / np.sqrt(T0) * phi)})
        sweeps["B_cd061"].append(
            {"beta": round(b, 4), "mdot": mB,
             "cd_eff": mB / (A_ORIF * P_UP / np.sqrt(T0) * phi)})
        sweeps["C_jobson"].append(
            {"beta": round(b, 4), "mdot": mC,
             "cd_eff": mC / (A_ORIF * P_UP / np.sqrt(T0) * phi)})

    # ---- 判定 ----
    ptsA = sweeps["A_cd1"]
    m_ch = A_ORIF * P_UP / np.sqrt(T0) * psi_nozzle(0.0)  # 壅塞闭式

    # 转折压比: 在"最大壅塞采样点"与"最小未壅塞采样点"之间二分
    # （谓词 is_choked(β) = 解出的流量等于壅塞闭式，单调跳变）
    def is_choked(beta: float) -> bool:
        m, _ = run_point(beta, 1.0)
        return abs(m - m_ch) < 1e-9

    lo = max(r["beta"] for r in ptsA if r["mdot"] >= m_ch - 1e-9)
    hi = min(r["beta"] for r in ptsA if r["mdot"] < m_ch - 1e-9)
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        if is_choked(mid):
            lo = mid
        else:
            hi = mid
    beta_transition = 0.5 * (lo + hi)

    checks = {
        "① A: |ṁ/Φ−1|<1e-9 全压比段":
            max(abs(r["cd_eff"] - 1.0) for r in ptsA) < 1e-9,
        "② A: 壅塞平台=闭式且转折在 β_crit±0.025":
            abs(beta_transition - BETA_CRIT) < 0.025,
        "③ B: ṁ_B/Φ = 0.61±1e-9":
            max(abs(r["cd_eff"] - ANCHOR_VT1987["cd_near_1"])
                for r in sweeps["B_cd061"]) < 1e-9,
        "④ C: 等效 Cd 复现 Jobson C(β) (|ΔC|<2e-3)":
            max(abs(r["cd_eff"] - jobson_c(r["beta"]))
                for r in sweeps["C_jobson"]) < 2e-3,
    }

    # ---- 出图 ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # 左图: ṁ–β 三条扫描
    for tag, pts, color, label in [
        ("A_cd1", ptsA, "tab:blue", "A: Cd_spec=1 (ideal check)"),
        ("B_cd061", sweeps["B_cd061"], "tab:orange",
         f"B: Cd_spec={ANCHOR_VT1987['cd_near_1']:.2f} (VT1987 anchor)"),
        ("C_jobson", sweeps["C_jobson"], "tab:green",
         "C: Cd_spec=Jobson C(β) (1960 Table 1)"),
    ]:
        axes[0].plot([p["beta"] for p in pts], [p["mdot"] for p in pts],
                     "o", ms=3.5, color=color, label=label)
    b_th = np.linspace(0.02, 1.0, 300)
    m_ch_line = A_ORIF * P_UP / np.sqrt(T0) * psi_nozzle(0.0)
    axes[0].axhline(m_ch_line, color="gray", ls="--", lw=1,
                    label=r"choked closed form $\Gamma A p_0/\sqrt{T_0}$")
    axes[0].axvline(BETA_CRIT, color="red", ls=":", lw=1,
                    label=r"$\beta_{crit}=0.5283$")
    axes[0].set_xlabel(r"pressure ratio $\beta = p_2/p_1$")
    axes[0].set_ylabel(r"$\dot m$ [kg/s]")
    axes[0].set_title("V1: orifice mass-flow characteristic\n"
                      f"(p_up=3 bar, T0=600 K, A={A_ORIF:.0e} m²)")
    axes[0].legend(fontsize=7.5)
    axes[0].grid(alpha=0.3)

    # 右图: 等效 Cd–β vs 文献带
    axes[1].plot([p["beta"] for p in ptsA], [p["cd_eff"] for p in ptsA],
                 "o", ms=3.5, color="tab:blue", label="A: Cd_eff (must be 1.0)")
    axes[1].plot([p["beta"] for p in sweeps["B_cd061"]],
                 [p["cd_eff"] for p in sweeps["B_cd061"]],
                 "s", ms=3.5, color="tab:orange", label="B: Cd_eff (=0.61)")
    axes[1].plot([p["beta"] for p in sweeps["C_jobson"]],
                 [p["cd_eff"] for p in sweeps["C_jobson"]],
                 "^", ms=4, color="tab:green", label="C: Cd_eff = C_J(β)")
    b_sub = [x for x in b_th if x >= BETA_CRIT]
    axes[1].fill_between(b_sub,
                         [JOBSON_CV * jobson_c(x) for x in b_sub],
                         [jobson_c(x) for x in b_sub],
                         alpha=0.25, color="green",
                         label="Jobson band [Cv·C, C] (experiment)")
    axes[1].plot([x for x in b_th if x < BETA_CRIT],
                 [jobson_c(x) for x in b_th if x < BETA_CRIT],
                 "-", color="green", lw=1, alpha=0.6)
    axes[1].axhspan(0.60, 0.65, color="steelblue", alpha=0.12,
                    label="handbook band 0.60–0.65")
    axes[1].plot([ANCHOR_VT1987["pr"]], [ANCHOR_VT1987["cd_at_pr_027"]],
                 "v", color="purple", ms=8,
                 label="VT1987: Cd=0.82 @ β=0.27 (real sharp edge)")
    axes[1].axvline(BETA_CRIT, color="red", ls=":", lw=1)
    axes[1].set_xlabel(r"pressure ratio $\beta = p_2/p_1$")
    axes[1].set_ylabel(r"equivalent $C_d$")
    axes[1].set_title("V1: Cd realization vs literature bands\n"
                      "(Cd is an input correlation — sweep C tracks Jobson)")
    axes[1].set_ylim(0.55, 1.05)
    axes[1].legend(fontsize=7, loc="center right")
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    fig.savefig(r"E:\mywork\programDesign\mysas_dev\yzysas\fuzz\results\bench"
                r"\fig_v1_orifice.png", dpi=130)

    # ---- 落盘 ----
    out = {
        "benchmark": "V1 sharp-edged orifice mass-flow characteristic",
        "solver": "pysas M1 (damped discrete Newton, scaled)",
        "config": {"p_up": P_UP, "T0": T0, "A": A_ORIF, "R": R, "gamma": GAMMA},
        "choked_closed_form": m_ch,
        "beta_transition": beta_transition,
        "sweeps": sweeps,
        "checks": {k: bool(v) for k, v in checks.items()},
        "anchors": {"jobson_cv": JOBSON_CV, "vt1987": ANCHOR_VT1987,
                    "beta_crit": BETA_CRIT},
    }
    with open(r"E:\mywork\programDesign\mysas_dev\yzysas\fuzz\results\bench"
              r"\v1_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    n = sum(len(v) for v in sweeps.values())
    print(f"共 {n} 个工作点全部收敛（最大迭代 {max(p['iters'] for p in ptsA)} 步）")
    print(f"壅塞平台闭式 = {m_ch:.6f} kg/s，转折 β = {beta_transition:.4f}")
    for k, v in checks.items():
        print(("  ✓ " if v else "  ✗ ") + k)
    print("图: fig_v1_orifice.png   数据: v1_results.json")
    if not all(checks.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
