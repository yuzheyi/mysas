# -*- coding: utf-8 -*-
"""fuzz 族关键四例回归快查（2026-10-02，maxT 口径 + 告警 + 初值修复后）。

口径与上轮交付验收相同（upwind cap 对照），验证求解器改动后行为不劣化：
A0004（多尺度曾 iters=0 冻结→修后 1.024）、A0199（曾报表口径）、
A0257（junction 互锁→豁免）、A0275（极端多尺度 M2 领地，允许不收敛）。

用法: cd yzysas/fuzz && python -X utf8 run_key4_regress.py
判读: A0004/A0199/A0257 收敛且告警数合理；A0275 允许 clean_fail。
"""
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pysas.assembly import NetworkSystem
from pysas.io import build_models
from pysas.io.netinf import netinf_from_dict
from pysas.solver import initial_guess, solve

CASES = ["A0004", "A0199", "A0257", "A0275"]
HERE = Path(__file__).resolve().parent

for name in CASES:
    data = json.loads((HERE / "cases" / f"{name}.json").read_text(
        encoding="utf-8"))
    net, ctx = netinf_from_dict(data)
    sysm = NetworkSystem(net, build_models(net))
    x0 = initial_guess(sysm, ctx)   # 等价旧 default_guess（策略 default）
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        res = solve(sysm, x0, ctx)
        soft = [x for x in w if "软壅塞" in str(x.message)]
    worst = 0.0
    for comp in net.comps:
        model = sysm.models[comp.comp_id]
        if model.anchor_P_values() or len(comp.ports) != 2:
            continue
        for j, u in enumerate(model.row_units):
            if u != 1 or j >= len(comp.ports):
                continue
            m_a = res.x[model._m_idx[j]]
            if abs(m_a) <= 0.0:
                continue
            if m_a > 0.0:
                k_up = j
            else:
                ins = [k for k in range(len(comp.ports))
                       if res.x[model._m_idx[k]] > 0.0]
                if not ins:
                    continue
                k_up = max(ins, key=lambda k: model._total_p(res.x, ctx, k))
            cap = model.choke_capacity(
                max(model._total_p(res.x, ctx, k_up), 1.0),
                max(model._total_t(res.x, ctx, k_up), 10.0), ctx, j)
            if cap > 0.0:
                worst = max(worst, abs(m_a) / cap)
    print(f"{name}: converged={res.converged} iters={res.report.iters} "
          f"两口件worst|m|/cap_up={worst:.3f} 告警数={len(soft)}")
