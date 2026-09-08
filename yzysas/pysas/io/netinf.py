"""netinf — JSON 拓扑文件读入与元件工厂。

文件格式见 netinf.json：
  gas   { R, gamma, T0_default, mu }
  nodes [ { id, boundary, p0, T0 } ]
  comps [ { id, type, ports: [{area, node}], params: [...] } ]
"""
from __future__ import annotations

import json

from pysas.elements.base import ElementModel, SolveContext
from pysas.datamodel import Comp, ElemType, Network, Node, Port


# ---------- 读入 ----------
def load_netinf(path: str) -> tuple[Network, SolveContext]:
    """读 JSON → (Network, SolveContext)。边界条件与物性进 ctx。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # 物性 → ctx
    ctx = SolveContext()
    gas = data.get("gas", {})
    ctx.gas_R = gas.get("R", 287.05)
    ctx.gamma = gas.get("gamma", 1.4)
    ctx.T0_default = gas.get("T0_default", 288.15)
    ctx.mu = gas.get("mu", 1.8e-5)

    # 节点
    nodes = []
    for n in data["nodes"]:
        node = Node(
            node_id=n["id"],
            is_boundary=n.get("boundary", False),
            total_pressure=n.get("p0", 0.0),
            total_temperature=n.get("T0", 0.0),
        )
        if node.is_boundary:
            ctx.boundary_p0[node.node_id] = node.total_pressure
            ctx.boundary_T0[node.node_id] = node.total_temperature
        nodes.append(node)

    # 组件
    comps = []
    for c in data["comps"]:
        comps.append(Comp(
            comp_id=c["id"],
            elem_type=ElemType[c["type"]],
            ports=[Port(area=p["area"], node_id=p["node"])
                   for p in c["ports"]],
            params=c["params"],
        ))

    net = Network(nodes=nodes, comps=comps)
    net.n_interior = sum(1 for n in nodes if not n.is_boundary)
    net.n_boundary = sum(1 for n in nodes if n.is_boundary)
    return net, ctx


# ---------- 元件工厂 ----------
_MODEL_REGISTRY: dict[int, type[ElementModel]] = {}


def register_model(cls):
    """装饰器：注册 ElementModel 子类到工厂（按 elem_type）。"""
    _MODEL_REGISTRY[cls.elem_type] = cls
    return cls


def _default_registry():
    """惰性注册自带元件（避免 import 环：io 不顶层依赖 elements 子模块）。"""
    if _MODEL_REGISTRY:
        return
    from pysas.elements.orifice import OrificeModel
    from pysas.elements.pipe import PipeModel

    register_model(OrificeModel)
    register_model(PipeModel)


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
