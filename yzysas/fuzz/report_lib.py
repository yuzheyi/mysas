# -*- coding: utf-8 -*-
"""fuzz/report_lib.py — 软壅塞守卫回归套件共享基础设施（想法 33 验收）。

三块职责：
  1) guard_rows() —— 守卫容量比口径的**独立复刻**（与
     assembly/system._comp_overloads 同款规则，测试层独立实现，防
     "自己验自己"）：非锚定元件、row_units[i]==1 的行 i、i<端口数、
     >2 口豁免；upwind 口 k_up：ṁ_i>0 → i，ṁ_i<0 → 进料口总压最大者；
     cap = choke_capacity(max(p0_up,1), max(t0_up,10), ctx, i)。
  2) solve_case() —— 组装 + default_guess 冷启动 solve + 终态告警捕获 +
     守卫口径提取的统一驱动（永不抛异常，口径同 fuzz/common.run_solve）。
  3) netinf_to_mermaid() —— netinf 字典 → mermaid 拓扑全景图（"每例一图"
     的基础设施）：节点圆框（解出时标 p/T）、元件方框（类型/关键参数/
     口面积，科学计数 2 位）、PRESSURE_BOUNDARY/MASS_SOURCE/BOOSTER
     样式类、有解时连线方向按端口 ṁ 符号（ṁ>0=流入元件：节点→元件）、
     壅塞口（守卫口径 ratio≥1 或 pstate choked 标志）标红加粗。

纪律：只 import pysas、只读不写其源码树；产出仅落 fuzz/。
运行方式：python -X utf8 <script>（Windows GBK 终端）。
"""
from __future__ import annotations

import contextlib
import io
import math
import sys
import traceback
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # .../yzysas
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

# 牛顿试探点可能踩负压/负温 → pysas 内未防护 √ 产生 NaN（线搜索按
# +inf 拒绝，设计内行为）。与 common.py 同纪律：静音数值告警。
np.seterr(all="ignore")

from pysas.assembly import NetworkSystem             # noqa: E402
from pysas.io import build_models, netinf_from_dict  # noqa: E402
from pysas.solver import initial_guess, solve        # noqa: E402
from pysas.solver.scaling import ScaledProblem, make_scaling  # noqa: E402

SOFT_CHOKE_KAPPA = 1.0e3   # 回归基准值（与 system.SOFT_CHOKE_KAPPA 同款）


# ============================================================ 守卫口径（独立复刻）
def guard_rows(sysm, x, ctx) -> list[dict]:
    """守卫判定口径独立复刻（报表口径，覆盖全部合格行含亚容量）。

    与 system._comp_overloads 的**激活**口径差异：本函数对每个合格行
    （非锚定、两口以内、row_units[i]==1、i<端口数）都报 ratio（含
    ratio<1 的亚容量行，供报表余量列）；方程侧守卫只在 |ṁ|>cap>0 的
    行激活（active=True）。m_a==0 的行守卫跳过（死区），此处记
    ratio=0（upwind 取全口总压最大者，仅报表参考）。

    返回行 dict: {comp_id, elem_type, port, mdot, cap, ratio, offset,
    active}。任何一行现算异常（负压区 √NaN 等）记 cap=None 跳过，
    不中断其余行——clean-fail 解（可能负压）也要能提取解处 ratio。
    """
    rows = []
    for comp in sorted(sysm.net.comps, key=lambda c: c.comp_id):
        model = sysm.models[comp.comp_id]
        if model.anchor_P_values():
            continue                      # 锚定元件豁免（PB/BOOSTER）
        if len(comp.ports) > 2:
            continue                      # 多口混合件豁免（A0257 实证）
        units = model.row_units
        for i, u in enumerate(units):
            if u != 1 or i >= len(comp.ports):
                continue
            m_a = float(x[model._m_idx[i]])
            try:
                if m_a > 0.0:
                    k_up = i
                elif m_a < 0.0:
                    ins = [k for k in range(len(comp.ports))
                           if x[model._m_idx[k]] > 0.0]
                    if not ins:
                        continue          # 无进料（中间态）：不设闸
                    k_up = max(ins, key=lambda k: model._total_p(x, ctx, k))
                else:
                    k_up = max(range(len(comp.ports)),
                               key=lambda k: model._total_p(x, ctx, k))
                p0_up = max(float(model._total_p(x, ctx, k_up)), 1.0)
                t0_up = max(float(model._total_t(x, ctx, k_up)), 10.0)
                cap = float(model.choke_capacity(p0_up, t0_up, ctx, i))
            except Exception:             # noqa: BLE001
                cap = math.nan
            if not math.isfinite(cap) or cap <= 0.0:
                rows.append(dict(comp_id=comp.comp_id,
                                 elem_type=int(comp.elem_type), port=i,
                                 mdot=m_a, cap=None, ratio=None,
                                 offset=None, active=False))
                continue
            ratio = abs(m_a) / cap
            rows.append(dict(comp_id=comp.comp_id,
                             elem_type=int(comp.elem_type), port=i,
                             mdot=m_a, cap=cap, ratio=ratio,
                             offset=abs(m_a) - cap,
                             active=abs(m_a) > cap))
    return rows


