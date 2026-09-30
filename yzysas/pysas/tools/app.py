"""app — 单文件 HTML 前后处理工具生成器（pysas/tools 的本体）。

产物：零依赖 HTML（原生 JS + Canvas，无构建/无 CDN）——离线可用，
浏览器打开即用。生成期把两样 Python 侧知识注入 JS 常量：
  * SCHEMA：元件类型元数据（端口数/params 名义/单位/默认值）——
    与 datamodel.ElemType 对拍，元件库演进时生成期报错防漂移
  * DEMOS：三个内置算例（两管串联/B 单孔板/JN 汇流）的 JSON 原文

功能闭环：
  前处理  拖放搭网（节点/元件/端口连线）→ 属性表单（按 SCHEMA 渲染）
          → 导出 netinf JSON（与 io/netinf.py 同 schema，绘图坐标存
          _ui 字段——netinf_from_dict 忽略未知键，回读自动复布局）
  后处理  粘贴 out/ 结果文本 → 节点/端口表格渲染 + 流量条形图

落点裁决（2026-09-30）：原拟独立 pysas/ui 子包，用户裁定收编
pysas/tools——tools 就是工具集合体（surrogate 训练管线 + 本件），
依赖单向纪律不变：核心包永不 import tools。
"""
from __future__ import annotations

import json
from pathlib import Path

# ---------------- 元件元数据（单一事实源 = ElemType docstring，此处消费） ----------------
# ports: 端口数; params: [(键名, 单位, 缺省), ...]; model_path: 是否带模型文件
# etype: ElemType 枚举名（JSON type 字段的真值——导入导出的映射键，
#        与显示名解耦：显示名可改，etype 永远等于枚举）
# impl:  False = 面板显示但禁用（未实现，防误生成坏 JSON）
SCHEMA: dict[int, dict] = {
    0: dict(etype="ORIFICE", name="孔板 ORIFICE", cat="节流", ports=2, impl=True,
            params=[("面积比", "–", 1.0), ("流量系数 Cd", "–", 0.8)]),
    1: dict(etype="SEAL", name="篦齿封严 SEAL", cat="节流", ports=2, impl=False,
            params=[("齿高", "m", 1e-3), ("齿宽", "m", 1e-3),
                    ("齿数", "–", 10), ("间隙", "m", 3e-4)]),
    2: dict(etype="PIPE", name="直管 PIPE", cat="管路", ports=2, impl=True,
            params=[("长度 L", "m", 0.5), ("直径 D", "m", 0.02),
                    ("粗糙度 ε", "m", 1e-5)]),
    3: dict(etype="PRESWIRL_NOZZLE", name="预旋喷嘴 PRESWIRL", cat="节流", ports=2, impl=False,
            params=[("半径", "m", 0.3), ("角度", "°", 0.0),
                    ("流量系数 Cd", "–", 0.8)]),
    4: dict(etype="VOLUME", name="容腔 VOLUME", cat="容器", ports=1, impl=False,
            params=[("体积", "m³", 1e-3)]),
    5: dict(etype="PRESSURE_BOUNDARY", name="压力边界 P_BOUND", cat="边界", ports=1, impl=True,
            params=[("总压 p0", "Pa", 3.0e5), ("总温 T0", "K", 600.0)]),
    6: dict(etype="MASS_SOURCE", name="流量边界 M_SOURCE", cat="边界", ports=1, impl=True,
            params=[("注入流量 ṁ(>0)", "kg/s", 0.1), ("总温 T0", "K", 600.0)]),
    7: dict(etype="BOOSTER", name="升压器 BOOSTER", cat="主动", ports=2, impl=True,
            params=[("进口 p_in", "Pa", 2.0e5), ("出口 p_out", "Pa", 3.0e5),
                    ("锚温 T_spec(可选)", "K", None)]),
    8: dict(etype="HEATER", name="加热器 HEATER", cat="热", ports=2, impl=True,
            params=[("功率 q(>0加热)", "W", 1000.0)]),
    9: dict(etype="JUNCTION", name="三通 JUNCTION", cat="混合", ports=3, impl=True,
            params=[]),
    10: dict(etype="AREA_CHANGE", name="突扩/缩 AREA_CHG", cat="管路", ports=2, impl=True,
             params=[("损失系数 ζ", "–", 0.5)]),
    11: dict(etype="WALL_FILM", name="壁面换热 WALL_FILM", cat="耦合", ports=1, impl=False,
             params=[("参考面积 A_ref", "m²", 1e-3)]),
    12: dict(etype="SURROGATE_FLOW", name="代理件 SURROGATE", cat="数据", ports=2, impl=True,
             model_path=True,
             params=[("面积比", "–", 1.0)]),
}

