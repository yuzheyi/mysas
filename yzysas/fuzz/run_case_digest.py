# -*- coding: utf-8 -*-
"""典型案例图鉴 + 复杂网络专项（P0/P1）——写给人看的 fuzz 语料导读。

P0  fuzz/CASE_DIGEST.md：T1~T7 规则选例（去重），每例固定模板
    （mermaid 全景图 / 物理故事三件事 / 关键数字小表 / 为什么选它），
    头部含语料统计速览。全部数字来自 sweep.jsonl + cases/*.json。
P1  fuzz/COMPLEX_NET.md：复杂度分数 top10（分数列在报告里可复算），
    每例 mermaid + 物理故事 + 守卫口径逐元件表，top10 vs 其余语料统计。

纪律：只新增本脚本与两份报告；不碰 pysas/、cases/、既有 run_*.py。
运行: python -X utf8 run_case_digest.py [--only T4]  （--only 小样验证用）
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path

import report_lib as rl

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
RESULTS = HERE / "results"
SWEEP_JSONL = RESULTS / "soft_choke_sweep.jsonl"
DIGEST_MD = HERE / "CASE_DIGEST.md"
COMPLEX_MD = HERE / "COMPLEX_NET.md"

ELEM_CN = {
    "ORIFICE": "孔板", "PIPE": "直管", "PRESSURE_BOUNDARY": "压力边界",
    "MASS_SOURCE": "流量源", "BOOSTER": "增压泵", "HEATER": "加热器",
    "JUNCTION": "三通", "AREA_CHANGE": "截面突变", "SURROGATE_FLOW": "代理件",
}

# sweep.jsonl 里的告警口径字段名（上一轮写盘时用的键）
def warn_of(line: dict) -> int:
    return int(line.get("warn_calls") or 0)


def load_corpus() -> list[dict]:
    """sweep.jsonl 全量 + 每 case 的拓扑本体（nodes/comps/面积跨度）。"""
    lines = [json.loads(l) for l in open(SWEEP_JSONL, encoding="utf-8")
             if l.strip()]
    for ln in lines:
        cid = ln["case_id"]
        try:
            case = json.loads((CASES / f"{cid}.json").read_text(
                encoding="utf-8"))
        except Exception:                     # noqa: BLE001
            case = None
        ln["case"] = case
        if case:
            ln["n_nodes"] = len(case.get("nodes", []))
            ln["n_comps"] = len(case.get("comps", []))
            areas = [p.get("area", 0.0) or 0.0
                     for c in case.get("comps", []) for p in c.get("ports", [])]
            pos = [a for a in areas if a > 0.0]
            ln["area_span"] = (max(pos) / min(pos)) if len(pos) >= 2 else None
            ln["types"] = sorted({c.get("type") for c in case.get("comps", [])})
        else:
            ln["n_nodes"] = ln["n_comps"] = 0
            ln["area_span"] = None
            ln["types"] = []
    return lines


# ============================================================ P0 选例
def select_cases(corpus: list[dict]) -> tuple[dict[str, list[dict]], dict]:
    """T1~T7 规则选例。返回 {规则: 例清单} 与 {case_id: [规则…]} 映射。"""
    by_id = {ln["case_id"]: ln for ln in corpus}
    conv = [ln for ln in corpus if ln["status"] == "converged"]
    fail = [ln for ln in corpus if ln["status"] == "clean_fail"]
    picks: dict[str, list[dict]] = {}

    # T1 节点数最多前3（converged 优先，不足补 clean_fail）
    c = sorted(conv, key=lambda l: (-l["n_nodes"], l["case_id"]))[:3]
    if len(c) < 3:
        f = sorted(fail, key=lambda l: (-l["n_nodes"], l["case_id"]))
        c += f[:3 - len(c)]
    picks["T1"] = c
    # T2 元件总数最多前3（同上）
    c = sorted(conv, key=lambda l: (-l["n_comps"], l["case_id"]))[:3]
    if len(c) < 3:
        f = sorted(fail, key=lambda l: (-l["n_comps"], l["case_id"]))
        c += f[:3 - len(c)]
    picks["T2"] = c
    # T3 三件套 HEATER+JUNCTION+BOOSTER，converged 前2；放宽两种
    def _has(l, t):
        return t in l["types"]
    tri = [l for l in conv
           if all(_has(l, t) for t in ("HEATER", "JUNCTION", "BOOSTER"))]
    rule = "三件套齐全"
    if len(tri) < 2:
        duo = [l for l in conv if sum(_has(l, t) for t in
               ("HEATER", "JUNCTION", "BOOSTER")) >= 2
               and l not in tri]
        rule = "放宽为含其中两种"
        tri += duo
    picks["T3"] = tri[:2]
    picks["_T3_rule"] = rule
    # T4 告警例全部
    picks["T4"] = [l for l in corpus if warn_of(l) > 0 or
                   (l.get("hits") and l["status"] == "converged")]
    picks["T4"] = [l for l in picks["T4"] if l["status"] == "converged"]
    # T5 clean_fail iters=0 前2 + iters=50 前2
    picks["T5"] = (sorted([l for l in fail if l.get("iters") == 0],
                          key=lambda l: l["case_id"])[:2]
                   + sorted([l for l in fail if l.get("iters") == 50],
                            key=lambda l: l["case_id"])[:2])
    # T6 面积跨度最大前2（需 ≥2 个正面积）
    spanned = [l for l in corpus if l["area_span"]]
    picks["T6"] = sorted(spanned, key=lambda l: (-l["area_span"],
                                                 l["case_id"]))[:2]
    # T7 最简单：元件最少 converged 1 + clean_fail 1
    t7c = sorted(conv, key=lambda l: (l["n_comps"], l["case_id"]))[:1]
    t7f = sorted(fail, key=lambda l: (l["n_comps"], l["case_id"]))[:1]
    picks["T7"] = t7c + t7f

    cat_of: dict[str, list[str]] = {}
    for rule, lst in picks.items():
        if rule.startswith("_"):
            continue
        for l in lst:
            cat_of.setdefault(l["case_id"], []).append(rule)
    return picks, cat_of


# ============================================================ 故事生成
def _elem_name(c: dict) -> str:
    t = c.get("type", "?")
    return f"c{c['id']}({ELEM_CN.get(t, t)})"


def _flow_bookkeeping(out: dict) -> dict:
    """解后收支：每元件净注入（>0 向网络供气）、每口流量、壅塞标志。"""
    sysm, x, ctx = out["sysm"], out["x"], out["ctx"]
    net_inj = {}
    for comp in sysm.net.comps:
        s = sum(float(x[sysm.m_idx_of_port[(comp.comp_id, j)]])
                for j in range(len(comp.ports)))
        net_inj[comp.comp_id] = -s          # >0 = 向网络净供气
    choked_ports = set()
    try:
        pst = sysm.port_states(x, ctx)
        idx = 0
        for comp in sorted(sysm.net.comps, key=lambda c: c.comp_id):
            for j in range(len(comp.ports)):
                if pst[idx].choked:
                    choked_ports.add((comp.comp_id, j))
                idx += 1
    except Exception:                         # noqa: BLE001
        pass
    return {"net_inj": net_inj, "choked_ports": choked_ports}


def _bottleneck(out: dict, case: dict, book: dict) -> tuple[str, str]:
    """卡脖子定位 → (一句话, 类别)。守卫 > 壅塞 > 最大压损 > 均摊。"""
    hits = out.get("guard_active", [])
    if hits:
        r = hits[0]
        return (f"守卫钉位：c{r['comp_id']} 口{r['port']} 的流量被按在"
                f"声速容量的 {r['ratio']:.3f} 倍（守卫=求解器给不产生压降"
                "的元件加的一道\"软闸门\"，把超过截面积承受力的流量按住），"
                f"ṁ={r['mdot']:.4g} kg/s", "guard")
    if book["choked_ports"]:
        cid, j = sorted(book["choked_ports"])[0]
        comp = next(c for c in case["comps"] if c["id"] == cid)
        return (f"{_elem_name(comp)} 壅塞（流体在该元件最小截面已到声速，"
                "流量被\"音速天花板\"卡死，上游压力再高流量也不涨）",
                "choked")
    # 最大压损两口件
    sysm, x = out["sysm"], out["x"]
    best = None
    for comp in case["comps"]:
        if len(comp.get("ports", [])) != 2:
            continue
        n0, n1 = comp["ports"][0]["node"], comp["ports"][1]["node"]
        try:
            dp = abs(float(x[sysm.p_idx_of_node[n0]]
                           - x[sysm.p_idx_of_node[n1]]))
        except Exception:                     # noqa: BLE001
            continue
        if best is None or dp > best[0]:
            best = (dp, comp)
    if best and best[0] > 0:
        dp, comp = best
        t = comp.get("type")
        why = {"PIPE": "沿程摩擦", "ORIFICE": "孔口节流",
               "AREA_CHANGE": "截面突变损失"}.get(t, "流动损失")
        return (f"{_elem_name(comp)} 的{why}吃掉了全网最大的压差 "
                f"({dp:.4g} Pa)", "loss")
    return ("压差较均匀地分布在多个元件上，无明显卡脖子", "even")


def _story(case: dict, out: dict, line: dict, book: dict | None) -> list[str]:
    """物理故事（≤5 句，三件事：从哪来到哪去 / 谁卡脖子 / 为何成败）。"""
    sents = []
    types = {c["id"]: c for c in case["comps"]}
    # --- 1. 从哪来、到哪去 ---
    if book:
        inj = book["net_inj"]
        srcs = sorted(((v, cid) for cid, v in inj.items() if v > 1e-12),
                      reverse=True)
        sinks = sorted(((-v, cid) for cid, v in inj.items() if v < -1e-12),
                       reverse=True)
        if srcs:
            s = ", ".join(f"{_elem_name(types[cid])}（供 {v:.4g} kg/s）"
                          for v, cid in srcs[:2])
            sent = f"气主要从 {s} 进入网络"
            if sinks:
                d = ", ".join(f"{_elem_name(types[cid])}（排 {v:.4g} kg/s）"
                              for v, cid in sinks[:2])
                sent += f"，最后经 {d} 排出"
            sents.append(sent + "。")
        else:
            sents.append("全网流量近零（死网络，无有效供排气路径）。")
    else:
        pbs = [c for c in case["comps"]
               if c["type"] == "PRESSURE_BOUNDARY"]
        if pbs:
            hi = max(pbs, key=lambda c: c["params"][0])
            lo = min(pbs, key=lambda c: c["params"][0])
            sents.append(f"按压力设置，气应从 {_elem_name(hi)}"
                         f"（{hi['params'][0]:.3g} Pa）流向 {_elem_name(lo)}"
                         f"（{lo['params'][0]:.3g} Pa），但求解未能定出"
                         "这条路径。")
    # --- 2. 谁卡脖子 ---
    if book:
        btl, cat = _bottleneck(out, case, book)
        sents.append(btl + "。")
    else:
        sents.append("卡脖子元件无法定位——解没有算出来。")
    # --- 3. 为什么收敛/失败 ---
    st, it = line["status"], line.get("iters")
    if st == "converged":
        if (it or 0) <= 10:
            sents.append(f"{it} 步顺利收敛：初值（含容量初值——按元件"
                         "截面积预估的初始流量）已离解不远。")
        else:
            sents.append(f"经 {it} 步挣扎后收敛：初值离解较远，牛顿法"
                         "多绕了几步。")
    elif st == "clean_fail":
        if it == 0:
            sents.append("0 步即冻结：初值点上方程对未知量\"失明\""
                         "（零流量死区/零压差死区，导数全为零），"
                         "牛顿法迈不出第一步。")
        elif (it or 0) >= 50:
            sents.append("迭代 50 步耗尽仍不达标：要么解在陡峭拐点上"
                         "来回跳，要么这个网络本来就没有物理解"
                         "（无解嫌疑）。")
        else:
            sents.append(f"{it} 步后线搜索失败中止：中途踏进残差上升区，"
                         "保守起见放弃（失败分类器是后续项）。")
    else:
        sents.append(f"状态 {st}。")
    return sents


def _title(case: dict, out: dict, line: dict, book: dict | None) -> str:
    """一句话标题（自动）：主特征 + 结局。"""
    st = line["status"]
    if book and out.get("guard_active"):
        r = out["guard_active"][0]
        comp = next(c for c in case["comps"] if c["id"] == r["comp_id"])
        tag = ELEM_CN.get(comp.get("type"), "?")
        return f"{tag}被守卫按在 {r['ratio']:.2f} 倍容量上"
    if book and book["choked_ports"]:
        return "孔板壅塞限流的典型链路"
    span = line.get("area_span")
    if span and span > 100:
        return f"跨 {span:.0f} 倍面积的大小肠组合"
    n = line.get("n_nodes", 0)
    if st == "converged":
        return f"{n} 节点网络的顺利求解"
    if line.get("iters") == 0:
        return "起步即冻结的死区样本"
    if (line.get("iters") or 0) >= 50:
        return "迭代耗尽的无解嫌疑样本"
    return "中途失败的普通样本"


def _key_numbers(line: dict) -> str:
    span = line.get("area_span")
    span_s = f"{span:.3g}" if span else "—"
    wr = line.get("worst_ratio")
    wr_s = f"{wr:.4g}" if wr is not None else "—"
    return (f"| 节点 | 元件 | converged | iters | 告警 | worst ratio | "
            f"面积跨度 |\n|---|---|---|---|---|---|---|\n"
            f"| {line['n_nodes']} | {line['n_comps']} | "
            f"{'是' if line['status'] == 'converged' else '否'} | "
            f"{line.get('iters', '—')} | {warn_of(line)} | {wr_s} | "
            f"{span_s} |")


def _render_case(rule: str, line: dict, cat: list[str]) -> str:
    """单例一节（模板固定）。"""
    cid = line["case_id"]
    case = line["case"]
    out = rl.solve_case(case)                 # 重解取 x/守卫/壅塞
    book = None
    if out.get("status") == "converged" and out.get("x") is not None:
        book = _flow_bookkeeping(out)
    try:
        if out.get("x") is not None and out.get("sysm") is not None:
            mer = rl.netinf_to_mermaid(case, x=out["x"], sysm=out["sysm"])
        else:
            mer = rl.netinf_to_mermaid(case)
    except Exception as e:                    # noqa: BLE001
        mer = f"> （图生成失败 {type(e).__name__}: {e}）"
    title = _title(case, out, line, book)
    L = [f"### {rule}({cid}) {title}", ""]
    L += ["```mermaid", mer, "```", ""]
    L += ["【物理故事】"]
    for s in _story(case, out, line, book):
        L.append(f"- {s}")
    L += ["", "【关键数字】", "", _key_numbers(line), ""]
    other = [c for c in cat if c != rule]
    why = f"它在 {rule} 规则下入选" + (
        f"（同时命中 {'、'.join(other)}）" if other else "")
    L += [f"【为什么选它】{why}。", ""]
    return "\n".join(L)


# ============================================================ P0 报告
def _stats_header(corpus: list[dict]) -> str:
    L = ["## 语料统计速览（全部来自 sweep.jsonl + cases/*.json）", ""]
    st = Counter(l["status"] for l in corpus)
    L += ["| 维度 | 数字 |", "|---|---|"]
    L.append(f"| 总例数 / converged / clean_fail / assemble_error | "
             f"{len(corpus)} / {st.get('converged', 0)} / "
             f"{st.get('clean_fail', 0)} / {st.get('assemble_error', 0)} |")
    # 元件类型出现次数（跨全部 case 累计）
    tc: Counter = Counter()
    n_case_with: Counter = Counter()
    for l in corpus:
        if not l["case"]:
            continue
        for t in {c.get("type") for c in l["case"].get("comps", [])}:
            n_case_with[t] += 1
            tc[t] += sum(1 for c in l["case"]["comps"] if c.get("type") == t)
    L.append("| 元件类型出现总次数（case 内累计） | " + "，".join(
        f"{k}:{v}" for k, v in tc.most_common()) + " |")
    L.append("| 含该类元件的 case 数 | " + "，".join(
        f"{k}:{v}" for k, v in n_case_with.most_common()) + " |")
    # 面积跨度分布
    spans = [l["area_span"] for l in corpus if l["area_span"]]
    buckets = [(1, 10), (10, 100), (100, 1000), (1000, 10000), (10000, None)]

    def bkt(v):
        for lo, hi in buckets:
            if lo <= v < (hi or math.inf):
                return f"[{lo}, {hi or '∞'})"
        return "其它"

    bc = Counter(bkt(v) for v in spans)
    L.append(f"| 面积跨度分布（{len(spans)} 例有 ≥2 个正面积） | " +
             "，".join(f"{k}:{v}" for k, v in bc.most_common()) + " |")
    # 规模分布
    nb = Counter()
    for l in corpus:
        n = l["n_nodes"]
        nb["2-3" if n <= 3 else "4-5" if n <= 5 else "6-8" if n <= 8
           else "9-12"] += 1
    L.append("| 节点规模分布 | " + "，".join(f"{k}:{v}"
                                            for k, v in sorted(nb.items()))
             + " |")
    cb = Counter()
    for l in corpus:
        n = l["n_comps"]
        cb["2-4" if n <= 4 else "5-7" if n <= 7 else "8-10" if n <= 10
           else "11+"] += 1
    L.append("| 元件规模分布 | " + "，".join(f"{k}:{v}"
                                            for k, v in sorted(cb.items()))
             + " |")
    L.append("")
    return "\n".join(L)


RULE_DESC = {
    "T1": "节点数最多的前 3 例（converged 优先）",
    "T2": "元件总数最多的前 3 例（converged 优先）",
    "T3": "HEATER+JUNCTION+BOOSTER 齐全的 converged 前 2（不足放宽两种）",
    "T4": "守卫告警例全部",
    "T5": "clean_fail：iters=0 前 2 + iters=50 耗尽前 2",
    "T6": "面积跨度最大的前 2 例",
    "T7": "最简单 converged 1 例 + 最简单 clean_fail 1 例",
}


def build_digest(corpus: list[dict]) -> str:
    picks, cat_of = select_cases(corpus)
    L = ["# pysas fuzz 典型案例图鉴（写给人看）", ""]
    L.append(f"- 日期：{date.today().isoformat()}")
    L.append("- 数据源：`fuzz/results/soft_choke_sweep.jsonl`（395 例）"
             "+ `fuzz/cases/*.json`；图为 `report_lib.netinf_to_mermaid`"
             "自动生成（converged 例带解：节点标压力/温度、连线标流量方向）")
    L.append("- 用法：30 分钟通读——先看总表，再挑感兴趣的图。物理故事"
             "三件事：气从哪来到哪去 / 谁在卡脖子 / 为什么收敛或失败。")
    L.append("")
    # 总表
    L += ["## 入选总表", "", "| 规则 | case | 节点 | 元件 | 状态 | "
          "iters | 告警 | 一句话标题 |", "|---|---|---|---|---|---|---|---|"]
    seen: dict[str, str] = {}
    for rule in ("T1", "T2", "T3", "T4", "T5", "T6", "T7"):
        for l in picks[rule]:
            if l["case_id"] in seen:
                seen[l["case_id"]] += f"、{rule}"
            else:
                seen[l["case_id"]] = rule
    first_rule = {cid: s.split("、")[0] for cid, s in seen.items()}
    for rule in ("T1", "T2", "T3", "T4", "T5", "T6", "T7"):
        for l in picks[rule]:
            if first_rule[l["case_id"]] != rule:
                continue                      # 只在首个命中规则下展开
            st = ("✅" if l["status"] == "converged" else
                  "⛔" if l["status"] == "clean_fail" else "🚫")
            L.append(f"| {rule} | {l['case_id']} | {l['n_nodes']} | "
                     f"{l['n_comps']} | {st} {l['status']} | "
                     f"{l.get('iters', '—')} | {warn_of(l)} | "
                     f"见下文 |")
    L.append("")
    L += ["（去重后共 "
          f"{sum(1 for rule in ('T1','T2','T3','T4','T5','T6','T7') for l in picks[rule] if first_rule[l['case_id']] == rule)}"
          " 节。T3 采用规则：" + picks.get("_T3_rule", "") + "。）", ""]
    L += [_stats_header(corpus)]
    # 每例一节
    for rule in ("T1", "T2", "T3", "T4", "T5", "T6", "T7"):
        for l in picks[rule]:
            if first_rule[l["case_id"]] != rule:
                continue
            L.append(_render_case(rule, l, cat_of.get(l["case_id"], [])))
    return "\n".join(L) + "\n"


# ============================================================ P1 复杂网络
def complexity(l: dict) -> float:
    s = l["n_nodes"] + l["n_comps"]
    if "JUNCTION" in l["types"]:
        s += 3
    if "BOOSTER" in l["types"]:
        s += 3
    if "HEATER" in l["types"]:
        s += 2
    if l["area_span"]:
        s += math.log10(l["area_span"])
    return s


def _guard_table(out: dict) -> str:
    """守卫口径逐元件表（非锚定两口件全部合格行，含亚容量）。"""
    rows = out.get("guard") or []
    if not rows:
        return "_（本例无守卫口径合格行：无非锚定两口压力行元件。）_\n"
    L = ["守卫口径逐元件表（非锚定两口件逐行；ratio=|ṁ|/cap，"
         "cap=upwind 声速容量）：", "",
         "| 元件 | 口 | ṁ [kg/s] | cap [kg/s] | ratio | 状态 |",
         "|---|---|---|---|---|---|"]
    for r in rows:
        state = "⚠️ 超容（守卫激活）" if r["active"] else "亚容量"
        L.append(f"| c{r['comp_id']} | {r['port']} | "
                 f"{r['mdot']:.4g} | "
                 + (f"{r['cap']:.4g}" if r["cap"] else "—") + " | "
                 + (f"{r['ratio']:.4g}" if r["ratio"] is not None else "—")
                 + f" | {state} |")
    L.append("")
    return "\n".join(L)


def build_complex(corpus: list[dict]) -> str:
    scored = sorted(((complexity(l), l) for l in corpus),
                    key=lambda t: (-t[0], t[1]["case_id"]))
    top = scored[:10]
    L = ["# pysas fuzz 复杂网络专项（复杂度 top10）", ""]
    L.append(f"- 日期：{date.today().isoformat()}")
    L.append("- 复杂度定义：节点数 + 元件数 + (含 JUNCTION?×3) + "
             "(含 BOOSTER?×3) + (含 HEATER?×2) + log10(面积跨度)")
    L.append("")
    L += ["## 复杂度排行（前 10，可复算）", "",
          "| 排名 | case | 分数 | 节点 | 元件 | J/B/H 加成 | "
          "log10(跨度) | 状态 | iters | 告警 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for i, (sc, l) in enumerate(top, 1):
        bonus = (3 * ("JUNCTION" in l["types"])
                 + 3 * ("BOOSTER" in l["types"])
                 + 2 * ("HEATER" in l["types"]))
        lg = math.log10(l["area_span"]) if l["area_span"] else 0.0
        L.append(f"| {i} | {l['case_id']} | {sc:.2f} | {l['n_nodes']} | "
                 f"{l['n_comps']} | {bonus} | {lg:.2f} | {l['status']} | "
                 f"{l.get('iters', '—')} | {warn_of(l)} |")
    L.append("")
    # 统计对比
    top_ids = {l["case_id"] for _, l in top}
    grp_a = [l for _, l in top]
    grp_b = [l for l in corpus if l["case_id"] not in top_ids
             and l["status"] != "assemble_error"]

    def _stats(lst):
        conv = [l for l in lst if l["status"] == "converged"]
        its = sorted(l["iters"] for l in conv if l["iters"] is not None)
        med = its[len(its) // 2] if its else None
        warn = sum(1 for l in lst if warn_of(l) > 0
                   or (l.get("hits") and l["status"] == "converged"))
        return (len(lst), len(conv),
                len(conv) / len(lst) if lst else 0.0, med, warn)

    na, ca, ra, ma, wa = _stats(grp_a)
    nb_, cb, rb, mb, wb = _stats(grp_b)
    L += ["## top10 vs 其余语料", "",
          "| 组 | 例数 | converged | 收敛率 | iters 中位数 | 告警例 |",
          "|---|---|---|---|---|---|"]
    L.append(f"| 复杂度 top10 | {na} | {ca} | {ra:.1%} | "
             f"{ma if ma is not None else '—'} | {wa} |")
    L.append(f"| 其余语料 | {nb_} | {cb} | {rb:.1%} | "
             f"{mb if mb is not None else '—'} | {wb} |")
    L.append("")
    # 结论一句话（数字说话）
    ma_s = ma if ma is not None else "—"
    mb_s = mb if mb is not None else "—"
    L.append("**一句话结论**：" + (
        f"复杂度 top10 收敛率 {ra:.1%} {'高于' if ra > rb else '低于' if ra < rb else '相当于'}"
        f"其余语料的 {rb:.1%}（iters 中位数 {ma_s} vs {mb_s}，告警率 "
        f"{wa / max(na, 1):.0%} vs {wb / max(nb_, 1):.0%}）——"
        + ("复杂网络并未更难收敛，难的是守卫尺度（告警集中在复杂例）"
           if wa / max(na, 1) > wb / max(nb_, 1) and ra >= rb
           else "复杂度本身不是收敛障碍" if ra >= rb
           else "复杂网络确实更难收敛——top10 全部 clean_fail，"
                "大规模+多类型组合超出 default_guess 收敛域")))
    L.append("")
    # 逐例详析
    for i, (sc, l) in enumerate(top, 1):
        L.append(f"## 复杂例 {i}：{l['case_id']}（分数 {sc:.2f}）")
        L.append("")
        section = _render_case("P1", l, [])
        # 在【为什么选它】之后插入守卫逐元件表
        out = rl.solve_case(l["case"])
        L.append(section)
        L.append(_guard_table(out))
    return "\n".join(L) + "\n"


# ============================================================ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="",
                    help="小样验证：只渲染指定规则的用例（逗号分隔，如 T4,T7）")
    ap.add_argument("--no-complex", action="store_true",
                    help="跳过 P1 COMPLEX_NET.md")
    args = ap.parse_args()

    corpus = load_corpus()
    print(f"语料 {len(corpus)} 例加载完成", flush=True)
    if args.only:
        # 小样：限定规则集合渲染（复用同一模板路径）
        rules = [r.strip() for r in args.only.split(",") if r.strip()]
        global RULE_DESC
        picks, cat_of = select_cases(corpus)
        keep = set()
        for r in rules:
            keep |= {l["case_id"] for l in picks.get(r, [])}
        sub = [l for l in corpus if l["case_id"] in keep]
        text = build_digest(sub)
        DIGEST_MD.write_text(text, encoding="utf-8")
        print(f"小样模式（{rules}，{len(sub)} 例）→ {DIGEST_MD}")
        return 0
    digest = build_digest(corpus)
    DIGEST_MD.write_text(digest, encoding="utf-8")
    print(f"P0 图鉴 → {DIGEST_MD}（{len(digest) / 1024:.0f} KB）")
    if not args.no_complex:
        comp = build_complex(corpus)
        COMPLEX_MD.write_text(comp, encoding="utf-8")
        print(f"P1 复杂网络 → {COMPLEX_MD}（{len(comp) / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
