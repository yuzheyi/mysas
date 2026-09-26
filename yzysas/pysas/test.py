"""test — netinf.json 管网算例验证脚本（边界元件化后，2026-09-15）。

验证链（每步打印核对）:
  ① 读入：拓扑/边界元件/物性与 JSON 逐项对照
  ② 组装：方程组规模 n = N + M（全部节点 + 端口）
  ③ 求解：scipy root + 物理自检
     两管全同（对称）：不可压直觉猜 p_mid=(3+1)/2=2 bar 是错的——
     rho 取各自上游压力 -> rho0Dp0 = rho1Dp1 -> p^2 + 2e5·p - 9e10 = 0
     -> 精确手算根 p_mid = 216227.8 Pa（可压修正，见 test_solver.py ②A）
  ⑤ 结果输出：不同算法各写一份文件（out/ 目录），格式统一可逐行 diff
"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.optimize import root

sys.path.insert(0, r"e:\mywork\programDesign\mysas\yzysas")

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import build_models, load_netinf    # noqa: E402


OUT_DIR = Path(r"e:\mywork\programDesign\mysas\yzysas\pysas\out")


def write_result(path: Path, tag: str, sys_, ctx, x: np.ndarray,
                 converged: bool, extra: list[str] | None = None):
    """统一结果输出：节点 p/T + 各口流量 + 连续性核对 -> UTF-8 文本。

    不同算法各写一份，列格式完全一致——直接逐行 diff 即对照。
    残差在写文件时重算一次（不信任调用方传数，文件自带 max|F|）。
    """
    F = sys_.residual(x, ctx)
    lines = [
        f"pysas 算例结果  算法={tag}",
        f"时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"规模: n={sys_.n} (N={sys_.n_interior}, N_T={sys_.n_T}, M={sys_.n_m})",
        f"收敛: {converged}  max|F|={np.abs(F).max():.3e}",
    ]
    if extra:
        lines += [f"  {e}" for e in extra]
    lines.append("")
    lines.append("节点 (id, p0 Pa, T0 K)")
    for i, nid in enumerate(sys_.interior_ids):
        t_str = f"{x[sys_.T_idx_of_node[nid]]:.4f}"
        lines.append(f"  n{nid}  p0={x[i]:.4f}  T0={t_str}")
    lines.append("")
    lines.append("端口流量 (comp口, node, area m^2, mdot kg/s; m>0=流入组件)")
    for c in sorted(sys_.net.comps, key=lambda c: c.comp_id):
        for j, port in enumerate(c.ports):
            k = sys_.m_idx_of_port[(c.comp_id, j)]
            lines.append(f"  c{c.comp_id}口{j}(n{port.node_id})  "
                         f"A={port.area:.6e}  m={x[k]:+.8f}")
    lines.append("")
    lines.append("节点连续性 (nid, 口数, sum m)——应约=0")
    for nid, ports in sorted(sys_.ports_on_node.items()):
        s = sum(x[sys_.m_idx_of_port[cj]] for cj in ports)
        lines.append(f"  n{nid} ({len(ports)}口): {s:+.3e}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"   -> 已写 {path.name}")


def main():
    # ---------- ① 读入 ----------
    #load_netinf有个总温ctx.boundary_T0问题后续得进行进一步商榷
    net, ctx = load_netinf(r"e:\mywork\programDesign\mysas\yzysas\pysas\netinf_junction.json")
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

    # ftol/xtol 必须收紧：lm 默认容差把能量行的 ε 正则（~1e-8 量级）
    # 当噪声，max|F| 还剩 29.9 就报成功（H 算例同款教训，见 test_solver ③）
    sol = root(sys_.residual, x0, args=(ctx,), method="lm",
               options={"ftol": 1.0e-12, "xtol": 1.0e-12})
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

    # ---------- ③b 手写阻尼牛顿（最小实现，debug 理解迭代流程用） ----------
    # 与 solver/newton.py 同构，但原始坐标、零依赖、逐步打印主干:
    #   F(x) → 收敛判据 → 差分雅可比（逐列扰动，n+1 次残差评估）
    #   → 解 J·dx = -F → 线搜索 alpha 减半 → 接受进入下一轮
    # 真实求解器多包一层缩放（压力 Pa / 流量 kg/s 单位悬殊，原始坐标
    # cond 大）；这里为了看清楚主干先裸跑——能收敛，收敛得慢正是
    # 缩放层存在的理由。静参数向量化将来挂在 residual 内部第一行，
    # 对本循环完全透明。
    n_eval = [0]

    def F_cnt(x_):                        # 残差评估计数（看雅可比的代价）
        n_eval[0] += 1
        return sys_.residual(x_, ctx)

    def mini_newton(x0_, tol=1e-17, max_iter=30):
        x = x0_.copy()
        print("      it |   max|F|    | alpha |  max|dx|")
        for it in range(max_iter):
            F0 = F_cnt(x)
            r = np.abs(F0).max()
            if r < tol:
                print(f"      {it:2d} | {r:.3e} |  ——   | 收敛退出")
                return x, True
            J = np.empty((x.size, x.size))
            for j in range(x.size):       # 前向差分逐列扰动，可以进行并行化处理
                h = 1e-7 * max(1.0, abs(x[j]))
                xp = x.copy()             # 新数组对象，x 不被原地改
                xp[j] += h
                F_xp = F_cnt(xp)
                J[:, j] = (F_xp - F0) / h
            if it == 0:
                print(f"      cond(J) = {np.linalg.cond(J):.3e}（原始坐标）")
            dx = np.linalg.solve(J, -F0)  # 牛顿方向求解是否可以并行计算？
            alpha, ok = 1.0, False
            while alpha >= 1e-8:          # 线搜索：残差下降才接受
                r_t = np.abs(F_cnt(x + alpha * dx)).max()
                if r_t < r:
                    print(f"      {it:2d} | {r:.3e} | {alpha:.3f} | "
                          f"{np.abs(alpha * dx).max():.3e}")
                    x = x + alpha * dx
                    ok = True
                    break
                alpha *= 0.5
            if not ok:
                print(f"      {it:2d} | {r:.3e} | 触底  | 线搜索失败退出")
                return x, False
        return x, False

    print("\n③b 手写阻尼牛顿（同一初值，逐迭代打印）")
    x_mini, ok_mini = mini_newton(x0)
    print(f"   收敛={ok_mini}  残差评估共 {n_eval[0]} 次"
          f"（每轮雅可比 n+1={sys_.n + 1} 次 + 线搜索若干）")
    p1_mini = x_mini[1]
    m1_mini = x_mini[off_m + sys_.m_idx_of_port[(1, 0)] - off_m]
    print(f"   手写牛顿解: p_n1={p1_mini:.2f} Pa  "
          f"T_n1={x_mini[sys_.n_interior]:.3f} K  "
          f"管1流量={m1_mini:+.6f} kg/s")
    print(f"   与 scipy max|Δx| = {np.abs(x_mini - sol.x).max():.2e}")

    # 自研求解器对照（同一初值）
    res = pysas_solve(sys_, x0, ctx)
    print(f"\n④ 自研求解器对照: converged={res.converged}"
          f"  iters={res.report.iters}"
          f"  与 scipy max|Δx| = {np.abs(res.x - sol.x).max():.2e}")
    print(f"   自研 vs 手写牛顿 max|Δx| = "
          f"{np.abs(res.x - x_mini).max():.2e}"
          f"  （两者一致 = scipy 假收敛实锤）")

    # 手算核对：主管 D30、L0.5、总压降 5bar->3.55bar 量级的 Darcy 流量
    rho = ctx.gas.rho_from_pT(3.5e5, ctx.T0_default)   # 物性从气体类取
    f = 0.02
    A, D, L = 7.0686e-4, 0.03, 0.5
    m_hand = A * np.sqrt(2 * rho * 1.45e5 * D / (f * L))
    print(f"   手算参考(主管 f=0.02) = {m_hand:.4f} kg/s"
          f"（精确 f 由 Re 定，量级应一致）")

    # ---------- ⑤ 结果输出：不同算法各写一份 ----------
    print(f"\n⑤ 结果输出（out/ 目录，每算法一份，格式统一可 diff）")
    write_result(OUT_DIR / "result_scipy_lm.txt", "scipy-lm", sys_, ctx,
                 sol.x, bool(sol.success), extra=[f"nfev={sol.nfev}"])
    write_result(OUT_DIR / "result_mini_newton.txt", "mini-newton(原始坐标)",
                 sys_, ctx, x_mini, ok_mini,
                 extra=[f"残差评估次数={n_eval[0]}"])
    write_result(OUT_DIR / "result_pysas_solver.txt", "pysas(缩放+阻尼牛顿)",
                 sys_, ctx, res.x, res.converged,
                 extra=[f"iters={res.report.iters}",
                        f"final_residual(缩放坐标)={res.report.final_residual:.3e}"])

    # 对照文件：三算法两两 max|Δx|（收敛互证一眼可见）
    cmp_lines = [
        f"pysas 三算法对照  {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"scipy vs 自研      max|dx| = {np.abs(sol.x - res.x).max():.3e}",
        f"scipy vs 手写牛顿  max|dx| = {np.abs(sol.x - x_mini).max():.3e}",
        f"自研 vs 手写牛顿  max|dx| = {np.abs(res.x - x_mini).max():.3e}",
    ]
    (OUT_DIR / "result_compare.txt").write_text(
        "\n".join(cmp_lines) + "\n", encoding="utf-8")
    print(f"   -> 已写 result_compare.txt（两两 max|dx| 对照）")


if __name__ == "__main__":
    main()
