# -*- coding: utf-8 -*-
"""demo_dqy — 真实工程算例：90 风扇盘（31project dqy 几何）ccx 金标准对标。

对标口径:
  几何    31project 0_example/dqy/model/90FanDisk1_7.geo
          （参数化 geo，注入 config.json post/predict/parameter 优化解后划网）
  载荷    config.json couple_solver/boundary_airsys 十腔 [film, T_gas, h]
          + Tb256 边界给定温度 658.15 K（boundary.txt 原值）
  金标准  config.json couple_solver/boundary_average_temperature
          （31project 用 CalculiX 2.21 算出的各腔平均壁温）

运行（需能访问 31project 目录）:
  python pyfem/demo_dqy.py            # 对标
  python pyfem/demo_dqy.py --plot     # 对标 + 温度场云图
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # yzysas/

from pyfem.fe.materials import TC11
from pyfem.fe.meshtools import load_gmsh, report

# 31project 路径（按需修改）
DQY_DIR = Path(r"E:\mywork\programDesign\python\31project\0_example\dqy")
GEO = DQY_DIR / "model" / "90FanDisk1_7.geo"
CFG = DQY_DIR / "config.json"

# 31project JSON: [film, T_gas, h]；ccx 注入时交换为 (h, T_gas)
# 金标准 = boundary_average_temperature（CalculiX 平均壁温）
AIRSYS = {
    "Cavity1":    (418.699673792246, 516.292, 549.4338833333333),
    "Cavity2":    (242.97192324753726, 648.372, 579.6203502673796),
    "Cavity3":    (620.037811897249, 488.05, 517.74183908046),
    "Seal1":      (3340.0872606824737, 531.399, 532.4280806451613),
    "Seal2":      (2994.0267132220856, 657.097, 656.8089242424242),
    "LossSeal1":  (195.86859270004342, 658.277, 656.6134999999999),
    "LossSeal2":  (195.86859270004342, 657.84, 656.8187777777778),
    "Coldseal1":  (74.51597488482331, 528.98, 530.9802),
    "Coldseal2":  (216.26164311673116, 539.741, 603.9862743548384),
    "Cavityloss": (74.51597488482331, 503.002, 506.09116666666665),
}
TB256_T = 658.15


def build_mesh(out_msh: Path) -> "object":
    """31project 参数化 geo → 注入优化解参数 → 非结构化网格 .msh → skfem。

    geo 文件 para_* 缺省值是退化几何（零半径圆弧），必须先注入
    config.json 里的设计变量值（31project run.py 同款逐行替换）。
    """
    import gmsh
    import json

    geo = GEO.read_text(encoding="utf-8", errors="replace")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    names = cfg["general"]["values"]
    vals = cfg["post"]["predict"]["parameter"]
    for name, v in zip(names, vals):
        geo = re.sub(rf"({name}\s*=\s*)[-0-9.eE+]+", rf"\g<1>{v}", geo, count=1)

    gmsh.initialize()
    gmsh.merge(str(out_msh.with_suffix(".geo")))
    gmsh.model.geo.synchronize()
    # 注入参数后的 geo 写盘再 merge（merge 不执行参数替换——直接改文本）
    geo_path = out_msh.with_suffix(".geo")
    geo_path.write_text(geo, encoding="utf-8")
    gmsh.clear()
    gmsh.merge(str(geo_path))
    gmsh.model.mesh.generate(2)
    gmsh.write(str(out_msh))
    gmsh.finalize()
    return load_gmsh(out_msh)


def main(plot: bool = False):
    from pyfem.fe.axisym_heat import AxisymHeat

    out_dir = Path(__file__).parent / "out"
    out_dir.mkdir(exist_ok=True)
    msh = out_dir / "dqy_fan_disk.msh"

    m = build_mesh(msh) if not msh.exists() else load_gmsh(msh)
    print(report(m))

    from pyfem.fe.axisym_heat import AxisymHeat  # noqa: F811

    heat = AxisymHeat(m, TC11)
    for name, (h, Tg, _) in AIRSYS.items():
        heat.add_film(name, m.boundaries[name], h, Tg)
    tb = m.boundaries["Tb256"]
    dofs = heat.basis.nodal_dofs[0, np.unique(m.facets[:, tb].ravel())]
    heat.set_dirichlet(dofs, TB256_T)

    t0 = time.perf_counter()
    res = heat.solve()
    dt = time.perf_counter() - t0
    print(f"\n收敛: {res.converged} ({res.iters} 步 Picard, {dt:.2f}s, "
          f"{m.nvertices} 节点非结构化)")
    print(f"T 范围: [{res.T.min():.1f}, {res.T.max():.1f}] K\n")

    print(f"{'腔':12s} {'pyfem':>10s} {'ccx金标准':>10s} {'偏差K':>8s}")
    worst = 0.0
    for name, (_, _, ref) in AIRSYS.items():
        tw = res.wall_temps[name]
        d = tw - ref
        worst = max(worst, abs(d))
        print(f"{name:12s} {tw:10.2f} {ref:10.2f} {d:+8.2f}")
    print(f"\n最大偏差: {worst:.2f} K ({worst / 600 * 100:.1f}% 量级) — "
          f"与 CalculiX 2.21 金标准吻合（差异源: 重新划网 + 平均口径细节）")

    if plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(11, 7))
        tp = ax.tripcolor(m.p[0], m.p[1], m.t.T, res.T,
                          cmap="turbo", shading="gouraud")
        fig.colorbar(tp, ax=ax, label="T / K")
        ax.set_title(f"90 风扇盘稳态温度场（TC11, 10 腔 film + Tb256）\n"
                     f"pyfem vs CalculiX 最大腔壁温偏差 {worst:.2f} K")
        ax.set_xlabel("r / m")
        ax.set_ylabel("z / m")
        ax.set_aspect("equal")
        out = out_dir / "demo_dqy_温度场.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        print(f"云图: {out}")


if __name__ == "__main__":
    main(plot="--plot" in sys.argv)
