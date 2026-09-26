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

from pysas.elements.base import ElementModel, SolveContext
from pysas.datamodel import Comp, ElemType, Network, Node, Port


# ---------- 读入 ----------
def load_netinf(path: str) -> tuple[Network, SolveContext]:
    """读 JSON 文件 → (Network, SolveContext)。物性进 ctx，边界在元件。"""
    with open(path, encoding="utf-8") as f:
        return netinf_from_dict(json.load(f))


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
        ))

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
    """惰性注册自带元件（避免 import 环：io 不顶层依赖 elements 子模块）。"""
    if _MODEL_REGISTRY:
        return
    from pysas.elements.areachange import AreaChangeModel
    from pysas.elements.booster import BoosterModel
    from pysas.elements.boundary import (
        MassSourceModel, PressureBoundaryModel)
    from pysas.elements.heater import HeaterModel
    from pysas.elements.junction import JunctionModel
    from pysas.elements.orifice import OrificeModel
    from pysas.elements.pipe import PipeModel

    register_model(OrificeModel)
    register_model(PipeModel)
    register_model(PressureBoundaryModel)
    register_model(MassSourceModel)
    register_model(BoosterModel)
    register_model(AreaChangeModel)
    register_model(HeaterModel)
    register_model(JunctionModel)


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