def worst_ratio(rows: list[dict]) -> float | None:
    """行清单中的 worst ratio（None=无合格行或全 cap 退化）。"""
    rs = [r["ratio"] for r in rows if r["ratio"] is not None]
    return max(rs) if rs else None


# ============================================================ 统一求解驱动
def solve_case(case: dict, x0: np.ndarray | None = None) -> dict:
    """组装 + 冷启动 solve + 告警捕获 + 守卫口径提取。永不抛异常。

    返回关键字段：
      status ∈ {converged, clean_fail, assemble_error, solve_error}
        （与 fuzz/common.run_solve 同名同义；port_states 失败不降级
         status，记 pstates_error）
      iters / final_residual_scaled / max_F_raw / m_ref / p_ref
      x（原始坐标，收敛与否都在）、net/ctx/sysm（sysm 挂 _ctx 供
      netinf_to_mermaid 用）、pstates（收敛且成功时）
      warnings_all（全部捕获告警文本）、warn_soft_choke（含"软壅塞"
      的告警条数——终态告警文案特征）
      guard（最终 x 上的 guard_rows——clean_fail 也提取，DG-8 解处
      ratio 用）、worst_ratio、guard_active（active 行子集）
    """
    out: dict = {"status": "assemble_error", "stage": "assemble"}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            net, ctx = netinf_from_dict(case)
            models = build_models(net)
            sysm = NetworkSystem(net, models)
            sysm._ctx = ctx               # mermaid 层读取（容量比口径用）
            out.update(net=net, ctx=ctx, sysm=sysm)
            out["stage"] = "solve"
            if x0 is None:
                x0 = initial_guess(sysm, ctx)   # 等价旧 default_guess（策略 default）
            with warnings.catch_warnings(record=True) as wlist:
                warnings.simplefilter("always")
                res = solve(sysm, x0, ctx)
            out["warnings_all"] = [str(w.message) for w in wlist]
            out["warn_soft_choke"] = sum(
                1 for t in out["warnings_all"] if "软壅塞" in t)
            out["stage"] = "postprocess"
            out["status"] = ("converged" if res.report.converged
                             else "clean_fail")
            out["iters"] = int(res.report.iters)
            out["final_residual_scaled"] = float(res.report.final_residual)
            out["max_F_raw"] = float(res.max_F_raw)
            out["m_ref"] = float(res.scaling.m_ref)
            out["p_ref"] = float(res.scaling.p_ref)
            out["res"] = res
            out["x"] = np.asarray(res.x, dtype=float).copy()
            out["x0"] = np.asarray(x0, dtype=float).copy()
            # 守卫口径提取（收敛与否都做——clean_fail 解处 ratio 是
            # DG-8 的记录项；负压解的单行异常在 guard_rows 内吞掉）
            out["guard"] = guard_rows(sysm, out["x"], ctx)
            out["worst_ratio"] = worst_ratio(out["guard"])
            out["guard_active"] = [r for r in out["guard"] if r["active"]]
            if res.report.converged:
                try:
                    out["pstates"] = sysm.port_states(out["x"], ctx)
                except Exception as e:    # noqa: BLE001
                    out["pstates_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:                    # noqa: BLE001
        out["status"] = ("assemble_error" if out["stage"] == "assemble"
                         else "solve_error")
        out["exception"] = f"{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc()
    out["stdout_tail"] = buf.getvalue()[-600:]
    return out


# ============================================================ scipy 独立对照
def scipy_crosscheck(sysm, ctx, x0: np.ndarray, x_ref: np.ndarray,
                     tol_f: float = 1.0e-8) -> dict:
    """scipy.optimize.least_squares(lm) 独立求解同一问题（同初值）。

    返回 {ok, max_F_scaled, max_dx_scaled, max_dx_raw, method}；异常记
    error。与 pysas 完全独立的实现——BM-1 对照与全量扫描"双解嫌疑"
    清单用。dx 以**缩放坐标**为主口径（pysas 收敛判据 max|F̃|<1e-6
    作用在缩放坐标上，raw 坐标差只反映容差平移：压力 ~1e-6·p_ref；
    "同根"判据在判据所在坐标系里下判）。
    """
    from scipy.optimize import least_squares
    try:
        sc = make_scaling(sysm, ctx)
        prob = ScaledProblem(sysm, ctx, sc)
        x_ref_t = sc.to_scaled(np.asarray(x_ref, float))
        r = least_squares(prob.residual, sc.to_scaled(np.asarray(x0, float)),
                          method="lm", xtol=1e-14, ftol=1e-14, gtol=1e-14,
                          max_nfev=60 * sysm.n)
    except Exception as e:                    # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    fmax = float(np.max(np.abs(r.fun))) if r.fun.size else 0.0
    dx_t = float(np.max(np.abs(r.x - x_ref_t)))
    dx_raw = float(np.max(np.abs(sc.to_raw(r.x) - np.asarray(x_ref, float))))
    return {"ok": bool(fmax < tol_f), "max_F_scaled": fmax,
            "max_dx_scaled": dx_t, "max_dx_raw": dx_raw, "method": "lm"}


# ============================================================ mermaid 转换器
def fmt_sci(v, nd: int = 2) -> str:
    """科学计数 2 位（1.00e-3）；None/非有限 → '—'。"""
    if v is None or not math.isfinite(v):
        return "—"
    return f"{v:.{nd}e}"


def _fmt_g(v) -> str:
    """紧凑数值：%g 语义（保留 4 位有效数字）。"""
    try:
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return str(v)


def _comp_label(c: dict) -> str:
    """元件方框标签：类型 + 关键参数 + 端口面积（科学计数 2 位）。"""
    t = c.get("type", "?")
    prm = c.get("params", []) or []
    ports = c.get("ports", []) or []
    areas = [float(p.get("area", 0.0) or 0.0) for p in ports]
    a_str = ""
    if any(a > 0.0 for a in areas):
        a_str = "A=" + "/".join(fmt_sci(a) for a in areas)
    if t == "PRESSURE_BOUNDARY":
        return f"PRESSURE_BOUNDARY<br/>p0={_fmt_g(prm[0]) if prm else '—'} Pa" \
               f"<br/>T0={_fmt_g(prm[1]) if len(prm) > 1 else '—'} K"
    if t == "MASS_SOURCE":
        return f"MASS_SOURCE<br/>ṁ={_fmt_g(prm[0]) if prm else '—'} kg/s" \
               f"<br/>T0={_fmt_g(prm[1]) if len(prm) > 1 else '—'} K"
    if t == "BOOSTER":
        if len(prm) >= 2 and prm[0]:
            pi = f"p: {_fmt_g(prm[0])}→{_fmt_g(prm[1])} Pa<br/>" \
                 f"增压比 π={prm[1] / prm[0]:.3f}"
        else:
            pi = ""
        ts = f"<br/>T0={_fmt_g(prm[2])} K" if len(prm) >= 3 else ""
        return f"BOOSTER<br/>{pi}{ts}"
    if t == "HEATER":
        return f"HEATER<br/>q={_fmt_g(prm[0]) if prm else '—'} W" + \
               (f"<br/>{a_str}" if a_str else "")
    if t == "ORIFICE":
        beta = _fmt_g(prm[0]) if len(prm) > 0 else "—"
        cd = _fmt_g(prm[1]) if len(prm) > 1 else "—"
        return f"ORIFICE<br/>β={beta} Cd={cd}" + \
               (f"<br/>{a_str}" if a_str else "")
    if t == "PIPE":
        return f"PIPE<br/>L={_fmt_g(prm[0]) if prm else '—'}" \
               f" D={_fmt_g(prm[1]) if len(prm) > 1 else '—'}" + \
               (f"<br/>{a_str}" if a_str else "")
    if t == "AREA_CHANGE":
        return f"AREA_CHANGE<br/>ζ={_fmt_g(prm[0]) if prm else '—'}" + \
               (f"<br/>{a_str}" if a_str else "")
    if t == "JUNCTION":
        return "JUNCTION<br/>零压差绝热混合"
    if t == "SURROGATE_FLOW":
        mp = c.get("model_path", "")
        name = Path(mp).name if mp else "—"
        return f"SURROGATE_FLOW<br/>{name}" + \
               (f"<br/>{a_str}" if a_str else "")
    label = t + (f"<br/>{a_str}" if a_str else "")
    return label


def netinf_to_mermaid(data: dict, x=None, sysm=None) -> str:
    """netinf 字典 → mermaid flowchart 拓扑全景图（每例一图基础设施）。

    节点画圆（解出时标 kPa/K），元件画方框（类型/关键参数/口面积）；
    PRESSURE_BOUNDARY/MASS_SOURCE/BOOSTER 样式类高亮，BOOSTER 标增压比；
    有解（x+sysm 齐备）时连线方向按端口 ṁ 符号（ṁ>0=流入元件：
    节点→元件 / 元件→节点）并标 ṁ 值，无解用无向线；
    壅塞口（守卫口径 ratio≥1 或 pstate choked 标志）标红加粗——
    守卫口径需要 ctx（sysm._ctx，solve_case 会挂上），缺 ctx 时
    仍出图但跳过壅塞标记。
    返回 mermaid 图体（不含 ``` 围栏）。
    """
    solved = x is not None and sysm is not None
    ctx = getattr(sysm, "_ctx", None) if solved else None

    p_of: dict[int, float] = {}
    t_of: dict[int, float] = {}
    m_of: dict[tuple[int, int], float] = {}
    if solved:
        for nid in sysm.interior_ids:
            p_of[nid] = float(x[sysm.p_idx_of_node[nid]])
            t_of[nid] = float(x[sysm.T_idx_of_node[nid]])
        for (cid, j), k in sysm.m_idx_of_port.items():
            m_of[(cid, j)] = float(x[k])

    # 壅塞口集合：(comp_id, port)
    choked: set[tuple[int, int]] = set()
    if solved and ctx is not None:
        try:
            for r in guard_rows(sysm, x, ctx):
                if r["ratio"] is not None and r["ratio"] >= 1.0:
                    choked.add((r["comp_id"], r["port"]))
        except Exception:                     # noqa: BLE001
            pass
        try:
            comps = sorted(sysm.net.comps, key=lambda c: c.comp_id)
            pst = sysm.port_states(x, ctx)
            idx = 0
            for c in comps:
                for j in range(len(c.ports)):
                    if pst[idx].choked:
                        choked.add((c.comp_id, j))
                    idx += 1
        except Exception:                     # noqa: BLE001
            pass

    lines = ["flowchart LR"]
    # ---- 节点（圆） ----
    for n in data.get("nodes", []):
        nid = n["id"]
        if nid in p_of:
            p_kpa = p_of[nid] / 1000.0
            lab = (f"n{nid}<br/>{p_kpa:.4g} kPa<br/>T={t_of[nid]:.4g} K")
        else:
            lab = f"n{nid}"
        lines.append(f'N{nid}(("{lab}"))')
    # ---- 元件（方框）+ 连线 ----
    edge_idx = 0
    choked_edges: list[int] = []
    comp_class: dict[int, str] = {}
    for c in data.get("comps", []):
        cid = c["id"]
        ctype = c.get("type", "?")
        lab = f"c{cid} {_comp_label(c)}"
        lines.append(f'C{cid}["{lab}"]')
        if ctype == "PRESSURE_BOUNDARY":
            comp_class[cid] = "pbound"
        elif ctype == "MASS_SOURCE":
            comp_class[cid] = "msource"
        elif ctype == "BOOSTER":
            comp_class[cid] = "booster"
        if (cid, 0) in choked and ctype != "PRESSURE_BOUNDARY":
            comp_class[cid] = "softchoked"
        for j, p in enumerate(c.get("ports", [])):
            nid = p["node"]
            if solved and (cid, j) in m_of:
                m = m_of[(cid, j)]
                mtxt = f"ṁ={m:.4g}" if abs(m) > 0.0 else "ṁ=0"
                if m > 0.0:               # 流入元件：节点 → 元件
                    lines.append(f'N{nid} -- "{mtxt}" --> C{cid}')
                elif m < 0.0:             # 元件流出：元件 → 节点
                    lines.append(f'C{cid} -- "{mtxt}" --> N{nid}')
                else:
                    lines.append(f'N{nid} --- C{cid}')
            else:
                lines.append(f'N{nid} --- C{cid}')
            if (cid, j) in choked:
                choked_edges.append(edge_idx)
            edge_idx += 1
    # ---- 样式类与壅塞标红 ----
    # 浅色填充 + 强制深色文字（color:）：mermaid 文字色默认跟页面主题
    # （深色主题下浅底配浅字不可读），显式钉死深字后明暗两主题都清晰。
    lines += [
        "classDef pbound fill:#FFF3E0,stroke:#E65100,stroke-width:2px,"
        "color:#7B3F00",
        "classDef msource fill:#F3E5F5,stroke:#8E24AA,stroke-width:2px,"
        "color:#6A1B9A",
        "classDef booster fill:#E3F2FD,stroke:#1976D2,stroke-width:2px,"
        "color:#0D47A1",
        "classDef softchoked fill:#FFCDD2,stroke:#D32F2F,"
        "stroke-width:2.5px,color:#8B0000",
    ]
    for cid, cls in comp_class.items():
        lines.append(f"class C{cid} {cls}")
    for ei in choked_edges:
        lines.append(f"linkStyle {ei} stroke:#E53935,stroke-width:3px")
    return "\n".join(lines)


def md_mermaid(data: dict, x=None, sysm=None, indent: str = "") -> str:
    """netinf → 围栏包裹的 mermaid markdown 块（直接可贴报告）。"""
    body = netinf_to_mermaid(data, x=x, sysm=sysm)
    fenced = "```mermaid\n" + body + "\n```"
    if indent:
        fenced = "\n".join(indent + ln for ln in fenced.splitlines())
    return fenced


# ============================================================ 报表小工具
def case_stats_line(out: dict) -> str:
    """一行统计（附录折叠条目用）：状态/迭代/告警/worst ratio。"""
    st = out.get("status", "?")
    it = out.get("iters", "—")
    nw = out.get("warn_soft_choke", 0)
    wr = out.get("worst_ratio")
    wr_s = f"{wr:.4f}" if wr is not None else "—"
    extra = ""
    if out.get("exception"):
        extra = f"；异常 {out['exception']}"
    return f"status={st}，iters={it}，软壅塞告警={nw}，worst ratio={wr_s}{extra}"


def one_line_verdict(out: dict, case: dict) -> str:
    """一句话定性（全量扫描告警例详析用）：谁在超容、网络结构如何。"""
    if out.get("status") != "converged":
        return f"未收敛（{out.get('status')}）"
    parts = []
    for r in out.get("guard_active", []):
        parts.append(f"c{r['comp_id']}口{r['port']} ratio={r['ratio']:.4f}"
                     f"（ṁ={r['mdot']:.4g} vs cap={r['cap']:.4g}）")
    if not parts:
        return "收敛且无守卫激活"
    n_pb = sum(1 for c in case.get("comps", [])
               if c.get("type") == "PRESSURE_BOUNDARY")
    n_htr = sum(1 for c in case.get("comps", [])
                if c.get("type") == "HEATER")
    return ("；".join(parts) +
            f" —— 守卫钉位（网络含 {n_pb} 个 PB/{n_htr} 个 HEATER，"
            "超容口被钉在声速容量附近，非物理解）")
