# -*- coding: utf-8 -*-
"""A 层随机拓扑生成器——netinf 字典算例，固定种子可复现。

覆盖任务书参数域:
  节点 3~12；拓扑 ∈ {chain 链, branch 分支树, merge 汇合(树+冗余边)}；
  面积 1e-8~1e-2 对数均匀；边界压力比 0.05~0.98；总温 300~900 K。
  元件池：ORIFICE / PIPE / AREA_CHANGE / BOOSTER / HEATER / JUNCTION /
  PRESSURE_BOUNDARY / MASS_SOURCE。
  SURROGATE_FLOW 不入随机池——需 npz 模型文件（缺文件组装期报错是
  设计行为，任务书已知非 bug），随机层排除以免淹没真问题。

适定性护栏（排除"生成器自造病态"，只留求解器真压力）:
  · ≥2 个 PRESSURE_BOUNDARY 保证压差驱动（单 PB 网络是平凡零流解，
    不是 fuzz 样本；冒烟批次 4/10 例此类已修）；
  · 零压降元件不成环、不成串：HEATER 与 JUNCTION 不同网
    （heater 闭环/过 junction 是流量不定型 → J 结构奇异，A0002
    零空间诊断实证）；HEATER 每网 ≤1，且不跨两个异压锚点
    （零压降串联跨异压边界 = 模型矛盾，无解非求解器问题）；
  · 汇合拓扑的冗余边（成环边）强制含阻元件——每个环至少一条阻抗；
  · PB 不与 BOOSTER 同节点（锚压矛盾 → 纯净失败噪声）；
  · JUNCTION 三口接三个互异节点（同节点 → 零行奇异）。
"""
from __future__ import annotations

import math

import numpy as np

AREA_LO, AREA_HI = math.log(1e-8), math.log(1e-2)
TOPOLOGY = ("chain", "branch", "merge")


def _logu(rng):
    return float(math.exp(rng.uniform(AREA_LO, AREA_HI)))


def _orifice(rng, cid, a, b, area=None):
    area = area or _logu(rng)
    return {"id": cid, "type": "ORIFICE",
            "ports": [{"area": area, "node": a}, {"area": area, "node": b}],
            "params": [1.0, float(rng.uniform(0.5, 1.0))]}


def _edge_comp(rng, cid, a, b, resistive_only: bool):
    """一条边 → 随机两口元件（netinf 字典）。resistive_only 用于成环边。"""
    kinds = ["ORIFICE", "PIPE", "AREA_CHANGE"]
    if not resistive_only:
        kinds = ["ORIFICE", "PIPE", "AREA_CHANGE", "HEATER"]
    kind = rng.choice(kinds, p=[0.42, 0.34, 0.24] if resistive_only
                      else [0.38, 0.30, 0.21, 0.11])
    if kind == "ORIFICE":
        return _orifice(rng, cid, a, b)
    if kind == "PIPE":
        area = _logu(rng)
        d = 2.0 * math.sqrt(area / math.pi)          # 管流通面积=口面积
        return {"id": cid, "type": "PIPE",
                "ports": [{"area": area, "node": a},
                          {"area": area, "node": b}],
                "params": [float(math.exp(rng.uniform(math.log(0.1),
                                                      math.log(10.0)))),
                           d,
                           float(math.exp(rng.uniform(math.log(1e-7),
                                                      math.log(5e-5))))]}
    if kind == "AREA_CHANGE":
        return {"id": cid, "type": "AREA_CHANGE",
                "ports": [{"area": _logu(rng), "node": a},
                          {"area": _logu(rng), "node": b}],
                "params": [float(rng.uniform(0.3, 2.0))]}
    # HEATER（零压降发热；恒等式/二律不受影响，不变量 4 将跳过）
    area = _logu(rng)
    return {"id": cid, "type": "HEATER",
            "ports": [{"area": area, "node": a},
                      {"area": area, "node": b}],
            "params": [float(rng.uniform(1e2, 1e4))]}


