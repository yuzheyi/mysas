# -*- coding: utf-8 -*-
"""fuzz 公共层：路径引导、算例构建、求解包装、B 层不变量审计。

纪律：本目录全部脚本只 import pysas、只读不写其源码树；产出仅落 fuzz/。
运行方式：python -X utf8 <script>（Windows GBK 终端）。

审计口径（与任务书对应，严重度分级见 audit_case docstring）:
  不变量 1  节点连续性  |Σṁ| < 1e-9·scale
            scale = max(全网 max|ṁ|, 1e-6·m_ref)——下限取求解器自身
            分辨率（收敛判据 1e-6 作用在 1/m_ref 缩放的连续性行上），
            零流量算例不至于拿机器噪声当违反。
  不变量 2  出料口恒等式 |ρ·A·v − ṁ| < 1e-9（A=报表几何面积=port.area）。
  不变量 3  出料口隐含射流总压 ps·(T0/Ts)^(γ/(γ−1))
            ≤ max(进料口节点总压) + 1e-6·p0（热二律，违反即 P0）。
  不变量 4  节点 T0 ∈ [源温 min, max]（含发热元件的网络跳过——
            heater 加热合法抬温，此不变量不适用，记录 skip）。
  不变量 5  全场无 NaN/Inf；ps、Ts、ρ > 0（附带节点 p0/T0 有限且为正）。
"""
from __future__ import annotations

import contextlib
import io
import math
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # .../yzysas
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

# 牛顿试探点可能踩负压/负温 → pysas 内未防护 √ 产生 NaN（线搜索按
# +inf 拒绝，设计内行为）。驱动层静音 RuntimeWarning，避免 300 例刷屏。
np.seterr(all="ignore")

from pysas.assembly import NetworkSystem
from pysas.datamodel import ElemType
from pysas.io import build_models, netinf_from_dict
from pysas.solver import default_guess, solve

GAMMA = 1.4          # 空气（IdealGas 缺省）；审计公式与 ctx.gas 同源读取
SEED = 20260930      # 固定种子：全部随机层可复现


# ---------------------------------------------------------------- 构建/求解
def build_system(case: dict):
    """netinf 字典 → (net, ctx, sys_)。组装期异常（适定性断言等）向上抛。"""
    net, ctx = netinf_from_dict(case)
    models = build_models(net)
    sys_ = NetworkSystem(net, models)
    return net, ctx, sys_


def run_solve(case: dict) -> dict:
    """组装 + 求解 + 解后处理。永不抛异常：任何阶段异常记入返回值。

    返回关键字段：status ∈ {converged, clean_fail, assemble_error,
    solve_error, postprocess_error}；收敛解附 x / pstates / 引用对象。
    newton 内核逐迭代 print 重定向吞掉（300 例不至于刷屏）。
    """
    out = {"status": "assemble_error", "stage": "assemble"}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            net, ctx, sys_ = build_system(case)
            out.update(net=net, ctx=ctx, sys_=sys_)
            out["stage"] = "solve"
            x0 = default_guess(sys_, ctx)
            res = solve(sys_, x0, ctx)
            out["stage"] = "postprocess"
            out["status"] = "converged" if res.report.converged else "clean_fail"
            out["iters"] = int(res.report.iters)
            out["final_residual_scaled"] = float(res.report.final_residual)
            out["max_F_raw"] = float(res.max_F_raw)
            out["m_ref"] = float(res.scaling.m_ref)
            out["p_ref"] = float(res.scaling.p_ref)
            out["res"] = res
            out["x"] = np.asarray(res.x, dtype=float).copy()
            if res.report.converged:
                out["status"] = "postprocess_error"
                pstates = sys_.port_states(res.x, ctx)
                out["status"] = "converged"
                out["pstates"] = pstates
    except Exception as e:                                   # noqa: BLE001
        out["status"] = {"assemble": "assemble_error",
                         "solve": "solve_error",
                         "postprocess": "postprocess_error"}[out["stage"]]
        out["exception"] = f"{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc()
    return out


