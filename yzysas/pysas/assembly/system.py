"""system — NetworkSystem：拓扑 → 方程组索引 → 残差 F(x)。

未知量排布（整个 pysas 统一约定，M3 起）:
  x = [p0 全部节点 N 个 | T0 未锚温节点 N_T 个 | ṁ 每口 M 个]
      N + N_T + M = n（锚温节点 T 消元：值进 ctx.boundary_T0）
方程排布:
  F = [节点连续性 N 个 | 节点能量平衡 N_T 个 | 元件方程块 Σn_ports = M 个]
      ← 方阵 ✓（每个 T 未知量配一条能量平衡方程）

符号约定（见 base.py）:
  ṁᵢ > 0 = 流体经端口 i 流入组件（= 流出节点）
  → 连续性 = 挂在该节点上所有口的 ṁ 直接求和

节点能量平衡（M3，等比热 cp 消去、upwind 由 ṁ 符号携带）:
  F_E(n) = Σ_{ṁ_p>0} ṁ_p·T_n            （流出节点带节点温度）
         − Σ_{ṁ_p<0} (−ṁ_p)·T_out(p)     （元件向节点注入带输运温度）
         + Σ_{发热元件接 n} q/cp          （接口报 q 不报 ΔT，ṁ 被约去）
  T_out(p)：内部元件 = 其上游（另一口所连节点）温度；源元件 = T_supply。
  + ε·(T_n − T̄_anchor) 正则：零流量节点温度退化的兜底。

行量纲登记（缩放层用，scaling.py 行缩放分档）:
  连续性/多数元件行 0=流量纲；PRESSURE_BOUNDARY 等压力行 1；
  节点能量平衡行 2=能量纲 W → self.row_units。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from pysas.datamodel import ElemType  # noqa: F401  (测试脚本经此处引用，保留)
from pysas.elements.base import ElementModel

if TYPE_CHECKING:
    from pysas.elements.base import SolveContext


class NetworkSystem:
    """持有 Network + 元件模型实例，提供 residual(x) 与索引管理。"""

    def __init__(self, network, models: dict[int, ElementModel]):
        self.net = network
        self.models = models  # comp_id → ElementModel 实例
        self._build_indices()

    # ---------- 索引 ----------
    def _build_indices(self):
        net = self.net
        # 全部节点 p0 → x[0..N-1]（按 node_id 升序）
        self.interior_ids = sorted(n.node_id for n in net.nodes)
        self.p_idx_of_node = {nid: i for i, nid in enumerate(self.interior_ids)}
        self.n_interior = len(self.interior_ids)  # = 全部节点数（命名沿用）

        # 全部节点 T0 → x[N..N+N_T-1]（按 node_id 升序）。锚温消元已
        # 退役（想法 22 完成态）：所有节点 T 都是未知量，由能量方程
        # 解出；源温度一律走 T_supply/port_T_out 输运语义。
        self.T_ids = list(self.interior_ids)
        self.T_idx_of_node = {nid: self.n_interior + i
                              for i, nid in enumerate(self.T_ids)}
        self.n_T = len(self.T_ids)

        # ---------- 端口流量编号：建 (comp_id, 口序) → x 下标 的查询表 ----------
        # 排布约定（与 residual ③ 元件块的填充顺序严格一致）:
        #   全网端口按「comp_id 升序、元件内按 port 顺序」展平成一条序列，
        #   依次坐进 x 的流量段（从下标 N+N_T 起，紧接压力段与温度段之后）。
        #   例（两管算例 N=3, N_T=1，流量段从 4 起）:
        #     x[4]=c0口(边界)  x[5]=管1进口  x[6]=管1出口
        #     x[7]=管2进口     x[8]=管2出口  x[9]=c3口(边界)
        # 为什么 sorted：保证同一网络无论 comps 列表顺序如何，x 排布唯一
        #   确定——残差块顺序、行量纲登记、雅可比列含义全都依赖这个确定性。
        # k 是发号游标：每给一个口发一个座位号就 +1；循环结束时 k = n，
        #   顺带算出流量区长度 n_m 与方程组总规模 n。
        self.m_idx_of_port: dict[tuple[int, int], int] = {}
        k = self.n_interior + self.n_T      # 流量段起始下标（跳过 p 段与 T 段）
        for comp in sorted(net.comps, key=lambda c: c.comp_id):
            for j in range(len(comp.ports)):        # 元件内按端口顺序
                self.m_idx_of_port[(comp.comp_id, j)] = k   # 该口 ṁ 坐 x[k]
                k += 1
        self.n_m = k - self.n_interior - self.n_T   # 流量未知量个数 = 发出的号数
        self.n = self.n_interior + self.n_T + self.n_m   # n = N + N_T + M（方阵规模）

        # 行量纲登记：0=流量 kg/s / 1=压力 Pa / 2=能量 W（节点能量行）
        # 排布与 residual ①②③一致：连续性 N + 能量 N_T + 元件块（comp_id 序）
        units = [0] * self.n_interior + [2] * self.n_T
        for comp in sorted(net.comps, key=lambda c: c.comp_id):
            units.extend(self.models[comp.comp_id].row_units)
        self.row_units = units  # len = n

        # 注入元件局部索引（压力/温度/流量下标；想法 22 后全部节点
        # 都有 T 下标，无 -1 分支）。另注入 _off_m（流量段偏移）：
        # 元件查静参数白板的座位号 = _m_idx[i] - _off_m（发号序与
        # 白板序同序，零映射）
        off_m = self.n_interior + self.n_T
        for comp in net.comps:
            model = self.models[comp.comp_id]
            p_ids = [self.p_idx_of_node[p.node_id] for p in comp.ports]
            m_ids = [self.m_idx_of_port[(comp.comp_id, j)]
                     for j in range(len(comp.ports))]
            t_ids = [self.T_idx_of_node[p.node_id] for p in comp.ports]
            model.set_indices(p_ids, m_ids, t_ids)
            model._off_m = off_m

        # 节点 → 挂在其上的口列表（comp_id, port_index）——两套节点方程用
        self.ports_on_node: dict[int, list[tuple[int, int]]] = {}
        for comp in net.comps:
            for j, port in enumerate(comp.ports):
                self.ports_on_node.setdefault(port.node_id, []).append(
                    (comp.comp_id, j))

        # 发热元件不做静态登记（旧挂点表方案已废弃：组装期不知流向，
        # 挂全部口会双倍计账 + 污染上游节点）——改为残差里动态处理：
        # 发热温升并入注入项 T_out（见 residual ②），方向由解出的 ṁ
        # 符号即时决定，q 只计一次且只落在真正的下游节点。

        # 能量正则参考温度（锚温退役后）：全部单口源元件 T_supply 均值
        # （作正则参考足够；无源网络 = None 等温不动）。
        t_anchor_pool = [m.T_supply(ctx=None) for m in self.models.values()
                         if len(m.comp.ports) == 1]
        self.T_bar_anchor = (float(np.mean(t_anchor_pool))
                             if t_anchor_pool else None)

        # ---------- 静参数白板：口级参数表（向量化 prime 用，2026-09-25）----------
        # 发号序红利：x 的流量段本身就是按口序展平的连续切片 → ṁ 免 gather；
        # p0/T0 一次 fancy gather；A 纯常数。
        # 静参数是元件私有量（不跨元件共享）——白板仅免除每元件重复的
        # 60 轮二分，生命周期 = 单次残差评估（纯函数保证），非全局重建。
        port_area, port_p_idx, port_t_idx = [], [], []
        for comp in sorted(net.comps, key=lambda c: c.comp_id):
            for port in comp.ports:
                port_area.append(port.area)
                port_p_idx.append(self.p_idx_of_node[port.node_id])
                port_t_idx.append(self.T_idx_of_node[port.node_id])
        self._port_area = np.array(port_area)          # 每口 A（几何常数）
        self._port_p_idx = np.array(port_p_idx, dtype=int)   # 每口→p0 下标，在x求解向量中的位置（gather 用）
        self._port_t_idx = np.array(port_t_idx, dtype=int)   # 每口→T0 下标，在x求解向量中的位置（gather 用）
        self._port_dyn = self._port_area > 0.0       # 有效口掩码（边界口 A=0）

        # 能量平衡 ε 正则系数 kg/s：零流量节点温度退化兜底
        # （物理：无流动无输运，T 无约束 → J 奇异；正则拉回锚温均值，
        #   能量误差 ~ε·cp·ΔT ~ 1e-8·1000·100 ≈ 1e-3 W 量级可忽略）
        self.EPS_T_REG = 1.0e-8

        # 适定性断言：至少 1 个压力锚定元件——绝对压力水平必须有元件规定
        # （纯压差/全流量元件的网络 → J 零空间：只感知压差，水平浮动）。
        # 锚定能力由元件自报值推导（anchor_P_values() 非空即锚定，见 base.py），
        # 新元件（TANK 等）报值即接入，此处零改动。
        n_anchor = sum(1 for m in self.models.values() if m.anchor_P_values())
        if n_anchor == 0:
            raise ValueError(
                "网络无压力锚定元件（无元件报 anchor_P_values）：绝对压力"
                "水平无锚定，方程组奇异（纯压差/全流量网络）。"
                "至少挂 1 个压力锚定元件（如 PRESSURE_BOUNDARY）")

    # ---------- 静参数白板 prime ----------
    def _prime_static(self, x: np.ndarray, ctx) -> None:
        """每次 residual(x) 开头：全部有效口一次向量化反算静参数。

        写入 ctx._static_table（身份键 x）；元件 _static_state 查表零成本。
        残差路径纪律：p0/T0 入口 clamp（想法 19 纪律一，线搜索试探点
        可能踩负压）；区域外回退静=总（滞止）。①②段不消费静参数，
        但 prime 放函数开头统一入口（将来能量段若需也可查）。
        """
        from dataclasses import dataclass
        gas = ctx.gas

        d = self._port_dyn                     # 有效口掩码（压缩存储）
        p0 = np.maximum(x[self._port_p_idx[d]], 1.0)
        t0 = np.maximum(x[self._port_t_idx[d]], 10.0)
        md = x[self.n_interior + self.n_T:][d]  # 流量段切片免 gather（abs 归 gas）
        with np.errstate(divide="ignore", invalid="ignore"):
            q = gas.q_of_flow(md, p0, t0, self._port_area[d])
        ma, choked = gas.mach_from_q_arr(q)
        # 等熵闭式归 gas（想法 23）：assembly 只负责 gather/clamp/存表，
        # 换气体模型（变比热/真实气体）时此函数零改动
        T, p, rho, v = gas.statics_from_mach(ma, p0, t0)

        @dataclass
        class _Table:
            x: np.ndarray                     # 身份键：本表只为这个 x 而建
            ma: np.ndarray; p: np.ndarray; T: np.ndarray
            rho: np.ndarray; v: np.ndarray; choked: np.ndarray
            rows: np.ndarray                  # 有效口在全部口序中的行号
            def entry(self, seat: int):
                """座位号（发号序）→ StaticState；非有效口回退现算。"""
                from pysas.fluids import StaticState
                hit = np.searchsorted(self.rows, seat)
                if hit < len(self.rows) and self.rows[hit] == seat:
                    k = hit
                    return StaticState(float(self.ma[k]), float(self.p[k]),
                                       float(self.T[k]), float(self.rho[k]),
                                       float(self.v[k]), bool(self.choked[k]))
                return None                    # 调用方（base）回退标量现算

        ctx._static_table = _Table(x, ma, p, T, rho, v, choked,
                                   np.flatnonzero(d))

    # ---------- 残差 ----------
    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        """全局残差 F(x)：节点连续性 + 节点能量平衡 + 元件方程块。"""
        self._prime_static(x, ctx)   # 静参数白板：有效口一次向量化反算
        self._init_guard_refs(ctx)   # 软壅塞参考量（惰性一次）
        F = np.zeros(self.n)

        # ① 节点连续性：Σᵢ ṁᵢ = 0（ṁ 流入组件为正 → 对节点即流出为正）
        for nid in self.interior_ids:
            F[self.p_idx_of_node[nid]] = sum(
                x[self.m_idx_of_port[cid_j]] for cid_j in self.ports_on_node[nid])

        # ② 节点能量平衡（未锚温节点；等比热 cp 已约去，单位 kg·K/s）:
        #    流出项 ṁ_p·T_n − 注入项 (−ṁ_p)·T_out(p) + ε 正则
        #    T_out 由元件自报（port_T_out，见 base.py）——两口件走默认
        #    （另一口温度 + heat_input 温升），多口混合件覆盖；assembly
        #    对元件温度的全部知识就是这一个问句，方向由解出的 ṁ 符号
        #    即时决定（出料口才被问到，q 只计一次、只落在真正下游节点）
        for nid in self.T_ids:
            row = self.T_idx_of_node[nid]
            T_n = x[row]
            acc = 0.0
            for cid, j in self.ports_on_node[nid]:
                mp = x[self.m_idx_of_port[(cid, j)]]
                if mp > 0.0:            # 流出节点：带走节点温度
                    acc += mp * T_n
                elif mp < 0.0:          # 元件注入：问元件出流温度
                    model = self.models[cid]
                    m_flow = -mp        # 注入流量的绝对值
                    if len(model.comp.ports) == 1:
                        # 源/边界元件：无"另一口"概念，交 T_supply
                        T_out = model.T_supply(ctx)
                    else:
                        T_out = model.port_T_out(x, ctx, j)
                    acc -= m_flow * T_out
            # ε 正则：零流量节点拉回锚温均值（无锚温=等温网络不动它）
            if self.T_bar_anchor is not None:
                acc += self.EPS_T_REG * (T_n - self.T_bar_anchor)
            F[row] = acc

        # ③ 元件方程块（每口 1 个，共 M 个）
        off = self.n_interior + self.n_T
        for comp in sorted(self.net.comps, key=lambda c: c.comp_id):
            model = self.models[comp.comp_id]
            block = model.residual(x, ctx)
            self._soft_choke_guard(block, comp, model, x, ctx)
            F[off:off + len(block)] = block
            off += len(block)
        
        
        # print(f"residual: max|F|={np.abs(F).max():.2e}")
        return F

    def _cp(self, ctx) -> float:
        """定压比热（能量方程 q/cp 项；物性归 ctx.gas，与缩放层能量行
        参考量同源——同一实例保证数值一致）。"""
        return ctx.gas.cp()

    # ---------- 软壅塞陡坡（2026-10-01 用户方案，想法 33 修 2 定稿） ----------
    #: 陡坡强度（无量纲 κ = K·m_ref/p_ref；K 挂缩放层量纲推导，见
    #: solver/scaling.py——条件数冲击恰为 κ，跨网络尺度可移植）。
    #: 亚容量区项精确为 +0.0（正常网络解逐位不变）；超容量解钉在
    #: cap + Δp/K（K→∞ 收敛到"壅塞=压力 upwind"，想法 27 的正则化）。
    #: κ=1e3（三路审查窗口 1e3~1e5 的下端）：fuzz 多尺度网络实测
    #: κ=1e4 时陡坡高度 >> 网络压力尺度、单步刚性行程过大 iters=0
    #: 冻结（A0004）；1e3 下收敛且钳位偏差 ~1%（worst=1.024）。
    SOFT_CHOKE_KAPPA = 1.0e3

    def _soft_choke_guard(self, block, comp, model, x, ctx):
        """非锚定元件压力行的软壅塞兜底（row_units==1 才命中——框架
        不认识元件名，未来零压差元件自动免疫）。

        形式: block[i] -= K·sign(m_a)·max(0, |m_a| − cap_up)

        三条设计纪律（头脑风暴三路审查定稿）:
        ① 配对规则——压力行 p_a − p_b 配 **+号口 a**（heater f2=p2−p1
           配口1 会反向无解；junction 侥幸盲配也对但不能当规则）。
           现有元件压力行恰好都是"本口 − 参考"形（i 行 = i 口），框架
           按 i=i 配并依赖该约定；未来元件若写反式需覆盖本方法豁免。
        ② upwind cap——cap 取上风侧滞止态：m_a>0 时上游=本口节点；
           m_a<0 时上游=全部进料口节点总压最大者（与 exit_state 的
           p0_up=max(进料口)（想法 31）同源）。反向用本口会把容量
           定在低压侧，错压比倍数且破坏 K→∞ 极限。
        ③ 豁免锚定元件（anchor_P_values 非空：PB/booster 的压力行是
           规定值不是关系式，加项会移动锚点）。

        多口件豁免（2026-10-01 实证，A0257）：junction 的零压差行组
        是"约束多腔等压"的结构，逐口陡坡与它互锁（f2/f3 各自把腔压
        钉在一起，口超容需要腔压抬升——两行打架无解）。多口混合件
        的容量约束属于腔整体（Σ 进料口容量），逐口方程侧兜底语义
        不成立——只做报表侧超容量警告（port_states，后续项）。
        故 n_ports>2 的元件跳过方程侧兜底。

        cap 从当前 x 现算（choke_capacity 契约保证无状态闭式）；
        p0/T0 进 cap 前 clamp 防线搜索试探点负压 NaN（同
        _prime_static 纪律）。挂在 residual 热路径的是**兜底闭式**
        （base 默认 = 等熵 Cd=1 + A_min 单行），不是 pipe 那种
        50 轮 Fanno 定点——后者住元件特性内部，不经此路。
        """
        for i, cap, m_a in self._comp_overloads(model, comp, x, ctx):
            K = self.SOFT_CHOKE_KAPPA * self._p_ref_for_guard / max(
                self._m_ref_for_guard, 1.0e-12)
            block[i] -= K * (m_a - (1.0 if m_a > 0 else -1.0) * cap)

    def _comp_overloads(self, model, comp, x, ctx):
        """软壅塞守卫的纯判定（生成器）——yield (行号, cap, ṁ)。

        方程侧（guard 减陡坡项）与告警侧（solve 尾部终态检查）共用
        同一份判定（含全部豁免规则），两者永远同条件——告警不会在
        方程侧未激活的点上误报，方程侧也不会有告警看不见的激活。
        """
        if model.anchor_P_values():
            return
        if len(comp.ports) > 2:
            return      # 多口混合件：报表侧警告领地（A0257 实证）
        units = model.row_units
        # 参考量初始化在 residual 开头（惰性一次，2026-10-02 用户裁决
        # 保留那处、删此处冗余）：本生成器不消费 _p/_m_ref_for_guard，
        # 消费者是 _soft_choke_guard 的 K——它只在 residual ③ 段被调，
        # 开头必已初始化；solve 尾部告警路径也必经 residual 后到达。
        for i, u in enumerate(units):
            if u != 1 or i >= len(comp.ports):
                continue
            m_a = x[model._m_idx[i]]
            if abs(m_a) <= 0.0:
                continue
            # upwind 上游侧滞止态
            if m_a > 0.0:
                k_up = i
            else:
                ins = [k for k in range(len(comp.ports))
                       if x[model._m_idx[k]] > 0.0]
                if not ins:
                    continue        # 无进料（中间态）：不设闸
                k_up = max(ins, key=lambda k: model._total_p(x, ctx, k))
            p0_up = max(model._total_p(x, ctx, k_up), 1.0)
            t0_up = max(model._total_t(x, ctx, k_up), 10.0)
            cap = model.choke_capacity(p0_up, t0_up, ctx, i)
            if cap <= 0.0 or abs(m_a) <= cap:
                continue           # 亚容量：精确零扰动（+0.0）
            yield i, cap, m_a

    def _init_guard_refs(self, ctx):
        """兜底用的参考量（惰性缓存一次；与 scaling.make_scaling 同源
        口径：p_ref=锚定压力上限，m_ref=全网最大口壅塞容量）。

        温度口径（2026-10-02 用户裁决）：T0_default → 网络源温度上限
        maxT（单口源元件 T_supply 池的 max，无源回落 T0_default；与
        T_bar_anchor/default_guess 的 mean_T 同池取 max）。容量 ∝
        p0/√T0：maxT 下 m_ref 最保守（偏小）→ K 偏大——junction 网
        （源温 500/800）实测 K ↑~15%（κ_eff 1.15e3），远离 1e4
        冻结阈值；供温全等于 T0_default 的网络 K 逐位不变。

        ⚠ 已知漏洞（待定问题 #14 记档，先注释后查）：参考量只进 K
        （陡坡刚度定标），**cap 本身永远从当前节点滞止态现算**
        （_comp_overloads 里 _total_p/_total_t）——所以升压/温升
        元件（燃烧室、极端增压泵）错的是 K 的标度，不是容量值：
        局部 p0/T0 超过此处全局上限时 κ_eff 偏离设计值，影响收敛
        行为/条件数，不影响解的正确性（终态告警也会指路）。
        """
        if getattr(self, "_p_ref_for_guard", None) is None:
            anchors = {}
            for model in self.models.values():
                anchors.update(model.anchor_P_values())
            p0s = [v for v in anchors.values() if v > 0.0]
            p_ref = max(p0s) if p0s else 1.0e5
            supplies = [m.T_supply(ctx) for m in self.models.values()
                        if len(m.comp.ports) == 1]
            t_ref = max(supplies) if supplies else ctx.T0_default
            m_ref = 0.0
            for model in self.models.values():
                for j, port in enumerate(model.comp.ports):
                    if port.area > 0.0:
                        m_ref = max(m_ref, model.choke_capacity(
                            max(p_ref, 1.0), max(t_ref, 10.0), ctx, j))
            self._p_ref_for_guard = p_ref
            self._m_ref_for_guard = max(m_ref, 1.0e-6)

    # ---------- 解后处理：端口静参数持久化 ----------
    def port_states(self, x: np.ndarray, ctx) -> list:
        """解向量 x → 每口一份 PortState（发号序 = 流量段排布序）。

        报表/后处理用：求解中白板生命周期只有单次残差评估，此方法
        解后重建一次（prime + 查表），把静参数六件套（ps/Ts/ρs/v/
        Ma/choked）连同总参数与 ṁ 持久化进 datamodel.PortState。
        A=0 边界口无动通量：静=总（滞止）、v=Ma=0、ρ=ρ0。

        出料口出口状态调度（2026-09-29 归位裁决：调度归系统、物理归
        元件、数学归 fluids）——对**全部出料口**（ṁ<0）统一契约调用
        model.exit_state（2026-09-29c 低速统一实验定案）：
          ① 元件覆盖（pipe：Fanno 记账/声速闭合——精确）
          ② 基类默认（两口件：守恒律重建——见 base.exit_state）
          ③ None（多口/零流量/退化）→ 维持白板（腔假设兜底）
        调度归本方法，出口物理全住元件层（base 默认/覆盖）——
        本方法不含任何重构公式。

        为什么含低速（实验，tmp_diag3，B 算例孔板扫背压 280→220k）：
        白板对出料口是"下游腔滞止态等熵加速过 A_geo"——亚声速时
        它不满足出口压力匹配（射流匹配静压应=腔压），ps 系统性偏低
        （280k: −4.8%、260k: −11.1%、220k: −38.6%），且跨 q-clamp
        阈值时报表跳变（220k→215k 时 Ma 0.86→0.57 不连续）——低速
        白板不是无害近似，是结构性错口径。守恒律口径在低速极限
        精确退化为 ps=腔压 + v=ṁ/(ρA)（不可压极限的正确答案），
        q-clamp 门铃不再是调度开关，纯报表标志。
        """
        from pysas.datamodel import PortState
        self._prime_static(x, ctx)          # 重建白板（纯函数，无副作用风险）
        states = []
        for comp in sorted(self.net.comps, key=lambda c: c.comp_id):
            model = self.models[comp.comp_id]
            for j, port in enumerate(comp.ports):
                mdot = x[self.m_idx_of_port[(comp.comp_id, j)]]
                p0 = x[self.p_idx_of_node[port.node_id]]
                t0 = x[self.T_idx_of_node[port.node_id]]
                if port.area > 0.0:
                    st = model._static_state(x, ctx, j)   # 白板查表
                    if mdot < 0.0:
                        # 出料口统一调度（2026-09-29c 定案：含低速——
                        # 实验证明白板亚声速口径同样违反压力匹配，见
                        # docstring）。契约调用（元件覆盖 > 基类默认
                        # 守恒律重建）；None（多口/退化/零流量）
                        # 维持白板
                        st_exit = model.exit_state(x, ctx, j)
                        if st_exit is not None:
                            st = st_exit
                            # 报表总温同步为射流输运总温（2026-10-01
                            # 口径裁决配套）：静参数四件套已是射流自身
                            # 的，旁边配节点混合温度会让 (ps,Ts,Ma) 与
                            # T0 描述两股不同流体——审计/后处理用报表
                            # 值重构隐含总压将误判（fuzz A0199 教训）
                            t0 = model.port_T_out(x, ctx, j)
                    states.append(PortState(
                        mass_flow=mdot, static_pressure=st.p,
                        static_temperature=st.T, total_pressure=p0,
                        total_temperature=t0, density=st.rho,
                        velocity=st.v, mach_number=st.ma, choked=st.choked))
                else:   # 边界口：滞止态（无动通量）
                    states.append(PortState(
                        mass_flow=mdot, static_pressure=p0,
                        static_temperature=t0, total_pressure=p0,
                        total_temperature=t0,
                        density=ctx.gas.rho_from_pT(p0, t0)))
        return states
