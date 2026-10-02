# -*- coding: utf-8 -*-
"""scipy 独立求解分诊：判定 clean_fail 且重启无效的网络是否可解。

方法：对重启全灭算例，用 scipy.optimize.least_squares（lm/trf，与
pysas 牛顿完全独立的实现）从多个起点极小化缩放残差 max|F̃|。
  · scipy 达到 <1e-8 → 网络有解，pysas mode=1 够不着（鲁棒性发现）；
  · scipy 卡在 >1e-2 → 疑似模型级无解（生成器病态或模型矛盾），
    从"求解器失败"清单中剔除并归入"疑似无解"。
黑箱诊断，不改求解器。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

import common

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"


def scipy_try(sys_, ctx, x0_list):
    from pysas.solver import make_scaling
    from pysas.solver.scaling import ScaledProblem
    sc = make_scaling(sys_, ctx)
    prob = ScaledProblem(sys_, ctx, sc)
    best = (np.inf, None)
    for tag, x0 in x0_list:
        for method in ("trf", "lm"):
            try:
                r = least_squares(prob.residual, np.asarray(x0, float),
                                  method=method, xtol=1e-14, ftol=1e-14,
                                  gtol=1e-14, max_nfev=60 * sys_.n)
            except Exception as e:                       # noqa: BLE001
                continue          # lm 踩 NaN 残差会抛 ValueError，跳过
            res = float(np.max(np.abs(r.fun))) if r.fun.size else 0.0
            if res < best[0]:
                best = (res, f"{tag}/{method}")
    return best


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0,
                    help="仍死算例抽样数（0=全量）")
    ap.add_argument("--jsonl", default=str(RESULTS_DIR / "restart_diag.json"))
    args = ap.parse_args()
    diag = json.load(open(args.jsonl, encoding="utf-8"))
    dead = [d for d in diag["detail"] if not d.get("rescued_by")]
    if args.sample and args.sample < len(dead):
        rng = np.random.default_rng(common.SEED)
        idx = sorted(rng.choice(len(dead), args.sample, replace=False)
                     .tolist())
        dead = [dead[i] for i in idx]
    print(f"重启全灭 {len(dead)} 例 → scipy 独立求解分诊", flush=True)

    from pysas.solver import default_guess
    out_rows = []
    n_solvable = n_nosol = 0
    for d in dead:
        case = json.load(open(HERE / "cases" / f"{d['case_id']}.json",
                              encoding="utf-8"))
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                net, ctx, sys_ = common.build_system(case)
                base = default_guess(sys_, ctx)
        except Exception as e:                           # noqa: BLE001
            out_rows.append({"case_id": d["case_id"],
                             "class": "assemble_error", "err": str(e)})
            continue
        rng = np.random.default_rng([common.SEED, 99, hash(d["case_id"]) % 10**6])
        x0_list = [("base", base)]
        for k in range(3):
            x = base.copy()
            n_p = sys_.n_interior
            anchored = {}
            for model in sys_.models.values():
                anchored.update(model.anchor_P_values())
            for i, nid in enumerate(sys_.interior_ids):
                if nid not in anchored:
                    x[i] *= float(rng.lognormal(0.0, 0.8))
            for k2 in range(sys_.n_T):
                x[sys_.n_interior + k2] = float(rng.uniform(300.0, 900.0))
            x0_list.append((f"rand{k}", x))
        best_res, best_tag = scipy_try(sys_, ctx, x0_list)
        if best_res < 1e-8:
            cls = "SOLVABLE_pysas_missed"
            n_solvable += 1
        elif best_res < 1e-2:
            cls = "MARGINAL"
        else:
            cls = "LIKELY_NO_SOLUTION"
            n_nosol += 1
        out_rows.append({"case_id": d["case_id"], "class": cls,
                         "best_scaled_residual": best_res,
                         "best_start": best_tag})
        print(f"  {d['case_id']}: {cls:24s} best|F̃|={best_res:.3e} ({best_tag})",
              flush=True)

    summary = {"n_dead": len(dead), "n_solvable_pysas_missed": n_solvable,
               "n_likely_no_solution": n_nosol, "rows": out_rows}
    (RESULTS_DIR / "scipy_diag.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
