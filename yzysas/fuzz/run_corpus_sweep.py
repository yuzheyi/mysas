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


def _regress_section(records: list[dict]) -> list[str]:
    out = []
    for r in records:
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
        ap(f"| {r['id']} | {r['group']} | {r['verdict']} | "
           f"{m.get('status', '—')} | {m.get('iters', '—')} | "
           f"{len(m.get('hits', []) or [])} | {m.get('warn_calls', '—')} | "
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
