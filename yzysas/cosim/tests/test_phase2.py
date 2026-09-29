# -*- coding: utf-8 -*-
"""test_phase2 — Phase 2 验收：关联式 / WALL_FILM / h 分布通路 / pysas 回归。

运行: python cosim/tests/test_phase2.py（yzysas 目录下）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # yzysas/


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name} {detail}")
    return ok


# ========== 1. 关联式注册表与两条参考关联式 ==========
def test_correlations():
    from cosim.correlations import H_REGISTRY, get_h, make_h

    ok0 = check("注册表含两条参考", {"disk_cavity", "pipe_duct"} <= set(H_REGISTRY),
                f"got {sorted(H_REGISTRY)}")

    # 盘腔: h(r) 分布，量级与单调性
    fn = get_h("disk_cavity")
    rs = np.linspace(0.1, 0.4, 7)
    h = fn({"omega": 1000.0, "T_gas": 600.0, "p_gas": 2.0e5, "mdot": 0.05},
           {"facet_rs": rs})
    ok2 = check("disk_cavity 返回 (nf,) 分布", np.shape(h) == rs.shape,
                f"h ∈ [{np.min(h):.1f}, {np.max(h):.1f}]")
    ok3 = check("disk_cavity 量级合理 (10~5000)", 10.0 < np.min(h) and np.max(h) < 5000.0)
    ok4 = check("disk_cavity h 随 r 增（Re_phi↑）", bool(np.all(np.diff(h) > 0)))
    # omega=0 → 兜底分支不 NaN
    h0 = fn({"omega": 0.0, "T_gas": 600.0}, {"facet_rs": rs})
    ok5 = check("disk_cavity omega=0 不崩", bool(np.all(np.isfinite(h0))))

    # 管道: 标量，湍流 D-B 量级
    fn2 = get_h("pipe_duct")
    h_pipe = fn2({"mdot": 0.5, "T_gas": 600.0, "p_gas": 3.0e5}, {"D": 0.05})
    ok6 = check("pipe_duct 标量且量级合理", 100.0 < h_pipe < 2000.0,
                f"h = {h_pipe:.1f}")
    h_lam = fn2({"mdot": 0.001, "T_gas": 600.0}, {"D": 0.05})  # 层流兜底 Nu=3.66
    ok7 = check("pipe_duct 层流兜底", 0.5 < h_lam < 20.0, f"h = {h_lam:.2f}")

    # make_h 兼容 callable
    ok8 = check("make_h 兼容 callable", make_h(fn2) is fn2)

    try:
        get_h("nonexistent")
        ok9 = False
    except KeyError:
        ok9 = True
    check("get_h 未知名 KeyError", ok9)
    return all([ok0, ok2, ok3, ok4, ok5, ok6, ok7, ok8, ok9])


# ========== 2. pysas ElemType + WALL_FILM 注册 ==========
def test_registration():
    from pysas.datamodel import ElemType
    ok0 = check("ElemType.WALL_FILM = 11", int(ElemType.WALL_FILM) == 11)

    import cosim.netelem  # noqa: F401  ← import 即注册
    from pysas.io.netinf import _MODEL_REGISTRY, build_models
    from pysas.datamodel import Comp, Port
    ok1 = check("WALL_FILM 进工厂", 11 in _MODEL_REGISTRY)

    comp = Comp(comp_id=0, elem_type=ElemType.WALL_FILM,
                ports=[Port(area=0.01, node_id=0)], params=[500.0])
    models = build_models(type("N", (), {"comps": [comp]})())
    ok2 = check("build_models 能实例化", 0 in models)
    return ok0 and ok1 and ok2


# ========== 3. WALL_FILM 元件物理 ==========
def test_filmwall_physics():
    from cosim.netelem.filmwall import FilmWallModel
    from pysas.datamodel import Comp, Port
    from pysas.elements.base import SolveContext
    ctx = SolveContext()
    ctx.T0_default = 600.0

    comp = Comp(comp_id=0, elem_type=11,
                ports=[Port(area=0.02, node_id=0)], params=[450.0])
    fw = FilmWallModel(comp)
    # 索引注入（assembly 通常做）：单口 ṁ 索引 0
    fw._m_idx = [0]

    # 独立跑缺省: h=0 → q=0（绝热中性）
    ok0 = check("未耦合缺省 q=0", fw.heat_input(np.array([0.1]), ctx) == 0.0)

    # 标量注入: q = h·A·(T_w − T_gas)
    fw.set_coupling(h=500.0, T_gas=600.0)   # T_w_default=450 < T_gas → q<0 冷却
    q = fw.heat_input(np.array([0.1]), ctx)
    q_ex = 500.0 * 0.02 * (450.0 - 600.0)
    ok1 = check("标量 q = h·A·(T_w−T_gas)", abs(q - q_ex) < 1e-9, f"q={q:.1f}")

    # 分布注入: q = Σ h_f (T_w_f − T_gas) w_f
    Tw = np.array([500.0, 600.0])       # K
    w = np.array([0.012, 0.008])        # m²
    hd = np.array([400.0, 700.0])
    fw.set_coupling(h=0.0, T_gas=550.0, T_w_dist=Tw, weights=w, h_dist=hd)
    q2 = fw.heat_input(np.array([0.1]), ctx)
    q2_ex = 400.0 * (500 - 550) * 0.012 + 700.0 * (600 - 550) * 0.008
    ok2 = check("分布 q = Σ h_f(T_w_f−T_gas)w_f", abs(q2 - q2_ex) < 1e-9,
                f"q={q2:.2f}")

    # 成对校验
    try:
        fw.set_coupling(h=1.0, T_gas=550.0, T_w_dist=Tw)   # weights 缺
        ok3 = False
    except ValueError:
        ok3 = True
    check("T_w_dist/weights 成对校验", ok3)
    return ok0 and ok1 and ok2 and ok3


# ========== 4. pyfem h 分布通路（非均匀 h 数组进表单） ==========
def test_pyfem_h_array():
    from pyfem.solver.axisym_heat import AxisymHeat
    from pyfem.materials import Material
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pyfem.examples.test_fe import rectangle_mesh, facets_on

    # 圆筒内壁 h(r,z) 沿 z 线性分布（假想关联式输出）
    r1, r2, L = 0.06, 0.12, 0.05
    m = rectangle_mesh(r1, r2, 0, L, 40, 20)
    mat = Material("k", {"k": [(200., 7.0), (800., 7.0)],
                         "E": [(200., 1e11), (800., 1e11)],
                         "nu": [(200., 0.3), (800., 0.3)],
                         "alpha": [(373.15, 9e-6), (473.15, 9e-6)],
                         "rho": [(200., 4480.), (800., 4480.)]})
    inner = facets_on(m, lambda mid: np.isclose(mid[0], r1))
    outer = facets_on(m, lambda mid: np.isclose(mid[0], r2))

    h_nodes = 200.0 + 800.0 * m.p[1] / L    # h(z): 200→1000 线性
    heat = AxisymHeat(m, mat)
    heat.add_film("inner", inner, h=h_nodes, T_gas=500.0)
    heat.add_film("outer", outer, h=300.0, T_gas=300.0)
    try:
        res = heat.solve()
        ok0 = check("h 分布求解收敛", res.converged, f"{res.iters} 步")
    except Exception as e:
        return check("h 分布求解", False, str(e)[:60])

    # 能量闭合（分布 h 两侧仍应闭合）
    Qi, Qo = res.film_heats["inner"], res.film_heats["outer"]
    ok1 = check("h 分布能量闭合", abs(Qi + Qo) < 1e-6 * abs(Qi),
                f"|ΣQ|={abs(Qi + Qo):.2e}")

    # update_film 接口
    heat.update_film("inner", h=500.0, T_gas=520.0)
    res2 = heat.solve()
    ok2 = check("update_film 后再解收敛", res2.converged)
    return ok0 and ok1 and ok2


# ========== 5. pysas 回归（含稀疏分发改动） ==========
def test_pysas_regression():
    """稀疏分发不破坏原路径 + 稀疏雅可比真的能解。"""
    import scipy.sparse as sp
    from pysas.solver.newton import damped_newton
    from pysas.datamodel.solver import NewtonOptions

    # 稠密路径: 简单 2D 非线性 (x0*x1=2, x0+x1=3) → 解 (2,1)
    # （初值避开 x0=x1 的奇异雅可比线）
    f = lambda x: np.array([x[0] * x[1] - 2.0, x[0] + x[1] - 3.0])
    j = lambda x: np.array([[x[1], x[0]], [1.0, 1.0]])
    x, rep = damped_newton(f, j, np.array([3.0, 1.5]), NewtonOptions(tol=1e-12))
    ok0 = check("稠密雅可比路径不回归", rep.converged and abs(x[0] - 2) < 1e-8)

    # 稀疏路径: 同一问题雅可比包成稀疏 → 应同样收敛且解一致
    js = lambda x: sp.csr_matrix(j(x))
    x2, rep2 = damped_newton(f, js, np.array([3.0, 1.5]), NewtonOptions(tol=1e-12))
    ok1 = check("稀疏雅可比自动分发", rep2.converged and np.allclose(x, x2, atol=1e-8))
    return ok0 and ok1


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 2 验收: cosim 关联式 / WALL_FILM / h 分布 / 回归")
    print("=" * 60)
    results = [
        test_correlations(),
        test_registration(),
        test_filmwall_physics(),
        test_pyfem_h_array(),
        test_pysas_regression(),
    ]
    n = sum(results)
    print("-" * 60)
    print(f"{n}/5 组通过")
    sys.exit(0 if n == 5 else 1)
