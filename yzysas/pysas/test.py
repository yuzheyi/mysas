"""test — netinf.json 管网算例验证脚本（边界元件化后，2026-09-15）。

验证链（每步打印核对）:
  ① 读入：拓扑/边界元件/物性与 JSON 逐项对照
  ② 组装：方程组规模 n = N + N_T + M（节点 + 温度段 + 端口）
  ③ 求解：scipy root（独立对照）+ 自研求解器（主攻）
     两管全同（对称）：不可压直觉猜 p_mid=(3+1)/2=2 bar 是错的——
     rho 取各自上游压力 -> rho0Dp0 = rho1Dp1 -> p^2 + 2e5·p - 9e10 = 0
     -> 精确手算根 p_mid = 216227.8 Pa（可压修正，见 test_solver.py ②A）
  ⑤ 结果输出：两算法各写一份（out/ 目录），格式统一可逐行 diff
（旧 ③b 手写阻尼牛顿已删 2026-09-26：教学/debug 使命完成，主攻
 solver 可靠性验证，scipy 独立对照足够）
"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.optimize import root

# __file__ 相对解析（2026-09-30）：worktree 拷贝 import 自己的代码
# （绝对路径硬编码会把 worktree 测试偷偷指回主工作区——假绿）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import build_models, load_netinf, write_result  # noqa: E402


OUT_DIR = Path(r"e:\mywork\programDesign\mysas\yzysas\pysas\out")


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

    # 需要改成网络初始化， 目前是直接调用pysas_solve，后续需要改成网络初始化
    #初始化可以选择不同的方法，现在是最粗暴的平均
    from pysas.solver import default_guess, solve as pysas_solve
    x0 = default_guess(sys_, ctx)
    assert x0.size == sys_.n, f"x0 长度 {x0.size} != n {sys_.n}"

    # ftol/xtol 必须收紧：lm 默认容差把能量行的 ε 正则（~1e-8 量级）
    # 当噪声，max|F| 还剩 29.9 就报成功（H 算例同款教训，见 test_solver ③）
    sol = root(sys_.residual, x0, args=(ctx,), method="lm",
               options={"ftol": 1.0e-7, "xtol": 1.0e-7})
    print(f"\n③ 求解  收敛={sol.success}  max|F|={np.abs(sol.fun).max():.2e}")
    P = sol.x[:sys_.n_interior]
    off_m = sys_.n_interior + sys_.n_T   # M3 后 x = [p|T|m]，流量段偏移含 T 段
    m = sol.x[off_m:]
    print("   节点压力 Pa:", np.round(P, 1))
    if sys_.n_T:
        print("   节点温度 K :", np.round(sol.x[sys_.n_interior:off_m], 2))
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
        s = sum(m[sys_.m_idx_of_port[cj] - off_m] for cj in ports)
        print(f"      n{nid} ({len(ports)}口): {s:+.2e}")

    # ---------- ④ 自研求解器（主攻方向；scipy 仅作独立对照） ----------
    # （旧 ③b 手写阻尼牛顿已删，2026-09-26：教学/debug 使命完成，
    #   主攻 solver 可靠性验证——scipy-lm 作独立对照足够）
    res = pysas_solve(sys_, x0, ctx)
    print(f"\n④ 自研求解器: converged={res.converged}"
          f"  iters={res.report.iters}")
    print(f"   与 scipy max|Δx| = {np.abs(res.x - sol.x).max():.2e}")

    # 手算核对：主管 D30、L0.5、总压降 5bar->3.55bar 量级的 Darcy 流量
    rho = ctx.gas.rho_from_pT(3.5e5, ctx.T0_default)   # 物性从气体类取
    f = 0.02
    A, D, L = 7.0686e-4, 0.03, 0.5
    m_hand = A * np.sqrt(2 * rho * 1.45e5 * D / (f * L))
    print(f"   手算参考(主管 f=0.02) = {m_hand:.4f} kg/s"
          f"（精确 f 由 Re 定，量级应一致）")

    # ---------- ⑤ 结果输出：不同算法各写一份 ----------
    print(f"\n⑤ 结果输出（out/ 目录，两算法各一份，格式统一可 diff）")
    write_result(OUT_DIR / "result_scipy_lm.txt", "scipy-lm", sys_, ctx,
                 sol.x, bool(sol.success), extra=[f"nfev={sol.nfev}"])
    write_result(OUT_DIR / "result_pysas_solver.txt", "pysas(缩放+阻尼牛顿)",
                 sys_, ctx, res.x, res.converged,
                 extra=[f"iters={res.report.iters}",
                        f"final_residual(缩放坐标)={res.report.final_residual:.3e}"])

    # 对照文件：两算法 max|Δx|（收敛互证一眼可见）
    cmp_lines = [
        f"pysas 两算法对照  {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"scipy vs 自研  max|dx| = {np.abs(sol.x - res.x).max():.3e}",
    ]
    (OUT_DIR / "result_compare.txt").write_text(
        "\n".join(cmp_lines) + "\n", encoding="utf-8")
    print(f"   -> 已写 result_compare.txt（两两 max|dx| 对照）")


if __name__ == "__main__":
    main()
