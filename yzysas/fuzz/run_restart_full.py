# -*- coding: utf-8 -*-
"""全量 300 例 restart=True 对照：救活增量 + 死例漂移 + 总耗时（2026-10-02）。

输出 fuzz/results/restart_full.json：逐例
{case_id, status, restart, iters, wall_s}
对照 a_fuzz.jsonl 基准（注意：基准是早期代码快照，仅作参考量级）。

用法: cd yzysas/fuzz && python -X utf8 run_restart_full.py
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np                      # noqa: E402
np.seterr(all="ignore")

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import build_models, netinf_from_dict  # noqa: E402
from pysas.solver import initial_guess, solve     # noqa: E402

OUT = HERE / "results" / "restart_full.json"


def main() -> int:
    rows = []
    t_all = time.perf_counter()
    cases = sorted((HERE / "cases").glob("A*.json"))
    print(f"{len(cases)} 例 restart=True 全量", flush=True)
    for i, p in enumerate(cases):
        cid = p.stem
        data = json.loads(p.read_text(encoding="utf-8"))
        t0 = time.perf_counter()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import contextlib
                import io
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    net, ctx = netinf_from_dict(data)
                    sysm = NetworkSystem(net, build_models(net))
                    x0 = initial_guess(sysm, ctx)
                    res = solve(sysm, x0, ctx, restart=True)
            rows.append(dict(case_id=cid,
                             status="converged" if res.converged
                             else "clean_fail",
                             restart=res.restart,
                             iters=int(res.report.iters),
                             wall_s=round(time.perf_counter() - t0, 3)))
        except Exception as e:                    # noqa: BLE001
            rows.append(dict(case_id=cid, status="solve_error",
                             error=f"{type(e).__name__}: {e}",
                             wall_s=round(time.perf_counter() - t0, 3)))
        if (i + 1) % 30 == 0:
            print(f"  [{i + 1}/{len(cases)}]", flush=True)
    total = time.perf_counter() - t_all

    n_conv = sum(1 for r in rows if r["status"] == "converged")
    n_resc = sum(1 for r in rows if r.get("restart"))
    by_probe = {}
    for r in rows:
        if r.get("restart"):
            by_probe[r["restart"]] = by_probe.get(r["restart"], 0) + 1
    wall_resc = sum(r["wall_s"] for r in rows if r.get("restart"))
    OUT.write_text(json.dumps(
        dict(rows=rows, total_s=round(total, 1),
             n_converged=n_conv, n_restarted=n_resc,
             by_probe=by_probe), ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"收敛 {n_conv}/{len(rows)}（含重启救活 {n_resc}：{by_probe}）")
    print(f"总耗时 {total:.1f}s（救活例合计 {wall_resc:.1f}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
