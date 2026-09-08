"""test — netinf.json 管网算例验证脚本。

验证链（每步打印核对）:
  ① 读入：拓扑/边界/物性与 JSON 逐项对照
  ② 组装：方程组规模 n = Nint + M
  ③ 求解：scipy root + 物理自检
     两管全同（对称）→ 中间节点 p0 应 = (3+1)/2 = 2 bar
"""
import sys

import numpy as np
from scipy.optimize import root

sys.path.insert(0, r"e:\mywork\programDesign\mysas\yzysas")

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import build_models, load_netinf    # noqa: E402


def main():
    # ---------- ① 读入 ----------
    net, ctx = load_netinf(r"e:\mywork\programDesign\mysas\yzysas\pysas\netinf.json")
    print("① 拓扑读入")
    print(f"   节点 {len(net.nodes)} 个"
          f"（边界 {net.n_boundary} / 内部 {net.n_interior}），"
          f"元件 {len(net.comps)} 个")
    for n in net.nodes:
        tag = "边界" if n.is_boundary else "内部"
        extra = f" p0={n.total_pressure:.0f}Pa T0={n.total_temperature:.0f}K" \
            if n.is_boundary else ""
        print(f"   node {n.node_id} [{tag}]{extra}")
    for c in net.comps:
        links = " → ".join(f"n{p.node_id}" for p in c.ports)
        print(f"   comp {c.comp_id} {c.elem_type.name} [{links}]"
              f" params={c.params}")

    # ---------- ② 组装 ----------
    models = build_models(net)
    sys_ = NetworkSystem(net, models)
    print(f"\n② 方程组规模 n = {sys_.n}"
          f"（{sys_.n_interior} 内部节点 + {sys_.n_m} 端口）")

    # ---------- ③ 求解 ----------
    # 初值：内部压力取边界平均，流量猜 0.1 量级（流入为正）
    p_mid0 = 0.5 * (net.nodes[0].total_pressure + net.nodes[2].total_pressure)
    m0 = 0.1
    x0 = np.array([p_mid0, -m0, m0, -m0, m0])

    sol = root(sys_.residual, x0, args=(ctx,), method="lm")
    print(f"\n③ 求解  收敛={sol.success}  max|F|={np.abs(sol.fun).max():.2e}")
    X = sol.x[0]
    m = sol.x[1:]
    print(f"   中间节点 p0 = {X:.1f} Pa")
    print(f"   各口流量    = {np.round(m, 6)}（ṁ>0=流入组件）")
    print(f"   管网流量    = {m[0]:.6f} kg/s（管0进口，应>0=顺压流动）")

    # 手算核对：单管 Δp=1bar 的 Darcy 流量（f≈0.02 量级）
    rho = p_mid0 / (ctx.gas_R * ctx.T0_default)
    f = 0.02
    A, D, L = 3.1416e-4, 0.02, 0.5
    m_hand = A * np.sqrt(2 * rho * 1.0e5 * D / (f * L))
    print(f"   手算参考(f=0.02) = {m_hand:.6f} kg/s"
          f"（精确 f 由 Re 定，量级应一致）")


if __name__ == "__main__":
    main()
