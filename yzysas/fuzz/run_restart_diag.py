# -*- coding: utf-8 -*-
"""重启诊断：对 A 层 clean_fail 算例做黑箱初值重启实验。

目的：把"default_guess 初值脆弱"与"根本性失败/无解"分开——
  · 重启可救 → 收敛域问题（局部法 + 零流量初值点结构性病态，
    default_guess 文档自认 cond~1e16 仅在其精选算例可自愈）；
  · 重启全灭 → 更硬的失败（疑似无解/ traps），逐例待定性。
只用公开 API（solve 接受自定义 x0），不改求解器。

重启策略（每例最多 5 次尝试，全部记录）:
  R1 压力抖动   未锚节点 p0 ×= lognormal(σ=0.5)，T ×= lognormal(σ=0.2)
  R2 源温就近   T0 初始化改取"最接近的源元件供温"（替代均值），p 同 R1
  R3 流量种子   各口 ṁ += U(−1,1)·0.1·m_ref（打破零流量死点）
  R4 大抖动     p ×= lognormal(σ=1.0)，T ∈ [300,900] 均匀，ṁ 同 R3
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np

import common

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"


def restart_guesses(sys_, ctx, m_ref, rng):
    """yield (tag, x0)：default_guess 打底的四种重启初值。"""
    from pysas.solver import initial_guess
    base = initial_guess(sys_, ctx).copy()   # 等价旧 default_guess（策略 default）
    n_p = sys_.n_interior
    anchored = {}
    for model in sys_.models.values():
        anchored.update(model.anchor_P_values())
    # 源供温池（R2 用）
    supplies = [m.T_supply(ctx) for m in sys_.models.values()
                if len(m.comp.ports) == 1]
    T_lo = min(supplies) if supplies else ctx.T0_default
    T_hi = max(supplies) if supplies else ctx.T0_default

    def jitter(tag, p_sigma, t_mode, m_seed):
        x = base.copy()
        for i, nid in enumerate(sys_.interior_ids):
            if nid not in anchored:            # 未锚压力抖动
                x[i] *= float(rng.lognormal(0.0, p_sigma))
        for k, nid in enumerate(sys_.T_ids):
            j = sys_.n_interior + k
            if t_mode == "uniform":
                x[j] = float(rng.uniform(300.0, 900.0))
            else:                              # "jitter"
                x[j] *= float(rng.lognormal(0.0, 0.2))
            if t_mode == "near_source":
                x[j] = float(rng.uniform(T_lo, T_hi))
        if m_seed:
            off = sys_.n_interior + sys_.n_T
            x[off:] += rng.uniform(-1.0, 1.0, size=x[off:].size) * 0.1 * m_ref
        return tag, x

    yield jitter("R1_pjitter", 0.5, "jitter", False)
    yield jitter("R2_nearsrcT", 0.5, "near_source", False)
    yield jitter("R3_mseed", 0.5, "jitter", True)
    yield jitter("R4_wild", 1.0, "uniform", True)
    # R5 平坦化：全部节点 p = max(锚压)（C2 锚点技巧推广——default_guess
    # 的"锚点均值"起点落在特性 √ 尖点坏盆地；链路 m<<容量 时真解中节点
    # 压≈上游压）。流量/温度保持 default_guess 值。
    p_top = max(anchored.values()) if anchored else 1.0e5
    x5 = base.copy()
    for i in range(sys_.n_interior):
        x5[i] = p_top
    yield ("R5_flat_top", x5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=str(RESULTS_DIR / "a_fuzz.jsonl"))
    args = ap.parse_args()

    recs = [json.loads(l) for l in open(args.jsonl, encoding="utf-8")]
    fails = [r for r in recs if r["status"] == "clean_fail"]
    print(f"clean_fail 算例 {len(fails)} 例，逐例重启实验（每例 5 策略）")

    rescued_by = {"R1_pjitter": 0, "R2_nearsrcT": 0, "R3_mseed": 0,
                  "R4_wild": 0, "R5_flat_top": 0}
    still_dead = []
    detail = []
    for r in fails:
        case = json.load(open(HERE / "cases" / f"{r['case_id']}.json",
                              encoding="utf-8"))
        try:
            net, ctx, sys_ = common.build_system(case)
        except Exception as e:                           # noqa: BLE001
            detail.append({"case_id": r["case_id"], "error": str(e)})
            continue
        rng = np.random.default_rng([common.SEED, 7, r["case_index"]])
        rescued = None
        attempts = []
        for tag, x0 in restart_guesses(sys_, ctx, r.get("m_ref", 1.0), rng):
            # 自定义 x0 走裸 solve 调用（run_solve 固定用 default_guess）；
            # stdout 抑制口径与 common 一致
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    from pysas.solver import solve
                    res = solve(sys_, x0, ctx)
                ok = bool(res.report.converged)
                attempts.append({"tag": tag, "converged": ok,
                                 "iters": res.report.iters,
                                 "res": res.report.final_residual})
                if ok and rescued is None:
                    rescued = tag
            except Exception as e:                       # noqa: BLE001
                attempts.append({"tag": tag, "converged": False,
                                 "error": f"{type(e).__name__}: {e}"})
        if rescued:
            rescued_by[rescued] += 1
        else:
            still_dead.append(r["case_id"])
        detail.append({"case_id": r["case_id"], "iters_orig": r.get("iters"),
                       "rescued_by": rescued, "attempts": attempts})

    n_rescued = sum(rescued_by.values())
    summary = {"n_clean_fail": len(fails), "n_rescued": n_rescued,
               "rescued_by": rescued_by, "n_still_dead": len(still_dead),
               "still_dead": still_dead, "detail": detail}
    (RESULTS_DIR / "restart_diag.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "detail"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