def port_rows(net):
    """发号序端口清单 [(comp, j, port)]——与 port_states 输出一一对应
    （assembly 按 comp_id 升序、口序展平，见 system._build_indices）。"""
    return [(c, j, p) for c in sorted(net.comps, key=lambda c: c.comp_id)
            for j, p in enumerate(c.ports)]


# ---------------------------------------------------------------- 审计
def _sev_band(err, tiny, big):
    """通用严重度：≤tiny 通过；≤big P2；>big P1。"""
    if err <= tiny:
        return None
    return "P2" if err <= big else "P1"


def audit_case(case: dict, out: dict) -> tuple[list[dict], dict]:
    """B 层五项不变量审计（仅对收敛解）。返回 (findings, stats)。

    finding = {severity, invariant, where, detail}。
    分级约定（任务书只硬性规定二律违反=P0，其余按后果定级）:
      P0  热二律违反（任务书指定）；收敛解出现 NaN/Inf 或 ps/Ts/ρ/
          节点 p0/T0 非正（物理合法性根坏，一切数字不可信）。
      P1  连续性 |Σṁ| > 1e-6·scale（超出求解器自身判据的真实破绽）；
          恒等式误差 > 1e-6；节点 T0 越界 > 1 K（真实能量破绽量级）。
      P2  处于"求解器容差带内但超出任务书 1e-9 指标"的弱违反
          （连续性/恒等式 1e-9~1e-6 档；T0 越界 ≤1 K）。
    """
    findings: list[dict] = []
    stats: dict = {}
    if out.get("status") != "converged":
        return findings, stats

    net, sys_, x = out["net"], out["sys_"], out["x"]
    ps = out["pstates"]
    gas = out["ctx"].gas
    gamma = gas.gamma
    m_ref = out["m_ref"]
    rows = port_rows(net)
    assert len(rows) == len(ps)
    mdot_of = {(c.comp_id, j): ps[i].mass_flow
               for i, (c, j, _p) in enumerate(rows)}
    p0_of = {(c.comp_id, j): ps[i].total_pressure
             for i, (c, j, _p) in enumerate(rows)}

    def add(sev, inv, where, detail):
        findings.append({"severity": sev, "invariant": inv,
                         "where": where, "detail": detail})

    # ---- 不变量 5：NaN/Inf + 正性（先跑：坏场时其余项数值无意义） ----
    nonfinite, nonpos = [], []
    for i, nid in enumerate(sys_.interior_ids):
        p0, t0 = float(x[i]), float(x[sys_.T_idx_of_node[nid]])
        if not (math.isfinite(p0) and math.isfinite(t0)):
            nonfinite.append(f"n{nid}(p0,t0)")
        elif p0 <= 0.0 or t0 <= 0.0:
            nonpos.append(f"n{nid}(p0={p0:.3e},T0={t0:.3e})")
    for (c, j, port), st in zip(rows, ps):
        tag = f"c{c.comp_id}口{j}"
        vals = {"mdot": st.mass_flow, "ps": st.static_pressure,
                "Ts": st.static_temperature, "p0": st.total_pressure,
                "T0": st.total_temperature, "rho": st.density,
                "v": st.velocity, "Ma": st.mach_number}
        for k, v in vals.items():
            if not math.isfinite(v):
                nonfinite.append(f"{tag}.{k}")
        if math.isfinite(st.static_pressure) and st.static_pressure <= 0.0:
            nonpos.append(f"{tag}.ps={st.static_pressure:.3e}")
        if math.isfinite(st.static_temperature) and st.static_temperature <= 0.0:
            nonpos.append(f"{tag}.Ts={st.static_temperature:.3e}")
        if math.isfinite(st.density) and st.density <= 0.0:
            nonpos.append(f"{tag}.rho={st.density:.3e}")
    if nonfinite:
        add("P0", "INV5_NAN_INF", f"{len(nonfinite)}处", "; ".join(nonfinite[:8]))
    if nonpos:
        add("P0", "INV5_NONPOS", f"{len(nonpos)}处", "; ".join(nonpos[:8]))
    if nonfinite or nonpos:
        stats["inv5_broken"] = True

    # ---- 不变量 1：节点连续性 ----
    m_all = [abs(st.mass_flow) for st in ps]
    scale = max(max(m_all, default=0.0), 1.0e-6 * m_ref)
    stats["scale"] = scale
    worst_cont = 0.0
    for nid in sys_.interior_ids:
        s = sum(x[sys_.m_idx_of_port[cj]] for cj in sys_.ports_on_node[nid])
        worst_cont = max(worst_cont, abs(float(s)))
        sev = _sev_band(abs(s), 1e-9 * scale, 1e-6 * scale)
        if sev:
            ports = sys_.ports_on_node[nid]
            add(sev, "INV1_CONTINUITY", f"n{nid}",
                f"|Σṁ|={abs(s):.3e}  scale={scale:.3e}  "
                f"tol=1e-9·scale={1e-9 * scale:.3e}  挂口={ports}")
    stats["max_continuity"] = worst_cont

    # ---- 不变量 2：出料口恒等式 ρ·A·v = |ṁ|（A=报表几何面积） ----
    # 口径注记一：PortState.velocity 是无符号幅值（等熵正支），按 |ṁ|
    # 对照（物理恒等式 ρA|v|=|ṁ|；带号 ṁ 在出料口为负——直接减带号值
    # 会得 2|ṁ| 假误差，首版审计已踩坑纠正）。
    # 口径注记二：任务书限定"出料口"（ṁ<0）——exit_state 契约支路。
    # 进料口白板支路作扩展观察（EXT2）：q-clamp（ṁ 超口壅塞容量时
    # Ma 钳 1）是 port_states 文档登记的报表标志，钳位口恒等式必破
    # ——记 P2/SUSPECT 并注根因，不当 P1 误报。
    worst_ident = 0.0
    for (c, j, port), st in zip(rows, ps):
        if port.area <= 0.0:
            continue                     # 边界口无动通量，恒等式不适用
        lhs = st.density * port.area * st.velocity
        err = abs(lhs - abs(st.mass_flow))
        is_discharge = st.mass_flow < 0.0
        if is_discharge:
            worst_ident = max(worst_ident, err)
        sev = _sev_band(err, 1e-9, 1e-6)
        if sev:
            if not is_discharge and (st.choked
                                     or st.mach_number >= 1.0 - 1e-6):
                add("P2", "EXT2_WHITEBOARD_CLAMP", f"c{c.comp_id}口{j}",
                    f"进料口白板 q-clamp（Ma 钳 1，ṁ 超口壅塞容量）"
                    f"|ρAv−ṁ|={err:.3e}  ρAv={lhs:.9e}  ṁ={st.mass_flow:.9e}")
            elif not is_discharge:
                add("P2", "EXT2_INLET_IDENTITY", f"c{c.comp_id}口{j}",
                    f"进料口恒等式偏差 |ρAv−ṁ|={err:.3e}  "
                    f"ρAv={lhs:.9e}  ṁ={st.mass_flow:.9e}  A={port.area:.3e}")
            else:
                add(sev, "INV2_RHOAV", f"c{c.comp_id}口{j}",
                    f"|ρAv−ṁ|={err:.3e}  ρAv={lhs:.9e}  "
                    f"ṁ={st.mass_flow:.9e}  A={port.area:.3e}")
    stats["max_identity_err"] = worst_ident

    # ---- 不变量 3：出料口隐含射流总压 ≤ max(进料口节点总压)（热二律） ----
    # 适用域（报告口径）：被动元件（ORIFICE/PIPE/AREA_CHANGE/HEATER/
    # JUNCTION）的出料口。BOOSTER 是主动增压源（p_out_spec>p_in_spec
    # 是其参数本职），出料口射流总压超过进料侧合法，不适用（首版
    # 误报已排除）。
    # 口径注记三（首版误报纠正）：p0_jet 的 T0 取**报告状态自洽的
    # 流股总温** T0_jet = Ts·(1+(γ−1)Ma²/2)——由出口静态自身重构，
    # 等价于 ps·(T0_jet/Ts)^(γ/(γ−1)) = ps·(1+(γ−1)Ma²/2)^(γ/(γ−1))。
    # base 守恒律支构造 Ts=t0−v²/2cp ⟹ T0_jet≡节点 T0（不变）；
    # pipe 覆盖支 ⟹ T0_jet≡上游流股 T0（Fanno 沿程守恒）——任何
    # 节点/流股簿记歧义都被消除（A0061/A0245 首版误报实证）。
    # 口径注记四：|ṁ| < 1e-6·scale 的近零流量口不判——死肢端口
    # ṁ 在噪声地板上，"出料"分类本身无数值意义（A0187 首版误报
    # 实证：exit_state 在 Ma→0 退化区给出任意 ps，再被 T0/Ts 放大）。
    passive = (ElemType.ORIFICE, ElemType.PIPE, ElemType.AREA_CHANGE,
               ElemType.HEATER, ElemType.JUNCTION)
    worst_margin = math.inf
    for (c, j, port), st in zip(rows, ps):
        if port.area <= 0.0 or st.mass_flow >= 0.0:
            continue                     # 只看出料口（ṁ<0）且 A>0
        if c.elem_type not in passive:
            continue                     # 主动件（BOOSTER）不适用
        if abs(st.mass_flow) < 1e-6 * scale:
            continue                     # 近零流量死肢，出料判定无意义
        if not (math.isfinite(st.static_pressure)
                and math.isfinite(st.static_temperature)
                and st.static_temperature > 0.0):
            continue                     # INV5 已记，此处跳过防幂运算崩
        ma2 = st.mach_number * st.mach_number
        if not math.isfinite(ma2) or ma2 > 2.5:
            continue                     # Ma 异常态（INV2/INV5 已覆盖）
        t_ratio = 1.0 + 0.5 * (gamma - 1.0) * ma2
        p0_jet = st.static_pressure * t_ratio ** (gamma / (gamma - 1.0))
        ins = [k for k in range(len(c.ports))
               if mdot_of[(c.comp_id, k)] > 0.0]
        if not ins:
            continue                     # 无进料口（未定性场景，不判）
        bound = max(p0_of[(c.comp_id, k)] for k in ins)
        margin = bound - p0_jet
        worst_margin = min(worst_margin, margin / bound if bound > 0 else margin)
        if p0_jet > bound * (1.0 + 1e-6):
            add("P0", "INV3_JET_P0_THERMAL2", f"c{c.comp_id}口{j}",
                f"隐含射流总压 p0_jet={p0_jet:.6e} > 进料上界 "
                f"{bound:.6e}（超出 {p0_jet / bound - 1.0:.3e} 相对）")
    stats["min_jet_margin_rel"] = (None if worst_margin is math.inf
                                   else worst_margin)

    # ---- 不变量 4：节点 T0 ∈ [源温 min, max]（含发热件的网络跳过） ----
    heaters = [c for c in net.comps
               if c.elem_type == ElemType.HEATER and c.params[0] != 0.0]
    sources = []
    for c in net.comps:
        if c.elem_type == ElemType.PRESSURE_BOUNDARY:
            sources.append(float(c.params[1]))
        elif c.elem_type == ElemType.MASS_SOURCE:
            sources.append(float(c.params[1]))
        elif c.elem_type == ElemType.BOOSTER and len(c.params) >= 3:
            sources.append(float(c.params[2]))
    if heaters:
        stats["inv4"] = "skipped(heater)"
    elif not sources:
        stats["inv4"] = "skipped(no-temperature-source)"
    else:
        lo, hi = min(sources), max(sources)
        tol = max(1e-3, 1e-9 * hi)           # 1 mK：滤 Newton/ε 正则噪声档
        worst_T = 0.0
        for i, nid in enumerate(sys_.interior_ids):
            t0 = float(x[sys_.T_idx_of_node[nid]])
            if not math.isfinite(t0):
                continue                 # INV5 已记
            over = max(lo - t0, t0 - hi, 0.0)
            worst_T = max(worst_T, over)
            if over > tol:
                sev = "P1" if over > 1.0 else "P2"
                add(sev, "INV4_NODE_T0_RANGE", f"n{nid}",
                    f"T0={t0:.4f} K 超出源温范围 [{lo:.1f}, {hi:.1f}] K "
                    f"越界 {over:.3e} K")
        stats["inv4_worst_over_K"] = worst_T
        stats["inv4"] = f"checked[{lo:.1f},{hi:.1f}]K"

    return findings, stats
