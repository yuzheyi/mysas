"""flow — SURROGATE_FLOW 数据驱动代理元件（两口压差→流量特性）。

残差结构与 orifice 同构（开发日志·想法 8 / M7 落地 v1）:
  f1 = ṁ1 + ṁ2                          （连续性）
  f2 = ṁ_k − C_ref·p_hi/√T_hi · Φ(PR)    （特性，k = 高压侧口）

  PR = p_lo/p_hi ∈ (0,1]；C_ref = A_ref·√(γ/R)·(2/(γ+1))^((γ+1)/(2(γ−1)))
  —— 与 orifice 壅塞流量同源的物理标度：p/√T/A 依赖被解析析出，模型
  （表/NN）只携带一维无量纲特性 Φ(PR)，换工况零外推风险。

params = [面积比]（A_ref = 面积比 × 口0 面积，与 ORIFICE 面积比同约定——
同一 Φ 特性可按几何比例缩放到不同面积；训练基准面积 a_ref_train 记录
在模型 meta 里仅作溯源）；特性模型文件 = Comp.model_path（.npz 表 /
.onnx，加载与校验见 model_bank，训练管线见 tools/surrogate）。

壅塞容量（容量契约）：cap = C_ref·p0/√T0·Φ_max——与特性自同源
（Φ 平台 = 表左端值 / ONNX 自报最大）；反流翻转 hi/lo 与 orifice 同款，
T_hi 取真正上游（高压侧）总温。
"""
from __future__ import annotations

import numpy as np

# 脚本直跑自举（本模块无注册副作用，直跑安全——见文末 __main__）；
# 包内正常导入时此三行是空操作（pysas 已在 sys.path）
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

from pysas.elements.base import ElementModel
from pysas.elements.surrogate.model_bank import load_model


class SurrogateFlowModel(ElementModel):
    elem_type = 12  # ElemType.SURROGATE_FLOW

    def __init__(self, comp):
        super().__init__(comp)
        if not comp.model_path:
            raise ValueError(
                f"SURROGATE_FLOW (comp_id={comp.comp_id}) 缺 model_path——"
                f"特性模型文件由 Comp.model_path 携带（.npz/.onnx）")
        if len(comp.params) != 1:
            raise ValueError(
                f"SURROGATE_FLOW (comp_id={comp.comp_id}) params 应为 "
                f"[面积比]，实际 {comp.params}")
        (area_ratio,) = comp.params
        self.area_ref = area_ratio * comp.ports[0].area
        self.runtime = load_model(comp.model_path)
        self._gamma_warned = False

    # ---------- 物理标度（与 orifice 壅塞流量同源） ----------
    def _c_ref(self, ctx) -> float:
        g, R = ctx.gas.gamma, ctx.gas.R
        return self.area_ref * np.sqrt(g / R) \
            * (2.0 / (g + 1.0)) ** ((g + 1.0) / (2.0 * (g - 1.0)))

    def _warn_gas_mismatch(self, ctx):
        """训练/运行气体不一致提示（一次性；无量纲 Φ 只依赖 γ，R 已析出）。"""
        if self._gamma_warned:
            return
        g_tr = self.runtime.meta.get("gamma_train")
        if g_tr is not None and abs(ctx.gas.gamma - float(g_tr)) > 0.02:
            print(f"[surrogate] 提示：{self.comp.model_path} 训练 γ="
                  f"{float(g_tr):.3f} 与运行 γ={ctx.gas.gamma:.3f} 不一致"
                  f"（Φ 特性依赖 γ，结果仅供参考）")
        self._gamma_warned = True

    def mass_flow_hat(self, p_hi: float, p_lo: float, t_hi: float,
                      ctx) -> float:
        """代理特性流量 ṁ̂（恒正；方向由残差行的高压侧口 k 携带）。"""
        self._warn_gas_mismatch(ctx)
        p_hi = max(p_hi, 1.0)
        pr = min(max(p_lo / p_hi, 0.0), 1.0)
        phi = self.runtime.flow_coefficient(pr)
        return self._c_ref(ctx) * p_hi / np.sqrt(max(t_hi, 10.0)) * phi

    def choke_capacity(self, p0_up: float, t0_up: float, ctx, j: int) -> float:
        """容量 = C_ref·p0/√T0·Φ_max（与特性自同源：PR→0 平台）。"""
        return self._c_ref(ctx) * max(p0_up, 1.0) \
            / np.sqrt(max(t0_up, 10.0)) * self.runtime.phi_max

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        p1 = self._total_p(x, ctx, 0)
        p2 = self._total_p(x, ctx, 1)
        m1 = x[self._m_idx[0]]
        m2 = x[self._m_idx[1]]

        # 高压侧为上游（与 orifice 同款翻转；反流时 T_hi 取真正上游）
        if p1 >= p2:
            k, p_hi, p_lo, t_hi = 0, p1, p2, self._total_t(x, ctx, 0)
        else:
            k, p_hi, p_lo, t_hi = 1, p2, p1, self._total_t(x, ctx, 1)

        m_hat = self.mass_flow_hat(p_hi, p_lo, t_hi, ctx)
        return np.array([
            m1 + m2,                        # f1 连续性
            x[self._m_idx[k]] - m_hat,      # f2 特性：高压口流入 = +ṁ̂
        ])


