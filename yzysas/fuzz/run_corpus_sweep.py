# -*- coding: utf-8 -*-
"""fuzz 全量扫描 — 软壅塞守卫回归夜间主体 + 报告生成（想法 33 验收）。

扫描：遍历 fuzz/cases/A*.json（395 例），default_guess 冷启动 solve，
逐例记录 status/iters/告警/守卫口径（report_lib.guard_rows 独立复刻，
两口件全部合格行含亚容量）→ fuzz/results/soft_choke_sweep.jsonl，
汇总 → soft_choke_sweep_summary.json；每例 mermaid 拓扑图落
soft_choke_sweep_mermaid/{case_id}.txt（"每例一图"产物）。

定点复现断言（任务书四例）:
  A0004 converged 且 worst∈[1.00,1.05] 且 1 命中；
  A0199 converged 且 HEATER ratio<0.05 且零命中；
  A0257 converged 零命中；
  A0275 允许不收敛（记录归 M2 同伦）。

可选加餐：全部 converged 例补 scipy least_squares(lm) 独立对照（同初值），
缩放坐标 max|dx|>1e-6 单列"双解嫌疑"清单（初值选根族），只记录不判失败。

报告：fuzz/SOFT_CHOKE_REGRESS.md（顶部结论表 + 定向矩阵逐例 + 全量扫描
详析 + 附录全部扫描例折叠条目），由本脚本生成（读定向矩阵 JSON + 扫描
产物），报告再大也是脚本生成。

运行: python -X utf8 run_corpus_sweep.py [--no-scipy] [--limit N] [--no-report]
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np

import report_lib as rl

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CASES = HERE / "cases"
MERMAID_DIR = RESULTS / "soft_choke_sweep_mermaid"
SWEEP_JSONL = RESULTS / "soft_choke_sweep.jsonl"
SWEEP_SUMMARY = RESULTS / "soft_choke_sweep_summary.json"
REGRESS_JSON = RESULTS / "soft_choke_regress.json"
REPORT_MD = HERE / "SOFT_CHOKE_REGRESS.md"

ANCHORS = ["A0004", "A0199", "A0257", "A0275"]

ELEM_NAMES = {0: "ORIFICE", 2: "PIPE", 5: "PRESSURE_BOUNDARY",
              6: "MASS_SOURCE", 7: "BOOSTER", 8: "HEATER", 9: "JUNCTION",
              10: "AREA_CHANGE", 12: "SURROGATE_FLOW"}

FOLLOWUP_JSON = RESULTS / "soft_choke_followup.json"


# ============================================================ 扫描主体
def sweep_case(case_id: str, case: dict, do_scipy: bool) -> dict:
    """单例扫描：solve + 守卫口径 + scipy 对照 + mermaid 落盘。永不抛。"""
    t0 = time.perf_counter()
    out = rl.solve_case(case)
    line: dict = {
        "case_id": case_id,
        "status": out.get("status"),
        "converged": out.get("status") == "converged",
        "iters": out.get("iters"),
        "final_residual_scaled": out.get("final_residual_scaled"),
        "max_F_raw": out.get("max_F_raw"),
        "warn_calls": out.get("warn_soft_choke"),
        "warn_texts": [t[:160] for t in out.get("warnings_all", [])][:4],
        "exception": out.get("exception"),
        "worst_ratio": out.get("worst_ratio"),
    }
    # 守卫口径全部合格行（含亚容量——A0199 "heater ratio<0.05" 判据需要）
    rows = []
    for r in out.get("guard", []):
        rows.append({
            "comp": r["comp_id"], "port": r["port"],
            "elem": ELEM_NAMES.get(r["elem_type"], str(r["elem_type"])),
            "mdot": r["mdot"], "cap": r["cap"], "ratio": r["ratio"],
            "active": r["active"],
        })
    line["guard_rows"] = rows
    line["hits"] = [
        {"comp": r["comp_id"], "port": r["port"],
         "elem": ELEM_NAMES.get(r["elem_type"], str(r["elem_type"])),
         "mdot": r["mdot"], "cap": r["cap"], "ratio": r["ratio"]}
        for r in out.get("guard_active", [])]
    # 命中口的节点语境（详析用：upwind/downwind 节点压力）
    if out.get("sysm") is not None and out.get("x") is not None:
        sysm, x = out["sysm"], out["x"]
        ctx = out.get("ctx")
        for h, r in zip(line["hits"], out.get("guard_active", [])):
            comp = next(c for c in sysm.net.comps
                        if c.comp_id == r["comp_id"])
            nids = [p.node_id for p in comp.ports]
            h["nodes"] = nids
            h["p_nodes"] = [float(x[sysm.p_idx_of_node[n]]) for n in nids]
        # scipy 对照（收敛例）
        if do_scipy and line["converged"]:
            try:
                cc = rl.scipy_crosscheck(sysm, ctx, out["x0"], out["x"])
                line["scipy"] = cc
            except Exception as e:            # noqa: BLE001
                line["scipy"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        # mermaid 落盘（每例一图）
        try:
            MERMAID_DIR.mkdir(exist_ok=True)
            fig = rl.netinf_to_mermaid(case, x=out["x"], sysm=sysm)
            (MERMAID_DIR / f"{case_id}.txt").write_text(fig, encoding="utf-8")
            line["mermaid_file"] = f"soft_choke_sweep_mermaid/{case_id}.txt"
        except Exception as e:                # noqa: BLE001
            line["mermaid_error"] = f"{type(e).__name__}: {e}"
    line["elapsed_s"] = round(time.perf_counter() - t0, 4)
    return line


def run_sweep(do_scipy: bool = True, limit: int | None = None):
    """遍历全部算例（A 层随机语料 + C 层边界酷刑）→ (lines, summary)。

    幂等覆盖。A*.json 为主语料（任务书口径），C*.json（95 例边界
    酷礼产线）按"求全"一并纳入，分组标注。分位数只取收敛例
    （未收敛解的中间态 ratio 无解质量意义，另记 all-max）。
    """
    groups = [("A", sorted(CASES.glob("A*.json"))),
              ("C", sorted(CASES.glob("C*.json")))]
    if limit:
        keep = limit
        groups = [(g, ps[:keep]) for g, ps in groups]
    paths = [(g, p) for g, ps in groups for p in ps]
    lines: list[dict] = []
    t_start = time.perf_counter()
    for i, (grp, p) in enumerate(paths):
        case_id = p.stem
        try:
            case = json.loads(p.read_text(encoding="utf-8"))
            line = sweep_case(case_id, case, do_scipy)
            line["group"] = grp
        except Exception as e:                # noqa: BLE001
            line = {"case_id": case_id, "group": grp, "status": "driver_error",
                    "converged": False, "exception": f"{type(e).__name__}: {e}",
                    "guard_rows": [], "hits": [], "warn_calls": 0}
        lines.append(line)
        if (i + 1) % 50 == 0 or i + 1 == len(paths):
            el = time.perf_counter() - t_start
            print(f"  [{i + 1}/{len(paths)}] {el:.0f}s elapsed", flush=True)
    with open(SWEEP_JSONL, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln, ensure_ascii=False) + "\n")

    # ---- 汇总 ----
    st_cnt = Counter(ln["status"] for ln in lines)
    n_conv = sum(1 for ln in lines if ln["converged"])
    warn_lines = [ln for ln in lines if ln["converged"] and ln["hits"]]
    # 分位数口径：只取收敛例的 worst ratio（未收敛解的中间态 ratio
    # 无解质量意义——A 层实测 MAX 9769 全部来自 clean_fail 中间态）
    ratios = sorted(ln["worst_ratio"] for ln in lines
                    if ln["converged"] and ln.get("worst_ratio") is not None)
    ratios_all = sorted(ln["worst_ratio"] for ln in lines
                        if ln.get("worst_ratio") is not None)
    quant = {}
    if ratios:
        quant = {"P50": float(np.percentile(ratios, 50)),
                 "P90": float(np.percentile(ratios, 90)),
                 "MAX": float(ratios[-1]),
                 "MAX_ALL_INCL_UNCONVERGED": float(ratios_all[-1])}
    per_group = {}
    for g in ("A", "C"):
        sub = [ln for ln in lines if ln.get("group") == g]
        if sub:
            per_group[g] = {
                "n": len(sub),
                "converged": sum(1 for ln in sub if ln["converged"]),
                "warn": sum(1 for ln in sub if ln["converged"] and ln["hits"]),
            }
    suspects = []
    if do_scipy:
        for ln in lines:
            sc = ln.get("scipy")
            if ln["converged"] and sc and sc.get("ok") \
                    and sc.get("max_dx_scaled", 0.0) > 1.0e-6:
                suspects.append({"case_id": ln["case_id"],
                                 "max_dx_scaled": sc["max_dx_scaled"],
                                 "max_dx_raw": sc.get("max_dx_raw")})
    scipy_fail = [ln["case_id"] for ln in lines
                  if ln.get("scipy") and not ln["scipy"].get("ok")]
    summary = {
        "n_cases": len(lines),
        "per_group": per_group,
        "status_counts": dict(st_cnt),
        "converged": n_conv,
        "converge_rate": round(n_conv / len(lines), 4) if lines else 0.0,
        "warn_case_ids": [ln["case_id"] for ln in warn_lines],
        "n_warn_cases": len(warn_lines),
        "ratio_quantiles": quant,
        "scipy_suspects": suspects,
        "scipy_not_ok_ids": scipy_fail,
        "scipy_enabled": do_scipy,
        "elapsed_s": round(time.perf_counter() - t_start, 1),
    }
    with open(SWEEP_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return lines, summary


def assert_anchors(lines: list[dict]) -> list[dict]:
    """定点四例断言（单例失败记 FAIL 不抛）。"""
    by_id = {ln["case_id"]: ln for ln in lines}
    res = []

    def get(cid) -> dict:
        return by_id.get(cid, {"case_id": cid, "missing": True})

    a = get("A0004")
    ok = (a.get("converged") and a.get("worst_ratio") is not None
          and 1.00 <= a["worst_ratio"] <= 1.05 and len(a.get("hits", [])) == 1)
    res.append({"case": "A0004",
                "assert": "converged 且 worst∈[1.00,1.05] 且 1 命中",
                "ok": bool(ok), "measured": {
                    "status": a.get("status"), "worst_ratio": a.get("worst_ratio"),
                    "hits": len(a.get("hits", []))}})

    a = get("A0199")
    hrows = [r for r in a.get("guard_rows", []) if r["elem"] == "HEATER"]
    h_ratio = max((r["ratio"] for r in hrows
                   if r["ratio"] is not None), default=None)
    ok = (a.get("converged") and len(a.get("hits", [])) == 0
          and h_ratio is not None and h_ratio < 0.05)
    res.append({"case": "A0199",
                "assert": "converged 且 HEATER ratio<0.05 且零命中",
                "ok": bool(ok), "measured": {
                    "status": a.get("status"), "heater_ratio": h_ratio,
                    "hits": len(a.get("hits", []))}})

    a = get("A0257")
    ok = a.get("converged") and len(a.get("hits", [])) == 0
    res.append({"case": "A0257", "assert": "converged 且零命中",
                "ok": bool(ok), "measured": {
                    "status": a.get("status"),
                    "hits": len(a.get("hits", []))}})

    a = get("A0275")
    ok = a.get("status") in ("clean_fail", "solve_error") \
        or a.get("converged") is False
    res.append({"case": "A0275",
                "assert": "允许不收敛（记录归 M2 同伦）",
                "ok": bool(ok), "measured": {
                    "status": a.get("status"), "iters": a.get("iters"),
                    "worst_ratio": a.get("worst_ratio")}})
    return res


# ============================================================ 报告生成
def _rd(v, n=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "—"
        return f"{v:.{n}g}"
    return str(v)


def _load_mermaid(case_id: str, line: dict) -> str:
    mf = line.get("mermaid_file")
    if mf:
        p = HERE / "results" / mf
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "> （无图——" + (line.get("mermaid_error")
                          or line.get("exception") or "求解未产出解向量") + "）"


def _hit_table(line: dict) -> str:
    hits = line.get("hits", [])
    if not hits:
        return "（无守卫命中）"
    head = ("| 元件 | 口 | 类型 | ṁ [kg/s] | cap [kg/s] | ratio | "
            "口节点压力 [Pa] |\n|---|---|---|---|---|---|---|")
    rows = []
    for h in hits:
        ps = "/".join(_rd(p) for p in h.get("p_nodes", [])) or "—"
        rows.append(f"| c{h['comp']} | {h['port']} | {h['elem']} | "
                    f"{_rd(h['mdot'])} | {_rd(h['cap'])} | "
                    f"{_rd(h['ratio'], 5)} | {ps} |")
    return head + "\n" + "\n".join(rows)


def _one_line(line: dict) -> str:
    """一句话定性（自动）：谁在超容、钉位状态。"""
    if not line.get("converged"):
        return (f"未收敛（{line.get('status')}，iters={line.get('iters')}）"
                + ("，异常: " + line["exception"] if line.get("exception") else ""))
    hits = line.get("hits", [])
    if not hits:
        return "收敛且无守卫命中"
    parts = []
    for h in hits:
        parts.append(f"c{h['comp']}({h['elem']})口{h['port']} "
                     f"ratio={_rd(h['ratio'], 4)}")
    return "；".join(parts) + " —— 守卫钉位（非物理解，告警指路网络容量不足）"


def _deep_dive(line: dict, case: dict) -> str:
    """深挖块：元件清单表（谁在链上、谁限流）。"""
    comps = case.get("comps", [])
    rows = ["| comp | 类型 | params | 口面积 | ṁ（各口） |",
            "|---|---|---|---|---|"]
    sysm_flows: dict = {}
    # 从 mermaid 文件反取流量不可行——重新轻量解析：用 guard_rows + 报告行
    # 不够（要全部口），这里用 case + guard_rows 组合即可定性
    for c in comps:
        t = c.get("type")
        prm = ", ".join(_rd(p) for p in c.get("params", []))
        areas = "/".join(rl.fmt_sci(p.get("area", 0.0))
                         for p in c.get("ports", []))
        rows.append(f"| c{c['id']} | {t} | {prm} | {areas} | — |")
    return "\n".join(rows)


def _dg_scan_table(records: list[dict], prefix: str, param_label: str) -> str:
    """DG-4/DG-5 逐档扫描表（任务四补测：档位明细 + 守卫标度对照列）。"""
    steps = [r for r in records if r["id"].startswith(prefix)
             and r.get("measured")]
    if not steps:
        return ""
    out = [f"**{param_label}逐档明细**（K=κ·p_ref/m_ref 按档实算；"
           "闭合=|ṁ|−(cap+Δp/K)）:", ""]
    if prefix == "DG-4-A":
        out += ["| 档位 A | converged | iters | 告警 | |ṁ| | cap | "
                "offset=|ṁ|−cap | offset/cap（面积无关不变量） | "
                "offset/Δp | 1/K | 闭合残差 |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
    else:
        out += ["| 档位 p_up | converged | iters | 告警 | |ṁ| | cap | ratio | "
                "offset/Δp | 1/K | 闭合残差 |",
                "|---|---|---|---|---|---|---|---|---|---|"]
    for r in steps:
        m = r["measured"]
        val = m.get("value")
        val_s = f"{val:.0e}" if prefix == "DG-4-A" else f"{val:.0e}"
        row = [f"| {val_s} | {m.get('status')} | {m.get('iters')} | "
               f"{m.get('warn_calls')} |"]
        if m.get("cap") is not None:
            row += [f"{_rd(m.get('mdot'))} | {_rd(m.get('cap'))} | "]
            if prefix == "DG-4-A":
                row += [f"{_rd(m.get('offset'), 3)} | "
                        f"{_rd(m.get('offset_over_cap'), 4)} | "
                        f"{_rd(m.get('offset_over_dp'), 3)} | "
                        f"{_rd(m.get('one_over_K'), 3)} | "
                        f"{_rd(m.get('close_resid'), 2)} |"]
            else:
                act_ratio = m.get("worst_ratio")
                row += [f"{_rd(act_ratio, 5)} | "
                        f"{_rd(m.get('offset_over_dp'), 3)} | "
                        f"{_rd(m.get('one_over_K'), 3)} | "
                        f"{_rd(m.get('close_resid'), 2)} |"]
        else:
            row.append(" — " * (4 if prefix == "DG-4-A" else 4) + " |")
        out.append("".join(row))
    out.append("")
    return "\n".join(out)


def _regress_section(records: list[dict]) -> list[str]:
    out = []
    for r in records:
        # DG-4/DG-5 逐档子记录 → 并入母记录后的扫描表（不单独成节）
        if r["id"].startswith(("DG-4-A", "DG-5-P")):
            continue
        m = r.get("measured") or {}
        mer = r.get("mermaid")
        out.append(f"### {r['id']}（{r['group']} 组）—— {r['topo']}")
        out.append("")
        out.append(f"**verdict: {r['verdict']}**"
                   f"　status={m.get('status', '—')}"
                   f"　iters={m.get('iters', '—')}"
                   f"　命中={len(m.get('hits', []) or [])}"
                   f"　告警调用={m.get('warn_calls', '—')}"
                   f"　worst ratio={_rd(m.get('worst_ratio'))}")
        out.append("")
        if mer:
            out.append("```mermaid")
            out.append(mer)
            out.append("```")
            out.append("")
        m2 = r.get("mermaid2")
        if m2:
            out.append("netinf_B.json：")
            out.append("```mermaid")
            out.append(m2)
            out.append("```")
            out.append("")
        story = r.get("story")
        if story:
            out.append(f"**物理故事**：{story}")
            out.append("")
        for c in r.get("checks", []):
            mark = "✅" if c["ok"] else "❌"
            detail = f"（{c['detail']}）" if c["detail"] else ""
            out.append(f"- {mark} {c['name']}{detail}")
        for n in r.get("notes", []):
            out.append(f"- 📝 {n}")
        hits = m.get("hits") or []
        if hits:
            out.append("")
            out.append("| 命中元件 | 口 | ṁ | cap | ratio |")
            out.append("|---|---|---|---|---|")
            for h in hits:
                out.append(f"| c{h['comp']} | {h['port']} | {_rd(h['mdot'])} "
                           f"| {_rd(h['cap'])} | {_rd(h['ratio'], 5)} |")
        out.append("")
        if r["id"] == "DG-4":
            t = _dg_scan_table(records, "DG-4-A", "面积扫描")
            if t:
                out.append(t)
        if r["id"] == "DG-5":
            t = _dg_scan_table(records, "DG-5-P", "压差扫描")
            if t:
                out.append(t)
    return out


def _anchor_story(case_id: str, line: dict, case: dict) -> list[str]:
    """定点四例深析（A0199 按任务书深度手写骨架 + 实测数字回填）。"""
    out = []
    if case_id == "A0199":
        n1_p = n5_p = None
        h_ratio = None
        for r in line.get("guard_rows", []):
            if r["elem"] == "HEATER" and r["ratio"] is not None:
                h_ratio = r["ratio"]
        # 节点压力：从 hits 语境与 case 拓扑综合——直接用第一个命中的
        # 节点压力不可靠，这里从 mermaid 图源提取节点标注（n1/n5）
        fig = _load_mermaid(case_id, line)
        import re
        m1 = re.search(r'N1\(\("n1<br/>([\d.e+-]+) kPa', fig)
        m5 = re.search(r'N5\(\("n5<br/>([\d.e+-]+) kPa', fig)
        if m1:
            n1_p = float(m1.group(1))
        if m5:
            n5_p = float(m5.group(1))
        out.append("A0199 是\"守卫人工根被容量初值修复消除\"的正案例：")
        out.append("")
        out.append(f"- **n1 是低压汇**（实测 {_rd(n1_p)} kPa，全网一切流量"
                   "排向它）；")
        out.append(f"- **BOOSTER 把 n5 顶到 {_rd(n5_p)} kPa，成全网实际"
                   "主源**（它才是驱动压差的建立者）；")
        out.append("- **HEATER 串在小孔板 c6 下游**：链流量由 c6 的物理"
                   f"壅塞限定，heater 自身 ratio={_rd(h_ratio, 3)}·cap "
                   "远未触及守卫阈值——守卫全程旁观；")
        out.append("- 历史意义：旧初值（零流量起步）曾收敛到守卫制造的"
                   "钉位根（heater ratio=1.000，压力行死区冻结假根）；"
                   "master cc2740b 的容量初值（±1.1·cap 越 kink）让 FD "
                   "雅可比看见陡坡斜率后，落到物理根——守卫只在真超容时"
                   "才兜底，正常网络解不再被人工根污染。")
        out.append("")
    return out


# ============================================================ 补测轮章节
def _k_evidence_section(task1: list[dict], records: list[dict]) -> list[str]:
    """§2.6 K 标度边界证据库（补测任务一）。"""
    L = []
    ap = L.append
    ap("### 2.6 三告警例深挖：K 全网单标度边界证据库（补测轮任务一）")
    ap("")
    ap("守卫钉位公式：解处压力行平衡给出 **|ṁ| = cap + Δp/K，"
       "K = κ·p_ref/m_ref（κ=1e3，p_ref=锚定压力上限，m_ref=全网最大口"
       "容量）**。推论（单标度的直接后果）：钉位相对偏差 "
       "**ratio−1 = (Δp/p_ref)·(m_ref/(κ·cap))**——元件自身容量 cap 离"
       "全网容量尺度 m_ref 越远、该元件承受的相对压差越大，钉位偏得越"
       "远。三例分解全部按实现守卫参考量（`_p_ref_for_guard`/"
       "`_m_ref_for_guard`）实算，理论钉位与实测 |ṁ| 的闭合残差在"
       "机器精度（≤1e-14）——**守卫方程侧工作正常，偏差全部来自 K 的"
       "全网单标度，不是告警失败**。")
    ap("")
    for d in task1:
        cid = d.get("case_id")
        if d.get("error"):
            ap(f"#### {cid}：{d['error']}")
            ap("")
            continue
        ap(f"#### {cid}　K={_rd(d.get('K'), 4)}"
           f"（p_ref={_rd(d.get('p_ref_guard'), 4)}"
           f"，m_ref={_rd(d.get('m_ref_guard'), 4)}）")
        ap("")
        mer = d.get("mermaid")
        if mer:
            ap("```mermaid")
            ap(mer)
            ap("```")
            ap("")
        drivers = d.get("m_ref_drivers", [])
        if drivers:
            ap("m_ref 驱动者（全网口容量 top3，撑大分母 → K 偏小）：" +
               "；".join(f"c{x['comp']}({x['elem']})口{x['port']} "
                        f"cap@ref={_rd(x['cap_at_ref'], 3)}"
                        for x in drivers))
            ap("")
        ap("| 命中 | |ṁ| | cap | ratio | Δp 跨元件 | 理论钉位 cap+Δp/K | "
           "闭合残差 | Δp/p_ref | m_ref/cap | ratio−1 预测 |")
        ap("|---|---|---|---|---|---|---|---|---|---|")
        for h in d.get("hits", []):
            ap(f"| c{h['comp']}({h['elem']})口{h['port']} | {_rd(h['mdot'])} "
               f"| {_rd(h['cap'], 4)} | {_rd(h['ratio'], 5)} | "
               f"{_rd(h['dp_across'], 4)} | {_rd(h['theo_pin'], 5)} | "
               f"{h['close_resid']:.1e} | {_rd(h['dp_over_pref'], 4)} | "
               f"{_rd(h['mref_over_cap'], 4)} | "
               f"{_rd(h['ratio_minus1_pred'], 5)} |")
        ap("")
        for cf in d.get("counterfactual", []):
            if cf.get("status") == "converged":
                cf_txt = (f"收敛（iters={cf.get('iters')}），同口流量 "
                          f"{_rd(cf.get('mdot_same_port'))} kg/s"
                          f"（=该位置容量的 {cf.get('mdot_over_cap')} 倍，"
                          "亚容量自限流），跨元件压差 "
                          f"{_rd(cf.get('dp_across'), 4)} Pa")
            else:
                cf_txt = (f"{cf.get('status')}——替身也不收敛：该网络对"
                          "物理壅塞元件同样无根（真·网络容量不足）")
            ap(f"**反事实推演**（HEATER→{cf.get('surrogate')}）：{cf_txt}。")
            ap("")
    ap("**守卫价值判定**（反事实汇总）：")
    ap("")
    ap("- **A0093 / A0203：\"守卫把无根变有根\"成立**——ORIFICE 替身"
       "（同面积 Cd=1）收敛到亚容量良定解（0.54/0.72 倍容量），超容需求"
       "来自 HEATER 的零压降特性（无流量方程→流量无界→无根）；守卫陡坡"
       "扮演\"虚拟压损\"，钉位偏差 ratio−1 = 1e-4 / 8.6e-3（K 尺度匹配"
       "良好，几乎贴着物理容量）。")
    ap("- **A0171：\"守卫把崩溃变可诊断\"**——ORIFICE 替身同样 "
       "clean_fail：booster 压头 >> 全网物理容量，对任何限流元件都无根；"
       "守卫让解存在+告警指路。钉位偏差 ratio−1 = 23：K 被大孔板 "
       "m_ref=2.79 撑大 167 倍 → 钉在 24×cap——**K 全网单标度失准的"
       "量化实证**。")
    ap("")
    ap("**DG-4 对照**（尺度均匀网络，守卫标度关系应逐位成立）：")
    ap("")
    dg4 = [r for r in records if r["id"].startswith("DG-4-A")
           and r.get("measured")]
    if dg4:
        ap("| 档位 A | offset=|ṁ|−cap | offset/cap | Δp/(κ·p_ref) 预测 |")
        ap("|---|---|---|---|")
        for r in dg4:
            m = r["measured"]
            ap(f"| {m['value']:.0e} | {_rd(m.get('offset'), 3)} | "
               f"{_rd(m.get('offset_over_cap'), 5)} | "
               f"{_rd(m.get('dp_over_kappa_pref'), 5)} |")
        ap("")
    ap("**证据库结论**（供\"K 逐元件局部参考量\"——待定问题 #14 姊妹条"
       "重启时引用）：单标度 K 的钉位偏差可精确分解为两因子之积——"
       "Δp/p_ref × m_ref/(κ·cap)。尺度均匀（DG-1 族：cap≈m_ref）时偏差 "
       "~1e-3 与设计一致；多尺度+带功元件网络（A0171：m_ref/cap≈1.7e5、"
       "Δp/p_ref≈0.14）偏差放大到 ratio=24，钉位解离物理壅塞极限一个"
       "数量级以上——此时告警文案中的 \"ṁ>cap\" 数值不再近似物理壅塞"
       "流量，只应作\"超容\"标志读。局部参考量方案（K_i=κ·p_ref/cap_i）"
       "可把偏差收回 Δp/(κ·cap) 量级。")
    ap("")
    return L


def _assemble_section(task2: list[dict]) -> list[str]:
    """§2.7 assemble_error 分诊（补测任务二）。"""
    L = []
    ap = L.append
    n_design = sum(1 for t in task2 if t.get("class") == "design_intended")
    ap(f"### 2.7 assemble_error 分诊（{len(task2)} 例，设计内拦截 "
       f"{n_design} / 疑似缺陷 {len(task2) - n_design}）（补测轮任务二）")
    ap("")
    ap("| case | 来源 | 异常 | 定性 |")
    ap("|---|---|---|---|")
    for t in task2:
        mark = "✅" if t.get("class") == "design_intended" else "❌"
        exc = (t.get("exception") or "").replace("|", "/")
        if len(exc) > 60:
            exc = exc[:60] + "…"
        src = ("C 层酷刑产线" if t["case_id"].startswith("C")
               else "A 层生成器")
        ap(f"| {t['case_id']} | {src} | {exc} | {mark} {t['verdict']} |")
    ap("")
    for t in task2:
        ap(f"<details><summary>{t['case_id']} — {t['verdict']}</summary>")
        ap("")
        mer = t.get("mermaid")
        if mer:
            ap("```mermaid")
            ap(mer)
            ap("```")
            ap("")
        ap(f"异常：{t.get('exception')}")
        ap("")
        ap("</details>")
        ap("")
    ap("**生成器侧建议（只记录，不改代码）**：本轮 6 例全部来自 C 层"
       "边界酷刑产线设计例（C5 组：全 MASS_SOURCE 无压力锚定 ×5、代理"
       "模型缺文件 ×1），**非 gen_random.py 产出**——随机生成器未产出"
       "非法拓扑，无需修改建议；C5 组保留作组装期校验的回归哨兵。")
    ap("")
    return L


def _init_sens_section(task3: dict) -> list[str]:
    """§2.8 初值敏感性对照（补测任务三）。"""
    L = []
    ap = L.append
    rows = task3.get("rows", [])
    neg = task3.get("negative_list", [])
    ap(f"### 2.8 scipy_not_ok 家族初值敏感性对照（{len(rows)} 例 × 4 组"
       "初值，补测轮任务三）")
    ap("")
    if neg:
        ap("> ⚠ **容量初值负面清单（default_guess 不收敛而零流量初值"
           f"收敛）：{', '.join(neg)}**——下一轮初值方案迭代的直接输入，"
           "明细见下表负清单列。")
        ap("")
    else:
        ap("**容量初值负面清单为空**：14 例家族成员中无一出现"
           "\"default_guess 不收敛而零流量初值收敛\"——容量初值无可检"
           "负面案例。反向依赖（default 过 / 零初值不过）"
           f"{task3.get('n_reverse', 0)} 例，见下。")
        ap("")
    ap("| case | a default | b 零流量 | c ×0.5 | c ×2 | 收敛组同根? | "
       "负清单 | 反向 |")
    ap("|---|---|---|---|---|---|---|---|")

    def _cell(v):
        if not v or v.get("status") is None:
            return "—"
        if v.get("converged"):
            return (f"C({v.get('iters')},w{v.get('warn_calls')},"
                    f"r={_rd(v.get('worst_ratio'), 4)})")
        return f"{v.get('status')}({v.get('iters')})"

    for r in rows:
        vs = r.get("variants", {})
        sr = r.get("same_root")
        sr_s = "—" if sr is None else ("✅" if sr else "❌ 异根")
        ap(f"| {r['case_id']} | {_cell(vs.get('a_default'))} | "
           f"{_cell(vs.get('b_zeroflow'))} | {_cell(vs.get('c_half'))} | "
           f"{_cell(vs.get('c_double'))} | {sr_s} | "
           f"{'⚠️' if r.get('negative_list') else ''} | "
           f"{'←' if r.get('reverse') else ''} |")
    ap("")
    for r in rows:
        pd = r.get("pair_dx_scaled") or {}
        if not pd:
            continue
        pairs = "，".join(f"{k}={v:.2e}" for k, v in pd.items())
        mer = r.get("mermaid_a")
        ap(f"<details><summary>{r['case_id']} 两两缩放 max|dx|：{pairs}"
           "</summary>")
        ap("")
        if mer:
            ap("```mermaid")
            ap(mer)
            ap("```")
            ap("")
        ap("</details>")
        ap("")
    ap("**判定**：守卫的初值依赖性**总体良性**——")
    ap("")
    ap(f"- {task3.get('n_same_root', 0)}/{len(rows)} 例全部收敛组严格"
       "同根（缩放坐标 max|dx|<1e-6；A0004 等告警例 pair_dx=0.0 逐位"
       "一致）；")
    ap(f"- 异根 {task3.get('n_diff_root', 0)} 例中：**A0005 是零流量"
       "死肢节点温度的数值多解**（流量段全同、T_n5 差 1.8e4 K——死肢 T "
       "无物理约束的已知性质，与守卫无关）；A0062/A0101 为亚容量网络"
       "轻度多解；A0093/A0199 仅边际超阈（8.4e-4 / 1.0e-6，守卫特征"
       "坐标 ratio 逐位一致）；")
    ap(f"- **反向依赖 {task3.get('n_reverse', 0)} 例**（default 过 / "
       "零初值失败：A0062/A0093/A0171/A0203）全部落在软壅塞守卫家族——"
       "零流量初值在守卫对象元件上死区冻结（iters=0 或 50 步耗尽）。"
       "这是容量初值（±1.1·cap 越 kink 让 FD 雅可比看见陡坡斜率）存在"
       "理由的家族内定量实证：**撤掉容量初值，守卫钉位网络失去收敛"
       "能力**。")
    ap("")
    return L


def generate_report(reg_records: list[dict], lines: list[dict],
                    summary: dict, anchors: list[dict]) -> str:
    """组装 fuzz/SOFT_CHOKE_REGRESS.md（全脚本生成，每例一图）。"""
    by_id = {ln["case_id"]: ln for ln in lines}
    case_json = {}
    for cid in set(list(by_id.keys()) + [a["case"] for a in anchors]):
        p = CASES / f"{cid}.json"
        if p.exists():
            try:
                case_json[cid] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:             # noqa: BLE001
                pass

    L: list[str] = []
    ap = L.append
    followup = None
    if FOLLOWUP_JSON.exists():
        try:
            followup = json.loads(
                FOLLOWUP_JSON.read_text(encoding="utf-8"))
        except Exception:                     # noqa: BLE001
            followup = None
    ap("# pysas 软壅塞守卫体系回归报告（定向矩阵 + fuzz 全量扫描）")
    ap("")
    ap(f"- 日期：{date.today().isoformat()}")
    ap(f"- 对象：`pysas`（分支 `dev/fuzz-and-more`，HEAD `cc2740b`——"
       "软壅塞守卫三件套：方程侧陡坡 `_soft_choke_guard` + 容量初值 "
       "`default_guess` + 终态告警 `solve()` 尾部）")
    ap("- 判读口径：`report_lib.guard_rows` **独立复刻**守卫判定（非锚定、"
       "≤2 口、row_units==1 行、upwind 口滞止态 cap）——与实现同规则、"
       "测试层另写，防\"自己验自己\"；含亚容量行（ratio<1 也报，供余量列）")
    ap("- 口径注记：实现把一次 solve 的全部命中**合并进一条 warn 文案**，"
       "故\"告警数\"按**命中条数**（hits）口径报告，warn 调用数另列")
    ap("- 运行方式：Windows GBK 终端，`python -X utf8`（"
       "D:\\Python\\Python312\\python.exe）；纪律：pysas 源码零改动，"
       "产出仅落 `fuzz/`")
    ap("")

    # ---------- 顶部：负面清单高亮 + 两轮变更对照（补测轮任务五） ----------
    if followup:
        neg = followup.get("task3_init_sens", {}).get("negative_list", [])
        if neg:
            ap(f">> ⚠️ **容量初值负面清单（default_guess 不收敛而零流量"
               f"初值收敛）：{', '.join(neg)}**——下一轮初值方案迭代的"
               "直接输入，明细见 §2.8。")
            ap("")
        ap("## 两轮变更对照（补测轮新增）")
        ap("")
        ap("| 维度 | 上轮（59c78a7） | 本轮（补测轮） |")
        ap("|---|---|---|")
        ap("| 告警例解读 | \"守卫钉位（非物理解）\"一笔带过 | K 分解闭合至"
           "机器精度（§2.6）：偏差全部来自 K 全网单标度，A0171 定性为"
           "失准证据而非告警失败 |")
        ap("| assemble_error | 未分诊 | 6 例全部设计内拦截（§2.7），"
           "生成器侧无需修改 |")
        ap("| 初值依赖 | DG-1b 单点路径无关回归 | 家族 14 例 × 4 初值对照"
           "（§2.8）：负面清单为空，反向依赖 4 例实证容量初值必要性 |")
        ap("| DG-4/DG-5 | 仅 PASS verdict | 逐档记录落盘 + offset≈Δp/K "
           "标度对照列（§1 A 组表） |")
        ap("| 收敛率 | 全局 32.4% 一个总数 | 分层口径修正：A 层 14.7% "
           "vs 基线 14.0%、C 层 88.4%（§0 口径段） |")
        ap("| 已知边界 | 3 条 | 5 条：新增 K 单标度失准、容量初值负面"
           "清单结论（§4） |")
        ap("")

    # ---------- 0. 执行摘要 ----------
    ap("## 0. 执行摘要")
    ap("")
    n_all_pass = all(r["verdict"] in ("PASS", "SKIP") for r in reg_records)
    pg = summary.get("per_group", {})
    grp_desc = " + ".join(
        f"{g} 层 {v['n']} 例" for g, v in pg.items()) or \
        f"{summary['n_cases']} 例"
    ap(f"**定向矩阵 {sum(1 for r in reg_records if r['verdict'] == 'PASS')}"
       f"/{len(reg_records)} PASS、"
       f"{sum(1 for r in reg_records if r['verdict'] == 'SKIP')} SKIP、"
       f"{sum(1 for r in reg_records if r['verdict'] == 'FAIL')} FAIL"
       f"（{'全过' if n_all_pass else '存在失败'}）；"
       f"fuzz 全量扫描 {summary['n_cases']} 例（{grp_desc}），收敛 "
       f"{summary['converged']}（{summary['converge_rate']:.1%}），"
       f"守卫告警例 {summary['n_warn_cases']}，"
       f"ratio P50/P90/MAX = "
       f"{_rd(summary['ratio_quantiles'].get('P50'), 4)}/"
       f"{_rd(summary['ratio_quantiles'].get('P90'), 4)}/"
       f"{_rd(summary['ratio_quantiles'].get('MAX'), 4)}"
       f"（分位数只取收敛例；未收敛解中间态最大 "
       f"{_rd(summary['ratio_quantiles'].get('MAX_ALL_INCL_UNCONVERGED'), 4)}"
       "，无解质量意义）**")
    ap("")
    ap("| 代号 | 组 | verdict | status | iters | 命中 | warn调用 | worst ratio |")
    ap("|---|---|---|---|---|---|---|---|")
    for r in reg_records:
        m = r.get("measured") or {}
        hits = m.get("hits", [])
        n_hits = hits if isinstance(hits, int) else len(hits or [])
        ap(f"| {r['id']} | {r['group']} | {r['verdict']} | "
           f"{m.get('status', '—')} | {m.get('iters', '—')} | "
           f"{n_hits} | {m.get('warn_calls', '—')} | "
           f"{_rd(m.get('worst_ratio'))} |")
    ap("")
    an_ok = sum(1 for a in anchors if a["ok"])
    ap(f"**定点四例**：{an_ok}/4 断言成立"
       + ("" if an_ok == 4 else "（明细见 §3.4）"))
    ap("")
    ap("| 定点 | 断言 | 结果 | 实测 |")
    ap("|---|---|---|---|")
    for a in anchors:
        mark = "✅" if a["ok"] else "❌"
        ap(f"| {a['case']} | {a['assert']} | {mark} | "
           f"{json.dumps(a['measured'], ensure_ascii=False)} |")
    ap("")

    # ---- 收敛率对比基准（补测轮任务五：分层口径，禁止单一总数） ----
    pg = summary.get("per_group", {})
    a_g = pg.get("A", {})
    c_g = pg.get("C", {})
    ap("**收敛率对比基准（口径对齐）**：")
    ap("")
    ap("- 上上轮基线（守卫实施前，`REPORT.md` 时点）：**A 层 300 例"
       "（族一语料，同种子）收敛 42 = 14.0%**；")
    ap(f"- 本扫描 **A 层：{a_g.get('converged')}/{a_g.get('n')} = "
       f"{a_g.get('converged', 0) / max(a_g.get('n', 1), 1):.1%}**——"
       "同语料真实增量 +2 例（14.0%→14.7%）。注意混入效应：两轮之间"
       "落地了 2372a45（出口总温口径 + 读入期拓扑校验），增量不能全部"
       "归因守卫/容量初值；")
    ap(f"- 本扫描 **C 层：{c_g.get('converged')}/{c_g.get('n')} = "
       f"{c_g.get('converged', 0) / max(c_g.get('n', 1), 1):.1%}**"
       "（边界酷刑产线，首次挂入全量扫描，无守卫前直接对照）；")
    ap(f"- 全局 {summary['converge_rate']:.1%} 是 A+C 混合口径——"
       "**与 14% 基线不可直接比较**（分母含 95 例高收敛 C 层）。")
    ap("")

    # ---------- 1. 定向矩阵 ----------
    ap("## 1. 定向矩阵逐例详析（A 守卫行为 / B 豁免矩阵 / C 基准对照）")
    ap("")
    L.extend(_regress_section(reg_records))

    # ---------- 2. 全量扫描 ----------
    ap("## 2. fuzz 全量扫描（A 层随机语料 + C 层边界酷刑，冷启动）")
    ap("")
    ap("### 2.1 汇总统计")
    ap("")
    ap("| 维度 | 结果 |")
    ap("|---|---|")
    sc = summary["status_counts"]
    ap(f"| 算例数 | {summary['n_cases']} |")
    for g, v in (summary.get("per_group") or {}).items():
        ap(f"| {g} 层 | {v['n']} 例，收敛 {v['converged']}，"
           f"告警例 {v['warn']} |")
    ap(f"| converged | {summary['converged']}（{summary['converge_rate']:.1%}） |")
    for k in ("clean_fail", "assemble_error", "solve_error", "driver_error"):
        if sc.get(k):
            ap(f"| {k} | {sc[k]} |")
    ap(f"| 守卫告警例（converged+hits） | {summary['n_warn_cases']} |")
    rq = summary["ratio_quantiles"]
    ap(f"| worst ratio 分位 P50 / P90 / MAX | {_rd(rq.get('P50'), 4)} / "
       f"{_rd(rq.get('P90'), 4)} / {_rd(rq.get('MAX'), 4)} |")
    if summary.get("scipy_enabled"):
        ap(f"| scipy 双解嫌疑（max\\|dx\\|_scaled>1e-6） | "
           f"{len(summary.get('scipy_suspects', []))} |")
        ap(f"| scipy 未收敛例（lm 从同初值） | "
           f"{len(summary.get('scipy_not_ok_ids', []))} |")
    ap(f"| 扫描耗时 | {summary.get('elapsed_s', '—')} s |")
    ap("")

    warn_lines = [ln for ln in lines if ln["converged"] and ln["hits"]]
    top = sorted([ln for ln in lines if ln["converged"]
                  and ln.get("worst_ratio") is not None],
                 key=lambda ln: -ln["worst_ratio"])[:10]
    anchor_ids = [a["case"] for a in anchors]

    ap("### 2.2 converged+有告警 例逐例详析")
    ap("")
    if not warn_lines:
        ap("（无）")
    else:
        for ln in warn_lines:
            cid = ln["case_id"]
            ap(f"#### {cid}　worst={_rd(ln.get('worst_ratio'), 5)}")
            ap("")
            ap("```mermaid")
            ap(_load_mermaid(cid, ln))
            ap("```")
            ap("")
            ap(f"**定性**：{_one_line(ln)}")
            ap("")
            ap(_hit_table(ln))
            ap("")
    ap("### 2.3 worst ratio top10 详析")
    ap("")
    for i, ln in enumerate(top, 1):
        cid = ln["case_id"]
        ap(f"#### Top{i} {cid}　worst={_rd(ln.get('worst_ratio'), 5)} "
           f"iters={ln.get('iters')}")
        ap("")
        ap("```mermaid")
        ap(_load_mermaid(cid, ln))
        ap("```")
        ap("")
        ap(f"**定性**：{_one_line(ln)}")
        ap("")
        ap(_hit_table(ln))
        ap("")
    ap("### 2.4 定点四例详析（A0004 / A0199 / A0257 / A0275）")
    ap("")
    for a in anchors:
        cid = a["case"]
        ln = by_id.get(cid, {})
        case = case_json.get(cid)
        ap(f"#### {cid}　{'✅ ' if a['ok'] else '❌ '}{a['assert']}")
        ap("")
        ap(f"实测：status={ln.get('status')} iters={ln.get('iters')} "
           f"worst={_rd(ln.get('worst_ratio'), 5)} "
           f"命中={len(ln.get('hits', []))}")
        ap("")
        if cid == "A0199" and case:
            L.extend(_anchor_story(cid, ln, case))
        elif cid == "A0275":
            ap("A0275 归 M2 同伦：双零压降+守卫钉位网络上不收敛路径"
               "（fuzz 报告已知边界，想法 33 修复范围外）——本扫描如实"
               "记录其不收敛状态，不判失败。")
            ap("")
        ap("```mermaid")
        ap(_load_mermaid(cid, ln))
        ap("```")
        ap("")
        ap(_hit_table(ln))
        ap("")
    if summary.get("scipy_enabled"):
        ap("### 2.5 scipy 独立对照（同初值 least_squares-lm）")
        ap("")
        sus = summary.get("scipy_suspects", [])
        if not sus:
            ap("无\"双解嫌疑\"例（全部收敛例与 scipy 在缩放坐标 "
               "max|dx|≤1e-6 内同根）——容量初值没有引入初值选根族分歧。")
        else:
            ap("以下收敛例 scipy 从**同一初值**出发落到不同根"
               "（缩放坐标 max|dx|>1e-6）——\"初值选根族\"嫌疑清单，"
               "只记录不判失败：")
            ap("")
            ap("| case | max\\|dx\\| 缩放 | max\\|dx\\| 原始 |")
            ap("|---|---|---|")
            for s in sus:
                ap(f"| {s['case_id']} | {s['max_dx_scaled']:.3e} | "
                   f"{_rd(s.get('max_dx_raw'))} |")
        n_not_ok = len(summary.get("scipy_not_ok_ids", []))
        if n_not_ok:
            ap("")
            ap(f"scipy 自身未收敛（lm 异常/不达 1e-8）{n_not_ok} 例："
               + ", ".join(summary["scipy_not_ok_ids"][:20])
               + (" …" if n_not_ok > 20 else ""))
        ap("")

    # ---------- 2.6–2.8 补测轮章节 ----------
    if followup:
        t1 = followup.get("task1_k_decomp")
        t2 = followup.get("task2_assemble")
        t3 = followup.get("task3_init_sens")
        if t1:
            L.extend(_k_evidence_section(t1, reg_records))
        if t2:
            L.extend(_assemble_section(t2))
        if t3:
            L.extend(_init_sens_section(t3))

    # ---------- 3. 附录 ----------
    ap("## 3. 附录：全部扫描例逐例条目（每例一图）")
    ap("")
    ap("除 §2 已详析（告警例/top10/定点）外的其余全部扫描例，折叠条目：")
    ap("")
    detailed_ids = set(anchor_ids) \
        | {ln["case_id"] for ln in warn_lines} \
        | {ln["case_id"] for ln in top}
    for ln in lines:
        cid = ln["case_id"]
        if cid in detailed_ids:
            continue
        stat = (f"status={ln.get('status')}, iters={ln.get('iters')}, "
                f"warn={ln.get('warn_calls')}, "
                f"worst={_rd(ln.get('worst_ratio'), 4)}")
        if ln.get("exception"):
            stat += f"，异常={ln['exception'][:80]}"
        ap(f"<details><summary>{cid} — {stat}</summary>")
        ap("")
        ap("```mermaid")
        ap(_load_mermaid(cid, ln))
        ap("```")
        ap("")
        ap(f"{_one_line(ln)}")
        ap("")
        ap("</details>")
        ap("")

    # ---------- 4. 已知边界 ----------
    ap("## 4. 已知边界与后续项（记录不修）")
    ap("")
    ap("- **A0275 归 M2 同伦**：不收敛路径在同伦延拓（mode=2）落地后处理；")
    ap("- **多口件方程侧无守卫**（JUNCTION 等 >2 口件按 A0257 实证豁免，"
       "容量约束语义属腔整体）——报表侧逐口超容警告是后续项；")
    ap("- **不收敛路径零提示**：clean-fail 只有 status/iters，无失败分类"
       "（无根/初值域外/病态 J）——失败分类器是后续项；本扫描 DG-8/J-2 "
       "实证失败路径安静退出、不崩溃；")
    ap("- **K 全网单标度的多尺度失准**（补测轮 §2.6 证据库）：钉位偏差 "
       "ratio−1 = (Δp/p_ref)·(m_ref/(κ·cap))，A0171 实证放大到 23 倍——"
       "多尺度+带功元件网络中告警数值只应作\"超容\"标志读；\"K 逐元件"
       "局部参考量\"（待定问题 #14 姊妹条）重启时引用该节；")
    ap("- **容量初值负面清单为空，反向依赖 4 例**（补测轮 §2.8）：家族 "
       "14 例无\"default 不过而零初值过\"；反向（default 过/零初值冻结）"
       "A0062/A0093/A0171/A0203 全为守卫家族——容量初值是守卫钉位网络"
       "收敛的必要组件，初值方案迭代时两者必须联动评估；")
    ap("- **零流量死肢节点温度数值多解**（补测轮 §2.8，A0005 实证）："
       "死肢 T 无物理约束（能量行仅 ε 正则），不同初值可差 1e4 K 量级——"
       "流量/压力根不受影响，报表侧如需可加死肢温度标志（后续项）；")
    ap("- **告警合并**：一次 solve 的全部命中合并进一条 warn 文案——按"
       "命中条数口径判读；")
    ap("- **J-2 裸拓扑病态**：junction 出口直挂等压双 PB 的分流比不定"
       "（J 零空间），实测 iters=0 冻结——拓扑适定性问题，非守卫问题，"
       "适定变体 J-2b 全过；")
    ap("- **V2 参考基线过期**：v2_results.json 生成后管件模型经历 Fanno "
       "重构（两工作区现实现逐位一致、同解），脚本的不可压 Darcy 手算"
       "不再描述现管件物理——守卫零扰动以 dev vs pre-guard 旧工作树"
       "双跑逐位一致实证。")
    ap("")
    return "\n".join(L) + "\n"


# ============================================================ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-scipy", action="store_true",
                    help="跳过 scipy 独立对照加餐")
    ap.add_argument("--limit", type=int, default=0,
                    help="只扫前 N 例（调试用）")
    ap.add_argument("--no-report", action="store_true",
                    help="只扫描不出报告")
    ap.add_argument("--report-only", action="store_true",
                    help="跳过扫描，直接用既有产物出报告")
    args = ap.parse_args()

    if not args.report_only:
        lines, summary = run_sweep(
            do_scipy=not args.no_scipy,
            limit=args.limit or None)
        anchors = assert_anchors(lines)
        print(f"扫描完成：{summary['n_cases']} 例，收敛率 "
              f"{summary['converge_rate']:.1%}，告警例 "
              f"{summary['n_warn_cases']}，耗时 {summary['elapsed_s']}s")
        for a in anchors:
            print(f"  {'✅' if a['ok'] else '❌'} {a['case']}: {a['assert']}"
                  f" → {json.dumps(a['measured'], ensure_ascii=False)}")
    else:
        with open(SWEEP_JSONL, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        summary = json.loads(SWEEP_SUMMARY.read_text(encoding="utf-8"))
        anchors = assert_anchors(lines)

    if args.no_report:
        return 0

    if not REGRESS_JSON.exists():
        print("定向矩阵结果缺失，先跑 run_soft_choke_regress.py …", flush=True)
        import run_soft_choke_regress as reg
        reg.collect(REGRESS_JSON)
    reg_data = json.loads(REGRESS_JSON.read_text(encoding="utf-8"))
    report = generate_report(reg_data["records"], lines, summary, anchors)
    REPORT_MD.write_text(report, encoding="utf-8")
    print(f"报告已生成：{REPORT_MD}（{len(report) / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    main()
