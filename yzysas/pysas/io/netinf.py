"""netinf — JSON 拓扑文件读入与元件工厂。

文件格式见 netinf.json（2026-09-15 边界元件化，旧 boundary 字段已不支持）:
    gas   { type, T0_default }
    nodes [ { id } ]                        ← 全部内部节点，无 boundary 字段
    comps [ { id, type, ports: [{area, node}], params: [...] } ]
      边界条件 = 单口元件：PRESSURE_BOUNDARY params=[p0, T0]、
                            MASS_SOURCE params=[ṁ_spec(>0注入), T0]

    气体数值参数（R/gamma/mu/...）不再是 JSON 字段：gas.type 选类
    （缺省 IdealGas），数值一律由 fluid 类构造器赋予——JSON 只选身份，
    不传数值（2026-09-26 口径，与元件 params 不携带物性同理）。
"""
from __future__ import annotations

import json
import os

from pysas.elements.base import ElementModel, SolveContext
from pysas.datamodel import Comp, ElemType, Network, Node, Port


# ---------- 读入 ----------
def load_netinf(path: str) -> tuple[Network, SolveContext]:
    """读 JSON 文件 → (Network, SolveContext)。物性进 ctx，边界在元件。
    代理元件（SURROGATE_FLOW）的相对 model_path 相对 JSON 所在目录解析
    （模型文件常与算例同目录）；netinf_from_dict 内联算例无此上下文，
    model_path 原样透传（测试用绝对路径）。"""
    with open(path, encoding="utf-8") as f:
        net, ctx = netinf_from_dict(json.load(f))
    base = os.path.dirname(os.path.abspath(path))
    for c in net.comps:
        if c.model_path and not os.path.isabs(c.model_path):
            c.model_path = os.path.join(base, c.model_path)
    return net, ctx


def _validate_topology(nodes: list[Node], comps: list[Comp]) -> list[Node]:
    """读入期拓扑校验（2026-10-01 用户裁决：校验放读入层，不进方程构造）。

    报错（引用/约定错误——没得舍）:
      节点 id 重复 / 元件 id 重复
      端口引用未声明的节点 id（最常见 typo）
      端口数与 PORT_COUNTS 约定不符（方程计数依赖端口数，不齐必炸）
      同一元件多个口挂同一节点（初步建模即错误：零压差方程退化
        0=0 雅可比奇异——JN 算例第一课；孔板两口同节点虽只是平凡
        零流解，建模意图也必然是错的）
    警告 + 舍去:
      声明了但无任何端口引用的节点（孤立节点物理无害——连续性
        0=0 恒成立；但可能是连线 typo，故打印 id 让用户核对）
    返回：舍去孤立节点后的节点列表。
    """
    seen: set[int] = set()
    for n in nodes:
        if n.node_id in seen:
            raise ValueError(f"节点 id 重复: {n.node_id}")
        seen.add(n.node_id)
    seen_c: set[int] = set()
    for c in comps:
        if c.comp_id in seen_c:
            raise ValueError(f"元件 id 重复: {c.comp_id}")
        seen_c.add(c.comp_id)

    from pysas.datamodel.topology import PORT_COUNTS
    referenced: set[int] = set()
    for c in comps:
        lo, hi = PORT_COUNTS.get(c.elem_type, (1, 99))
        if not (lo <= len(c.ports) <= hi):
            raise ValueError(
                f"c{c.comp_id}（{c.elem_type.name}）端口数应为"
                f" {lo}~{hi}，实给 {len(c.ports)}——方程计数依赖端口数")
        per_comp: set[int] = set()
        for j, p in enumerate(c.ports):
            if p.node_id not in seen:
                raise ValueError(
                    f"c{c.comp_id} 口{j} 引用了未声明的节点 "
                    f"{p.node_id}（检查 nodes 数组与 port.node 拼写）")
            if p.node_id in per_comp:
                raise ValueError(
                    f"c{c.comp_id}（{c.elem_type.name}）的口 {j} 与其他口"
                    f"挂同一节点 n{p.node_id}——同一元件的多个口应各接"
                    f"独立节点（否则该元件方程退化/流量无意义）")
            per_comp.add(p.node_id)
            referenced.add(p.node_id)

    orphans = sorted(seen - referenced)
    if orphans:
        print(f"[netinf] 警告：孤立节点 {orphans} 无任何端口引用，已舍去"
              f"（若非本意，请检查端口连线）")
    return [n for n in nodes if n.node_id in referenced]