def gen_case(rng: np.random.Generator) -> dict:
    """一个随机 netinf 字典算例。rng 由调用方按固定种子创建。"""
    n_nodes = int(rng.integers(3, 13))
    topo = TOPOLOGY[int(rng.integers(0, len(TOPOLOGY)))]
    nodes = [{"id": i} for i in range(n_nodes)]
    comps, cid = [], 0

    use_junction = n_nodes >= 3 and rng.random() < 0.25
    tree_edges, extra_edges = [], []

    # ---- 内部元件骨架：链 / 分支树 / 汇合（树 + 含阻冗余边） ----
    if topo == "chain":
        order = [int(v) for v in rng.permutation(n_nodes)]
        tree_edges = list(zip(order[:-1], order[1:]))
    else:
        for i in range(1, n_nodes):                   # 随机父链 = 分支树
            p = int(rng.integers(0, i))
            tree_edges.append((p, i))
        if topo == "merge":                           # 冗余边造汇合/环路
            want = int(rng.integers(1, max(2, n_nodes)))
            have = {(min(a, b), max(a, b)) for a, b in tree_edges}
            for _ in range(80):
                if len(extra_edges) >= want:
                    break
                a, b = sorted(int(v) for v in rng.choice(n_nodes, 2,
                                                         replace=False))
                if a != b and (a, b) not in have:
                    have.add((a, b))
                    extra_edges.append((a, b))

    heater_used = False
    for (a, b) in tree_edges:
        c = _edge_comp(rng, cid, a, b, resistive_only=False)
        if c["type"] == "HEATER":                     # 每网 ≤1 台 heater
            if heater_used or use_junction:
                c = _orifice(rng, cid, a, b,
                             c["ports"][0]["area"])
            else:
                heater_used = True
        comps.append(c)
        cid += 1
    for (a, b) in extra_edges:                        # 成环边强制含阻
        comps.append(_edge_comp(rng, cid, a, b, resistive_only=True))
        cid += 1

    # ---- JUNCTION（与 HEATER 互斥；三口接互异节点） ----
    booster_nodes = set()
    if use_junction:
        tri = [int(v) for v in rng.choice(n_nodes, 3, replace=False)]
        comps.append({"id": cid, "type": "JUNCTION",
                      "ports": [{"area": _logu(rng), "node": t}
                                for t in tri],
                      "params": []})
        cid += 1
    # ---- BOOSTER（锚定型两口源；占位节点排除后续 PB） ----
    elif n_nodes >= 3 and rng.random() < 0.14:
        a, b = sorted(int(v) for v in rng.choice(n_nodes, 2, replace=False))
        if a != b:
            p_in = float(math.exp(rng.uniform(math.log(2e5), math.log(5e5))))
            params = [p_in, p_in * float(rng.uniform(1.05, 2.0))]
            if rng.random() < 0.5:
                params.append(float(rng.uniform(300.0, 900.0)))
            comps.append({"id": cid, "type": "BOOSTER",
                          "ports": [{"area": 1e-4, "node": a},
                                    {"area": 1e-4, "node": b}],
                          "params": params})
            booster_nodes = {a, b}
            cid += 1

    # ---- 边界：PB 2~3 个（面积 0），压力比 0.05~0.98，总温 300~900 K ----
    free = [n for n in range(n_nodes) if n not in booster_nodes]
    if len(free) >= 2:
        n_pb = int(rng.integers(2, min(3, len(free)) + 1))
    else:
        n_pb = 1                                   # booster 占满的 3 节点网
    pb_nodes = [int(v) for v in rng.choice(free, n_pb, replace=False)]
    p_hi = float(math.exp(rng.uniform(math.log(2e5), math.log(1e6))))
    for k, nd in enumerate(pb_nodes):
        p0 = p_hi if k == 0 else p_hi * float(rng.uniform(0.05, 0.98))
        comps.append({"id": cid, "type": "PRESSURE_BOUNDARY",
                      "ports": [{"area": 0.0, "node": nd}],
                      "params": [p0, float(rng.uniform(300.0, 900.0))]})
        cid += 1

    # ---- 修缺：HEATER 两端不得都是异压锚（PB/BOOSTER 对）——零压降
    #      串联跨异压边界是模型矛盾（无解），换含阻孔板 ----
    anchored_pairs = set()
    for i in range(len(pb_nodes)):
        for k2 in range(i + 1, len(pb_nodes)):
            anchored_pairs.add((pb_nodes[i], pb_nodes[k2]))
    if len(booster_nodes) == 2:
        anchored_pairs.add(tuple(sorted(booster_nodes)))
    for c in comps:
        if c["type"] == "HEATER":
            pair = tuple(sorted([c["ports"][0]["node"],
                                 c["ports"][1]["node"]]))
            if pair in anchored_pairs:
                c["type"] = "ORIFICE"
                c["params"] = [1.0, float(rng.uniform(0.5, 1.0))]

    # ---- MASS_SOURCE（≤1 个；注入为主，抽取需多 PB 兜底；单 PB 网
    #      必配注入，保证压差驱动非平凡） ----
    want_ms = rng.random() < 0.25 or n_pb < 2
    if want_ms:
        nd = int(rng.choice(n_nodes))
        sign = 1.0 if (rng.random() < 0.8 or n_pb < 2) else -1.0
        comps.append({"id": cid, "type": "MASS_SOURCE",
                      "ports": [{"area": 0.0, "node": nd}],
                      "params": [sign * float(rng.uniform(0.01, 0.3)),
                                 float(rng.uniform(300.0, 900.0))]})
        cid += 1

    return {"gas": {"type": "IdealGas", "T0_default": 600.0},
            "nodes": nodes, "comps": comps,
            "_meta": {"topology": topo, "n_nodes": n_nodes}}
