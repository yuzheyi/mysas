"""result — 求解结果统一输出（netinf 的反向件，2026-09-26）。

write_result：解向量 x → 人类可读 UTF-8 文本报告（节点 p/T + 各口
流量 + 连续性核对）。不同算法各写一份、列格式完全一致——直接逐行
diff 即对照（test.py 的三算法互证就建在这上面）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np


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
    lines.append("端口流量与静参数 (comp口, node, A, mdot, ps Pa, Ts K, rho, v m/s, Ma, choked)")
    pstates = sys_.port_states(x, ctx)          # 静参数持久化（白板重建）
    off_m = sys_.n_interior + sys_.n_T          # 流量段偏移（pstates 发号序）
    for c in sorted(sys_.net.comps, key=lambda c: c.comp_id):
        for j, port in enumerate(c.ports):
            k = sys_.m_idx_of_port[(c.comp_id, j)]
            ps_ = pstates[k - off_m]
            lines.append(
                f"  c{c.comp_id}口{j}(n{port.node_id})  A={port.area:.4e}"
                f"  m={x[k]:+.6f}  ps={ps_.static_pressure:.1f}"
                f"  Ts={ps_.static_temperature:.2f}  rho={ps_.density:.4f}"
                f"  v={ps_.velocity:.2f}  Ma={ps_.mach_number:.4f}"
                f"  {'choked' if ps_.choked else 'sub'}")
    lines.append("")
    lines.append("节点连续性 (nid, 口数, sum m)——应约=0")
    for nid, ports in sorted(sys_.ports_on_node.items()):
        s = sum(x[sys_.m_idx_of_port[cj]] for cj in ports)
        lines.append(f"  n{nid} ({len(ports)}口): {s:+.3e}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"   -> 已写 {path.name}")
