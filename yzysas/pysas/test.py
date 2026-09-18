"""test — netinf.json 管网算例验证脚本（边界元件化后，2026-09-15）。

验证链（每步打印核对）:
  ① 读入：拓扑/边界元件/物性与 JSON 逐项对照
  ② 组装：方程组规模 n = N + M（全部节点 + 端口）
  ③ 求解：scipy root + 物理自检
     两管全同（对称）：不可压直觉猜 p_mid=(3+1)/2=2 bar 是错的——
     rho 取各自上游压力 -> rho0Dp0 = rho1Dp1 -> p^2 + 2e5·p - 9e10 = 0
     -> 精确手算根 p_mid = 216227.8 Pa（可压修正，见 test_solver.py ②A）
"""
import sys

import numpy as np
from scipy.optimize import root

sys.path.insert(0, r"e:\mywork\programDesign\mysas\yzysas")

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import build_models, load_netinf    # noqa: E402


def main():
    # ---------- ① 读入 ----------
    #load_netinf有个总温ctx.boundary_T0问题后续得进行进一步商榷
    net, ctx = load_netinf(r"e:\mywork\programDesign\mysas\yzysas\pysas\netinf.json")
    print("① 拓扑读入（节点统一内部，边界 = 单口元件）")
    print(f"   节点 {len(net.nodes)} 个（全部进 x），元件 {len(net.comps)} 个")
    for n in net.nodes:
        print(f"   node {n.node_id} [内部]")
    for c in net.comps:
        links = " -> ".join(f"n{p.node_id}" for p in c.ports)
        print(f"   comp {c.comp_id} {c.elem_type.name} [{links}]"
              f" params={c.params}")

    # ---------- ② 组装 ----------
    models = build_models(net)
    sys_ = NetworkSystem(net, models)
    print(f"\n② 方程组规模 n = {sys_.n}"
          f"（{sys_.n_interior} 节点 + {sys_.n_m} 端口）")

    # ---------- ③ 求解 ----------
    # 初值：default_guess（边界节点钉 p0_spec，其余取均值，各口 ṁ=0）；
    # 五口网络 15 个流量位不再手写——x0 策略已沉淀进 solver
    from pysas.solver import default_guess, solve as pysas_solve
    x0 = default_guess(sys_, ctx)
    assert x0.size == sys_.n, f"x0 长度 {x0.size} != n {sys_.n}"

    sol = root(sys_.residual, x0, args=(ctx,), method="lm")
    print(f"\n③ 求解  收敛={sol.success}  max|F|={np.abs(sol.fun).max():.2e}")
    P = sol.x[:sys_.n_interior]
    m = sol.x[sys_.n_interior:]
    print("   节点压力 Pa:", np.round(P, 1))
    print("   各口流量（m>0=流入组件）:")
    off = 0
    for c in sorted(net.comps, key=lambda c: c.comp_id):
        for j in range(len(c.ports)):
            print(f"      c{c.comp_id}口{j}(n{c.ports[j].node_id})"
                  f"  m = {m[off]:+.6f}")
            off += 1
    # 连续性核对：每个节点各口 m 求和应为 0（下标要减去压力区偏移）
    print("   节点连续性核对（各口 m 求和，应约=0）:")
    for nid, ports in sorted(sys_.ports_on_node.items()):
        s = sum(m[sys_.m_idx_of_port[cj] - sys_.n_interior] for cj in ports)
        print(f"      n{nid} ({len(ports)}口): {s:+.2e}")

    # 自研求解器对照（同一初值）
    res = pysas_solve(sys_, x0, ctx)
    print(f"\n④ 自研求解器对照: converged={res.converged}"
          f"  iters={res.report.iters}"
          f"  与 scipy max|Δx| = {np.abs(res.x - sol.x).max():.2e}")

    # 手算核对：主管 D30、L0.5、总压降 5bar->3.55bar 量级的 Darcy 流量
    rho = 3.5e5 / (ctx.gas_R * ctx.T0_default)
    f = 0.02
    A, D, L = 7.0686e-4, 0.03, 0.5
    m_hand = A * np.sqrt(2 * rho * 1.45e5 * D / (f * L))
    print(f"   手算参考(主管 f=0.02) = {m_hand:.4f} kg/s"
          f"（精确 f 由 Re 定，量级应一致）")


if __name__ == "__main__":
    main()
