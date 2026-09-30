"""app — 单文件 HTML 前后处理工具生成器（pysas/tools/ui 的本体）。

产物：零依赖 HTML（原生 JS + Canvas，无构建/无 CDN）——离线可用，
浏览器打开即用。生成期把两样 Python 侧知识注入模板占位符：
  * __SCHEMA__：元件元数据（与 datamodel.ElemType 生成期对拍防漂移）
  * __DEMOS__ ：pysas/ 下三个真实算例 JSON（离线 demo）

画布架构（2026-09-30 v2，路线 A"借架构"）：
  调研 basketikun/infinite-canvas（MIT，React+自研画布）后将其交互
  模型移植为原生 JS——viewport {x,y,k} 单一状态 + world/screen 互
  换（screen = world·k + offset），节点/连线画在 world 坐标，一次
  ctx.setTransform 完成整体缩放平移。交互原语对齐该项目：
  滚轮缩放（鼠标锚点）、中键/空格平移、rAF 节流拖拽、框选多选、
  撤销重做栈（400ms 防抖 commit）、minimap、按视图裁剪绘制。
  数据模型同构：nodes[]+comps[]（对应其 nodes+connections）。

落点终裁（2026-09-30，四轮）：yzysas 根 → pysas/ui → tools 平铺
→ tools/ui 子包。依赖单向纪律不变：核心包永不 import tools。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# ---------------- 元件元数据（单一事实源 = ElemType docstring，此处消费） ----------------
# etype: ElemType 枚举名（JSON type 字段真值——导入导出映射键）
# ports: 端口数; params: [(键名, 单位, 缺省)]; impl=False 面板禁用（防坏 JSON）
SCHEMA: dict[int, dict] = {
    0: dict(etype="ORIFICE", name="孔板", sym="orifice", cat="节流", ports=2, impl=True,
            params=[("面积比", "–", 1.0), ("流量系数 Cd", "–", 0.8)]),
    1: dict(etype="SEAL", name="篦齿封严", sym="seal", cat="节流", ports=2, impl=False,
            params=[("齿高", "m", 1e-3), ("齿宽", "m", 1e-3),
                    ("齿数", "–", 10), ("间隙", "m", 3e-4)]),
    2: dict(etype="PIPE", name="直管", sym="pipe", cat="管路", ports=2, impl=True,
            params=[("长度 L", "m", 0.5), ("直径 D", "m", 0.02),
                    ("粗糙度 ε", "m", 1e-5)]),
    3: dict(etype="PRESWIRL_NOZZLE", name="预旋喷嘴", sym="preswirl", cat="节流", ports=2, impl=False,
            params=[("半径", "m", 0.3), ("角度", "°", 0.0),
                    ("流量系数 Cd", "–", 0.8)]),
    4: dict(etype="VOLUME", name="容腔", sym="volume", cat="容器", ports=1, impl=False,
            params=[("体积", "m³", 1e-3)]),
    5: dict(etype="PRESSURE_BOUNDARY", name="压力边界", sym="pbound", cat="边界", ports=1, impl=True,
            params=[("总压 p0", "Pa", 3.0e5), ("总温 T0", "K", 600.0)]),
    6: dict(etype="MASS_SOURCE", name="流量边界", sym="msource", cat="边界", ports=1, impl=True,
            params=[("注入流量 ṁ(>0)", "kg/s", 0.1), ("总温 T0", "K", 600.0)]),
    7: dict(etype="BOOSTER", name="升压器", sym="booster", cat="主动", ports=2, impl=True,
            params=[("进口 p_in", "Pa", 2.0e5), ("出口 p_out", "Pa", 3.0e5),
                    ("锚温 T_spec(可选)", "K", None)]),
    8: dict(etype="HEATER", name="加热器", sym="heater", cat="热", ports=2, impl=True,
            params=[("功率 q(>0加热)", "W", 1000.0)]),
    9: dict(etype="JUNCTION", name="三通", sym="junction", cat="混合", ports=3, impl=True,
            params=[]),
    10: dict(etype="AREA_CHANGE", name="突扩/缩", sym="areachg", cat="管路", ports=2, impl=True,
             params=[("损失系数 ζ", "–", 0.5)]),
    11: dict(etype="WALL_FILM", name="壁面换热", sym="wallfilm", cat="耦合", ports=1, impl=False,
             params=[("参考面积 A_ref", "m²", 1e-3)]),
    12: dict(etype="SURROGATE_FLOW", name="代理件", sym="surrogate", cat="数据", ports=2, impl=True,
             model_path=True,
             params=[("面积比", "–", 1.0)]),
}

_CAT_COLOR = {"边界": "#e67e22", "节流": "#3498db", "管路": "#27ae60",
              "热": "#e74c3c", "主动": "#9b59b6", "混合": "#1abc9c",
              "数据": "#f1c40f", "容器": "#95a5a6", "耦合": "#95a5a6"}


def _check_schema():
    """生成期对拍：SCHEMA 覆盖且仅覆盖 ElemType 全体成员（防元件库漂移）。
    etype 必须真等于枚举名（导入导出的映射键，错一个回读就炸）。"""
    from pysas.datamodel import ElemType
    enum_ids = {int(t) for t in ElemType}
    schema_ids = set(SCHEMA)
    missing = enum_ids - schema_ids
    extra = schema_ids - enum_ids
    bad_etype = {i: SCHEMA[i]["etype"] for i in sorted(schema_ids & enum_ids)
                 if SCHEMA[i]["etype"] != ElemType(i).name}
    if missing or extra or bad_etype:
        names = {int(t): t.name for t in ElemType}
        raise RuntimeError(
            f"ui SCHEMA 与 ElemType 漂移：缺 {sorted(missing)} "
            f"{[names[i] for i in missing]}，多 {sorted(extra)}，"
            f"etype 错误 {bad_etype}——新元件必须同步 tools/ui/app.py 元数据")


def _demos() -> dict[str, str]:
    """内置 demo：pysas/ 下三个真实算例原文（离线可加载）。"""
    base = Path(__file__).resolve().parents[2]
    out = {}
    for key, fn in [("两管串联 A", "netinf.json"),
                    ("单孔板 B", "netinf_B.json"),
                    ("JN 汇流", "netinf_junction.json")]:
        p = base / fn
        if p.exists():
            out[key] = json.dumps(json.loads(p.read_text(encoding="utf-8")),
                                  ensure_ascii=False)
    return out


def build_html() -> str:
    _check_schema()
    tpl = (Path(__file__).resolve().parent / "template.html") \
        .read_text(encoding="utf-8")
    schema_js = {k: {**v, "color": _CAT_COLOR.get(v["cat"], "#555")}
                 for k, v in SCHEMA.items()}
    out = (tpl.replace("__SCHEMA__", json.dumps(schema_js, ensure_ascii=False))
              .replace("__DEMOS__", json.dumps(_demos(), ensure_ascii=False)))
    assert "__SCHEMA__" not in out and "__DEMOS__" not in out
    return out


def _selftest():
    """模块级自验：生成 → 占位符清零 → 关键结构存在（python -c 可调）。"""
    html = build_html()
    assert len(html) > 20000, f"产物过小 {len(html)}"
    for probe in ("SCHEMA", "DEMOS", "w2s(", "s2w(", "viewport", "minimap",
                  "undo", "fitView", "netinf"):
        assert probe in html, f"缺关键结构 {probe}"
    print(f"OK tools/ui.app 自验通过（{len(html)//1024} KB）")


if __name__ == "__main__":
    _selftest()
