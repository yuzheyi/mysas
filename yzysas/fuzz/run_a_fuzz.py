# -*- coding: utf-8 -*-
"""A 层驱动：随机拓扑 fuzz（默认 300 例，固定种子可复现）。

用法: python -X utf8 run_a_fuzz.py [--cases 300] [--seed 20260930]
产出: fuzz/cases/A####.json（全部算例，可复现）
      fuzz/results/a_fuzz.jsonl（逐例结果）
      fuzz/results/a_fuzz_summary.json（统计汇总）
      fuzz/results/crashes/A####.txt（异常完整 traceback）
每例只判三件事：不抛未捕获异常 / solve 收敛或干净报失败 / 解物理合法（B 层审计）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

import common
from gen_random import gen_case

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
RESULTS_DIR = HERE / "results"
CRASH_DIR = RESULTS_DIR / "crashes"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--seed", type=int, default=common.SEED)
    args = ap.parse_args()

    CASES_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)
    CRASH_DIR.mkdir(exist_ok=True)

    jsonl_path = RESULTS_DIR / "a_fuzz.jsonl"
    summary_path = RESULTS_DIR / "a_fuzz_summary.json"
    n_exhausted = 0                     # 迭代耗尽型 clean_fail 的档位计数

    status_cnt = Counter()
    exc_kinds = Counter()
    finding_cnt = Counter()
    inv4_modes = Counter()
    iters_hist = Counter()
    jet_margins = []
    wall_times = []
    lines = []

    for i in range(args.cases):
        case_id = f"A{i + 1:04d}"
        rng = np.random.default_rng([args.seed, i])   # 每例独立子流，可复现
        case = gen_case(rng)
        meta = case.pop("_meta", {})
        (CASES_DIR / f"{case_id}.json").write_text(
            json.dumps(case, ensure_ascii=False, indent=1), encoding="utf-8")

        t0 = time.perf_counter()
        out = common.run_solve(case)
        wall = time.perf_counter() - t0
        wall_times.append(wall)
        status = out["status"]
        status_cnt[status] += 1

        findings, stats = [], {}
        rec = {"case_id": case_id, "seed": args.seed, "case_index": i,
               "topology": meta.get("topology"),
               "n_nodes": meta.get("n_nodes"),
               "n_comps": len(case["comps"]), "status": status,
               "wall_s": round(wall, 3)}
        if status in ("converged", "clean_fail"):
            rec["iters"] = out.get("iters")
            rec["final_residual_scaled"] = out.get("final_residual_scaled")
            rec["max_F_raw"] = out.get("max_F_raw")
            rec["m_ref"] = out.get("m_ref")
            iters_hist[out.get("iters")] += 1
            if status == "clean_fail":
                n_exhausted += 1
        if status == "converged":
            findings, stats = common.audit_case(case, out)
            rec.update({k: stats.get(k) for k in
                        ("scale", "max_continuity", "max_identity_err",
                         "min_jet_margin_rel", "inv4", "inv4_worst_over_K")})
            if isinstance(stats.get("min_jet_margin_rel"), float):
                jet_margins.append(stats["min_jet_margin_rel"])
            inv4_modes[str(stats.get("inv4"))] += 1
        elif status in ("assemble_error", "solve_error", "postprocess_error"):
            exc = out.get("exception", "?")
            exc_kinds[exc.split(":")[0] + ": " + exc.split(":", 1)[-1].strip()[:120]] += 1
            (CRASH_DIR / f"{case_id}.txt").write_text(
                f"case: {case_id}\ncase_json: cases/{case_id}.json\n"
                f"stage/status: {status}\n\n{out.get('traceback', '')}",
                encoding="utf-8")
        for f in findings:
            finding_cnt[f"{f['severity']}/{f['invariant']}"] += 1
        if findings:
            rec["findings"] = findings
        lines.append(json.dumps(rec, ensure_ascii=False))
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{args.cases}  状态分布 "
                  f"{dict(status_cnt)}", flush=True)

    jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    conv = status_cnt["converged"]
    summary = {
        "seed": args.seed, "cases": args.cases,
        "status_counts": dict(status_cnt),
        "convergence_rate": round(conv / args.cases, 4),
        "clean_fail_note": (
            f"{n_exhausted} 例 solve 正常返回但牛顿未收敛（干净失败）"
            if n_exhausted else "无 clean_fail"),
        "exception_kinds": dict(exc_kinds),
        "findings_by_severity_invariant": dict(finding_cnt),
        "inv4_modes": dict(inv4_modes),
        "iters_histogram": {str(k): v for k, v in sorted(
            iters_hist.items(), key=lambda kv: (kv[0] is None, kv[0]))},
        "wall_s_total": round(sum(wall_times), 1),
        "wall_s_mean": round(float(np.mean(wall_times)), 3),
        "jet_margin_rel_min": (min(jet_margins) if jet_margins else None),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                            encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
