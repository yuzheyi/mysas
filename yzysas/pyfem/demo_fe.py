"""demo_fe — FE 独立求解演示：TC11 圆筒，内热外冷 film + 离心 + 热应力。

演示 pyfem.fe 全链路: 材料插值 → 稳态热传导(k(T) Picard) →
热弹性(D(T) Picard + 初应变 + 离心) → 云图输出。

运行（yzysas 目录下）:
    python pyfem/demo_fe.py            # 终端打印摘要
    python pyfem/demo_fe.py --plot     # 另存云图 PNG（不弹窗）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyfem.fe.axisym_heat import AxisymHeat                 # noqa: E402
from pyfem.fe.axisym_thermoelastic import AxisymThermoElastic  # noqa: E402
from pyfem.fe.materials import TC11                          # noqa: E402
from pyfem.test_fe import facets_on, nodes_on, rectangle_mesh  # noqa: E402


def main(plot: bool = False):
    # ---------- 几何/网格: 空心圆筒 r 50~150mm, 长 100mm ----------
    r1, r2, L = 0.05, 0.15, 0.10
    mesh = rectangle_mesh(r1, r2, 0.0, L, nr=48, nz=32)

    inner = facets_on(mesh, lambda x: np.isclose(x[0], r1))
    outer = facets_on(mesh, lambda x: np.isclose(x[0], r2))

    # ---------- 1) 稳态热传导: 内壁热气流 700K h=500，外壁冷气 350K h=200 ----------
    heat = AxisymHeat(mesh, material=TC11)
    heat.add_film("inner", inner, h=500.0, T_gas=700.0)
    heat.add_film("outer", outer, h=200.0, T_gas=350.0)
    hres = heat.solve()

    print("== 稳态热传导 (TC11, k(T) Picard) ==")
    print(f"  收敛: {hres.converged}  迭代: {hres.iters}")
    print(f"  T 范围: {hres.T.min():.2f} ~ {hres.T.max():.2f} K")
    for name, Tw in hres.wall_temps.items():
        print(f"  T_w[{name}] = {Tw:.3f} K")
    for name, Q in hres.film_heats.items():
        print(f"  Q[{name}] = {Q:.2f} W")

    # ---------- 2) 热弹性: 温度场 + 离心 ω² = 7.16e5 ----------
    te = AxisymThermoElastic(mesh, material=TC11, T_field=hres.T)
    te.add_centrifugal(omega2=7.159449797515024e5)   # 31project dqy 同值
    bottom = nodes_on(mesh, lambda x, y: np.isclose(y, 0.0))
    uz_dofs = te.basis.nodal_dofs[1, bottom]
    te.set_constraint(uz_dofs, np.zeros(len(uz_dofs)))
    tres = te.solve()

    ur = tres.u[te.basis.nodal_dofs[0]]
    uz = tres.u[te.basis.nodal_dofs[1]]
    print("\n== 热弹性 (D(T) Picard + 初应变 + 离心) ==")
    print(f"  收敛: {tres.converged}  迭代: {tres.iters}")
    print(f"  u_r 范围: {ur.min()*1e3:.4f} ~ {ur.max()*1e3:.4f} mm")
    print(f"  u_z 范围: {uz.min()*1e3:.4f} ~ {uz.max()*1e3:.4f} mm")
    print(f"  σr  范围: {tres.sigma['r'].min()/1e6:.2f} ~ {tres.sigma['r'].max()/1e6:.2f} MPa")
    print(f"  σθ  范围: {tres.sigma['theta'].min()/1e6:.2f} ~ {tres.sigma['theta'].max()/1e6:.2f} MPa")
    print(f"  von Mises max: {tres.von_mises.max()/1e6:.2f} MPa")

    # ---------- 3) 云图（可选） ----------
    if plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
        from matplotlib.tri import Triangulation

        out = Path(__file__).parent / "out"
        out.mkdir(exist_ok=True)
        tri = Triangulation(mesh.p[0], mesh.p[1], mesh.t.T)
        for field, name, unit in ((hres.T, "温度场", "K"),
                                  (tres.von_mises, "von Mises", "Pa"),
                                  (tres.sigma["theta"], "环向应力", "Pa"),
                                  (ur, "径向位移", "m")):
            fig, ax = plt.subplots(figsize=(6, 4))
            tc = ax.tricontourf(tri, field, levels=32, cmap="jet")
            ax.set_xlabel("r (m)")
            ax.set_ylabel("z (m)")
            ax.set_title(f"{name}（TC11 圆筒, 热流+离心）")
            fig.colorbar(tc, label=unit)
            fig.tight_layout()
            f = out / f"demo_fe_{name}.png"
            fig.savefig(f, dpi=150)
            print(f"  已存 {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(plot="--plot" in sys.argv))
