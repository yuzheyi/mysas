# -*- coding: utf-8 -*-
"""软壅塞守卫补测轮（59c78a7 之后的深挖）：告警例 K 分解 + assemble_error
分诊 + 初值敏感性对照 → fuzz/results/soft_choke_followup.json。

三个任务块（报告侧新章节由 run_corpus_sweep.py 生成器读取本产物渲染）:
  任务一  三告警例（A0171/A0093/A0203）K 标度分解:
          K=κ·p_ref/m_ref 实算、理论钉位 cap+Δp/K vs 实测 |ṁ| 闭合检查、
          m_ref 驱动者 top3、反事实推演（HEATER→ORIFICE(A_min,Cd=1) 对照解）
          ——判定"守卫价值=无根变有根"还是"解已良定、守卫只添人工根"。
  任务二  assemble_error 分诊: 逐例异常类型/消息/分类（NetworkSystem 适定性
          断言 ValueError=设计内拦截；其它=疑似缺陷）+ 无解 mermaid（拓扑图）。
  任务三  scipy_not_ok ∩ 含 HEATER/JUNCTION/BOOSTER 家族 14 例 × 四组初值
          对照（a default_guess / b 零流量 / c ×0.5 / c ×2）:
          converged/iters/告警/worst ratio + 收敛组两两缩放坐标 max|dx|；
          b) 收敛而 a) 不收敛 → "容量初值负面清单"。

运行: python -X utf8 run_soft_choke_followup.py [--sample N] [--skip t1|t2|t3 ...]
产出: fuzz/results/soft_choke_followup.json（幂等覆盖）。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

import report_lib as rl

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CASES = HERE / "cases"
SWEEP_JSONL = RESULTS / "soft_choke_sweep.jsonl"
SWEEP_SUMMARY = RESULTS / "soft_choke_sweep_summary.json"
FOLLOWUP_JSON = RESULTS / "soft_choke_followup.json"

WARN_CASES = ["A0171", "A0093", "A0203"]
FAMILY_TYPES = {"HEATER", "JUNCTION", "BOOSTER"}
ELEM_NAMES = {0: "ORIFICE", 2: "PIPE", 5: "PRESSURE_BOUNDARY",
              6: "MASS_SOURCE", 7: "BOOSTER", 8: "HEATER", 9: "JUNCTION",
              10: "AREA_CHANGE", 12: "SURROGATE_FLOW"}


def _load_case(cid: str) -> dict:
    return json.loads((CASES / f"{cid}.json").read_text(encoding="utf-8"))


def _r(v, n=5):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    return float(f"{float(v):.{n}g}")


# ============================================================ 任务一
def m_ref_drivers(sysm, ctx, topn: int = 3) -> list[dict]:
    """m_ref 驱动者：全网口容量 top（按守卫参考量 (p_ref, t_ref) 现算）。

    t_ref 按实现的同口径重算（_init_guard_refs：单口源 T_supply 池的 max，
    无源回落 T0_default——实现未把 t_ref 存成属性，此处独立重算）。
    """
    pg = max(float(sysm._p_ref_for_guard), 1.0)
    supplies = [m.T_supply(ctx) for m in sysm.models.values()
                if len(m.comp.ports) == 1]
    tg = max(max(supplies) if supplies else ctx.T0_default, 10.0)
    caps = []
    for model in sysm.models.values():
        for j, port in enumerate(model.comp.ports):
            if port.area > 0.0:
                caps.append((float(model.choke_capacity(pg, tg, ctx, j)),
                             model.comp.comp_id, j))
    caps.sort(reverse=True)
    return [{"comp": c, "port": j,
             "elem": ELEM_NAMES.get(sysm.models[c].elem_type,
                                    str(sysm.models[c].elem_type)),
             "cap_at_ref": _r(cap)} for cap, c, j in caps[:topn]]


def k_decompose(cid: str) -> dict:
    """单告警例 K 分解 + 闭合检查 + 反事实推演。"""
    case = _load_case(cid)
    out = rl.solve_case(case)
    d: dict = {"case_id": cid, "status": out.get("status"),
               "iters": out.get("iters"),
               "warn_calls": out.get("warn_soft_choke")}
    if out.get("status") != "converged":
        d["error"] = "上轮为 converged，复跑未复现——需人工检查"
        return d
    sysm, x, ctx = out["sysm"], out["x"], out["ctx"]
    pg = float(sysm._p_ref_for_guard)
    mg = float(sysm._m_ref_for_guard)
    k_guard = rl.SOFT_CHOKE_KAPPA * pg / mg
    d["K"] = _r(k_guard)
    d["p_ref_guard"] = _r(pg)
    d["m_ref_guard"] = _r(mg)
    d["m_ref_drivers"] = m_ref_drivers(sysm, ctx)
    d["hits"] = []
    for r in out.get("guard_active", []):
        comp = next(c for c in sysm.net.comps if c.comp_id == r["comp_id"])
        nids = [p.node_id for p in comp.ports]
        dp = abs(float(x[sysm.p_idx_of_node[nids[0]]]
                       - x[sysm.p_idx_of_node[nids[1]]]))
        theo = r["cap"] + dp / k_guard
        cap = r["cap"]
        # 偏差率守恒式：ratio−1 = (Δp/p_ref)·(m_ref/(κ·cap))（K 单标度的
        # 直接推论——两因子分别=该元件承受的相对压差 × 元件容量/全网容量比）
        ratio_minus1_pred = (dp / pg) * (mg / (rl.SOFT_CHOKE_KAPPA * cap))
        d["hits"].append({
            "comp": r["comp_id"], "port": r["port"],
            "elem": ELEM_NAMES.get(comp.elem_type, str(comp.elem_type)),
            "mdot": _r(abs(r["mdot"])), "cap": _r(cap),
            "ratio": _r(r["ratio"]), "dp_across": _r(dp),
            "theo_pin": _r(theo), "theo_ratio": _r(theo / cap),
            "close_resid": _r(abs(abs(r["mdot"]) - theo), 3),
            "dp_over_pref": _r(dp / pg),
            "mref_over_cap": _r(mg / cap),
            "ratio_minus1_pred": _r(ratio_minus1_pred),
            "nodes": nids,
            "p_nodes": [float(x[sysm.p_idx_of_node[n]]) for n in nids],
        })
    # ---- 反事实推演：HEATER → ORIFICE(同面积, β=1, Cd=1) ----
    cf_records = []
    for h in d["hits"]:
        cf = json.loads(json.dumps(case))       # 深拷贝
        a_min = None
        for c in cf["comps"]:
            if c["id"] == h["comp"]:
                a_min = min(p["area"] for p in c["ports"])
                c["type"] = "ORIFICE"
                c["params"] = [1.0, 1.0]
                break
        out2 = rl.solve_case(cf)
        rec = {"surrogate": f"ORIFICE(A={_r(a_min)}, Cd=1)",
               "status": out2.get("status"),
               "iters": out2.get("iters"),
               "warn_calls": out2.get("warn_soft_choke"),
               "worst_ratio": out2.get("worst_ratio")}
        if out2.get("status") == "converged":
            s2, x2 = out2["sysm"], out2["x"]
            midx = s2.m_idx_of_port[(h["comp"], h["port"])]
            rec["mdot_same_port"] = _r(abs(float(x2[midx])))
            rec["ratio_vs_guard"] = _r(abs(float(x2[midx]))
                                       / h["mdot"]) if h["mdot"] else None
            nids = h["nodes"]
            rec["dp_across"] = _r(abs(float(x2[s2.p_idx_of_node[nids[0]]]
                                          - x2[s2.p_idx_of_node[nids[1]]])))
            # 物理壅塞对照：orifice 喉口流量 / 声速容量
            model2 = s2.models[h["comp"]]
            p_up = max(float(x2[s2.p_idx_of_node[nids[0]]]),
                       float(x2[s2.p_idx_of_node[nids[1]]]))
            cap2 = float(model2.choke_capacity(max(p_up, 1.0), 600.0, ctx, 0))
            rec["cap_at_upwind"] = _r(cap2)
            rec["mdot_over_cap"] = _r(abs(float(x2[midx])) / cap2) \
                if cap2 else None
        cf_records.append(rec)
    d["counterfactual"] = cf_records
    try:
        d["mermaid"] = rl.netinf_to_mermaid(case, x=out["x"], sysm=sysm)
    except Exception as e:                    # noqa: BLE001
        d["mermaid_error"] = f"{type(e).__name__}: {e}"
    return d


def run_task1() -> list[dict]:
    out = []
    for cid in WARN_CASES:
        try:
            out.append(k_decompose(cid))
        except Exception as e:                # noqa: BLE001
            out.append({"case_id": cid, "error": f"{type(e).__name__}: {e}"})
    return out


# ============================================================ 任务二
DESIGN_MARKS = ("锚定", "适定", "孤立", "无压力锚", "拓扑")


def classify_assemble(exc: str) -> str:
    """assemble_error 分类：设计内拦截 vs 疑似缺陷（关键词口径）。"""
    if exc is None:
        return "unknown"
    if "ValueError" in exc and any(k in exc for k in DESIGN_MARKS):
        return "design_intended"
    if "FileNotFoundError" in exc or "模型文件不存在" in exc:
        return "design_intended"     # 代理模型缺文件=读入期校验设计行为
    return "suspect"


def run_task2() -> list[dict]:
    lines = [json.loads(l) for l in open(SWEEP_JSONL, encoding="utf-8")
             if l.strip()]
    ae = [l for l in lines if l.get("status") == "assemble_error"]
    out = []
    for l in ae:
        cid = l["case_id"]
        case = _load_case(cid)
        # 复跑取完整 traceback（sweep jsonl 只存了单行 exception）
        fresh = rl.solve_case(case)
        exc = fresh.get("exception") or l.get("exception") or ""
        tb = fresh.get("traceback") or ""
        cls = classify_assemble(exc + " " + tb)
        rec = {"case_id": cid, "exception": exc,
               "class": cls,
               "verdict": ("设计内拦截（组装期适定性校验，预期行为）"
                           if cls == "design_intended"
                           else "疑似缺陷——需人工检查"),
               "stage_line": "组装期（NetworkSystem 构造/netinf 读入）",
               "source": "C 层边界酷刑产线（run_c_torture 设计例）"
                         if cid.startswith("C") else "A 层随机生成器"}
        try:
            rec["mermaid"] = rl.netinf_to_mermaid(case)   # 无解→无向拓扑图
        except Exception as e:                # noqa: BLE001
            rec["mermaid_error"] = f"{type(e).__name__}: {e}"
        # traceback 尾段（触发断言的调用链）
        if tb:
            rec["tb_tail"] = tb.strip().splitlines()[-3:]
        out.append(rec)
    return out


# ============================================================ 任务三
def init_variants(sysm, ctx) -> dict[str, np.ndarray]:
    """四组初值：a default_guess / b 零流量 / c ×0.5 / c ×2（流量段）。"""
    from pysas.solver import default_guess
    base = default_guess(sysm, ctx)
    lo = sysm.n_interior + sysm.n_T
    out = {"a_default": base.copy(), "b_zeroflow": base.copy(),
           "c_half": base.copy(), "c_double": base.copy()}
    out["b_zeroflow"][lo:] = 0.0
    out["c_half"][lo:] *= 0.5
    out["c_double"][lo:] *= 2.0
    return out


def solve_variant(case: dict, x0: np.ndarray) -> dict:
    out = rl.solve_case(case, x0=x0)
    return {"converged": out.get("status") == "converged",
            "status": out.get("status"),
            "iters": out.get("iters"),
            "warn_calls": out.get("warn_soft_choke"),
            "worst_ratio": _r(out.get("worst_ratio")),
            "n_hits": len(out.get("guard_active", [])),
            "exception": out.get("exception"),
            "_out": out}


def run_task3(sample: int | None = None) -> dict:
    summary = json.loads(SWEEP_SUMMARY.read_text(encoding="utf-8"))
    not_ok = summary.get("scipy_not_ok_ids", [])
    fam = []
    for cid in not_ok:
        try:
            case = _load_case(cid)
        except Exception:                     # noqa: BLE001
            continue
        types = {c["type"] for c in case["comps"]}
        if types & FAMILY_TYPES:
            fam.append(cid)
    if sample:
        fam = fam[:sample]
    rows = []
    for cid in fam:
        case = _load_case(cid)
        first = rl.solve_case(case)           # a 情形（取 scaling 供两两对照）
        if first.get("sysm") is None:
            rows.append({"case_id": cid, "error": first.get("exception")})
            continue
        sysm, ctx = first["sysm"], first["ctx"]
        variants = init_variants(sysm, ctx)
        res = {}
        for tag, x0 in variants.items():
            try:
                res[tag] = solve_variant(case, x0)
            except Exception as e:            # noqa: BLE001
                res[tag] = {"converged": False, "status": "driver_error",
                            "exception": f"{type(e).__name__}: {e}"}
        # 两两缩放坐标 max|dx|（以 a 的缩放层为参照）
        pair_dx = {}
        conv = {t: r for t, r in res.items()
                if r.get("converged") and r["_out"].get("x") is not None}
        tags = list(conv)
        scaling = first["res"].scaling
        for i, t1 in enumerate(tags):
            for t2 in tags[i + 1:]:
                x1 = np.asarray(conv[t1]["_out"]["x"], float)
                x2 = np.asarray(conv[t2]["_out"]["x"], float)
                dx = float(np.max(np.abs(scaling.to_scaled(x1)
                                         - scaling.to_scaled(x2))))
                pair_dx[f"{t1}|{t2}"] = _r(dx, 4)
        for r in res.values():
            r.pop("_out", None)
        # a 情形 mermaid（收敛用解图；否则无向拓扑图）
        mer = None
        try:
            if first.get("x") is not None and first.get("status") == "converged":
                mer = rl.netinf_to_mermaid(case, x=first["x"], sysm=sysm)
            else:
                mer = rl.netinf_to_mermaid(case)
        except Exception as e:                # noqa: BLE001
            mer = None
        rows.append({"case_id": cid, "variants": res, "pair_dx_scaled": pair_dx,
                     "same_root": (all(v < 1e-6 for v in pair_dx.values())
                                   if pair_dx else None),
                     "negative_list": (res["b_zeroflow"].get("converged")
                                       and not res["a_default"].get("converged")),
                     "reverse": (res["a_default"].get("converged")
                                 and not res["b_zeroflow"].get("converged")),
                     "mermaid_a": mer})
    neg = [r["case_id"] for r in rows if r.get("negative_list")]
    return {"family": fam, "rows": rows, "negative_list": neg,
            "n_reverse": sum(1 for r in rows if r.get("reverse")),
            "n_same_root": sum(1 for r in rows
                               if r.get("same_root") is True),
            "n_diff_root": sum(1 for r in rows
                               if r.get("same_root") is False)}


# ============================================================ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0,
                    help="任务三只取前 N 例（小样验证用）")
    ap.add_argument("--skip", nargs="*", default=[],
                    help="跳过任务块 t1/t2/t3")
    args = ap.parse_args()

    out: dict = {}
    if "t1" not in args.skip:
        print("[t1] 三告警例 K 分解 + 反事实 …", flush=True)
        out["task1_k_decomp"] = run_task1()
        for d in out["task1_k_decomp"]:
            h0 = (d.get("hits") or [{}])[0]
            print(f"  {d['case_id']}: K={d.get('K')} "
                  f"ratio={h0.get('ratio')} theo_ratio={h0.get('theo_ratio')} "
                  f"close={h0.get('close_resid')} "
                  f"cf={[(c.get('status'), c.get('mdot_over_cap')) for c in d.get('counterfactual', [])]}")
    if "t2" not in args.skip:
        print("[t2] assemble_error 分诊 …", flush=True)
        out["task2_assemble"] = run_task2()
        for d in out["task2_assemble"]:
            print(f"  {d['case_id']}: {d['class']} — {d['exception'][:60]}")
    if "t3" not in args.skip:
        print(f"[t3] 初值敏感性对照（sample={args.sample or '全部'}）…",
              flush=True)
        out["task3_init_sens"] = run_task3(args.sample or None)
        t3 = out["task3_init_sens"]
        for r in t3["rows"]:
            vs = r.get("variants", {})
            print(f"  {r['case_id']}: " + " ".join(
                f"{t}={'C' if v.get('converged') else v.get('status', '—')[:4]}"
                f"({v.get('iters')},w{v.get('warn_calls')},"
                f"r={v.get('worst_ratio')})"
                for t, v in vs.items())
                + (f"  pair_dx={r['pair_dx_scaled']}" if r.get("pair_dx_scaled")
                   else ""))
        print(f"  负面清单(b过a不过)={t3['negative_list']}  "
              f"反向(a过b不过)={t3['n_reverse']}例  "
              f"同根={t3['n_same_root']} 不同根={t3['n_diff_root']}")

    FOLLOWUP_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写出 {FOLLOWUP_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
