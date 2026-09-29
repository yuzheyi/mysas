"""test_fe — Phase 1 验收门：三个解析解验证（rtol < 1e-3）。

V-FE1 空心圆筒 + 双侧 Robin: 对数温度场 + 边界热流闭合
V-FE2 均匀温升自由圆盘:    热应力 ≡ 0（初应变自平衡）
V-FE3 等厚旋转实心圆盘:    经典解析应力 σr/σθ

运行: python pyfem/test_fe.py（yzysas 目录下）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # yzysas/

from pyfem.fe.materials import Material, TC11  # noqa: E402


# ---------- 网格工具：矩形剖面 (r1..r2) × (0..L) 结构化三角网 ----------
def rectangle_mesh(r1, r2, z0, z1, nr, nz):
    """结构化三角网（skfem MeshTri），返回 (mesh, 边界选择器)。

    边界用坐标谓词选 facet（不依赖 named boundaries）:
      inner: r = r1,  outer: r = r2
    """
    from skfem import MeshTri

    r = np.linspace(r1, r2, nr + 1)
    z = np.linspace(z0, z1, nz + 1)
    R, Z = np.meshgrid(r, z, indexing="ij")
    p = np.vstack([R.ravel(), Z.ravel()])
    # 结构化三角化（每矩形两三角）
    t = []
    for i in range(nr):
        for j in range(nz):
            n00 = i * (nz + 1) + j
            n10 = (i + 1) * (nz + 1) + j
            n01 = n00 + 1
            n11 = n10 + 1
            t.append([n00, n10, n11])
            t.append([n00, n11, n01])
    t = np.array(t, dtype=np.int32).T
    mesh = MeshTri(p, t)
    return mesh


def facets_on(mesh, pred):
    """坐标谓词选边界 facet（pred(mid) -> bool 数组，mid (2, nbfacets) 中点）。"""
    bfacets = mesh.boundary_facets()
    f = mesh.facets                     # (2, nfacets) 节点索引
    p = mesh.p
    mids = 0.5 * (p[:, f[0, bfacets]] + p[:, f[1, bfacets]])
    return bfacets[pred(mids)]


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name} {detail}")
    return ok


# ================= V-FE1: 空心圆筒双侧 Robin =================
def test_vfe1():
    from pyfem.fe.axisym_heat import AxisymHeat

    r1, r2 = 0.05, 0.15      # m
    L = 0.10                 # m（轴向长度，有限筒）
    k_const = 10.0           # W/mK（常数材料，解析解可比）
    h_in, T_in = 500.0, 700.0
    h_out, T_out = 200.0, 350.0

    mat = Material("const", {
        "k":     [(200.0, k_const), (800.0, k_const)],
        "E":     [(200.0, 123e9), (800.0, 123e9)],
        "nu":    [(200.0, 0.33), (800.0, 0.33)],
        "alpha": [(373.15, 9.3e-6), (473.15, 9.3e-6)],
        "rho":   [(200.0, 4480.0), (800.0, 4480.0)],
    })

    mesh = rectangle_mesh(r1, r2, 0.0, L, nr=24, nz=16)
    inner = facets_on(mesh, lambda x: np.isclose(x[0], r1))
    outer = facets_on(mesh, lambda x: np.isclose(x[0], r2))

    heat = AxisymHeat(mesh, material=mat)
    heat.add_film("inner", inner, h=h_in, T_gas=T_in)
    heat.add_film("outer", outer, h=h_out, T_gas=T_out)
    res = heat.solve()

    # 解析解: 单位长度热流（串联热阻: 内对流+导热+外对流）
    R_conv_in = 1.0 / (h_in * 2 * np.pi * r1)
    R_cond = np.log(r2 / r1) / (2 * np.pi * k_const)
    R_conv_out = 1.0 / (h_out * 2 * np.pi * r2)
    qlen = (T_in - T_out) / (R_conv_in + R_cond + R_conv_out)   # W/m
    Q_exact = qlen * L                                          # W

    # 解析温度场（r 方向对数分布）
    T_r = lambda r: T_in - qlen * (R_conv_in + np.log(r / r1) / (2 * np.pi * k_const))

    ok1 = check("V-FE1 收敛", res.converged, f"iters={res.iters}")
    ok2 = check("V-FE1 内壁热流", abs(res.film_heats["inner"] / Q_exact - 1) < 1e-3,
                f"FE={res.film_heats['inner']:.4f} exact={Q_exact:.4f} W")
    ok3 = check("V-FE1 外壁热流", abs(res.film_heats["outer"] / (-Q_exact) - 1) < 1e-3,
                f"FE={res.film_heats['outer']:.4f} exact={-Q_exact:.4f} W")
    # 温度场抽样（用节点真实坐标比对——内壁梯度陡，探测半径≠节点半径）
    p_mesh = mesh.p
    ok4 = True
    for r_probe in (0.06, 0.08, 0.10, 0.13):
        d = np.hypot(p_mesh[0] - r_probe, p_mesh[1] - L / 2)
        n = int(np.argmin(d))
        r_node = p_mesh[0, n]
        T_fe = res.T[n]
        T_ex = T_r(r_node)
        rel = abs(T_fe - T_ex) / (T_in - T_out)
        good = rel < 1e-3
        ok4 &= good
        print(f"       r_node={r_node:.4f}  T_FE={T_fe:.3f}  T_exact={T_ex:.3f}  rel={rel:.2e}")
    check("V-FE1 温度场(4点)", ok4)
    return ok1 and ok2 and ok3 and ok4


# ================= V-FE2: 均匀温升自由圆盘热应力 ≡ 0 =================
def test_vfe2():
    from pyfem.fe.axisym_thermoelastic import AxisymThermoElastic

    # 等厚圆盘 r∈[0.01, 0.2]，均匀温度 T=500 K（α 常数材料）
    # 自由（仅约束刚体模态: 对称面 u_r(内孔)=0? 不——自由盘无对称面。
    # 经典处理: 约束轴向刚体平移 + 一点周向。圆盘用内孔边 u_r=0 会引入
    # 假应力。正确做法: 均匀 ΔT 无体力 → 精确解 u_r = αΔT·r，σ=0。
    # 约束最小集: 一节点 u_z=0（轴向平移）+ 一节点 u_r=0? r 向平移是
    # 轴对称允许的刚体模态吗——不是（u_r 分布场），但常数 u_r 会改变
    # εθ。安全约束: 中心区一点 (u_r,u_z)=0 附近不行（轴上 u_r≡0 天然）。
    # 简化: 盘带小中心孔，约束内孔 u_r = αΔT·r1（解析位移）+ 一点 u_z=0，
    # 检验全场应力 ≈ 0、u_r(r) = αΔT·r。
    alpha = 9.3e-6
    mat = Material("const", {
        "k":     [(200.0, 10.0), (800.0, 10.0)],
        "E":     [(200.0, 123e9), (800.0, 123e9)],
        "nu":    [(200.0, 0.33), (800.0, 0.33)],
        "alpha": [(373.15, alpha), (473.15, alpha)],
        "rho":   [(200.0, 4480.0), (800.0, 4480.0)],
    })
    r1, r2 = 0.01, 0.20
    L = 0.01
    T_uniform = 500.0
    dT = T_uniform - 293.15

    mesh = rectangle_mesh(r1, r2, 0.0, L, nr=30, nz=3)
    from pyfem.fe.axisym_thermoelastic import AxisymThermoElastic as TE
    te = TE(mesh, mat, np.full(heat_n(mesh), T_uniform))

    # 约束: 整个 z=0 面 u_z = 0。与自由膨胀 u_z = αΔT·z 在 z=0 处
    # 一致（不引入点力奇异），且消除轴对称唯一刚体模态（z 向平移）。
    # u_r 不需约束（u_r=const 非轴对称刚体模态，且有应变能非奇异）。
    # 精确解: u_r = αΔT·r，u_z = αΔT·z，σ ≡ 0。
    bottom_nodes = nodes_on(mesh, lambda x, y: np.isclose(y, 0.0))
    uz_dofs = te.basis.nodal_dofs[1, bottom_nodes]
    te.set_constraint(uz_dofs, np.zeros(len(uz_dofs)))
    res = te.solve()

    # 检验: σ 各分量 / von Mises ≈ 0（相对 E·αΔT 量级）
    E_alpha_dT = 123e9 * alpha * dT
    vm_max = np.max(res.von_mises)
    ok1 = check("V-FE2 均匀温升 σ ≈ 0", vm_max / E_alpha_dT < 1e-3,
                f"max|σv|={vm_max:.1f} Pa vs EαΔT={E_alpha_dT:.2e}")
    # 位移: u_r(r) = αΔT·r
    p = mesh.p
    ur = res.u[te.basis.nodal_dofs[0]]
    err = np.max(np.abs(ur - alpha * dT * p[0])) / (alpha * dT * r2)
    ok2 = check("V-FE2 u_r = αΔT·r", err < 1e-3, f"rel err={err:.2e}")
    return ok1 and ok2


def heat_n(mesh):
    return mesh.p.shape[1]


def nodes_on(mesh, pred):
    """坐标谓词选节点。"""
    p = mesh.p
    mask = pred(p[0], p[1])
    return np.nonzero(mask)[0]


# ================= V-FE3: 旋转圆盘应力 =================
def test_vfe3():
    from pyfem.fe.axisym_thermoelastic import AxisymThermoElastic

    # 等厚实心盘 → 用小中心孔避免轴奇异，孔半径 1mm，检验域 5mm..b
    # 解析解（等厚圆盘，内孔自由、外缘自由，ω² 体力）:
    #   实心盘: σr = (3+ν)/8 ρω²(b²−r²), σθ = ρω²/8[(3+ν)b² − (1+3ν)r²]
    #   带小孔 b/a>>1 时孔边应力 ≈ 实心盘中心应力 × 应力集中，远离孔处
    #   趋近实心盘解。验证取 r > 10a 的节点。
    nu = 0.33
    E = 123e9
    rho_m = 4480.0
    omega2 = 7.16e5          # (rad/s)²
    mat = Material("const", {
        "k":     [(200.0, 10.0), (800.0, 10.0)],
        "E":     [(200.0, E), (800.0, E)],
        "nu":    [(200.0, nu), (800.0, nu)],
        "alpha": [(373.15, 9.3e-6), (473.15, 9.3e-6)],
        "rho":   [(200.0, rho_m), (800.0, rho_m)],
    })
    a, b = 0.001, 0.20
    L = 0.01
    mesh = rectangle_mesh(a, b, 0.0, L, nr=80, nz=2)

    te = AxisymThermoElastic(mesh, mat, np.full(mesh.p.shape[1], 293.15))
    te.add_centrifugal(omega2)
    # 约束: u_z(全部底面节点)=0（对称面），内孔 u_r 自由
    # 外缘 u_r 自由——真实自由盘。轴向对称面约束 u_z 消除轴向平移+翻转。
    # u_r 的刚体... 轴对称下 u_r 场无刚体模态（u_r=const ≠ 刚体），但
    # r 向平移不存在（会破坏轴对称），故 u_z 对称面约束已够。
    bottom = nodes_on(mesh, lambda x, y: np.isclose(y, 0.0))
    uz_dofs = te.basis.nodal_dofs[1, bottom]
    te.set_constraint(uz_dofs, np.zeros(len(uz_dofs)))
    res = te.solve()

    # 解析应力
    sr = lambda r: (3 + nu) / 8 * rho_m * omega2 * (b * b - r * r)
    sth = lambda r: rho_m * omega2 / 8 * ((3 + nu) * b * b - (1 + 3 * nu) * r * r)
    # 采样远离孔的节点（r > 0.02 m）与远离外缘激波区（r < 0.19）
    p = mesh.p
    sel = (p[0] > 0.02) & (p[0] < 0.19)
    ref = np.max(np.abs(sr(p[0][sel])))
    err_r = np.max(np.abs(res.sigma["r"][sel] - sr(p[0][sel]))) / ref
    err_t = np.max(np.abs(res.sigma["theta"][sel] - sth(p[0][sel]))) / ref
    ok1 = check("V-FE3 σr", err_r < 5e-3, f"rel={err_r:.2e}")
    ok2 = check("V-FE3 σθ", err_t < 5e-3, f"rel={err_t:.2e}")
    return ok1 and ok2


def test_vfe4_gmsh_unstructured():
    """V-FE4 非结构化 gmsh 网格回归：named boundaries + 常数k 收敛。

    守护对象: meshtools.load_gmsh 通路（Physical Curve 命名 →
    boundaries 按名可用）+ 非结构化网格上 V-FE1 的数值精度。
    网格粗网固定（cl=0.006, ~200 节点）保证测试秒级。
    """
    import gmsh
    from pyfem.fe.axisym_heat import AxisymHeat
    from pyfem.fe.meshtools import load_gmsh

    r1, r2, L, k = 0.06, 0.12, 0.05, 7.0
    h1, T1, h2, T2 = 516.0, 418.7, 300.0, 288.15
    qp = 2 * np.pi * k * (T1 - T2) / (
        np.log(r2 / r1) + k / (h1 * r1) + k / (h2 * r2))
    Q_ex = qp * L

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "annulus.msh"
        gmsh.initialize()
        gmsh.model.add("vfe4")
        p = [gmsh.model.geo.addPoint(x, y, 0, 6e-3)
             for x, y in [(r1, 0), (r2, 0), (r2, L), (r1, L)]]
        ln = [gmsh.model.geo.addLine(p[i], p[(i + 1) % 4], i + 1)
              for i in range(4)]
        gmsh.model.geo.addCurveLoop(ln, 1)
        gmsh.model.geo.addPlaneSurface([1], 1)
        gmsh.model.geo.synchronize()
        gmsh.model.addPhysicalGroup(1, [ln[0]], name="bottom")
        gmsh.model.addPhysicalGroup(1, [ln[1]], name="outer")
        gmsh.model.addPhysicalGroup(1, [ln[2]], name="top")
        gmsh.model.addPhysicalGroup(1, [ln[3]], name="inner")
        gmsh.model.addPhysicalGroup(2, [1], name="solid")
        gmsh.option.setNumber("Mesh.Algorithm", 5)  # Delaunay 非结构化
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", 6e-3)
        gmsh.model.mesh.generate(2)
        gmsh.write(str(path))
        gmsh.finalize()

        m = load_gmsh(path)
        need = {"inner", "outer", "top", "bottom"}
        ok0 = check("V-FE4 named boundaries", need <= set(m.boundaries),
                    f"got {sorted(m.boundaries)}")
        mat = Material("const_k", {
            "k": [(200., k), (800., k)],
            "E": [(200., 1.1e11), (800., 1.1e11)],
            "nu": [(200., 0.3), (800., 0.3)],
            "alpha": [(373.15, 9.3e-6), (473.15, 9.3e-6)],
            "rho": [(200., 4480.), (800., 4480.)],
        })
        heat = AxisymHeat(m, mat)
        heat.add_film("inner", m.boundaries["inner"], h1, T1)
        heat.add_film("outer", m.boundaries["outer"], h2, T2)
        res = heat.solve()
        eQ = abs(res.film_heats["inner"] - Q_ex) / Q_ex
        ok1 = check("V-FE4 非结构化 Q 收敛", eQ < 5e-3, f"rel={eQ:.2e}")
        # 能量守恒: 两壁热流大小相等符号相反
        eE = abs(res.film_heats["inner"] + res.film_heats["outer"])
        ok2 = check("V-FE4 能量闭合", eE < 1e-6 * Q_ex, f"|ΣQ|={eE:.2e} W")
        return ok0 and ok1 and ok2


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 1 验收: pyfem/fe 解析解验证")
    print("=" * 60)
    import tempfile
    results = [test_vfe1(), test_vfe2(), test_vfe3(), test_vfe4_gmsh_unstructured()]
    n_pass = sum(results)
    print("-" * 60)
    print(f"{n_pass}/4 通过")
    sys.exit(0 if n_pass == 4 else 1)
