# -*- coding: utf-8 -*-
"""C 层边界酷刑：5 项定向 torture + 已知非 bug 复核。

用法: python -X utf8 run_c_torture.py
产出: fuzz/cases/C*_*.json（每例可复现算例）
      fuzz/results/c_torture.json（逐例结果 + 判定）
C1 背压扫 2.8e5→1.2e5（含历史教训 220k→215k 对）：出料口 ps/Ma 连续，
   允许壅塞 kink、不允许跳变（跳变判据 |ΔMa|>max(0.15, 10×中位步幅)）。
C2 面积比悬殊串联 1e-2:1e-6（含三级、极压比、Cd=1；另含已知非 bug 的
   等面积双壅塞演示——期望干净失败，不计违规）。
C3 零压差网络：期望 ṁ≡0 干净解（|ṁ| ≤ 1e-9·m_ref）。
C4 高低压对调流反转：ṁ 应近似反对称、节点 p0 镜像。容差说明：pipe
   的 ρs/μ 取上游口恢复静参数，反向时上游换边，O(可压缩性) 不对称
   是模型属性——P2 线划在相对 1e-2，1e-4~1e-2 记 SUSPECT 量化观察。
C5 全流量边界（无 PRESSURE_BOUNDARY）：组装期必须 ValueError（正确
   行为断言；不报错才是 bug）。
复核项：SURROGATE_FLOW 缺 npz 组装期报错 = 设计行为（勿误报清单）。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import common

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
RESULTS_DIR = HERE / "results"


# ---------------------------------------------------------------- 构件
def pb(cid, node, p0, t0=600.0):
    return {"id": cid, "type": "PRESSURE_BOUNDARY",
            "ports": [{"area": 0.0, "node": node}], "params": [p0, t0]}


def orif(cid, a, b, area, cd=0.8):
    return {"id": cid, "type": "ORIFICE",
            "ports": [{"area": area, "node": a}, {"area": area, "node": b}],
            "params": [1.0, cd]}


def pipe(cid, a, b, area, length=0.5, eps=1e-6):
    d = 2.0 * math.sqrt(area / math.pi)
    return {"id": cid, "type": "PIPE",
            "ports": [{"area": area, "node": a}, {"area": area, "node": b}],
            "params": [length, d, eps]}


def areachg(cid, a, b, a1, a2, zeta=1.0):
    return {"id": cid, "type": "AREA_CHANGE",
            "ports": [{"area": a1, "node": a}, {"area": a2, "node": b}],
            "params": [zeta]}


def ms(cid, node, mspec, t0=600.0):
    return {"id": cid, "type": "MASS_SOURCE",
            "ports": [{"area": 0.0, "node": node}], "params": [mspec, t0]}


def make_case(nodes, comps):
    return {"gas": {"type": "IdealGas", "T0_default": 600.0},
            "nodes": [{"id": i} for i in range(nodes)], "comps": comps}


def _pstate_of(out, comp_id, j):
    """(comp_id, 口 j) → PortState（发号序定位）。"""
    for i, (c, jj, _p) in enumerate(common.port_rows(out["net"])):
        if c.comp_id == comp_id and jj == j:
            return out["pstates"][i]
    raise KeyError((comp_id, j))


def _run(case_id, case):
    (CASES_DIR / f"{case_id}.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=1), encoding="utf-8")
    out = common.run_solve(case)
    findings, stats = ([], {})
    if out["status"] == "converged":
        findings, stats = common.audit_case(case, out)
    return out, findings, stats


# ================================================================ C1
def _solve_plane(fam_mk, p_back):
    case = make_case(2, [pb(0, 0, 3.0e5, 600.0), fam_mk(1, 0, 1),
                         pb(2, 1, float(p_back), 600.0)])
    out = common.run_solve(case)
    if out["status"] != "converged":
        return {"status": out["status"]}
    st = _pstate_of(out, 1, 1)
    return {"status": "converged", "Ma": float(st.mach_number),
            "ps": float(st.static_pressure), "choked": bool(st.choked),
            "mdot": float(st.mass_flow)}


def _refine_worst(mk, lo, hi, levels=(20, 25), tol=0.05):
    """对最大步幅区间递归细化；最细级步幅 ≤ tol 判连续 kink。
    返回 (final_max_step, detail)。真跳变在任意细化下步幅不降。"""
    detail = []
    cur_lo, cur_hi = lo, hi
    worst = abs(1.0)                     # 占位
    for lv, n in enumerate(levels):
        pts = [cur_lo + (cur_hi - cur_lo) * k / n for k in range(n + 1)]
        mas = []
        for p in pts:
            r = _solve_plane(mk, p)
            mas.append(r.get("Ma") if r["status"] == "converged" else None)
            detail.append({"p_back": p, **r})
        if any(m is None for m in mas):
            return None, detail          # 细化段出现不收敛 → 单列 FAIL
        steps = [abs(b - a) for a, b in zip(mas[:-1], mas[1:])]
        worst = max(steps)
        i = max(range(len(steps)), key=lambda k: steps[k])
        cur_lo, cur_hi = pts[i], pts[i + 1]
        if worst <= tol:
            return worst, detail
    return worst, detail


def torture_c1():
    """背压扫描跨壅塞阈值：孔板族 + 管族。

    两级判据：粗扫 5 kPa × 33 点（覆盖历史教训 220k→215k 对）；最大
    |ΔMa| 步长处自适应加密到 250 Pa——连续 kink 细化下步幅线性趋零，
    真跳变保持 ~0.3 不动。判跳变看细化级，不看粗扫（粗扫的 0.30 大步
    可能只是壅塞铰链恰落在步内）。
    """
    results = []
    for fam, mk in (("orifice",
                     lambda cid, a, b: orif(cid, a, b, 1e-4, 0.8)),
                    ("pipe",
                     lambda cid, a, b: pipe(cid, a, b, 1e-4, 0.5))):
        p_backs = [round(2.8e5 - 5e3 * k) for k in range(33)]  # →1.2e5
        sweeps = []
        for k, p_back in enumerate(p_backs):
            rec = {"p_back": p_back, **_solve_plane(mk, p_back)}
            sweeps.append(rec)
        mas = [s.get("Ma") for s in sweeps]
        steps = [abs(b - a) for a, b in zip(mas[:-1], mas[1:])
                 if a is not None and b is not None]
        i_max = max(range(len(steps)), key=lambda i: steps[i]) \
            if steps else 0
        # 两级细化：最大步长区间 20 等分，仍超阈再取最坏子区间 25 等分
        # ——连续 kink 细化下步幅线性趋零；真跳变保持不减。
        worst_fine, fine = _refine_worst(mk, p_backs[i_max],
                                         p_backs[i_max + 1])
        fine_ok = worst_fine is not None
        fine_max_step = worst_fine
        all_conv = all(s["status"] == "converged" for s in sweeps)
        # 真跳变判据：细化到最细级仍 |ΔMa| > 0.05
        jump = (not fine_ok) or fine_max_step > 0.05
        # Ma 有界性（报表口径不得超 1）
        ma_over = [(s["p_back"], s["Ma"]) for s in sweeps + fine
                   if s.get("status") == "converged"
                   and s.get("Ma", 0) > 1 + 1e-6]
        verdict = "PASS" if (all_conv and fine_ok and not jump
                             and not ma_over) else "FAIL"
        results.append({
            "family": fam, "n_sweeps": len(p_backs),
            "n_converged": sum(1 for s in sweeps
                               if s["status"] == "converged"),
            "coarse_max_step_Ma": max(steps, default=0.0),
            "refine_range": [p_backs[i_max], p_backs[i_max + 1]],
            "refine_max_step_Ma": fine_max_step,
            "jump_confirmed": jump, "ma_over_1": ma_over,
            "sweeps": sweeps, "fine": fine,
            "verdict": verdict})
    return results


# ================================================================ C2
def _chain_anchor(case, p_hi, p_lo, t0=600.0):
    """链式解析锚（C2 系列全是 0→末端串行链）：fsolve 解中节点压力与
    链路流量——孔板用元件自身 _ideal_mass_flow（含亚临界支，C2_02
    这类非壅塞链由此覆盖），管近似零压降（微流量下 Δp≈0，求解器
    自行抛光）。失败回退 _anchor_restart。"""
    import contextlib as _cl
    import io as _io
    from scipy.optimize import fsolve
    net, ctx, sys_ = common.build_system(case)
    from pysas.solver import solve
    import numpy as _np

    chain = sorted(net.comps, key=lambda c: c.comp_id)
    two = [c for c in chain if len(c.ports) == 2]
    # 串行性检查：两口件首尾相接成 0→last 路径
    path_ok = two and two[0].ports[0].node_id == 0 \
        and two[-1].ports[1].node_id == max(
            p.node_id for c in chain for p in c.ports)
    if path_ok:
        for a, b in zip(two[:-1], two[1:]):
            if a.ports[1].node_id != b.ports[0].node_id:
                path_ok = False
    if not path_ok:
        return _anchor_restart(case, p_hi, p_lo, t0)

    mids = [c.ports[1].node_id for c in two[:-1]]      # 中节点（不含末端）
    gamma, R = ctx.gas.gamma, ctx.gas.R

    def m_orifice(model, p_up, p_dn, t_up):
        return float(model._ideal_mass_flow(max(p_up, 1.0), max(p_dn, 1.0),
                                            t_up, R, gamma))

    def eqs(y):
        logs = y[:-1]
        m = y[-1]
        ps = [p_hi] + [float(_np.exp(l)) for l in logs] + [p_lo]
        out = []
        k = 0
        for c in two:
            if c.elem_type.name == "ORIFICE":
                out.append(m_orifice(sys_.models[c.comp_id], ps[k], ps[k + 1],
                                     t0) - m)
            else:                                    # PIPE 微流量零压降
                out.append(ps[k] - ps[k + 1])
            k += 1
        return out

    y0 = [_np.log(p_hi)] * len(mids) + [m_chain_est(case, p_hi, t0)]
    try:
        sol = fsolve(eqs, y0, full_output=True, xtol=1e-12)
        y, info, ier, _msg = sol
        if ier != 1:
            raise RuntimeError("fsolve 不收敛")
        x0 = _np.zeros(sys_.n)
        anchored = {}
        for mod in sys_.models.values():
            anchored.update(mod.anchor_P_values())
        pressures = [p_hi] + [float(_np.exp(l)) for l in y[:-1]] + [p_lo]
        node_p = dict(zip([0] + mids + [max(p.node_id for c in chain
                                                 for p in c.ports)],
                          pressures))
        for i, nid in enumerate(sys_.interior_ids):
            x0[i] = node_p.get(nid, anchored.get(nid, p_hi))
        for nid in sys_.T_ids:
            x0[sys_.T_idx_of_node[nid]] = t0
        m = float(y[-1])
        for c in net.comps:
            if len(c.ports) == 2:
                x0[sys_.m_idx_of_port[(c.comp_id, 0)]] = m
                x0[sys_.m_idx_of_port[(c.comp_id, 1)]] = -m
            elif len(c.ports) == 1:
                is_lo = abs(c.params[0] - p_lo) < 1e-9
                x0[sys_.m_idx_of_port[(c.comp_id, 0)]] = m if is_lo else -m
        buf = _io.StringIO()
        with _cl.redirect_stdout(buf):
            res = solve(sys_, x0, ctx)
        out = {"converged": bool(res.report.converged),
               "iters": int(res.report.iters),
               "residual": float(res.report.final_residual),
               "m_anchor": m}
        if res.report.converged:
            st = sys_.port_states(res.x, ctx)
            out["m_solved_max"] = float(max(abs(s.mass_flow) for s in st))
        return out
    except Exception as e:                          # noqa: BLE001
        return {"converged": False, "error": f"{type(e).__name__}: {e}",
                "fallback": _anchor_restart(case, p_hi, p_lo, t0)}


def m_chain_est(case, p_hi, t0):
    net, ctx, sys_ = common.build_system(case)
    caps = [sys_.models[c.comp_id].choke_capacity(p_hi, t0, ctx, 0)
            for c in net.comps if len(c.ports) == 2]
    return min(caps) if caps else 0.0


def _anchor_restart(case, p_hi, p_lo, t0=600.0):
    """压力平坦化重启：全部节点 p0=p_hi（链路携带 m<<容量 时中节点
    压降 ≈0——default_guess 的"锚点均值"把中节点压到 β 严重偏离 1
    的假工况上，恰落在特性 √ 尖点的坏盆地）。流量保持零。
    从此点收敛 = 解存在，default_guess 失败属收敛域问题而非无解。"""
    import contextlib as _cl
    import io as _io
    net, ctx, sys_ = common.build_system(case)
    from pysas.solver import solve
    import numpy as _np
    x0 = _np.zeros(sys_.n)
    anchored = {}
    for m in sys_.models.values():
        anchored.update(m.anchor_P_values())
    for i, nid in enumerate(sys_.interior_ids):
        x0[i] = anchored.get(nid, p_hi)     # 锚节点取规格值，未锚取 p_hi
    for nid in sys_.T_ids:
        x0[sys_.T_idx_of_node[nid]] = t0
    from pysas.fluids.isentropic import q_of_mach
    q1 = q_of_mach(1.0, ctx.gas.R, ctx.gas.gamma)
    caps = [models_cap
            for c in net.comps if len(c.ports) == 2
            for models_cap in [sys_.models[c.comp_id].choke_capacity(
                p_hi, t0, ctx, 0)] if models_cap > 0]
    m_chain = min(caps) if caps else 0.0
    # 解析 β*：链路流量 m_chain << 最宽元件容量时，该元件下游节点压
    # p_dn = p_up·β*，β* 由其亚临界支 m_ideal(β*)=m_chain 反解——
    # 不做这步，平坦压力起点恰落在 β=1 的 √ 尖点上（FD 雅可比失真）。
    from scipy.optimize import brentq
    gamma, R = ctx.gas.gamma, ctx.gas.R
    widest_cap, widest = -1.0, None
    for c in net.comps:
        if len(c.ports) == 2 and c.elem_type.name == "ORIFICE":
            cap = sys_.models[c.comp_id].choke_capacity(p_hi, t0, ctx, 0)
            if cap > widest_cap:
                widest_cap, widest = cap, c
    if widest is not None and widest_cap > 2.0 * m_chain > 0.0:
        ratio = widest.params[0]
        a_eff = ratio * widest.ports[0].area
        cd = widest.params[1]
        prefac = cd * a_eff * p_hi / t0 ** 0.5 \
            * (2.0 * gamma / (R * (gamma - 1.0))) ** 0.5

        def m_ideal(beta):
            return prefac * beta ** (1.0 / gamma) * (
                max(1.0 - beta ** ((gamma - 1.0) / gamma), 0.0)) ** 0.5

        try:
            beta_star = brentq(lambda b: m_ideal(b) - m_chain,
                               1.0 - 1e-6, 1.0 - 1e-15, xtol=1e-18)
            dn = widest.ports[1].node_id
            if dn not in anchored:
                up = widest.ports[0].node_id
                p_up = anchored.get(up, p_hi)
                x0[sys_.p_idx_of_node[dn]] = p_up * beta_star
        except Exception as e:                           # noqa: BLE001
            print(f"  [anchor] β* 反解失败: {type(e).__name__}: {e}",
                  flush=True)
    # 流量方向播种：C2 链均按 0→末端 声明路径——两口件口0=+m_chain、
    # 口1=−m_chain；上游 PB 口=−m_chain（向网供流）、下游 PB 口=+m_chain
    pb_lo = [c for c in net.comps if len(c.ports) == 1
             and abs(c.params[0] - p_lo) < 1e-9]
    for c in net.comps:
        if len(c.ports) == 2:
            x0[sys_.m_idx_of_port[(c.comp_id, 0)]] = m_chain
            x0[sys_.m_idx_of_port[(c.comp_id, 1)]] = -m_chain
        elif len(c.ports) == 1:
            is_lo = any(c.comp_id == d.comp_id for d in pb_lo)
            x0[sys_.m_idx_of_port[(c.comp_id, 0)]] = \
                m_chain if is_lo else -m_chain
    buf = _io.StringIO()
    try:
        with _cl.redirect_stdout(buf):
            res = solve(sys_, x0, ctx)
        out = {"converged": bool(res.report.converged),
               "iters": int(res.report.iters),
               "residual": float(res.report.final_residual),
               "m_chain": m_chain}
        if res.report.converged:
            st = sys_.port_states(res.x, ctx)
            out["m_solved_max"] = float(max(abs(s.mass_flow) for s in st))
        return out
    except Exception as e:                          # noqa: BLE001
        return {"converged": False, "error": f"{type(e).__name__}: {e}"}


def torture_c2():
    cases = [
        ("C2_01_A1e-2_A1e-6",
         make_case(3, [pb(0, 0, 3.0e5), orif(1, 0, 1, 1e-2, 0.8),
                       orif(2, 1, 2, 1e-6, 0.8), pb(3, 2, 1.0e5)]), None),
        ("C2_02_pratio005",
         make_case(3, [pb(0, 0, 3.0e5), orif(1, 0, 1, 1e-2, 0.8),
                       orif(2, 1, 2, 1e-6, 0.8), pb(3, 2, 2.85e5)]), None),
        ("C2_03_or_pipe_or",
         make_case(4, [pb(0, 0, 3.0e5), orif(1, 0, 1, 1e-2, 0.8),
                       pipe(2, 1, 2, 1e-3, 2.0),
                       orif(3, 2, 3, 1e-6, 0.8), pb(4, 3, 1.0e5)]), None),
        ("C2_04_three_stage",
         make_case(4, [pb(0, 0, 5.0e5), orif(1, 0, 1, 1e-2, 0.8),
                       orif(2, 1, 2, 1e-4, 0.8),
                       orif(3, 2, 3, 1e-6, 0.8), pb(4, 3, 2.5e4)]), None),
        ("C2_05_cd1_extreme",
         make_case(3, [pb(0, 0, 3.0e5), orif(1, 0, 1, 1e-2, 1.0),
                       orif(2, 1, 2, 1e-6, 1.0), pb(3, 2, 1.0e5)]), None),
        # 已知非 bug 演示：等面积双孔板同壅塞，坏初值下牛顿失败 = 局部法
        # 定义域（任务书勿误报清单）；期望 clean_fail，不计违规。
        ("C2_06_known_dual_choke",
         make_case(3, [pb(0, 0, 3.0e5), orif(1, 0, 1, 1e-4, 0.8),
                       orif(2, 1, 2, 1e-4, 0.8), pb(3, 2, 1.0e5)]),
         "known_clean_fail"),
    ]
    results = []
    for name, case, tag in cases:
        out, findings, stats = _run(name, case)
        rec = {"name": name, "status": out["status"], "findings": findings}
        if out["status"] == "converged":
            flows = {f"c{c.comp_id}": [_pstate_of(out, c.comp_id, j).mass_flow
                                       for j in range(len(c.ports))]
                     for c in out["net"].comps}
            rec["flows"] = flows
            rec["verdict"] = "PASS" if not findings else "AUDIT_FAIL"
        elif tag == "known_clean_fail" and out["status"] == "clean_fail":
            rec["verdict"] = "PASS(known-clean-fail)"
        elif out["status"] == "clean_fail":
            # 可解性分诊：链式解析锚（fsolve 亚临界链解 → x0）。
            # 收敛 = 解存在、default_guess 够不着（收敛域/尺度分离
            # 问题，P1 稳健性）；仍不收敛 = 待定性。
            pbs = sorted(c["params"][0] for c in case["comps"]
                         if c["type"] == "PRESSURE_BOUNDARY")
            anch = _chain_anchor(case, pbs[-1], pbs[0])
            rec["anchor_restart"] = anch
            fb = anch.get("fallback") or {}
            best_res = min([x for x in (anch.get("residual"),
                                        anch.get("converged") and 0.0,
                                        fb.get("residual")) if x is not None]
                           or [math.inf])
            if anch.get("converged") or best_res < 1e-9:
                rec["verdict"] = "SOLVABLE_DEFAULT_GUESS_MISS(P1)"
            elif best_res < 1e-4:
                # 锚点抛光钝在容差带边缘：解存在性已证（残差<1e-4 的
                # 近解点存在），牛顿在 β→1 尖点区无法收敛到 1e-6 判据
                rec["verdict"] = "NEAR_SOLVER_STALL(P1)"
            else:
                rec["verdict"] = "CLEAN_FAIL(未定性)"
        else:
            rec["verdict"] = "CRASH"
            rec["exception"] = out.get("exception")
        results.append(rec)
    return results


# ================================================================ C3
def torture_c3():
    p0 = 3.0e5
    two = lambda comps: make_case(2, comps)      # 零压差只涉及两节点
    cases = [
        ("C3_01_pipe", two([pb(0, 0, p0), pipe(1, 0, 1, 1e-4, 1.0),
                            pb(2, 1, p0)])),
        ("C3_02_orifice", two([pb(0, 0, p0), orif(1, 0, 1, 1e-4, 0.8),
                               pb(2, 1, p0)])),
        ("C3_03_areachange", two([pb(0, 0, p0),
                                  areachg(1, 0, 1, 1e-3, 1e-2, 1.0),
                                  pb(2, 1, p0)])),
        ("C3_04_pipe_orifice", make_case(3, [pb(0, 0, p0),
                                             pipe(1, 0, 1, 1e-4, 1.0),
                                             orif(2, 1, 2, 1e-4, 0.8),
                                             pb(3, 2, p0)])),
        ("C3_05_junction_3pb", make_case(3, [pb(0, 0, p0), pb(1, 1, p0),
                                             pb(2, 2, p0),
                                             {"id": 3, "type": "JUNCTION",
                                              "ports": [{"area": 1e-4,
                                                         "node": 0},
                                                        {"area": 1e-4,
                                                         "node": 1},
                                                        {"area": 1e-4,
                                                         "node": 2}],
                                              "params": []}])),
        ("C3_06_dT0_same_p", two([pb(0, 0, p0, 300.0),
                                  orif(1, 0, 1, 1e-4, 0.8),
                                  pb(2, 1, p0, 900.0)])),
        # 真实 bug 存证（首版 C3 偶然踩中，保留复现）：孤立节点（nodes
        # 列表声明但无任何元件挂接）——组装期不校验，求解期残差
        # ports_on_node[nid] 裸 KeyError（assembly/system.py:206）。
        # 期望行为应为组装期 ValueError 或空口列表处理。
        ("C3_07_orphan_node",
         make_case(3, [pb(0, 0, p0), pipe(1, 0, 1, 1e-4, 1.0),
                       pb(2, 1, p0)]), "known_crash"),
    ]
    results = []
    for item in cases:
        if len(item) == 3:
            name, case, tag = item
        else:
            name, case, tag = *item, None
        out, findings, stats = _run(name, case)
        rec = {"name": name, "status": out["status"], "findings": findings}
        if tag == "known_crash":
            if out["status"] == "solve_error" \
                    and isinstance(out.get("exception"), str) \
                    and out["exception"].startswith("KeyError"):
                rec["verdict"] = "CRASH(real-bug)"
                rec["exception"] = out["exception"]
            elif out["status"] == "converged":
                rec["verdict"] = "PASS(orphans now handled)"
            else:
                rec["verdict"] = f"CRASH(other): {out.get('exception')}"
        elif out["status"] == "converged":
            m_max = max(abs(st.mass_flow) for st in out["pstates"])
            m_ref = out["m_ref"]
            rec.update(max_abs_mdot=m_max, m_ref=m_ref)
            rec["verdict"] = ("PASS" if m_max <= 1e-9 * m_ref and not findings
                              else "FAIL")
            if rec["verdict"] == "FAIL":
                rec["reason"] = (f"零压差网络出现非零流量 max|ṁ|={m_max:.3e}"
                                 f"（判据 1e-9·m_ref={1e-9 * m_ref:.3e}）")
        else:
            rec["verdict"] = "FAIL"
            rec["reason"] = f"零压差网络未收敛（{out['status']}）"
            rec["exception"] = out.get("exception")
        results.append(rec)
    return results


# ================================================================ C4
def torture_c4():
    """高低压对调：ṁ 反对称、节点 p0 镜像。

    判定口径（三层）:
      · 流量反对称 |ṁf+ṁr|/|ṁf|：全件适用——串联链两端元素看到的
        上游不同（or+pipe 实测 ~0.2%），1e-4 内 PASS，1e-2 内记
        SUSPECT（方向敏感量化），更大 FAIL；
      · 节点 p0 镜像：只对"回文 + 方向对称律"网络生效（orifice/pipe
        链，镜像映射 i ↔ n−1−i）；AREA_CHANGE 容量设计上方向敏感
        （突缩 Cc vs 突扩 1.0，see areachange.choke_capacity），跳过
        镜像改做解析容量对拍：两向 |ṁ| 应各等于该向 cap（都壅塞）。
    """
    p_hi, p_lo = 5.0e5, 2.0e5
    nets = {
        "C4_01_orifice": ([orif(1, 0, 1, 1e-4, 0.8)], "sym"),
        "C4_02_pipe": ([pipe(1, 0, 1, 1e-4, 1.0)], "sym"),
        "C4_03_areachange": ([areachg(1, 0, 1, 1e-2, 1e-3, 0.8)], "ac"),
        "C4_04_or_pipe": ([orif(1, 0, 1, 1e-3, 0.8),
                           pipe(2, 1, 2, 1e-4, 1.0)], "chain"),
        "C4_05_pipe_or_pipe": ([pipe(1, 0, 1, 1e-3, 1.0),
                                orif(2, 1, 2, 1e-4, 0.8),
                                pipe(3, 2, 3, 1e-3, 1.0)], "sym"),
    }
    results = []
    for name, (inner, kind) in nets.items():
        n_nodes = max(p["node"] for c in inner for p in c["ports"]) + 1
        case_f = make_case(n_nodes, [pb(0, 0, p_hi)] + inner
                           + [pb(10, n_nodes - 1, p_lo)])
        case_r = make_case(n_nodes, [pb(0, 0, p_lo)] + inner
                           + [pb(10, n_nodes - 1, p_hi)])
        out_f, fin_f, _ = _run(name + "_fwd", case_f)
        out_r, fin_r, _ = _run(name + "_rev", case_r)
        rec = {"name": name, "kind": kind,
               "status_fwd": out_f["status"], "status_rev": out_r["status"]}
        if not (out_f["status"] == "converged"
                and out_r["status"] == "converged"):
            rec["verdict"] = "FAIL"
            rec["reason"] = (f"反转后未收敛或异常: fwd={out_f['status']} "
                             f"rev={out_r['status']}")
            results.append(rec)
            continue
        pf, pr = out_f["pstates"], out_r["pstates"]
        m_ref = out_f["m_ref"]
        worst_rel = max(
            abs(a.mass_flow + b.mass_flow)
            / max(abs(a.mass_flow), 1e-12 * m_ref)
            for a, b in zip(pf, pr))
        rec["worst_flow_asym_rel"] = worst_rel
        if kind == "ac":
            # 解析对拍：两向都应钳在各自方向容量上（不可压需求 1.5 kg/s
            # >> cap），cap_dir = C_choke(方向)·q(1)·p0_hi·A_ref/√T0
            from pysas.fluids.isentropic import q_of_mach
            gas = out_f["ctx"].gas
            q1 = q_of_mach(1.0, gas.R, gas.gamma)
            a_ref = 1e-3
            cc = 1.0 / (1.0 + 0.8 ** 0.5)     # c_contract（ζ=0.8）
            cap_contract = cc * q1 * p_hi * a_ref / 600.0 ** 0.5
            cap_expand = 1.0 * q1 * p_hi * a_ref / 600.0 ** 0.5
            mf = abs(pf[1].mass_flow)         # 元件口0 = 大口侧
            mr = abs(pr[1].mass_flow)
            rec.update(cap_fwd_expect=cap_contract, cap_rev_expect=cap_expand,
                       mdot_fwd=mf, mdot_rev=mr)
            ok = (abs(mf - cap_contract) / cap_contract < 1e-6
                  and abs(mr - cap_expand) / cap_expand < 1e-6)
            rec["verdict"] = ("PASS(direction-sensitive-by-design)"
                              if ok else "FAIL(ac-cap-mismatch)")
        else:
            if worst_rel <= 1e-4:
                rec["verdict"] = "PASS"
            elif worst_rel <= 1e-2:
                rec["verdict"] = "SUSPECT(direction-sensitivity)"
            else:
                rec["verdict"] = "FAIL"
        if kind == "sym":
            # 镜像压力核对（i ↔ n−1−i，含锚节点）
            xf, xr = out_f["x"], out_r["x"]
            n_int = out_f["sys_"].n_interior
            dp_max = max(abs(xf[i] - xr[n_int - 1 - i]) for i in range(n_int))
            rec["max_mirror_dp0"] = dp_max
            rec["p_ref"] = out_f["p_ref"]
            if rec["verdict"].startswith("PASS") \
                    and dp_max > 1e-5 * out_f["p_ref"]:
                rec["verdict"] = "SUSPECT(mirror-p0)"
            if fin_f or fin_r:
                rec["findings"] = fin_f + fin_r
        results.append(rec)
    return results


# ================================================================ C5
def torture_c5():
    """全流量边界（无 PRESSURE_BOUNDARY）：组装期必须 ValueError。"""
    cases = [
        ("C5_01_ms_or_ms", make_case(3, [ms(0, 0, 0.1), orif(1, 0, 1, 1e-4),
                                         ms(2, 1, -0.1)])),
        ("C5_02_ms_deadend", make_case(2, [ms(0, 0, 0.1),
                                           orif(1, 0, 1, 1e-4)])),
        ("C5_03_ms_pipe_ms", make_case(3, [ms(0, 0, 0.1),
                                           pipe(1, 0, 1, 1e-4, 1.0),
                                           ms(2, 1, -0.1)])),
        ("C5_04_ms_junction", make_case(4, [ms(0, 0, 0.1),
                                            {"id": 1, "type": "JUNCTION",
                                             "ports": [{"area": 1e-4,
                                                        "node": 0},
                                                       {"area": 1e-4,
                                                        "node": 1},
                                                       {"area": 1e-4,
                                                        "node": 2}],
                                             "params": []},
                                            ms(2, 1, -0.05),
                                            ms(3, 2, -0.05)])),
        ("C5_05_ms_extract_only", make_case(2, [ms(0, 0, -0.1),
                                                areachg(1, 0, 1, 1e-4,
                                                        1e-3, 1.0)])),
        # 复核（勿误报清单）：SURROGATE_FLOW 缺 npz 模型文件 → 组装期报错
        ("C5_06_surrogate_no_file",
         make_case(3, [pb(0, 0, 3.0e5),
                       {"id": 1, "type": "SURROGATE_FLOW",
                        "ports": [{"area": 1e-4, "node": 0},
                                  {"area": 1e-4, "node": 1}],
                        "params": [1.0],
                        "model_path": "fuzz/__no_such_model__.npz"},
                       pb(2, 1, 2.0e5)])),
    ]
    results = []
    for name, case in cases:
        out, findings, stats = _run(name, case)
        rec = {"name": name, "status": out["status"],
               "exception": out.get("exception")}
        if name.startswith("C5_06"):
            # 设计行为复核：期望组装期 ValueError（缺模型文件）
            rec["verdict"] = ("PASS(design-behavior)"
                              if out["status"] == "assemble_error"
                              and "代理模型文件不存在" in out.get("exception", "")
                              else "FAIL")
        elif out["status"] == "assemble_error" \
                and "锚定" in out.get("exception", ""):
            rec["verdict"] = "PASS(assert-fired)"
        elif out["status"] == "assemble_error":
            rec["verdict"] = "FAIL(wrong-exception)"
        else:
            rec["verdict"] = "FAIL(no-error-raised)"
        results.append(rec)
    return results


# ---------------------------------------------------------------- main
def main() -> int:
    CASES_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)
    all_out = {"C1_backpressure_sweep": torture_c1(),
               "C2_extreme_area_series": torture_c2(),
               "C3_zero_dp": torture_c3(),
               "C4_flow_reversal": torture_c4(),
               "C5_all_flow_boundary": torture_c5()}

    def jsonable(obj):
        """numpy 2 标量兜底（np.float64/np.bool 类名带 numpy 前缀不可序列化）。"""
        import numpy as _np
        if isinstance(obj, dict):
            return {k: jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [jsonable(v) for v in obj]
        if isinstance(obj, _np.generic):
            return obj.item()
        return obj

    all_out = jsonable(all_out)
    (RESULTS_DIR / "c_torture.json").write_text(
        json.dumps(all_out, ensure_ascii=False, indent=1), encoding="utf-8")

    for key, items in all_out.items():
        print(f"\n== {key} ==")
        for it in items:
            head = it.get("verdict", "?")
            extra = ""
            if "refine_max_step_Ma" in it:
                extra = (f"  收敛 {it['n_converged']}/{it['n_sweeps']}"
                         f"  粗扫最大步幅={it['coarse_max_step_Ma']:.3f}"
                         f"  细化区间 {it['refine_range']}"
                         f"  细化最大步幅={it['refine_max_step_Ma']}")
            elif "max_abs_mdot" in it:
                extra = (f"  max|ṁ|={it['max_abs_mdot']:.3e}"
                         f"  判据={it.get('m_ref', 0):.3e}·1e-9")
            elif "worst_flow_asym_rel" in it:
                extra = f"  流量反对称相对偏差={it['worst_flow_asym_rel']:.2e}"
                if "max_mirror_dp0" in it:
                    extra += f"  镜像Δp0={it['max_mirror_dp0']:.2e}"
                if "mdot_fwd" in it:
                    extra += (f"  ṁf={it['mdot_fwd']:.4f}(cap期望"
                              f"{it['cap_fwd_expect']:.4f})"
                              f"  ṁr={it['mdot_rev']:.4f}(cap期望"
                              f"{it['cap_rev_expect']:.4f})")
            print(f"  [{head:>24}] {it.get('name', it.get('family'))}"
                  f"  status={it.get('status', '-')}{extra}")
            for f in it.get("findings", [])[:4]:
                print(f"      - {f['severity']} {f['invariant']} "
                      f"{f['where']}: {f['detail'][:110]}")
    n_fail = sum(1 for items in all_out.values() for it in items
                 if str(it.get("verdict", "")).startswith("FAIL"))
    print(f"\nC 层判定汇总：FAIL {n_fail} 项；"
          f"明细见 fuzz/results/c_torture.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
