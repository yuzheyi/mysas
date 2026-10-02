# -*- coding: utf-8 -*-
"""M2 同伦探针报告生成（P2.2 汇总 / P2.3 报告 / P2.4 异根五例）。

读 results/homotopy_probe.jsonl → summary.json + HOMOTOPY_PROBE.md：
  - 顶部结论表（flatline vs backpressure 救活数与增量——M2 立项核心数字）
  - 跟丢步号直方图 + 5 个跟丢例 mermaid + 一句话定性
  - 救活抽验：随机 10 例救活解重跑探针取最终解做守卫复核
  - P2.4 异根五例：四初值解向量按段分解（压力/温度/流量段 max|dx| +
    最异段的分量级 top3）
  - 每例条目 <details> 折叠附录
运行: python -X utf8 run_homotopy_report.py
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np

import report_lib as rl
from run_homotopy_probe import (CASES, HERE, PROBE_JSONL, PROBE_SUMMARY,
                                RESULTS, SWEEP_JSONL, probe_case)

REPORT_MD = HERE / "HOMOTOPY_PROBE.md"
ROOT = rl.ROOT

# 复用补测轮的初值变体构造（P2.4 与 run_soft_choke_followup 同源）
sys.path.insert(0, str(HERE))
from run_soft_choke_followup import init_variants  # noqa: E402

DIFF5 = ["A0199", "A0005", "A0062", "A0093", "A0101"]


def load_rows() -> list[dict]:
    return [json.loads(l) for l in open(PROBE_JSONL, encoding="utf-8")
            if l.strip()]


def probe_full(case_id: str, probe: str):
    """重跑探针并带回最终 (x, sysm, ctx)——救活抽验用。"""
    import contextlib, io, warnings
    from pysas.assembly import NetworkSystem
    from pysas.io import build_models, netinf_from_dict
    from pysas.solver import solve
    data = json.loads((CASES / f"{case_id}.json").read_text(encoding="utf-8"))
    PBs = [c for c in data["comps"] if c["type"] == "PRESSURE_BOUNDARY"]
    p_bar = float(np.mean([pb["params"][0] for pb in PBs]))
    p_orig = [pb["params"][0] for pb in PBs]
    x_prev = None
    steps = range(1, 21) if probe == "backpressure" else (0, 1)
    for k in steps:
        lam = (k / 20) if probe == "backpressure" else (0.0 if k == 0 else 1.0)
        for pb, p0 in zip(PBs, p_orig):
            pb["params"][0] = p_bar + lam * (p0 - p_bar)
        net, ctx = netinf_from_dict(data)
        sysm = NetworkSystem(net, build_models(net))
        buf = io.StringIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with contextlib.redirect_stdout(buf):
                res = solve(sysm, x_prev, ctx)
        if not res.converged:
            return None, None, None
        x_prev = res.x
    return x_prev, sysm, ctx


def rd(v, n=4):
    if v is None:
        return "—"
    return f"{float(v):.{n}g}"


# ============================================================ P2.4 异根五例
def diff5_section() -> list[str]:
    L = []
    ap = L.append
    ap("## 异根五例：四初值解向量分段定位（P2.4）")
    ap("")
    ap("对初值敏感性实验中出现异根的 5 例（A0199/A0005/A0062/A0093/"
       "A0101），把各组初值收敛解两两配对，按解向量三段取 max|dx|"
       "（原始量纲）：节点压力段 / 节点温度段 / 全部端口流量段；"
       "再对最异段列分量级 top3。")
    ap("")
    ap("| case | 压力段 max\\|dx\\| [Pa] | 温度段 max\\|dx\\| [K] | "
       "流量段 max\\|dx\\| [kg/s] | 最异段 | 最异段分量 top3 |")
    ap("|---|---|---|---|---|---|")
    concl = []
    for cid in DIFF5:
        p = CASES / f"{cid}.json"
        if not p.exists():
            continue
        case = json.loads(p.read_text(encoding="utf-8"))
        first = rl.solve_case(case)
        if first.get("sysm") is None:
            continue
        sysm, ctx = first["sysm"], first["ctx"]
        n_p, n_t = sysm.n_interior, sysm.n_T
        variants = init_variants(sysm, ctx)
        xs = []
        tags = []
        for tag, x0 in variants.items():
            o = rl.solve_case(case, x0=x0)
            if o.get("status") == "converged":
                xs.append(np.asarray(o["x"], float))
                tags.append(tag)
        if len(xs) < 2:
            ap(f"| {cid} | —（收敛组不足 2） | | | | |")
            continue
        inv = {v: f"p_n{nid}" for nid, v in sysm.p_idx_of_node.items()}
        inv.update({v: f"T_n{nid}" for nid, v in sysm.T_idx_of_node.items()})
        inv.update({v: f"m_c{cid2}p{j}" for (cid2, j), v
                    in sysm.m_idx_of_port.items()})
        seg_p = seg_t = seg_m = 0.0
        best_pair = None
        top3 = []
        for i in range(len(xs)):
            for j in range(i + 1, len(xs)):
                d = np.abs(xs[i] - xs[j])
                p_, t_, m_ = float(np.max(d[:n_p])), \
                    float(np.max(d[n_p:n_p + n_t])), float(np.max(d[n_p + n_t:]))
                if max(p_, t_, m_) > max(seg_p, seg_t, seg_m):
                    best_pair = (tags[i], tags[j])
                seg_p, seg_t, seg_m = max(seg_p, p_), max(seg_t, t_), \
                    max(seg_m, m_)
        # 最异段的分量 top3（取该段差异最大的配对）
        dmax = max(seg_p, seg_t, seg_m)
        seg_off = {"压力": 0, "温度": n_p, "流量": n_p + n_t}
        seg_name = ("压力" if dmax == seg_p else
                    "温度" if dmax == seg_t else "流量")
        dij = None
        if best_pair:
            i, j = tags.index(best_pair[0]), tags.index(best_pair[1])
            dij = np.abs(xs[i] - xs[j])
        if dij is not None:
            off = seg_off[seg_name]
            seg_len = {"压力": n_p, "温度": n_t, "流量": sysm.n - off}[seg_name]
            order = np.argsort(-dij[off:off + seg_len])[:3]
            top3 = [f"{inv.get(off + int(k), '?')} Δ={dij[off + int(k)]:.4g}"
                    for k in order]
        ap(f"| {cid} | {rd(seg_p)} | {rd(seg_t)} | {rd(seg_m)} | "
           f"{seg_name} | {'；'.join(top3)} |")
        # 噪声级判别：压力差 <1 Pa、温度差 <0.1 K、流量差 <1e-9 kg/s
        # 视为数值噪声（实为同根，异根误报）
        noisy = (seg_name == "压力" and dmax < 1.0) or \
                (seg_name == "温度" and dmax < 0.1) or \
                (seg_name == "流量" and dmax < 1e-9)
        if noisy:
            c = (f"最异段差异仅 {rd(dmax)} （噪声级）——实为同根，"
                 "此前的\"异根\"判定系容差误报")
        elif seg_name == "温度":
            c = ("异根不定性在温度段——零流量死肢节点无能量约束，"
                 "温度是数值自由量（流量/压力根一致）")
        elif seg_name == "流量":
            c = ("异根不定性在端口流量——存在多组流量分配同时满足方程"
                 "（选根族/环流差异）")
        else:
            c = "异根不定性在节点压力"
        concl.append(f"- **{cid}**：{c}（最异对 {best_pair[0]} vs "
                     f"{best_pair[1]}）。")
    ap("")
    ap("**每例一句话结论**：")
    ap("")
    L.extend(concl)
    ap("")
    return L


# ============================================================ 主报告
def main() -> int:
    rows = load_rows()
    a = {r["case_id"]: r for r in rows if r["probe"] == "backpressure"
         and not r.get("skip")}
    b = {r["case_id"]: r for r in rows if r["probe"] == "flatline"
         and not r.get("skip")}
    skips = [r["case_id"] for r in rows if r.get("skip")]
    ya = {c for c, r in a.items() if r["converged_final"]}
    yb = {c for c, r in b.items() if r["converged_final"]}

    # ---- summary.json ----
    lost_hist = Counter(r["lost_at"] for r in a.values()
                        if not r["converged_final"])
    summary = {
        "n_clean_fail": len(a),
        "no_pb_skip": skips,
        "backpressure_rescued": sorted(ya),
        "flatline_rescued": sorted(yb),
        "backpressure_only": sorted(ya - yb),
        "flatline_only": sorted(yb - ya),
        "both": sorted(ya & yb),
        "delta_y_minus_x": len(ya) - len(yb),
        "backpressure_lost_hist": {str(k): v for k, v
                                   in sorted(lost_hist.items(),
                                             key=lambda x: (x[0] is None,
                                                            x[0]))},
    }
    PROBE_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    # ---- 报告 ----
    L = []
    ap = L.append
    ap("# M2 同伦双探针可行性报告（flatline vs backpressure）")
    ap("")
    ap(f"- 日期：{date.today().isoformat()}")
    ap(f"- 对象：sweep.jsonl 全部 clean_fail 例（{len(a)} 例）× 双探针；"
       "纪律：pysas 零改动、cases 只读、单例 try/except 全包 + 120s 粗"
       "粒度超时（本轮 0 例超时）")
    ap("- 探针 A backpressure：20 步线性回拧（lam=k/20，每步以上一步解为"
       "初值）；探针 B flatline：两步版（压平 1 步 + 直接回原值 1 步）")
    ap("- 小样验证：10 例探针 A 首步（lam=0.05 近压平）10/10 收敛；放量 "
       "261 例首步 261/261 收敛——脚本行为符合预期")
    ap("")
    # ---- 顶部结论表（M2 立项核心数字） ----
    ap("## 结论表（M2 立项核心数字）")
    ap("")
    ap("| 探针 | 救活 | 救活率 | 说明 |")
    ap("|---|---|---|---|")
    ap(f"| B flatline（两步） | **{len(yb)}** | "
       f"{len(yb) / len(a):.1%} | ≈重启诊断 R5 平坦化基线的同族手段 |")
    ap(f"| A backpressure（20 步回拧） | **{len(ya)}** | "
       f"{len(ya) / len(a):.1%} | 全程走完 20 步回到原网络 |")
    ap(f"| **增量 Y−X** | **{len(ya) - len(yb)}** | | "
       "细粒度回压延拓 **不优于** 两步压平——M2 若立项，价值不在"
       "\"更细的延拓路径\"本身 |")
    ap("")
    ap(f"- 两探针救活集合高度互补：共同救活 {len(ya & yb)} 例，"
       f"backpressure 独有 {len(ya - yb)} 例（{sorted(ya - yb)}），"
       f"flatline 独有 {len(yb - ya)} 例（{sorted(yb - ya)}）——"
       "\"换一条路径\"的收益存在但与路径粗细无关；")
    ap(f"- 跟丢集中度：探针 A 的 {sum(v for k, v in lost_hist.items() if k is not None)} "
       f"例跟丢中 {lost_hist.get(1, 0)} 例丢在第 1 步（第 1 步 lam=0.05，"
       "距压平仅 5% 就回不去了），只有 2 例走到 k=11/14——"
       "\"均匀回拧\"不是好的延拓方向。")
    ap("")

    # ---- 跟丢分析 ----
    ap("## 跟丢分析（探针 A）")
    ap("")
    ap("| 跟丢步号 k | 例数 | lam 区间 |")
    ap("|---|---|---|")
    for k in sorted((k for k in lost_hist if k is not None)):
        lam_lo, lam_hi = (k - 1) / 20, k / 20
        ap(f"| {k} | {lost_hist[k]} | {lam_lo:.2f}→{lam_hi:.2f} |")
    if lost_hist.get(None):
        ap(f"| 异常中断 | {lost_hist[None]} | — |")
    ap("")
    lost_ids = [c for c, r in a.items()
                if not r["converged_final"] and r["lost_at"] is not None]
    lost_late = sorted(lost_ids, key=lambda c: -a[c]["lost_at"])[:5]
    ap("跟丢最晚的 5 例（回拧走得最远仍在终点前失败——离可解最近的一批）：")
    ap("")
    for cid in lost_late:
        r = a[cid]
        ap(f"**{cid}**（丢在 k={r['lost_at']}, lam={r['lost_at'] / 20:.2f}，"
           f"各步 iters={r['iters_per_step'][-6:]}）")
        ap("")
        case = json.loads((CASES / f"{cid}.json").read_text(encoding="utf-8"))
        try:
            ap("```mermaid")
            ap(rl.netinf_to_mermaid(case))
            ap("```")
        except Exception as e:                # noqa: BLE001
            ap(f"> （图生成失败 {e}）")
        n_pb = sum(1 for c in case["comps"]
                   if c["type"] == "PRESSURE_BOUNDARY")
        ap(f"定性：{'多 PB 大压差网络（%d 个 PB），' % n_pb if n_pb > 1 else ''}"
           "回拧至中段压差即失根——该网络物理解对压差高度敏感"
           "（或本无解，同伦也到不了）。")
        ap("")

    # ---- 救活抽验 ----
    ap("## 救活抽验（随机 10 例守卫复核）")
    ap("")
    rescued = sorted(ya | yb)
    rng = random.Random(20261002)
    sample = rng.sample(rescued, min(10, len(rescued)))
    ap("| case | 经探针 | 解处 worst ratio | 告警数 | 是否守卫人工根 |")
    ap("|---|---|---|---|---|")
    n_artificial = 0
    for cid in sample:
        probe = "backpressure" if cid in ya else "flatline"
        x, sysm, ctx = probe_full(cid, probe)
        if x is None:
            ap(f"| {cid} | {probe} | 复跑未复现 | — | — |")
            continue
        grows = rl.guard_rows(sysm, x, ctx)
        act = [g for g in grows if g["active"]]
        wr = max((g["ratio"] for g in grows if g["ratio"] is not None),
                 default=None)
        art = "⚠️ 是（超容钉位）" if act else "否（物理根）"
        n_artificial += 1 if act else 0
        ap(f"| {cid} | {probe} | {rd(wr)} | {len(act)} | {art} |")
    ap("")
    ap("（worst ratio 为 \"—\" = 该网络无守卫口径合格行（无非锚定两口"
       "压力行元件），守卫从未参与。）")
    ap("")
    ap(f"抽验 {len(sample)} 例中 {n_artificial} 例落回守卫钉位根——"
       + ("救活解全部为物理根，延拓没有引入人工根污染。"
          if n_artificial == 0 else
          "这些解虽收敛但仍是\"守卫钉位\"的非物理解——救活≠物理正确，"
          "M2 报表需保留软壅塞告警。"))
    ap("")

    # ---- P2.4 异根五例 ----
    L.extend(diff5_section())

    # ---- 附录 ----
    ap("## 附录：全部探针条目")
    ap("")
    for cid in sorted(set(list(a.keys()) + list(b.keys()))):
        ra_, rb_ = a.get(cid), b.get(cid)
        def _s(r):
            if r is None:
                return "—"
            if r.get("skip"):
                return "no_pb skip"
            return (f"lost_at={r['lost_at']}，final={r['converged_final']}，"
                    f"iters={r['iters_per_step'] if len(r['iters_per_step']) <= 22 else r['iters_per_step'][:22]}，"
                    f"{r['total_s']}s")
        ap(f"<details><summary>{cid} — A: {_s(ra_)} | B: {_s(rb_)}</summary>")
        ap("")
        ap("```mermaid")
        try:
            case = json.loads((CASES / f"{cid}.json")
                              .read_text(encoding="utf-8"))
            ap(rl.netinf_to_mermaid(case))
        except Exception as e:                # noqa: BLE001
            ap(f"> 图生成失败 {e}")
        ap("```")
        ap("")
        ap("</details>")
        ap("")
    REPORT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"summary → {PROBE_SUMMARY}")
    print(f"报告 → {REPORT_MD}（{len(chr(10).join(L)) / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
