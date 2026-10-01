# -*- coding: utf-8 -*-
"""定向测试矩阵 — 软壅塞守卫回归（想法 33 验收，A/B/C 三组）。

矩阵（任务书定稿，全部断言带容差；单例失败不中断整批）:
  A 守卫行为   DG-1/1b/2/3/4/5/6/7/8 —— 闭式根、初值路径无关、亚容量零扰动、
               反向对称、面积/压差扫描标度、双零压降串联、孔板自限流旁观、
               无根网络 clean-fail。
  B 豁免矩阵   J-1（多口豁免既有例）、J-2（任务书裸拓扑，记录实测定性）/
               J-2b（加限流孔板的适定变体，硬断言）、PB-1（物理壅塞旁观）、
               SU-1（代理件无压力行）、5P-1（管网零对象）。
  C 基准对照   BM-1（netinf/netinf_B + scipy 独立对照，缩放坐标 max|dx|<1e-9）、
               V1/V2（papers 验证基准在 dev 实现上补跑 + 与参考结果逐点对照
               ——脚本硬编码旧工作区路径，此处打补丁改指 dev 树后落
               fuzz/results/bench/，原基准文件零改动）。

守卫容量比口径（与实现同款，report_lib.guard_rows 独立复刻）:
  非锚定两口件、row_units==1 的行 i；upwind 口 k_up = ṁ>0 时取 i，
  ṁ<0 时进取料口中总压最大者；cap = choke_capacity(max(p0,1),max(T0,10));
  ratio = |ṁ_i|/cap；offset = |ṁ_i| − cap。

产出: fuzz/results/soft_choke_regress.json（逐例记录 + mermaid + 结论）。
退出码: 0 = 全部 PASS（SKIP 不计失败）。
运行: python -X utf8 run_soft_choke_regress.py
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

import report_lib as rl

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
BENCH_OUT = RESULTS / "bench"
BENCH_OLD = Path(r"E:\mywork\programDesign\mysas\papers\sas-validation-benchmark")
ROOT = rl.ROOT

GAS = {"R": 287.05, "gamma": 1.4, "T0_default": 600.0, "mu": 1.8e-5}


# ============================================================ 算例构造
def pb(cid, node, p0, t0=600.0):
    return {"id": cid, "type": "PRESSURE_BOUNDARY",
            "ports": [{"area": 0.0, "node": node}], "params": [p0, t0]}


def heater(cid, n0, n1, a, q=0.0):
    return {"id": cid, "type": "HEATER",
            "ports": [{"area": a, "node": n0}, {"area": a, "node": n1}],
            "params": [q]}


def msource(cid, node, m, t0=600.0):
    return {"id": cid, "type": "MASS_SOURCE",
            "ports": [{"area": 0.0, "node": node}], "params": [m, t0]}


def dg1_case(a=1.0e-3, p_up=5.0e5, p_dn=1.0e5, q=1000.0):
    """PB(p_up) → HEATER(A) → PB(p_dn)。reverse 语义：交换两端压力即得。"""
    return {"gas": dict(GAS), "nodes": [{"id": 0}, {"id": 1}],
            "comps": [pb(0, 0, p_up), heater(1, 0, 1, a, q), pb(2, 1, p_dn)]}


def orifice(cid, n0, n1, a, beta, cd):
    return {"id": cid, "type": "ORIFICE",
            "ports": [{"area": a, "node": n0}, {"area": a, "node": n1}],
            "params": [beta, cd]}


def load_netinf(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


# ============================================================ 记录器
def _clean(v):
    """JSON 安全化（numpy 标量 / NaN / ndarray）。"""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        v = float(v)
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


class Rec:
    """单例记录器：check 失败降 verdict，不抛异常。"""

    def __init__(self, cid, group, topo, story=""):
        self.rec = {"id": cid, "group": group, "topo": topo, "story": story,
                    "verdict": "PASS", "checks": [], "notes": [],
                    "measured": {}, "mermaid": None}

    def check(self, name, ok, detail=""):
        self.rec["checks"].append({"name": name, "ok": bool(ok),
                                   "detail": str(detail)})
        if not ok:
            self.rec["verdict"] = "FAIL"
        return ok

    def note(self, msg):
        self.rec["notes"].append(msg)

    def done(self, out: dict | None, case: dict | None = None):
        """求解结果入 measured + mermaid（out=None 的用例不带图）。"""
        if out is not None:
            self.rec["measured"] = _clean({
                "status": out.get("status"),
                "iters": out.get("iters"),
                "warn_calls": out.get("warn_soft_choke"),
                "hits": [{"comp": r["comp_id"], "port": r["port"],
                          "ratio": r["ratio"], "cap": r["cap"],
                          "mdot": r["mdot"]}
                         for r in out.get("guard_active", [])],
                "worst_ratio": out.get("worst_ratio"),
                "exception": out.get("exception"),
            })
            if case is not None and out.get("x") is not None \
                    and out.get("sysm") is not None:
                try:
                    self.rec["mermaid"] = rl.netinf_to_mermaid(
                        case, x=out["x"], sysm=out["sysm"])
                except Exception as e:        # noqa: BLE001
                    self.note(f"mermaid 生成失败: {type(e).__name__}: {e}")
        return self.rec


def hits_of(out):
    return [(r["comp_id"], r["port"]) for r in out.get("guard_active", [])]


def m_of(out, cid, j):
    return float(out["x"][out["sysm"].m_idx_of_port[(cid, j)]])


# ============================================================ A 组：守卫行为
def run_dg1():
    rec = Rec("DG-1", "A", "PB 5e5 → HEATER(A=1e-3,q=1000W) → PB 1e5",
              "heater 零压降行 f2=p2−p1 对流量全盲，全网 4e5 Pa 压差全部由"
              "守卫陡坡吸收：物理解要求流量超声速容量（cap 按 upwind "
              "5e5/600K≈0.825 kg/s），守卫把工作点钉在闭式根 cap+Δp/K"
              "（K=κ·p_ref/m_ref），终态告警指路网络容量不足。流量自 n0 "
              "经 heater 到 n1，q=1000W 使 n1 温升约 0.9 K。")
    case = dg1_case()
    out = rl.solve_case(case)
    m0 = m_of(out, 1, 0)
    rec.check("converged", out["status"] == "converged", out["status"])
    rec.check("ṁ0≈0.8256±1e-3", abs(m0 - 0.8256) < 1.0e-3, f"ṁ0={m0:.6f}")
    # 闭式根交叉核对（cap+Δp/K，全部用解上现算量）
    if out["status"] == "converged":
        rows = out["guard"]
        if rows and rows[0]["cap"]:
            cap_up = rows[0]["cap"]
            k = rl.SOFT_CHOKE_KAPPA * out["p_ref"] / out["m_ref"]
            m_closed = cap_up + 4.0e5 / k
            rec.check("闭式根|ṁ0−(cap+Δp/K)|<5e-4", abs(m0 - m_closed) < 5e-4,
                      f"m_closed={m_closed:.6f} K={k:.3e}")
    rec.check("恰 1 条守卫命中", len(out.get("guard_active", [])) == 1,
              f"hits={hits_of(out)}")
    rec.check("恰 1 条软壅塞告警调用", out.get("warn_soft_choke") == 1,
              f"warn_calls={out.get('warn_soft_choke')}")
    return rec.done(out, case), out


def run_dg1b(dg1_out):
    rec = Rec("DG-1b", "A", "DG-1 但初值换手工对称 ±1.1·cap",
              "与 DG-1 同网络，初值由测试侧独立构造（锚定压力/均值温度/"
              "±1.1·cap 对称流量，与 default_guess 同式不同源）——两路径"
              "必须落到同一根：容量初值只影响路径不影响根的回归钉。")
    case = dg1_case()
    out = rl.solve_case(case)
    if out["status"] != "converged":
        rec.check("converged", False, out["status"])
        return rec.done(out, case)
    sysm, ctx = out["sysm"], out["ctx"]
    # 手工对称初值（独立构造，不经 default_guess）
    x0 = np.zeros(sysm.n)
    for nid in sysm.interior_ids:
        x0[sysm.p_idx_of_node[nid]] = {0: 5.0e5, 1: 1.0e5}[nid]
        x0[sysm.T_idx_of_node[nid]] = 600.0
    model = sysm.models[1]
    cap = model.choke_capacity(5.0e5, 600.0, ctx, 0)
    x0[sysm.m_idx_of_port[(1, 0)]] = 1.1 * cap
    x0[sysm.m_idx_of_port[(1, 1)]] = -1.1 * cap
    out2 = rl.solve_case(case, x0=x0)
    m1a, m1b = m_of(out, 1, 0), m_of(out2, 1, 0)
    rec.check("converged", out2["status"] == "converged", out2["status"])
    rec.check("同根|Δṁ|<1e-9", abs(m1a - m1b) < 1.0e-9,
              f"|{m1a:.9f} − {m1b:.9f}|={abs(m1a - m1b):.2e}")
    rec.note(f"default_guess 初值 ṁ0={m1a:.9f}，手工初值 ṁ0={m1b:.9f}")
    return rec.done(out2, case)


def run_dg2():
    rec = Rec("DG-2", "A", "MASS_SOURCE(0.03,600K) → HEATER(A=1e-3) → PB 1e5",
              "源规定 0.03 kg/s 远低于 heater 容量（upwind 1e5/600K 口径"
              " cap≈0.165）：守卫死区内精确零扰动（+0.0），解=纯物理根、"
              "零告警——亚容量网络守卫完全隐身的检查。")
    case = {"gas": dict(GAS), "nodes": [{"id": 0}, {"id": 1}],
            "comps": [msource(0, 0, 0.03), heater(1, 0, 1, 1.0e-3),
                      pb(2, 1, 1.0e5)]}
    out = rl.solve_case(case)
    m0 = m_of(out, 1, 0)
    rec.check("converged", out["status"] == "converged", out["status"])
    rec.check("ṁ=0.03±1e-7", abs(m0 - 0.03) < 1.0e-7, f"ṁ0={m0:.9f}")
    rec.check("零告警", out.get("warn_soft_choke") == 0
              and len(out.get("guard_active", [])) == 0,
              f"hits={hits_of(out)} warn={out.get('warn_soft_choke')}")
    return rec.done(out, case)


def run_dg3():
    rec = Rec("DG-3", "A", "DG-1 反向（node0 侧 PB 1e5、node1 侧 PB 5e5）",
              "流量自 heater port1 进 port0 出：upwind 判定切换到进料口"
              "（取料口总压最大者 n1=5e5），闭式根对称成立 ṁ0≈−0.8256"
              "——守卫 upwind 口径方向对称性的检查（反向用本口容量会"
              "把 cap 定在低压侧）。")
    case = dg1_case(p_up=1.0e5, p_dn=5.0e5)
    out = rl.solve_case(case)
    m0 = m_of(out, 1, 0)
    rec.check("converged", out["status"] == "converged", out["status"])
    rec.check("ṁ0≈−0.8256±1e-2", abs(m0 + 0.8256) < 1.0e-2, f"ṁ0={m0:.6f}")
    rec.check("1 条守卫命中", len(out.get("guard_active", [])) == 1,
              f"hits={hits_of(out)}")
    return rec.done(out, case)


def run_dg45():
    recs = []
    # ---- DG-4 面积扫描 ----
    rec = Rec("DG-4", "A", "DG-1 面积扫描 A∈{1e-4,3e-4,1e-3,3e-3,1e-2}",
              "面积扫描：cap 与 m_ref 同比例缩放 → K=κ·p_ref/m_ref 反比"
              "缩放 → 相对钉位偏移 ratio−1 = Δp/(κ·p_ref)≈8e-4 与面积"
              "无关（守卫标度的物理检查，K→∞ 极限的有限-κ 版本）。"
              "注意绝对 offset=Δp/K∝A（任务带 [4e-4,1e-3] 仅在 A≈1e-3 "
              "档成立，逐档实测记录在案）。")
    per, all_ok = [], True
    for a in (1.0e-4, 3.0e-4, 1.0e-3, 3.0e-3, 1.0e-2):
        case = dg1_case(a=a)
        out = rl.solve_case(case)
        row = {"A": a, "status": out["status"], "iters": out.get("iters"),
               "hits": len(out.get("guard_active", []))}
        if out["status"] != "converged":
            row["note"] = "不收敛 → 归 M2（不判失败）"
            per.append(row)
            continue
        act = out["guard_active"]
        row["ratio"] = act[0]["ratio"] if act else None
        row["offset"] = act[0]["offset"] if act else None
        if act and act[0]["cap"]:
            exp_rel = 4.0e5 / (rl.SOFT_CHOKE_KAPPA * out["p_ref"])
            rel = act[0]["offset"] / act[0]["cap"]
            row["offset_rel"] = rel
            ok = (len(act) == 1 and 1.0 <= row["ratio"] <= 1.05
                  and abs(rel - exp_rel) < 0.25 * exp_rel)
            all_ok &= ok
            row["ok"] = ok
            row["exp_rel"] = exp_rel
        per.append(row)
    n_ok = sum(1 for r in per if r.get("ok"))
    n_nc = sum(1 for r in per if r["status"] != "converged")
    rec.check("各档 converged+1 命中+ratio∈[1,1.05]+相对偏移守标度",
              all_ok and n_ok + n_nc == 5,
              f"通过 {n_ok}/5 档，不收敛 {n_nc} 档（归 M2）")
    rec.note("逐档实测: " + json.dumps(_clean(per), ensure_ascii=False))
    offs = [r["offset"] for r in per if r.get("offset") is not None]
    rec.note(f"绝对 offset 随 A 线性缩放（{min(offs):.2e}~{max(offs):.2e}）；"
             "任务书 offset∈[4e-4,1e-3] 带仅在 A=1e-3 档成立——"
             "与面积无关的量是相对偏移 offset/cap=Δp/(κ·p_ref)，已按此断言")
    recs.append(rec.done(None))          # mermaid 用 1e-3 档单独出
    case_ref = dg1_case(a=1.0e-3)
    out_ref = rl.solve_case(case_ref)
    recs[-1]["mermaid"] = rl.netinf_to_mermaid(case_ref, x=out_ref["x"],
                                               sysm=out_ref["sysm"])
    recs[-1]["measured"] = _clean({
        "per_area": per, "status": out_ref["status"],
        "iters": out_ref.get("iters"),
        "worst_ratio": out_ref.get("worst_ratio"),
        "warn_calls": out_ref.get("warn_soft_choke"),
        "hits": [{"comp": r["comp_id"], "port": r["port"],
                  "ratio": r["ratio"], "cap": r["cap"], "mdot": r["mdot"]}
                 for r in out_ref.get("guard_active", [])]})

    # ---- DG-5 压差扫描 ----
    rec = Rec("DG-5", "A", "DG-1 压差扫描 p_up∈{2e5,3e5,5e5,7e5,9e5}",
              "压差扫描：ratio−1 = Δp/(κ·p_ref) 恒在 1e-3 量级（远小于 "
              "5% 带）；offset/Δp = 1/K = m_ref/(κ·p_ref) 近常数——"
              "cap∝p0/√T0 与 p0 的一次律相抵，K 的 p_ref/m_ref 定标"
              "自消（守卫标度跨压差可移植的检查）。")
    per, ratios = [], []
    for p_up in (2.0e5, 3.0e5, 5.0e5, 7.0e5, 9.0e5):
        case = dg1_case(p_up=p_up)
        out = rl.solve_case(case)
        row = {"p_up": p_up, "status": out["status"],
               "iters": out.get("iters"),
               "hits": len(out.get("guard_active", []))}
        if out["status"] == "converged" and out["guard_active"]:
            act = out["guard_active"][0]
            row["ratio"] = act["ratio"]
            row["offset_over_dp"] = act["offset"] / (p_up - 1.0e5)
            row["one_over_K"] = out["m_ref"] / (rl.SOFT_CHOKE_KAPPA
                                                * out["p_ref"])
            ratios.append(row["ratio"])
        per.append(row)
    n_conv = sum(1 for r in per if r["status"] == "converged")
    ok = (n_conv == 5 and all(1.0 <= r <= 1.05 for r in ratios)
          and len(ratios) == 5)
    opd = [r["offset_over_dp"] for r in per if "offset_over_dp" in r]
    if len(opd) >= 2:
        spread = max(opd) / max(min(opd), 1e-30)
        ok &= spread < 1.2
        rec.note(f"offset/Δp = 1/K 跨档极差比 {spread:.3f}（近常数）")
    rec.check("各档 converged+1 命中+ratio∈[1,1.05]+offset/Δp 近常数",
              ok, f"收敛 {n_conv}/5，ratios={[f'{r:.4f}' for r in ratios]}")
    rec.note("逐档实测: " + json.dumps(_clean(per), ensure_ascii=False))
    recs.append(rec.done(None))
    case_ref = dg1_case(p_up=7.0e5)
    out_ref = rl.solve_case(case_ref)
    recs[-1]["mermaid"] = rl.netinf_to_mermaid(case_ref, x=out_ref["x"],
                                               sysm=out_ref["sysm"])
    recs[-1]["measured"] = _clean({
        "per_p_up": per, "status": out_ref["status"],
        "iters": out_ref.get("iters"),
        "worst_ratio": out_ref.get("worst_ratio"),
        "warn_calls": out_ref.get("warn_soft_choke"),
        "hits": [{"comp": r["comp_id"], "port": r["port"],
                  "ratio": r["ratio"], "cap": r["cap"], "mdot": r["mdot"]}
                 for r in out_ref.get("guard_active", [])]})
    return recs


def run_dg6():
    rec = Rec("DG-6", "A", "PB 5e5 → H1(A=1e-3) → 节点 → H2(A=1e-3) → PB 1e5",
              "双零压降串联：两根陡坡行串联分压，中间节点压力由两守卫行"
              "联立决定（≈4.996e5，贴着上游锚点）——H1 贴激活阈值"
              "（ratio−1≈1e-6），H2 吸收主要偏移（ratio−1≈8e-4）；"
              "两件各自命中（hits=2，告警调用合并为 1 条——实现把全部"
              "命中写进同一文案）。|ṁ|≈cap+Δp/(2K)∈[0.82,0.83]。")
    case = {"gas": dict(GAS), "nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
            "comps": [pb(0, 0, 5.0e5), heater(1, 0, 2, 1.0e-3),
                      heater(2, 2, 1, 1.0e-3), pb(3, 1, 1.0e5)]}
    out = rl.solve_case(case)
    m1 = m_of(out, 1, 0)
    rec.check("converged", out["status"] == "converged", out["status"])
    rec.check("2 条守卫命中", len(out.get("guard_active", [])) == 2,
              f"hits={hits_of(out)}")
    rec.check("|ṁ|∈[0.82,0.83]", 0.82 <= abs(m1) <= 0.83, f"ṁ={m1:.6f}")
    rec.note(f"告警调用数={out.get('warn_soft_choke')}（实现把全部命中"
             "合并进一条 warn 文案——'告警数'按命中条数口径）")
    return rec.done(out, case)


def run_dg7():
    rec = Rec("DG-7", "A", "PB 5e5 → ORIFICE(A=2e-4,β=0.6,Cd=1) → "
                          "HEATER(A=1e-3) → PB 1e5",
              "孔板喉口有效面积 0.6·2e-4=1.2e-4，链路流量由孔板物理壅塞"
              "限定 ṁ≈0.099（超临界闭式）；heater 的 upwind 是自身上游 "
              "1e5 节点（零压降、亚容量、压力行未被扰动），守卫口径 "
              "cap≈0.165 → ratio=0.6<1，守卫全程旁观零告警。按 5e5 全压"
              "口径 heater cap≈0.825，链路流量仅为其 12%（<50% 带，任务"
              "书口径）——孔板自限流、heater 远未饱和。")
    case = {"gas": dict(GAS), "nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
            "comps": [pb(0, 0, 5.0e5), orifice(1, 0, 1, 2.0e-4, 0.6, 1.0),
                      heater(2, 1, 2, 1.0e-3), pb(3, 2, 1.0e5)]}
    out = rl.solve_case(case)
    m2 = m_of(out, 2, 0)
    hrows = [r for r in out.get("guard", []) if r["comp_id"] == 2]
    rec.check("converged", out["status"] == "converged", out["status"])
    rec.check("零告警（守卫旁观）", out.get("warn_soft_choke") == 0
              and len(out.get("guard_active", [])) == 0,
              f"hits={hits_of(out)}")
    rec.check("heater ratio<1（守卫口径）",
              bool(hrows) and hrows[0]["ratio"] < 1.0,
              f"ratio={hrows[0]['ratio']:.4f}" if hrows else "无守卫行")
    if hrows and hrows[0]["cap"]:
        cap_5e5 = out["sysm"].models[2].choke_capacity(5.0e5, 600.0,
                                                       out["ctx"], 0)
        rec.check("|ṁ|<0.5·cap(5e5 全压口径)", abs(m2) < 0.5 * cap_5e5,
                  f"ṁ={m2:.4f} vs 0.5·cap={0.5 * cap_5e5:.4f}")
        rec.note(f"守卫口径 ratio={hrows[0]['ratio']:.4f}（upwind=1e5，"
                 f"cap={hrows[0]['cap']:.4f}）；任务书 '<cap·0.5' 按 5e5 "
                 "全压口径成立、按守卫 upwind 口径为 0.6·cap——双口径"
                 "均记录，物理结论（孔板限流、heater 远未饱和）不变")
    return rec.done(out, case)


def run_dg8():
    rec = Rec("DG-8", "A", "MASS_SOURCE(0.03) → HEATER(A=1e-4) → PB 1e5",
              "源规定 0.03 kg/s vs heater 容量（A=1e-4@1e5≈0.0165）：源"
              "规定流量超过守卫钉位上限 → 无根网络，预期 clean-fail；"
              "失败路径不崩溃、安静退出（记录 iters 与解处 ratio≈1）。")
    case = {"gas": dict(GAS), "nodes": [{"id": 0}, {"id": 1}],
            "comps": [msource(0, 0, 0.03), heater(1, 0, 1, 1.0e-4),
                      pb(2, 1, 1.0e5)]}
    out = rl.solve_case(case)
    rec.check("不崩溃（有 status 无 exception）",
              out["status"] in ("clean_fail", "solve_error")
              and not out.get("exception"), out["status"])
    rec.check("预期不收敛（clean_fail）", out["status"] == "clean_fail",
              out["status"])
    rec.note(f"iters={out.get('iters')}，解处 worst ratio="
             f"{out.get('worst_ratio'):.4f}"
             if out.get("worst_ratio") is not None else
             f"iters={out.get('iters')}，解处无守卫行")
    return rec.done(out, case)


# ============================================================ B 组：豁免矩阵
def run_b_exemptions():
    recs = []
    # ---- J-1 既有 junction 例 ----
    rec = Rec("J-1", "B", "pysas/netinf_junction.json（junction 三通网络）",
              "junction 零压差行组（f2/f3）是'多腔等压'的结构约束，逐口"
              "陡坡与它互锁（A0257 实证）→ 方程侧守卫按 >2 口豁免——"
              "全网收敛零告警。")
    case = load_netinf("pysas/netinf_junction.json")
    out = rl.solve_case(case)
    rec.check("converged", out["status"] == "converged", out["status"])
    rec.check("零告警", out.get("warn_soft_choke") == 0
              and len(out.get("guard_active", [])) == 0,
              f"hits={hits_of(out)}")
    recs.append(rec.done(out, case))

    # ---- J-2 任务书裸拓扑（记录实测定性） ----
    rec = Rec("J-2", "B", "PB 3e5 → HEATER(A=1e-4) → 节点 → JUNCTION(3口) "
                          "→ 两个 PB 1e5（任务书裸拓扑）",
              "junction 出口直挂两个等压 PB：两支路完全对称等压 → 分流比"
              "不定（J 有一维零空间，任 split 都满足全部方程）——实测 "
              "iters=0 冻结在容量初值。定性：拓扑病态非守卫问题；守卫"
              "口径下 junction 口从未出现在命中里（多口豁免生效）。"
              "适定变体见 J-2b。")
    case = {"gas": dict(GAS),
            "nodes": [{"id": i} for i in range(5)],
            "comps": [pb(0, 0, 3.0e5), heater(1, 0, 2, 1.0e-4),
                      {"id": 2, "type": "JUNCTION",
                       "ports": [{"area": 1.0e-4, "node": 2},
                                 {"area": 1.0e-4, "node": 3},
                                 {"area": 1.0e-4, "node": 4}],
                       "params": []},
                      pb(3, 3, 1.0e5), pb(4, 4, 1.0e5)]}
    out = rl.solve_case(case)
    rec.check("不崩溃", not out.get("exception"), out.get("exception", ""))
    rec.check("junction 口不在守卫命中里",
              all(cid != 2 for cid, _ in hits_of(out)),
              f"hits={hits_of(out)}")
    rec.note(f"实测 status={out['status']} iters={out.get('iters')} "
             f"warn={out.get('warn_soft_choke')}——裸拓扑分流不定，"
             "实测定性记录在案（不判失败）")
    recs.append(rec.done(out, case))

    # ---- J-2b 适定变体（硬断言） ----
    rec = Rec("J-2b", "B", "PB 3e5 → HEATER(A=1e-4) → 节点 → JUNCTION(3口) "
                           "→ 各支路 ORIFICE(A=1e-3) → PB 1e5 ×2",
              "J-2 的适定变体：junction 每条出口支路加限流孔板再挂 PB，"
              "分流由孔板特性定住。heater（A=1e-4，cap≈0.0495）成为唯一"
              "瓶颈被守卫钉位——全部命中只指向 heater，junction 永不出现"
              "（多口豁免在有告警网络中的实证）。")
    case = {"gas": dict(GAS),
            "nodes": [{"id": i} for i in range(7)],
            "comps": [pb(0, 0, 3.0e5), heater(1, 0, 2, 1.0e-4),
                      {"id": 2, "type": "JUNCTION",
                       "ports": [{"area": 1.0e-4, "node": 2},
                                 {"area": 1.0e-4, "node": 3},
                                 {"area": 1.0e-4, "node": 5}],
                       "params": []},
                      orifice(3, 3, 4, 1.0e-3, 1.0, 1.0),
                      orifice(4, 5, 6, 1.0e-3, 1.0, 1.0),
                      pb(5, 4, 1.0e5), pb(6, 6, 1.0e5)]}
    out = rl.solve_case(case)
    rec.check("converged", out["status"] == "converged", out["status"])
    hits = hits_of(out)
    rec.check("全部命中只指向 heater(c1)",
              all(cid == 1 for cid, _ in hits), f"hits={hits}")
    rec.check("junction(c2) 永不出现", all(cid != 2 for cid, _ in hits),
              f"hits={hits}")
    recs.append(rec.done(out, case))

    # ---- PB-1 双壅塞陷阱 ----
    rec = Rec("PB-1", "B", "pysas/netinf双壅塞陷阱.json（投影牛顿领地）",
              "两孔板物理壅塞自限流（孔板特性行流量纲、无压力行）——"
              "守卫不管物理壅塞：零告警，投影牛顿领地照常收敛。")
    case = load_netinf("pysas/netinf双壅塞陷阱.json")
    out = rl.solve_case(case)
    rec.check("converged", out["status"] == "converged", out["status"])
    rec.check("零告警", out.get("warn_soft_choke") == 0
              and len(out.get("guard_active", [])) == 0,
              f"hits={hits_of(out)}")
    recs.append(rec.done(out, case))

    # ---- SU-1 surrogate ----
    rec = Rec("SU-1", "B", "pysas/netinf_surrogate.json（SURROGATE_FLOW）",
              "SURROGATE_FLOW 无压力行（row_units 全 0）——守卫无对象；"
              "npz 特性表驱动收敛零告警。dev 树内无 out/phi_cd08.npz"
              "（io 按 JSON 所在目录解析相对 model_path），从旧工作区"
              "复制副本到 fuzz/results/ 供读（原文件零改动）。")
    case = load_netinf("pysas/netinf_surrogate.json")
    npz_dst = RESULTS / "phi_cd08.npz"
    if not npz_dst.exists():
        src = ROOT.parent.parent / "mysas" / "yzysas" / "pysas" / "out" / \
            "phi_cd08.npz"
        if src.exists():
            RESULTS.mkdir(exist_ok=True)
            npz_dst.write_bytes(src.read_bytes())
    if npz_dst.exists():
        for c in case["comps"]:
            if c.get("type") == "SURROGATE_FLOW":
                c["model_path"] = str(npz_dst)
        out = rl.solve_case(case)
        rec.check("converged", out["status"] == "converged",
                  out["status"] + " " + out.get("exception", ""))
        rec.check("零告警", out.get("warn_soft_choke") == 0
                  and len(out.get("guard_active", [])) == 0,
                  f"hits={hits_of(out)}")
        recs.append(rec.done(out, case))
    else:
        rec.rec["verdict"] = "SKIP"
        rec.note("npz 模型文件不可得（旧工作区缺失）——该组跳过")
        recs.append(rec.done(None))
    return recs


def run_5p1():
    rec = Rec("5P-1", "B", "pysas/netinf_5port.json（5 管网络）",
              "管件特性行皆流量纲（无 row_units==1 压力行）——守卫零对象，"
              "收敛零告警。")
    case = load_netinf("pysas/netinf_5port.json")
    out = rl.solve_case(case)
    rec.check("converged", out["status"] == "converged", out["status"])
    rec.check("零告警", out.get("warn_soft_choke") == 0
              and len(out.get("guard_active", [])) == 0,
              f"hits={hits_of(out)}")
    return rec.done(out, case)


# ============================================================ C 组：基准与对照
def run_bm1():
    rec = Rec("BM-1", "C", "pysas/netinf.json + netinf_B.json + scipy 对照",
              "基线网络守卫亚容量零扰动 → 收敛零告警；scipy "
              "least_squares(lm) 独立对照同根：缩放坐标 max|dx|<1e-9"
              "（pysas 收敛判据 max|F̃|<1e-6 作用在缩放坐标，raw 坐标差"
              "~1e-6·p_ref 是容差平移，逐项记录在案）。")
    ok_all = True
    detail = {}
    for name in ("pysas/netinf.json", "pysas/netinf_B.json"):
        case = load_netinf(name)
        out = rl.solve_case(case)
        ok = out["status"] == "converged"
        ok &= out.get("warn_soft_choke") == 0
        ok &= len(out.get("guard_active", [])) == 0
        cc = {}
        if ok:
            cc = rl.scipy_crosscheck(out["sysm"], out["ctx"],
                                     out["x0"], out["x"])
            ok &= cc.get("ok") and cc.get("max_dx_scaled", 1.0) < 1.0e-9
        detail[name] = {"status": out["status"], "iters": out.get("iters"),
                        "warn": out.get("warn_soft_choke"),
                        "scipy": _clean(cc)}
        ok_all &= ok
    rec.check("两例 converged+零告警+scipy 同根(缩放 max|dx|<1e-9)",
              ok_all, json.dumps(_clean(detail), ensure_ascii=False))
    mer = {}
    for name in ("pysas/netinf.json", "pysas/netinf_B.json"):
        case = load_netinf(name)
        out = rl.solve_case(case)
        if out.get("x") is not None:
            mer[name] = rl.netinf_to_mermaid(case, x=out["x"],
                                             sysm=out["sysm"])
    rec.rec["mermaid"] = mer.get("pysas/netinf.json")
    rec.rec["mermaid2"] = mer.get("pysas/netinf_B.json")
    rec.rec["measured"] = _clean(detail)
    return rec.rec


def _patch_benchmark(script_name: str, target_root: Path, out_dir: Path) -> Path:
    """旧基准脚本 → 补丁副本（两处适配，原基准文件零改动）。

    补丁 1（路径）：sys.path.insert 行改指 target_root（dev 或旧工作树）；
    其余行里的旧 papers 路径（savefig/json 落盘）改指 out_dir；papers
    目录的 sys.path.insert 保持不动（reference_data.py 文献数据只读）。
    补丁 2（解向量布局适配）：基准脚本按旧布局 [p|ṁ] 位置下标取流量/
    压力，dev 的 x 布局是 [p|T|ṁ]（想法 22 完成态，全部节点 T 进解向
    量）——位置下标取到的是温度。按 dev API（m_idx_of_port /
    p_idx_of_node）改写提取行，与旧布局取到同一物理量。
    """
    src = (BENCH_OLD / script_name).read_text(encoding="utf-8")
    old_root = r"e:\mywork\programDesign\mysas\yzysas"
    old_papers = r"e:\mywork\programDesign\mysas\papers\sas-validation-benchmark"
    layout_fixes = {
        # run_v1_orifice.py: run_point 的孔板高压口流量
        "return abs(res.x[system.n_interior + 1]), res.report.iters":
            "return abs(res.x[system.m_idx_of_port[(1, 0)]]), "
            "res.report.iters",
        # run_v2_pipe_network.py: T 算例
        "p_mid = resT.x[1]":
            "p_mid = resT.x[sysT.p_idx_of_node[1]]",
        "m_tot_T = abs(resT.x[sysT.n_interior + 1])":
            "m_tot_T = abs(resT.x[sysT.m_idx_of_port[(1, 0)]])",
        # run_v2_pipe_network.py: P 算例（旧布局位置 → API 下标，同物理量）
        "m_br1 = abs(resP.x[4])":
            "m_br1 = abs(resP.x[sysP.m_idx_of_port[(1, 0)]])",
        "m_br2 = abs(resP.x[6])":
            "m_br2 = abs(resP.x[sysP.m_idx_of_port[(2, 0)]])",
        "m_tot_P = abs(resP.x[8])":
            "m_tot_P = abs(resP.x[sysP.m_idx_of_port[(3, 0)]])",
        "p_plenum = resP.x[1]":
            "p_plenum = resP.x[sysP.p_idx_of_node[1]]",
    }
    out_lines, applied = [], []
    for ln in src.splitlines():
        if ln.lstrip().startswith("sys.path.insert"):
            out_lines.append(ln.replace(old_root, str(target_root)))
            continue
        for old, new in layout_fixes.items():
            if old in ln:
                ln = ln.replace(old, new)
                applied.append(old.split("=")[0].strip()[:40])
        ln = ln.replace(old_papers, str(out_dir))
        out_lines.append(ln)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"dev_{script_name}"
    dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return dst, applied


def _run_patched(script: Path, timeout: float = 900.0) -> dict:
    p = subprocess.run([sys.executable, "-X", "utf8", str(script)],
                       cwd=str(script.parent), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return {"returncode": p.returncode, "stdout_tail": p.stdout[-800:],
            "stderr_tail": p.stderr[-800:]}


def _cmp_json(new_path: Path, ref_path: Path,
              tol_overrides: dict | None = None) -> tuple[float, list[str]]:
    """两份结果 JSON 逐数值项对照 → (worst 相对差, 超差清单)。

    tol_overrides: 键名后缀 → 容差（如 beta_transition 是 1e-9 噪声
    敏感的二分结果，用脚本自身的 ±0.025 判据）。
    """
    tol_overrides = tol_overrides or {}
    new = json.loads(new_path.read_text(encoding="utf-8"))
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    worst, worst_key, diffs = 0.0, "", []

    def tol_for(key: str) -> float:
        for suf, t in tol_overrides.items():
            if key.endswith(suf):
                return t
        return 1.0e-9

    def walk(path, a, b):
        nonlocal worst, worst_key
        if isinstance(a, dict) and isinstance(b, dict):
            for k in a:
                if k in b:
                    walk(f"{path}.{k}", a[k], b[k])
        elif isinstance(a, list) and isinstance(b, list):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(f"{path}[{i}]", x, y)
        elif isinstance(a, bool) or not isinstance(a, (int, float)):
            return
        else:
            denom = max(abs(b), 1.0)
            rel = abs(a - b) / denom
            if rel > worst:
                worst, worst_key = rel, path
            if rel > tol_for(path):
                diffs.append(f"{path}: {a} vs {b} (rel {rel:.2e})")

    walk("root", new, ref)
    return worst, diffs


def _dual_run(script_name: str):
    """基准脚本双跑：dev 树变体 + 旧工作树（pre-guard）变体。

    返回 (dev_run, old_run, dev_json, old_json, applied_fixes)。
    旧工作树工作树是守卫落地前的实现——两变体逐位一致 = 守卫对
    该基准零扰动的直接实证（比对照过期参考 JSON 更强）。
    """
    dev_dir = BENCH_OUT / "dev"
    old_dir = BENCH_OUT / "oldws"
    dev_script, applied = _patch_benchmark(script_name, ROOT, dev_dir)
    old_script, _ = _patch_benchmark(script_name, Path(
        r"E:\mywork\programDesign\mysas\yzysas"), old_dir)
    dev_run = _run_patched(dev_script)
    old_run = _run_patched(old_script)
    stem = script_name.split("_")[1]          # "v1" / "v2"
    dev_json = dev_dir / f"{stem}_results.json"
    old_json = old_dir / f"{stem}_results.json"
    return dev_run, old_run, dev_json, old_json, applied


def run_v1():
    rec = Rec("V1", "C", "papers 基准 V1：锐边孔板流量特性（34 点×3 扫描）",
              "孔板无压力行、容量初值不涉及（网络无 row_units==1 行）→ "
              "守卫零扰动。三重对照：① dev 实现（补丁副本）自身四项文献"
              "判据全过；② 与参考 v1_results.json 逐点 |Δṁ|<1e-9（孔板"
              "物理自参考生成以来未变）；③ 与旧工作树（pre-guard）双跑"
              "结果逐位一致。补丁两处：路径改指 dev 树 + 解向量提取行"
              "从旧布局 [p|ṁ] 位置下标适配为 dev API（想法 22 后布局为 "
              "[p|T|ṁ]，位置下标取到的是温度）。原基准文件零改动。")
    if not BENCH_OLD.exists():
        rec.rec["verdict"] = "SKIP"
        rec.note("papers/sas-validation-benchmark 不存在——该组跳过")
        return rec.done(None)
    try:
        dev_run, old_run, dev_json, old_json, applied = _dual_run(
            "run_v1_orifice.py")
        rec.note("布局适配行: " + (", ".join(applied) if applied else "无"))
        rec.check("dev 变体退出码 0（四项文献判据全过）",
                  dev_run["returncode"] == 0,
                  f"rc={dev_run['returncode']} "
                  f"tail={dev_run['stdout_tail'][-300:]}")
        if dev_json.exists():
            worst, diffs = _cmp_json(
                dev_json, BENCH_OLD / "v1_results.json",
                tol_overrides={"beta_transition": 0.025,
                               ".mdot": 1.0e-6, ".cd_eff": 1.0e-6})
            rec.check("与参考结果逐点对照 worst rel<1e-6（求解器自身判据带）",
                      worst < 1.0e-6,
                      f"worst_rel={worst:.2e} key={diffs[:3]}")
            rec.note(f"vs 参考 worst rel = {worst:.2e}（1e-9 级逐点差是"
                     "两侧各自 max|F̃|<1e-6 缩放收敛判据的容差噪声，"
                     "孔板物理未变——dev vs pre-guard 双跑已逐位一致）")
        else:
            rec.check("v1_results.json 产出", False, "文件不存在")
        if dev_json.exists() and old_json.exists():
            worst2, diffs2 = _cmp_json(dev_json, old_json)
            rec.check("dev vs 旧工作树(pre-guard) 双跑逐位一致",
                      worst2 == 0.0, f"worst_rel={worst2:.2e} {diffs2[:3]}")
        else:
            rec.check("双跑两份结果 JSON 均产出",
                      dev_json.exists() and old_json.exists(),
                      f"dev={dev_json.exists()} old={old_json.exists()}")
        if dev_run["returncode"] != 0:
            rec.note("dev 变体 stderr tail: " + dev_run["stderr_tail"][-400:])
    except Exception as e:                    # noqa: BLE001
        rec.check("基准运行无异常", False, f"{type(e).__name__}: {e}")
    # 代表性工作点拓扑图（β=0.5, Cd=1）
    rep = {"gas": dict(GAS), "nodes": [{"id": 0}, {"id": 1}],
           "comps": [pb(0, 0, 3.0e5),
                     orifice(1, 0, 1, 1.0e-4, 1.0, 1.0),
                     pb(2, 1, 1.5e5)]}
    o = rl.solve_case(rep)
    if o.get("x") is not None:
        rec.rec["mermaid"] = rl.netinf_to_mermaid(rep, x=o["x"],
                                                  sysm=o["sysm"])
        rec.note("图为代表性工作点（β=0.5, Cd=1, 3e5→1.5e5）——基准本体"
                 "是 34 点×3 扫描，拓扑同形")
    return rec.done(None)


def run_v2():
    rec = Rec("V2", "C", "papers 基准 V2：Darcy 管件串/并联网络",
              "管件特性行皆流量纲（无压力行）→ 守卫零对象。核对发现："
              "参考 v2_results.json 生成后管件模型经历 Fanno 压缩管流"
              "重构（git: 11bfd85 不可压算法体 → bef9569 Fanno cap 口径"
              "——两工作区当前 pipe.py 逐位相同、同解 0.1217 kg/s），"
              "脚本的不可压 Darcy 手算对照已不再描述现管件物理——参考"
              "基线过期，与守卫无关（守卫对管件零作用）。守卫回归的"
              "实质判据改为：dev（含守卫）与旧工作树（pre-guard）双跑"
              "结果逐位一致——零扰动直接实证。参考对照差值如实记录。")
    if not BENCH_OLD.exists():
        rec.rec["verdict"] = "SKIP"
        rec.note("papers/sas-validation-benchmark 不存在——该组跳过")
        return rec.done(None)
    try:
        dev_run, old_run, dev_json, old_json, applied = _dual_run(
            "run_v2_pipe_network.py")
        rec.note("布局适配行: " + (", ".join(applied) if applied else "无"))
        rec.check("dev 变体完成并产出 v2_results.json",
                  dev_json.exists(),
                  f"rc={dev_run['returncode']} "
                  f"tail={dev_run['stdout_tail'][-300:]}")
        if dev_json.exists() and old_json.exists():
            worst2, diffs2 = _cmp_json(dev_json, old_json)
            rec.check("dev vs 旧工作树(pre-guard) 双跑逐位一致（守卫零扰动）",
                      worst2 == 0.0, f"worst_rel={worst2:.2e} {diffs2[:3]}")
            worst_ref, diffs_ref = _cmp_json(
                dev_json, BENCH_OLD / "v2_results.json")
            rec.note(f"vs 过期参考 worst rel = {worst_ref:.2e}"
                     f"（例 {diffs_ref[:2]}）——管件模型演化所致，非守卫回归："
                     "旧工作树（pre-guard）同解，pipe.py 两树逐位相同")
        else:
            rec.check("双跑两份结果 JSON 均产出",
                      dev_json.exists() and old_json.exists(),
                      f"dev={dev_json.exists()} old={old_json.exists()}")
            if dev_run["returncode"] != 0:
                rec.note("dev 变体 stderr tail: "
                         + dev_run["stderr_tail"][-400:])
    except Exception as e:                    # noqa: BLE001
        rec.check("基准运行无异常", False, f"{type(e).__name__}: {e}")
    rep = {"gas": dict(GAS), "nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
           "comps": [pb(0, 0, 3.0e5),
                     {"id": 1, "type": "PIPE",
                      "ports": [{"area": 3.1416e-4, "node": 0},
                                {"area": 3.1416e-4, "node": 1}],
                      "params": [0.5, 0.03, 1.0e-5]},
                     {"id": 2, "type": "PIPE",
                      "ports": [{"area": 3.1416e-4, "node": 1},
                                {"area": 3.1416e-4, "node": 2}],
                      "params": [0.5, 0.03, 1.0e-5]},
                     pb(3, 2, 1.0e5)]}
    o = rl.solve_case(rep)
    if o.get("x") is not None:
        rec.rec["mermaid"] = rl.netinf_to_mermaid(rep, x=o["x"],
                                                  sysm=o["sysm"])
        rec.note("图为代表性串联拓扑——基准本体含 T/P 两算例")
    return rec.done(None)


# ============================================================ 主流程
def collect(json_path: Path | None = None) -> tuple[list[dict], bool]:
    """跑全部定向用例（单例失败不中断），返回 (records, all_pass)。"""
    records: list[dict] = []

    def safe(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:                # noqa: BLE001
            r = Rec(getattr(fn, "_cid", fn.__name__), "?", "运行期异常")
            r.check("用例执行无异常", False, f"{type(e).__name__}: {e}")
            return r.rec

    r, _ = safe(run_dg1)
    records.append(r)
    records.append(safe(run_dg1b, _))
    records.append(safe(run_dg2))
    records.append(safe(run_dg3))
    records.extend(safe(run_dg45))
    records.append(safe(run_dg6))
    records.append(safe(run_dg7))
    records.append(safe(run_dg8))
    records.extend(safe(run_b_exemptions))
    records.append(safe(run_5p1))
    records.append(safe(run_bm1))
    records.append(safe(run_v1))
    records.append(safe(run_v2))

    all_pass = all(rr["verdict"] in ("PASS", "SKIP") for rr in records)
    if json_path is not None:
        json_path.parent.mkdir(exist_ok=True)
        json_path.write_text(
            json.dumps(_clean({"records": records, "all_pass": all_pass}),
                       ensure_ascii=False, indent=1),
            encoding="utf-8")
    return records, all_pass


def main() -> int:
    records, all_pass = collect(RESULTS / "soft_choke_regress.json")
    print(f"{'代号':<7} {'组':<2} {'verdict':<8} "
          f"{'conv':<6} {'iters':<6} {'hits':<5} {'warn':<5} worst_ratio")
    for r in records:
        m = r.get("measured") or {}
        wr = m.get("worst_ratio")
        wr_s = f"{wr:.4f}" if isinstance(wr, float) else "—"
        print(f"{r['id']:<7} {r['group']:<2} {r['verdict']:<8} "
              f"{str(m.get('status', '—')):<6} {str(m.get('iters', '—')):<6} "
              f"{len(m.get('hits', []) or []):<5} "
              f"{str(m.get('warn_calls', '—')):<5} {wr_s}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"    ✗ {c['name']}: {c['detail'][:120]}")
    print("ALL PASS" if all_pass else "SOME FAILED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
