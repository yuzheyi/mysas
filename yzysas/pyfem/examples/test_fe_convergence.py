"""网格收敛性检查（Phase 1 验收补充项）。

V-FE1 算例逐级加密: nr = 12/24/48/96，观测内壁热流相对误差收敛阶。
P1 单元 + Robin 光滑解 → 热流误差应 ~O(h²)（每级约降 4 倍）。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # yzysas/

from pyfem.examples.test_fe import facets_on, rectangle_mesh  # noqa: E402
from pyfem.solver.axisym_heat import AxisymHeat           # noqa: E402
from pyfem.materials import Material                     # noqa: E402


def run(nr):
    r1, r2, L = 0.05, 0.15, 0.10
    k = 10.0
    mat = Material("const", {
        "k": [(200.0, k), (800.0, k)],
        "E": [(200.0, 123e9), (800.0, 123e9)],
        "nu": [(200.0, 0.33), (800.0, 0.33)],
        "alpha": [(373.15, 9.3e-6), (473.15, 9.3e-6)],
        "rho": [(200.0, 4480.0), (800.0, 4480.0)],
    })
    mesh = rectangle_mesh(r1, r2, 0.0, L, nr=nr, nz=max(4, nr * 2 // 3))
    heat = AxisymHeat(mesh, material=mat)
    heat.add_film("inner", facets_on(mesh, lambda x: np.isclose(x[0], r1)),
                  h=500.0, T_gas=700.0)
    heat.add_film("outer", facets_on(mesh, lambda x: np.isclose(x[0], r2)),
                  h=200.0, T_gas=350.0)
    res = heat.solve()
    return res.film_heats["inner"]


if __name__ == "__main__":
    R_conv_in = 1.0 / (500.0 * 2 * np.pi * 0.05)
    R_cond = np.log(0.15 / 0.05) / (2 * np.pi * 10.0)
    R_conv_out = 1.0 / (200.0 * 2 * np.pi * 0.15)
    Q_exact = (700.0 - 350.0) / (R_conv_in + R_cond + R_conv_out) * 0.10

    errs = []
    for nr in (12, 24, 48, 96):
        Q = run(nr)
        err = abs(Q / Q_exact - 1.0)
        errs.append(err)
        print(f"nr={nr:3d}  Q={Q:.6f} W  |rel err|={err:.3e}")
    orders = [np.log2(errs[i] / errs[i + 1]) for i in range(len(errs) - 1)]
    print("收敛阶:", "  ".join(f"{o:.2f}" for o in orders))
    ok = all(o > 1.5 for o in orders)
    print("[PASS]" if ok else "[FAIL]", "网格收敛阶 > 1.5（P1 预期 ≈2）")
    sys.exit(0 if ok else 1)