_CAT_COLOR = {"边界": "#e67e22", "节流": "#2980b9", "管路": "#27ae60",
              "热": "#c0392b", "主动": "#8e44ad", "混合": "#16a085",
              "数据": "#7f8c8d", "容器": "#7f8c8d", "耦合": "#7f8c8d"}


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
            f"etype 错误 {bad_etype}——新元件必须同步 tools/app.py 元数据")


def _demos() -> dict[str, str]:
    """内置 demo：pysas/ 下三个真实算例原文（离线可加载）。"""
    base = Path(__file__).resolve().parents[1]
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
    schema_js = {k: {**v, "color": _CAT_COLOR.get(v["cat"], "#555")}
                 for k, v in SCHEMA.items()}
    return _TEMPLATE.replace("__SCHEMA__", json.dumps(schema_js, ensure_ascii=False)
                             ).replace("__DEMOS__", json.dumps(_demos(), ensure_ascii=False))


# 页面本体：布局 = 左元件面板 / 中画布 / 右属性；底部结果查看器
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>pysas studio — 网络前处理 / 结果查看</title>
<style>
  :root { --bg:#1e2124; --panel:#2a2d31; --edge:#3a3e44; --fg:#d8dce0;
          --dim:#8a9199; --accent:#3498db; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--fg);
         font:13px/1.45 "Segoe UI", "Microsoft YaHei", sans-serif; }
  header { display:flex; align-items:center; gap:10px; padding:8px 14px;
           background:var(--panel); border-bottom:1px solid var(--edge); }
  header h1 { font-size:15px; font-weight:600; color:#fff; margin-right:8px; }
  header code { color:var(--dim); font-size:11px; }
  button { background:#3d4248; color:var(--fg); border:1px solid var(--edge);
           border-radius:4px; padding:4px 12px; cursor:pointer; font-size:12px; }
  button:hover { background:#4a5057; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.danger:hover { background:#8e3030; }
  button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
  button:disabled { opacity:.35; cursor:not-allowed; }
  #main { display:flex; height:calc(62vh - 46px); min-height:380px; }
  aside { background:var(--panel); overflow-y:auto; }
  #palette { width:190px; border-right:1px solid var(--edge); padding:8px; }
  #props   { width:290px; border-left:1px solid var(--edge); padding:10px; }
  .grp { margin-bottom:10px; }
  .grp>h3 { font-size:11px; color:var(--dim); font-weight:600;
            text-transform:uppercase; letter-spacing:.05em; margin:8px 0 4px; }
  .pal-item { display:flex; justify-content:space-between; align-items:center;
              padding:5px 8px; margin:2px 0; border-radius:4px; cursor:pointer;
              border:1px solid transparent; }
  .pal-item:hover { background:#34383d; }
  .pal-item.on { border-color:var(--accent); background:#2e4053; }
  .pal-item.dis { opacity:.38; cursor:not-allowed; }
  .pal-dot { width:9px; height:9px; border-radius:2px; margin-right:6px; flex:none; }
  .pal-name { flex:1; }
  .pal-cat { font-size:10px; color:var(--dim); }
  #canvas-wrap { flex:1; position:relative; }
  canvas { position:absolute; inset:0; width:100%; height:100%;
           background:#232629; background-image:
           radial-gradient(#2d3136 1px, transparent 1px);
           background-size:22px 22px; cursor:default; }
  #hint { position:absolute; left:10px; bottom:8px; font-size:11px;
          color:var(--dim); pointer-events:none; }
  label { display:block; font-size:11px; color:var(--dim); margin:7px 0 2px; }
  input, select { width:100%; background:#1e2124; color:var(--fg);
                  border:1px solid var(--edge); border-radius:3px;
                  padding:4px 6px; font-size:12px; }
  input:focus, select:focus { outline:1px solid var(--accent); }
  .kv { display:flex; justify-content:space-between; padding:2px 0; font-size:12px; }
  .kv b { color:#fff; font-weight:500; }
  #results { height:38vh; min-height:240px; border-top:1px solid var(--edge);
             display:flex; flex-direction:column; }
  #results header { padding:6px 14px; }
  #res-body { flex:1; display:flex; gap:10px; padding:8px 14px; overflow:hidden; }
  #res-in { width:34%; display:flex; flex-direction:column; gap:6px; }
  #res-text { flex:1; resize:none; font:11px/1.4 Consolas, monospace;
              background:#1a1c1f; color:#b8e0c0; border:1px solid var(--edge);
              border-radius:4px; padding:8px; }
  #res-out { flex:1; overflow:auto; }
  table { border-collapse:collapse; font-size:12px; margin:4px 0 12px; }
  th, td { border:1px solid var(--edge); padding:3px 9px; text-align:right; }
  th { background:#31353a; color:var(--dim); font-weight:500; }
  th:first-child, td:first-child { text-align:left; }
  tr.choked td { color:#e74c3c; }
  .bar-wrap { display:flex; align-items:center; height:16px; }
  .bar { height:10px; border-radius:2px; background:var(--accent); }
  .bar.neg { background:#e67e22; }
  #toast { position:fixed; top:56px; left:50%; transform:translateX(-50%);
           background:#2e4053; border:1px solid var(--accent); color:#fff;
           padding:8px 18px; border-radius:5px; font-size:12px;
           display:none; z-index:9; max-width:80%; }
  .warn { color:#e67e22; }
</style>
</head>
<body>
<header>
  <h1>pysas studio</h1><code>io/netinf.py schema</code>
  <button id="bSel" class="on" title="点击选中 / 拖拽移动">⬚ 选择</button>
  <button id="bNode" title="点击画布空白放置内部节点">○ 节点</button>
  <button id="bLink" title="先点元件端口小圆，再点目标节点">↔ 连线</button>
  <button id="bDel" class="danger" disabled>✕ 删除</button>
  <span style="flex:1"></span>
  <select id="demoSel" title="加载内置算例"></select>
  <button id="bDemo">加载</button>
  <label style="margin:0"><input type="file" id="fImport" accept=".json"
         style="display:none"></label>
  <button id="bImport">导入</button>
  <button id="bExport" class="primary">导出 JSON</button>
</header>
<div id="main">
  <aside id="palette">
    <div class="grp"><h3>内部节点</h3>
      <div style="font-size:11px;color:var(--dim);padding:2px 4px">
        用「节点」工具放置；端口连线绑定</div>
    </div>
    <div class="grp"><h3>元件（点击后画布放置）</h3><div id="pal"></div></div>
  </aside>
  <div id="canvas-wrap">
    <canvas id="cv"></canvas>
    <div id="hint"></div>
  </div>
  <aside id="props"><div class="grp"><h3>属性</h3><div id="propBody">
    <div style="color:var(--dim);font-size:12px">未选中——点击画布中的节点或元件</div>
  </div></div></aside>
</div>
<section id="results">
  <header>
    <h1 style="font-size:13px">结果查看器</h1><code>粘贴 out/result_*.txt</code>
    <span style="flex:1"></span>
    <button id="bParse" class="primary">解析渲染</button>
  </header>
  <div id="res-body">
    <div id="res-in">
      <textarea id="res-text" placeholder="把 pysas out/ 目录下 result_pysas_solver.txt 的内容整段粘进来……"></textarea>
      <div style="font-size:11px;color:var(--dim)">
        解析节点 p0/T0、各口 m/ps/Ts/ρ/v/Ma/壅塞标志 + 连续性；红色行 = 壅塞口</div>
    </div>
    <div id="res-out"></div>
  </div>
</section>
<div id="toast"></div>

<script>
const SCHEMA = __SCHEMA__;
const DEMOS  = __DEMOS__;

// ---------------- 状态 ----------------
let S = { nodes:[], comps:[],
          nid:0, cid:0,            // 下一个 id（导入时取 max+1）
          sel:null,                // {kind:'node'|'comp', id} | null
          mode:'sel',              // sel | node | link | place
          placeType:null,          // mode='place' 时待放的 ElemType
          linkFrom:null };         // {cid, port}
let drag = null;                   // 拖拽 {obj, dx, dy}
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const $ = id => document.getElementById(id);

function toast(msg, isWarn){
  const t = $('toast'); t.textContent = msg;
  t.style.borderColor = isWarn ? '#e67e22' : '#3498db';
  t.style.display = 'block';
  clearTimeout(t._h); t._h = setTimeout(()=>t.style.display='none', 2600);
}

// ---------------- 元件面板 ----------------
(function(){
  const cats = {};
  for (const [id, m] of Object.entries(SCHEMA)) (cats[m.cat] ??= []).push([+id, m]);
  const pal = $('pal');
  for (const [cat, items] of Object.entries(cats)) {
    const h = document.createElement('div');
    h.className='pal-cat'; h.style.cssText='margin:6px 0 2px;font-size:10px;color:var(--dim)';
    h.textContent = cat; pal.appendChild(h);
    for (const [id, m] of items) {
      const d = document.createElement('div');
      d.className = 'pal-item' + (m.impl ? '' : ' dis');
      d.innerHTML = `<span style="display:flex;align-items:center">
        <span class="pal-dot" style="background:${m.color}"></span>
        <span class="pal-name">${m.name}${m.impl?'':' <span class="pal-cat">未实现</span>'}</span></span>`;
      if (m.impl) d.onclick = () => {
        setMode('place'); S.placeType = id;
        document.querySelectorAll('.pal-item').forEach(e=>e.classList.remove('on'));
        d.classList.add('on');
        $('hint').textContent = `放置 ${m.name}：点击画布空白`;
      };
      pal.appendChild(d);
    }
  }
  const ds = $('demoSel');
  for (const k of Object.keys(DEMOS)) ds.add(new Option(k, k));
})();

// ---------------- 模式 ----------------
function setMode(m){
  S.mode = m; S.linkFrom = null; S.placeType = null;
  document.querySelectorAll('.pal-item').forEach(e=>e.classList.remove('on'));
  for (const [b, mm] of [['bSel','sel'],['bNode','node'],['bLink','link']])
    $(b).classList.toggle('on', m===mm);
  $('hint').textContent = {sel:'拖拽移动 / 点击选中；Del 删除',
    node:'点击空白放置内部节点', link:'连线：先点元件端口小圆，再点目标节点',
    place:'点击空白放置元件（Esc 取消）'}[m];
  cv.style.cursor = m==='sel' ? 'default' : 'crosshair';
}
$('bSel').onclick = () => setMode('sel');
$('bNode').onclick = () => setMode('node');
$('bLink').onclick = () => setMode('link');
addEventListener('keydown', e => {
  if (e.key==='Escape') setMode('sel');
  if (e.key==='Delete' && S.sel) delSel();
});

// ---------------- 画布几何 ----------------
function fit(){
  const r = cv.parentElement.getBoundingClientRect(), dpr = devicePixelRatio||1;
  cv.width = r.width*dpr; cv.height = r.height*dpr;
  ctx.setTransform(dpr,0,0,dpr,0,0); draw();
}
addEventListener('resize', fit);

const CW=118, CH=null; // 元件盒宽；高按端口数
function compH(c){ return 26 + SCHEMA[c.type].ports*20 + 6; }
function compAt(c){ return {x:c.x, y:c.y, w:CW, h:compH(c)}; }
function portPos(c, j){
  const b = compAt(c);
  return {x:b.x + b.w, y:b.y + 30 + j*20};   // 端口圆在右侧缘
}
function nodeAt(n){ return {x:n.x, y:n.y, r:11}; }
function hitComp(mx, my){
  for (let i=S.comps.length-1; i>=0; i--){
    const b = compAt(S.comps[i]);
    if (mx>=b.x && mx<=b.x+b.w && my>=b.y && my<=b.y+b.h) return S.comps[i];
  } return null;
}
function hitNode(mx, my){
  for (let i=S.nodes.length-1; i>=0; i--){
    const n = S.nodes[i];
    if ((mx-n.x)**2 + (my-n.y)**2 <= (n.r+3)**2) return n;
  } return null;
}
function hitPort(mx, my){
  for (const c of S.comps) for (let j=0; j<c.ports.length; j++){
    const p = portPos(c,j);
    if ((mx-p.x)**2 + (my-p.y)**2 <= 49) return {c, j};
  } return null;
}

// ---------------- 绘制 ----------------
function draw(){
  ctx.clearRect(0,0,cv.width,cv.height);
  // 连线（端口→节点）
  for (const c of S.comps) c.ports.forEach((p,j)=>{
    const n = S.nodes.find(n=>n.id===p.node);
    if (!n) return;
    const a = portPos(c,j);
    ctx.strokeStyle = '#5a6470'; ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.moveTo(a.x, a.y);
    ctx.bezierCurveTo(a.x+38, a.y, n.x-38, n.y, n.x, n.y); ctx.stroke();
  });
  // link 预览
  if (S.linkFrom && S.mouse){
    const a = portPos(S.comps.find(c=>c.id===S.linkFrom.cid), S.linkFrom.j);
    ctx.strokeStyle = '#f1c40f'; ctx.setLineDash([5,4]);
    ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(S.mouse.x,S.mouse.y);
    ctx.stroke(); ctx.setLineDash([]);
  }
  // 节点
  ctx.font = '10px Consolas'; ctx.textAlign='center';
  for (const n of S.nodes){
    const selc = S.sel && S.sel.kind==='node' && S.sel.id===n.id;
    ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,7);
    ctx.fillStyle = selc ? '#f1c40f' : '#455a64'; ctx.fill();
    ctx.strokeStyle = '#cfd8dc'; ctx.lineWidth = selc?2:1; ctx.stroke();
    ctx.fillStyle = '#fff'; ctx.fillText(n.id, n.x, n.y+3.5);
    ctx.fillStyle = '#8a9199'; ctx.fillText('n'+n.id, n.x, n.y+n.r+11);
  }
  // 元件
  for (const c of S.comps){
    const b = compAt(c), m = SCHEMA[c.type];
    const selc = S.sel && S.sel.kind==='comp' && S.sel.id===c.id;
    ctx.fillStyle = '#31363b'; ctx.strokeStyle = selc ? '#f1c40f' : m.color;
    ctx.lineWidth = selc ? 2 : 1.4;
    ctx.beginPath(); ctx.roundRect(b.x,b.y,b.w,b.h,6); ctx.fill(); ctx.stroke();
    ctx.fillStyle = m.color; ctx.textAlign='left';
    ctx.font='600 11px "Segoe UI","Microsoft YaHei"';
    ctx.fillText(m.name.split(' ')[0], b.x+8, b.y+16);
    ctx.font='10px Consolas'; ctx.fillStyle='#9aa2a9';
    ctx.fillText('c'+c.id, b.x+b.w-30, b.y+16);
    // 端口行
    c.ports.forEach((p,j)=>{
      const pp = portPos(c,j);
      const n = S.nodes.find(n=>n.id===p.node);
      const hot = S.linkFrom && S.linkFrom.cid===c.id && S.linkFrom.j===j;
      ctx.beginPath(); ctx.arc(pp.x-4, pp.y, 5.5, 0, 7);
      ctx.fillStyle = hot ? '#f1c40f' : (n ? m.color : '#78818b');
      ctx.fill();
      ctx.fillStyle='#b8bec4'; ctx.textAlign='left';
      ctx.font='10px Consolas';
      ctx.fillText(`:${j} A=${sci(p.area)}${n?' →n'+n.id:' ⌀'}`, b.x+8, pp.y+3);
    });
  }
}
function sci(v){ return (+v).toExponential(1); }

// ---------------- 交互 ----------------
function mpos(e){
  const r = cv.getBoundingClientRect();
  return {x:e.clientX-r.left, y:e.clientY-r.top};
}
cv.addEventListener('mousemove', e => {
  S.mouse = mpos(e);
  if (drag){ drag.obj.x = S.mouse.x - drag.dx; drag.obj.y = S.mouse.y - drag.dy;
    drag.obj._moved = true; draw(); return; }
  if (S.mode==='link' && S.linkFrom) draw();
});
cv.addEventListener('mousedown', e => {
  const p = mpos(e);
  if (S.mode==='node'){
    S.nodes.push({id:S.nid++, x:p.x, y:p.y}); draw(); return;
  }
  if (S.mode==='place' && S.placeType!=null){
    const t = S.placeType, m = SCHEMA[t];
    const c = {id:S.cid++, type:t, x:p.x-59, y:p.y-20,
      ports: Array.from({length:m.ports}, ()=>({area:1.0e-4, node:null})),
      params: m.params.filter(pr=>pr[2]!=null).map(pr=>pr[2]),
      model_path:''};
    S.comps.push(c); select('comp', c.id); draw(); return;
  }
  if (S.mode==='link'){
    const port = hitPort(p.x,p.y);
    if (port){ S.linkFrom = {cid:port.c.id, j:port.j};
      $('hint').textContent = `已选 c${port.c.id} 口${port.j}——点击目标节点`; draw(); return; }
    const n = hitNode(p.x,p.y);
    if (n && S.linkFrom){
      const c = S.comps.find(c=>c.id===S.linkFrom.cid);
      c.ports[S.linkFrom.j].node = n.id;
      toast(`c${c.id} 口${S.linkFrom.j} → n${n.id}`); S.linkFrom=null; draw(); return;
    }
    return;
  }
  // 选择模式
  const port = hitPort(p.x,p.y);           // 端口小圆优先（便于点选连线起点）
  if (port){ setMode('link'); S.linkFrom={cid:port.c.id, j:port.j};
    $('hint').textContent=`已选 c${port.c.id} 口${port.j}——点击目标节点`; draw(); return; }
  const c = hitComp(p.x,p.y);
  if (c){ select('comp', c.id);
    drag = {obj:c, dx:p.x-c.x, dy:p.y-c.y, }; c._moved=false; return; }
  const n = hitNode(p.x,p.y);
  if (n){ select('node', n.id); drag={obj:n, dx:p.x-n.x, dy:p.y-n.y}; n._moved=false; return; }
  select(null);
});
cv.addEventListener('mouseup', ()=>{ drag=null; });
cv.addEventListener('dblclick', e => {
  const p = mpos(e), c = hitComp(p.x,p.y);
  if (c){ select('comp', c.id); $('props').scrollIntoView(); }
});

function select(kind, id){
  S.sel = kind ? {kind, id} : null;
  $('bDel').disabled = !S.sel;
  renderProps(); draw();
}
function delSel(){
  if (!S.sel) return;
  if (S.sel.kind==='node'){
    S.nodes = S.nodes.filter(n=>n.id!==S.sel.id);
    for (const c of S.comps) c.ports.forEach(p=>{ if(p.node===S.sel.id) p.node=null; });
  } else {
    S.comps = S.comps.filter(c=>c.id!==S.sel.id);
  }
  select(null); toast('已删除');
}
$('bDel').onclick = delSel;

// ---------------- 属性面板 ----------------
function renderProps(){
  const el = $('propBody');
  if (!S.sel){ el.innerHTML =
    `<div class="grp"><h3>全局</h3>
     <label>气体类型（fluids/make_gas）</label>
     <select id="gType"><option>IdealGas</option><option>WaterVapor</option></select>
     <label>缺省总温 T0_default (K)</label><input id="gT0" type="number" value="600">
     <div style="margin-top:10px;font-size:11px;color:var(--dim)">
       gas 数值不进 JSON——选身份不传数值（2026-09-26 口径）</div></div>`;
    const g = S.gas ??= {type:'IdealGas', T0_default:600};
    $('gType').value = g.type; $('gT0').value = g.T0_default;
    $('gType').onchange = e=>{ g.type=e.target.value; };
    $('gT0').onchange  = e=>{ g.T0_default=+e.target.value||600; };
    return;
  }
  if (S.sel.kind==='node'){
    const n = S.nodes.find(n=>n.id===S.sel.id);
    const deg = S.comps.flatMap(c=>c.ports.map((p,j)=>p.node===n.id?`c${c.id}:${j}`:null))
               .filter(Boolean);
    el.innerHTML = `<div class="kv"><span>内部节点</span><b>n${n.id}</b></div>
      <div class="kv"><span>挂接口</span><b>${deg.length?deg.join(' '):'—'}</b></div>
      <div style="font-size:11px;color:var(--dim);margin-top:6px">
        p0/T0 均为未知量，由方程解出；删除节点会解绑其全部端口</div>`;
    return;
  }
  const c = S.comps.find(c=>c.id===S.sel.id); if (!c){ select(null); return; }
  const m = SCHEMA[c.type];
  let h = `<div class="kv"><span>元件</span><b>${m.name}</b></div>
           <div class="kv"><span>comp id</span><b>c${c.id}</b></div>
           <label>类型</label><select id="pType">`;
  for (const [id, mm] of Object.entries(SCHEMA))
    h += `<option value="${id}" ${+id===c.type?'selected':''}
           ${mm.impl?'':'disabled'}>${mm.name}${mm.impl?'':'（未实现）'}</option>`;
  h += `</select>`;
  c.ports.forEach((p,j)=>{
    h += `<label>口 ${j} 面积 A (m²)</label>
          <input class="pa" data-j="${j}" type="number" step="any" value="${p.area}">
          <label>口 ${j} 绑定节点</label>
          <select class="pn" data-j="${j}"><option value="">— 未绑定 —</option>`;
    for (const n of S.nodes)
      h += `<option value="${n.id}" ${p.node===n.id?'selected':''}>n${n.id}</option>`;
    h += `</select>`;
  });
  m.params.forEach((pr,i)=>{
    h += `<label>${pr[0]} ${pr[1]!=='–'?`(${pr[1]})`:''}</label>
          <input class="pp" data-i="${i}" type="number" step="any"
                 value="${c.params[i] ?? ''}" placeholder="可选">`;
  });
  if (m.model_path)
    h += `<label>模型文件 model_path（相对本 JSON）</label>
          <input id="pPath" type="text" value="${c.model_path||''}"
                 placeholder="surrogate_table.npz">`;
  el.innerHTML = h;
  $('pType').onchange = e=>{
    const t = +e.target.value; if (t===c.type) return;
    c.type = t;
    const mm = SCHEMA[t];
    c.params = mm.params.filter(pr=>pr[2]!=null).map(pr=>pr[2]);
    while (c.ports.length < mm.ports) c.ports.push({area:1e-4, node:null});
    c.ports.length = mm.ports;
    renderProps(); draw();
  };
  el.querySelectorAll('.pa').forEach(inp=>inp.onchange=e=>{
    c.ports[+e.target.dataset.j].area = +e.target.value||0; draw(); });
  el.querySelectorAll('.pn').forEach(sel=>sel.onchange=e=>{
    c.ports[+e.target.dataset.j].node = e.target.value===''?null:+e.target.value; draw(); });
  el.querySelectorAll('.pp').forEach(inp=>inp.onchange=e=>{
    c.params[+e.target.dataset.i] = +e.target.value; });
  if (m.model_path) $('pPath').onchange = e=>{ c.model_path = e.target.value.trim(); };
}

// ---------------- 导出 / 导入 ----------------
function buildJSON(){
  const warn = [];
  for (const c of S.comps){
    c.ports.forEach((p,j)=>{
      if (p.node==null) warn.push(`c${c.id} 口${j} 未绑定节点`);
      if (SCHEMA[c.type].ports>1 && !(p.area>0)) warn.push(`c${c.id} 口${j} 面积≤0`);
    });
  }
  if (!S.comps.some(c=>c.type===5))
    warn.push('无 PRESSURE_BOUNDARY——方程组无压力锚定，组装期将报错');
  const gas = S.gas ??= {type:'IdealGas', T0_default:600};
  return { json: {
      gas: {type:gas.type, T0_default:+gas.T0_default},
      nodes: S.nodes.map(n=>({id:n.id, _ui:{x:Math.round(n.x), y:Math.round(n.y)}})),
      comps: S.comps.map(c=>{
        const meta = SCHEMA[c.type];
        // 可选参数（缺省 null）留空时从尾部剥除——JSON 只带有效段
        const params = [...c.params.map(Number)];
        while (params.length && params[params.length-1] == null) params.pop();
        const o = {id:c.id, type:meta.etype,
          ports:c.ports.map(p=>({area:+p.area, node:p.node})), params};
        if (meta.model_path && c.model_path) o.model_path = c.model_path;
        o._ui = {x:Math.round(c.x), y:Math.round(c.y)};
        return o; })
    }, warn };
}
$('bExport').onclick = () => {
  if (!S.nodes.length && !S.comps.length){ toast('空网络', 1); return; }
  const {json, warn} = buildJSON();
  const blob = new Blob([JSON.stringify(json, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'netinf_ui.json'; a.click(); URL.revokeObjectURL(a.href);
  toast(warn.length ? '已导出（注意：'+warn.join('；')+'）' : '已导出 netinf_ui.json', !!warn.length);
};

function loadJSON(data){
  S.nodes = (data.nodes??[]).map(n=>({id:n.id, x:n._ui?.x??100, y:n._ui?.y??100}));
  const byEtype = {}; for (const [k,m] of Object.entries(SCHEMA))
    byEtype[m.etype] = +k;
  const unknown = [];
  S.comps = (data.comps??[]).map(c=>({
    id:c.id, type:(()=>{ const t=byEtype[c.type];
                        if (t==null) unknown.push(c.type); return t??0; })(),
    x:c._ui?.x??100, y:c._ui?.y??100,
    ports:(c.ports??[]).map(p=>({area:+p.area, node:p.node})),
    params:(c.params??[]).map(Number), model_path:c.model_path||''}));
  if (unknown.length) toast('未知元件类型（按 ORIFICE 占位）: '+unknown.join(','), 1);
  // 无坐标时自动布局：节点圆周、元件右侧两列
  if ((data.nodes??[]).some(n=>!n._ui) || (data.comps??[]).some(c=>!c._ui)){
    const cx=380, cy=240, R=170;
    S.nodes.forEach((n,i)=>{ if(!data.nodes[i]._ui){
      const a = i/Math.max(S.nodes.length,1)*2*Math.PI - Math.PI/2;
      n.x = cx+R*Math.cos(a); n.y = cy+R*Math.sin(a); }});
    S.comps.forEach((c,i)=>{ if(!data.comps[i]._ui){ c.x=660+(i%2)*160; c.y=40+Math.floor(i/2)*110; }});
  }
  S.nid = Math.max(-1, ...S.nodes.map(n=>n.id))+1;
  S.cid = Math.max(-1, ...S.comps.map(c=>c.id))+1;
  S.gas = {type:data.gas?.type??'IdealGas', T0_default:data.gas?.T0_default??600};
  select(null); draw();
  toast(`已加载：${S.nodes.length} 节点 / ${S.comps.length} 元件`);
}
$('bImport').onclick = ()=>$('fImport').click();
$('fImport').onchange = e=>{
  const f = e.target.files[0]; if(!f) return;
  f.text().then(t=>{ try{ loadJSON(JSON.parse(t)); }
                     catch(err){ toast('JSON 解析失败: '+err.message, 1); } });
  e.target.value='';
};
$('bDemo').onclick = ()=>{
  const k = $('demoSel').value;
  if (k && DEMOS[k]) loadJSON(JSON.parse(DEMOS[k]));
};

// ---------------- 结果查看器 ----------------
$('bParse').onclick = ()=>{
  const t = $('res-text').value;
  if (!t.trim()){ toast('先粘贴结果文本', 1); return; }
  const meta = t.match(/收敛:\s*(\S+)\s+max\|F\|=([\d.eE+-]+)/);
  const nodes = [...t.matchAll(/n(\d+)\s+p0=([\d.eE+-]+)\s+T0=([\d.eE+-]+)/g)]
    .map(m=>({id:+m[1], p0:+m[2], T0:+m[3]}));
  const ports = [...t.matchAll(
    /c(\d+)口(\d+)\(n(\d+)\)\s+A=([\d.eE+-]+)\s+m=([+-][\d.eE+-]+)\s+ps=([\d.eE+-]+)\s+Ts=([\d.eE+-]+)\s+rho=([\d.eE+-]+)\s+v=([\d.eE+-]+)\s+Ma=([\d.eE+-]+)\s+(choked|sub)/g
  )].map(m=>({c:+m[1], j:+m[2], n:+m[3], A:+m[4], m:+m[5], ps:+m[6],
              Ts:+m[7], rho:+m[8], v:+m[9], Ma:+m[10], ch:m[11]==='choked'}));
  const cont = [...t.matchAll(/n(\d+)\s+\((\d+)口\):\s*([+-][\d.eE+-]+)/g)]
    .map(m=>({id:+m[1], k:+m[2], s:+m[3]}));
  if (!nodes.length && !ports.length){ toast('未解析到数据——确认是 write_result 格式',1); return; }
  const maxAbs = Math.max(1e-12, ...ports.map(p=>Math.abs(p.m)));
  let h = '';
  if (meta) h += `<div class="kv"><span>收敛</span><b style="color:${meta[1]==='True'?'#2ecc71':'#e74c3c'}">${meta[1]}</b>
    <span>max|F|</span><b>${meta[2]}</b></div>`;
  if (nodes.length){
    h += `<table><tr><th>节点</th><th>p0 (Pa)</th><th>T0 (K)</th></tr>` +
      nodes.map(n=>`<tr><td>n${n.id}</td><td>${n.p0.toFixed(1)}</td><td>${n.T0.toFixed(2)}</td></tr>`).join('') + `</table>`;
  }
  if (ports.length){
    h += `<table><tr><th>口</th><th>节点</th><th>A (m²)</th><th>ṁ (kg/s)</th><th>流量分布</th>
          <th>ps (Pa)</th><th>Ts (K)</th><th>ρ</th><th>v (m/s)</th><th>Ma</th></tr>` +
      ports.map(p=>{
        const w = Math.abs(p.m)/maxAbs*90, neg = p.m<0;
        return `<tr${p.ch?' class="choked"':''}><td>c${p.c}口${p.j}</td><td>n${p.n}</td>
          <td>${p.A.toExponential(1)}</td><td>${p.m.toPrecision(4)}</td>
          <td><div class="bar-wrap"><div class="bar${neg?' neg':''}" style="width:${w.toFixed(0)}px"></div>
              <span style="font-size:10px;margin-left:4px">${p.ch?'壅塞':''}</span></div></td>
          <td>${p.ps.toFixed(0)}</td><td>${p.Ts.toFixed(1)}</td><td>${p.rho.toFixed(3)}</td>
          <td>${p.v.toFixed(1)}</td><td>${p.Ma.toFixed(4)}</td></tr>`; }).join('') + `</table>`;
  }
  if (cont.length){
    h += `<table><tr><th>节点连续性</th><th>口数</th><th>Σṁ（应≈0）</th></tr>` +
      cont.map(c=>`<tr><td>n${c.id}</td><td>${c.k}</td>
        <td style="color:${Math.abs(c.s)>1e-6?'#e67e22':'#2ecc71'}">${c.s.toExponential(2)}</td></tr>`).join('') + `</table>`;
  }
  $('res-out').innerHTML = h;
};

// ---------------- 启动 ----------------
fit(); setMode('sel');
if (Object.keys(DEMOS).length){ $('demoSel').value = Object.keys(DEMOS)[0]; }
$('hint').textContent = '选择模式：点击选中/拖拽；点元件端口小圆开始连线；右侧面板编辑属性';
</script>
</body>
</html>
"""