# ---------------- 单文件自验证（python pysas/elements/surrogate/flow.py） ----------------
def _selftest():
    """独立手算闭式造表 → 元件级抽查（防自证：闭式在本函数重写，
    不经 tools/sample 与训练管线）。"""
    import os
    import tempfile

    from pysas.datamodel import Comp, ElemType, Port
    from pysas.elements.base import SolveContext
    from pysas.fluids import make_gas

    gam = make_gas().gamma          # 与算例同气体（缺省 IdealGas）
    crit = (2.0 / (gam + 1.0)) ** (gam / (gam - 1.0))
    choke = np.sqrt(gam) * (2.0 / (gam + 1.0)) ** (
        (gam + 1.0) / (2.0 * (gam - 1.0)))

    def phi_closed(pr, cd):         # 独立闭式（test_solver 手算同源不同路径）
        if pr <= crit:
            return cd
        return cd * np.sqrt(2.0 * gam / (gam - 1.0)) * pr ** (1.0 / gam) \
            * np.sqrt(1.0 - pr ** ((gam - 1.0) / gam)) / choke

    with tempfile.TemporaryDirectory() as td:
        t = np.linspace(0.0, 1.0, 65)
        pr = np.unique(np.concatenate([
            0.02 + 0.98 * (1.0 - (1.0 - t) ** 2), [crit, 1.0]]))
        phi = np.array([phi_closed(float(p), 0.8) for p in pr])
        tbl = os.path.join(td, "phi.npz")
        np.savez(tbl, format="pysas-surrogate-table", version=1,
                 pr_grid=pr, phi_grid=phi, pr_crit=crit,
                 gamma_train=gam, a_ref_train=1.0e-4, cd_train=0.8,
                 source="flow-selftest", note="")

        comp = Comp(comp_id=0, elem_type=ElemType.SURROGATE_FLOW,
                    ports=[Port(area=1.0e-4, node_id=0),
                           Port(area=1.0e-4, node_id=1)],
                    params=[1.0], model_path=tbl)
        model = SurrogateFlowModel(comp)
        model.set_indices([0, 1], [2, 3], [4, 5])
        ctx = SolveContext()
        ctx.gas = make_gas()

        rt = model.runtime
        assert abs(rt.flow_coefficient(0.02) - 0.8) < 1e-12, "壅塞平台"
        assert rt.flow_coefficient(1.0) <= 1e-12, "零压差零流量"
        vals = [rt.flow_coefficient(float(p)) for p in np.linspace(0.02, 1.0, 201)]
        assert np.all(np.diff(vals) <= 1e-12), "Φ 单调不增"
        err = max(abs(rt.flow_coefficient(float(p)) - phi_closed(float(p), 0.8))
                  for p in np.linspace(0.3, 0.95, 41))
        assert err < 5e-4, f"闭式对拍误差 {err:.2e}"

        c_ref = model._c_ref(ctx)
        m_hat = model.mass_flow_hat(3.0e5, 2.0e5, 600.0, ctx)
        m_ref = c_ref * 3.0e5 / np.sqrt(600.0) * phi_closed(2.0 / 3.0, 0.8)
        rel = abs(m_hat - m_ref) / m_ref
        assert rel < 5e-3, f"特性流量对拍 rel={rel:.2e}"
        cap = model.choke_capacity(3.0e5, 600.0, ctx, 0)
        cap_ref = c_ref * 3.0e5 / np.sqrt(600.0) * 0.8
        assert abs(cap - cap_ref) / cap_ref < 1e-12, "壅塞容量 = 平台值"
        # 残差在自洽点归零（x 按 set_indices 排布：x=[p1,p2 | m1,m2 | T1,T2]）
        x = np.array([3.0e5, 2.0e5, m_hat, -m_hat, 600.0, 600.0])
        F = model.residual(x, ctx)
        assert np.abs(F).max() < 1e-12, f"自洽点残差 {F}"
        print("flow.py 自验证通过：壅塞平台/单调性/闭式对拍"
              f"（max|ΔΦ|={err:.1e}）/容量契约/自洽残差")


if __name__ == "__main__":
    _selftest()
