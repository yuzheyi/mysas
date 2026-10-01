# -*- coding: utf-8 -*-
"""M2 同伦双探针（P2）：对 sweep clean_fail 例做回压延拓可行性测试。

探针 A backpressure：20 步线性回拧——把全部 PRESSURE_BOUNDARY 的
p0_spec 从均值 p_bar 逐步线性拉回原值（lam = k/20, k=1..20），每步
以上一步的解为初值。全程走到 k=20（=原网络）且收敛 = 救活。
探针 B flatline：两步版——先全部压平到 p_bar（1 步），再直接跳回
原值（1 步）。第 2 步收敛 = 救活（≈重启诊断 R5"平坦化"基线）。

输出: fuzz/results/homotopy_probe.jsonl（每例每探针一行，字段固定：
{case_id, probe, lost_at, converged_final, iters_per_step, total_s}）
      fuzz/results/homotopy_probe_summary.json
纪律: pysas/ 零改动；cases/ 只读（探针 A 修改的是内存中的 data，每例
重新 load 防探针间污染）；单例 try/except 全包 + 120s 粗粒度超时。
运行: python -X utf8 run_homotopy_probe.py [--sample N] [--probe A|B]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent      # .../yzysas
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

np.seterr(all="ignore")

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import build_models, netinf_from_dict  # noqa: E402
from pysas.solver import solve                    # noqa: E402

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
RESULTS = HERE / "results"
SWEEP_JSONL = RESULTS / "soft_choke_sweep.jsonl"
PROBE_JSONL = RESULTS / "homotopy_probe.jsonl"
PROBE_SUMMARY = RESULTS / "homotopy_probe_summary.json"

TIME_BUDGET_S = 120.0     # 单例（单探针）粗粒度超时：计步间检查
N_STEPS_A = 20


def solve_quiet(sysm, x0, ctx):
    """安静求解（吞 residual 逐迭代 print + 数值/软壅塞告警）。"""
    buf = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with contextlib.redirect_stdout(buf):
            return solve(sysm, x0, ctx)


def probe_case(case_id: str, probe: str) -> dict:
    """单例单探针。永不抛；返回固定字段行。"""
    t0 = time.perf_counter()
    row = {"case_id": case_id, "probe": probe, "lost_at": None,
           "converged_final": False, "iters_per_step": [], "total_s": 0.0}
    data = json.loads((CASES / f"{case_id}.json").read_text(encoding="utf-8"))
    PBs = [c for c in data["comps"] if c["type"] == "PRESSURE_BOUNDARY"]
    if len(PBs) == 0:
        row["skip"] = "no_pb"
        row["total_s"] = time.perf_counter() - t0
        return row
    p_bar = float(np.mean([pb["params"][0] for pb in PBs]))
    p_orig = [pb["params"][0] for pb in PBs]      # 原值副本（开跑前先存）
    x_prev = None
    timed_out = False

    def build_and_solve():
        net, ctx = netinf_from_dict(data)
        sysm = NetworkSystem(net, build_models(net))
        return solve_quiet(sysm, x_prev, ctx)     # x_prev None → default_guess

    steps = range(1, N_STEPS_A + 1) if probe == "backpressure" else (0, 1)
    for k in steps:
        if probe == "backpressure":
            lam = k / N_STEPS_A
        else:
            lam = 0.0 if k == 0 else 1.0          # flatline：压平→回原值
        for pb, p0 in zip(PBs, p_orig):
            pb["params"][0] = p_bar + lam * (p0 - p_bar)
        try:
            res = build_and_solve()
        except Exception as e:                    # noqa: BLE001
            row["lost_at"] = k
            row["error"] = f"{type(e).__name__}: {e}"
            break
        row["iters_per_step"].append(int(res.report.iters))
        if not res.converged:
            row["lost_at"] = k
            break
        x_prev = res.x
        if time.perf_counter() - t0 > TIME_BUDGET_S and k != steps[-1]:
            timed_out = True                      # 粗粒度超时：停在下步前
            break
    row["converged_final"] = (row["lost_at"] is None and not timed_out
                              and len(row["iters_per_step"]) > 0
                              and x_prev is not None)
    row["total_s"] = round(time.perf_counter() - t0, 3)
    if timed_out:
        row["timeout"] = True
    return row


def clean_fail_ids() -> list[str]:
    out = []
    for l in open(SWEEP_JSONL, encoding="utf-8"):
        if not l.strip():
            continue
        d = json.loads(l)
        if d.get("status") == "clean_fail":
            out.append(d["case_id"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0,
                    help="只跑前 N 例 clean_fail（小样验证）")
    ap.add_argument("--probe", default="", help="只跑 A 或 B（小样用）")
    args = ap.parse_args()

    ids = clean_fail_ids()
    if args.sample:
        ids = ids[:args.sample]
    probes = [args.probe] if args.probe else ["backpressure", "flatline"]
    print(f"clean_fail {len(ids)} 例 × 探针 {probes}", flush=True)

    PROBE_JSONL.parent.mkdir(exist_ok=True)
    n_flat1 = 0                     # 探针A 首步（lam=0.05 近压平）收敛数
    n_done = 0
    with open(PROBE_JSONL, "w", encoding="utf-8") as f:   # 幂等覆盖
        for i, cid in enumerate(ids):
            for probe in probes:
                try:
                    row = probe_case(cid, probe)
                except Exception as e:            # noqa: BLE001
                    row = {"case_id": cid, "probe": probe, "lost_at": None,
                           "converged_final": False, "iters_per_step": [],
                           "total_s": -1.0,
                           "error": f"driver {type(e).__name__}: {e}"}
                if probe == "backpressure" and row["iters_per_step"]:
                    n_flat1 += 1
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
            n_done += 1
            if (i + 1) % 20 == 0 or i + 1 == len(ids):
                print(f"  [{i + 1}/{len(ids)}] 完成", flush=True)
    print(f"完成 {n_done} 例；探针A 首步（lam=0.05 近压平）有解 "
          f"{n_flat1} 例")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