def netinf_from_dict(data: dict) -> tuple[Network, SolveContext]:
    """netinf 字典（JSON 等价）→ (Network, SolveContext)。

    独立于文件 IO 暴露，便于测试脚本内联定义算例（格式仍与 netinf.json 一致）。
    """
    # 物性 → ctx。gas 块两类键分家：T0_default 是求解缺省温度（ctx 层），
    # type 选气体类；数值参数（R/gamma/mu/...）由所选类的构造器赋予，
    # JSON 不携带（2026-09-26 口径——选身份不传数值；旧算例若仍写
    # R/gamma/mu 数值键将被忽略并提示）
    from pysas.fluids import make_gas
    ctx = SolveContext()
    gas = data.get("gas", {})
    _legacy = [k for k in gas if k not in ("type", "T0_default")]
    if _legacy:
        print(f"[netinf] 提示：gas 块数值键 {_legacy} 已退役（数值由"
              f"气体类构造器赋予），已忽略")
    ctx.gas = make_gas({"type": gas.get("type", "IdealGas")})
    ctx.T0_default = gas.get("T0_default", ctx.T0_default)

    # 节点（统一内部）
    nodes = [Node(node_id=n["id"]) for n in data["nodes"]]

    # 组件
    comps = []
    for c in data["comps"]:
        comps.append(Comp(
            comp_id=c["id"],
            elem_type=ElemType[c["type"]],
            ports=[Port(area=p["area"], node_id=p["node"])
                   for p in c["ports"]],
            params=c["params"],
            model_path=c.get("model_path", ""),
        ))

    # 拓扑校验（读入层，2026-10-01）：报错类直接抛；孤立节点警告+舍去
    nodes = _validate_topology(nodes, comps)

    net = Network(nodes=nodes, comps=comps)
    net.n_interior = len(nodes)

    # 锚温预填已移除（想法 22，2026-09-25）：PB/MASS_SOURCE 退锚，
    # 其节点 T 是未知量由能量行解出（T_spec 只作倒流 T_supply）；
    # booster 的真锚定在 assembly 组装时自报，无需 io 层预填。
    return net, ctx


# ---------- 元件工厂 ----------
_MODEL_REGISTRY: dict[int, type[ElementModel]] = {}


def register_model(cls):
    """装饰器：注册 ElementModel 子类到工厂（按 elem_type）。"""
    _MODEL_REGISTRY[cls.elem_type] = cls
    return cls


def _default_registry():
    """惰性注册自带元件（避免 import 环：io 不顶层依赖 elements 子模块）。

    幂等判据按 key（2026-09-29 修复）：旧 `if _MODEL_REGISTRY: return`
    会在外部包（如 cosim WALL_FILM）先自注册时静默吞掉全部内置注册——
    setdefault 逐 key 判定，外部已占的 key 不覆盖（扩展优先）。"""
    from pysas.elements.areachange import AreaChangeModel
    from pysas.elements.booster import BoosterModel
    from pysas.elements.boundary import (
        MassSourceModel, PressureBoundaryModel)
    from pysas.elements.heater import HeaterModel
    from pysas.elements.junction import JunctionModel
    from pysas.elements.orifice import OrificeModel
    from pysas.elements.pipe import PipeModel
    from pysas.elements.surrogate.flow import SurrogateFlowModel

    for cls in (OrificeModel, PipeModel, PressureBoundaryModel,
                MassSourceModel, BoosterModel, AreaChangeModel,
                HeaterModel, JunctionModel, SurrogateFlowModel):
        _MODEL_REGISTRY.setdefault(int(cls.elem_type), cls)


def build_models(net: Network) -> dict[int, ElementModel]:
    """Network → { comp_id: ElementModel 实例 }（按 elem_type 分发）。"""
    _default_registry()
    models = {}
    for comp in net.comps:
        cls = _MODEL_REGISTRY.get(int(comp.elem_type))
        if cls is None:
            raise ValueError(
                f"未注册的元件类型: {comp.elem_type}（comp_id={comp.comp_id}）")
        models[comp.comp_id] = cls(comp)
    return models
