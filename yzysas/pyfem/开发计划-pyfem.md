# 开发计划: pyfem — SAS×结构热耦合求解器（scikit-fem 路线）v3-定稿

> 状态：Phase 0+1 已完成（2026-09-29），验收门通过。Phase 2 起待实施。
> 本文档是会话计划的导出副本（权威版本随会话维护）。

## TL;DR
在 yzysas 下新建独立包 `pyfem/`（不塞进 pysas）：scikit-fem 轴对称热传导+热弹性 FE 求解器 + WALL_FILM 换热元件（外部注册进 pysas 工厂，零侵入）+ 双向迭代/整体牛顿两种耦合。对 pysas 主干仅 2 处最小改动（ElemType 加一个枚举值、damped_newton 线性求解按雅可比稀疏/稠密自动分发且 API 不变）。先 FE 独立求解验收（已完成），之后 partitioned 与 monolithic 并行推进。彻底消灭 31project 式"改geo→重划网→起4个进程→重写inp→重启ccx"开销（其 ccx 求解本身秒级，慢在全链路进程/文件）。

## 技术选型（已验证）
- **scikit-fem 12.0.2**：纯 Python（numpy+scipy）、BSD、活跃维护。
  ex17=Robin 热传导、ex11=线弹性、ex39=theta 瞬态（未来范式）；组装产物
  是 scipy 稀疏矩阵 → 整体牛顿雅可比可直接拼块；meshio 可读 gmsh .msh。
- 排除 pyfdm（无此成熟包、FDM 不适合盘类复杂边界）、FEniCS（Windows 重）、SfePy（备胎）。

## 关键背景
- 31project 真实瓶颈不在 ccx（1.6万 CAX3 秒级），在全链路进程/文件/重划网。
- pysas 扩展点：register_model 公开装饰器（外部包零侵入注册）、
  heat_input() 发热接口、CompType.COUPLED=3 预留。
- pysas damped_newton 线性求解硬编码 np.linalg.solve（稠密）——
  monolithic 万级 DOF 必须稀疏 → 改法：按雅可比稀疏/稠密自动分发，API 不变。

## 架构

### partitioned（双向迭代）
```
壁温 T_w → pysas 网络（WALL_FILM 报 q=h·A·(T_w−T_gas)）→ p/T/ṁ
  → h 关联式 h_fn（初期常数）
  → pyfem 稳态热传导（Robin: h, T_gas）→ 新 T_w
  → Aitken Δ² → 收敛后热弹性（+离心）→ von Mises
```

### monolithic（整体牛顿）
```
x = [x_net(~10) | T_node(~10⁴)]
F = [F_net(x_net; q(T_node)) | R_FE(T_node; T_gas(x_net))]
J = [J_net(稠密差分)   C_qT(解析: −h_j A_j C_j)]
    [C_Tg(FE残差差分)  K_FE(skfem 解析稀疏)]
```
稀疏 spsolve（SuperLU）；Scaling 在 pyfem 侧包装（FE 温度列 T_ref、
FE 残差行 1/(h_ref A_ref T_ref)）；初值取 partitioned 解（2-4 步收敛）。

## 实施步骤

### Phase 0 环境+骨架 ✅ 完成
scikit-fem[all] 装入 D:\Python\Python312；包骨架建立。

### Phase 1 FE 独立求解 ✅ 完成（验收门通过）
- fe/materials.py：TC11 表迁移（k/E/nu/alpha/rho 线性插值+端点外推）
- fe/axisym_heat.py：Robin 边界稳态热传导，k(T) Picard；
  wall_temps（∫Tr ds/∫r ds）/film_heats（2π∫h(T_gas−T)r ds）耦合交换量
- fe/axisym_thermoelastic.py：εθ=u_r/r、D(T) Picard、初应变 αΔT(T_ref=293.15)、
  离心 ρω²r、P1 常应变恢复→节点平均
- 验证：V-FE1 圆筒 Robin 热流 1.4e-4/温度 7e-7；V-FE2 均匀温升 σ≡0 精确、
  位移 5e-15；V-FE3 旋转盘 σr 4.2e-3/σθ 2.4e-3；网格收敛阶 2.00
- demo_fe.py 全链路（k(T) 9 步 + D(T) 2 步，能量闭合，4 云图）

### Phase 2 WALL_FILM 元件（待实施）
- pyfem/netelem/filmwall.py：单口，params=[h, A, T_w_default]；
  heat_input()=h·A·(T_w−T_gas)；T_w 参数默认或耦合器 ctx 注入
- pysas 改动①：topology.py ElemType 加 WALL_FILM=11（一行）
- test_netelm.py 单元测试 + pysas 旧用例无回归

### Phase 3A/3B 耦合（并行）
- 3A coupling/partitioned.py：Aitken Δ²，收敛后热弹性
- 3B coupling/monolithic.py + scaling_global.py；pysas 改动②
  （damped_newton 稀疏自动分发）
- solve_coupled(netinf, fe_config, mode=...) 统一 API
- 验收门A：能量闭合 |Σq_net−Q_FE|/Q<1e-6；门B：双模式一致 rtol<1e-6

### Phase 5 收尾
demo_coupled + 更新 yzysas 开发日志/计划（M7 FE 耦合条目）

## pysas 主干改动（仅 2 处）
1. topology.py：ElemType.WALL_FILM = 11
2. newton.py：damped_newton 线性求解按雅可比稀疏/稠密自动分发
   （spsolve / np.linalg.solve），API 不变，纯网络路径零变化

## 关键实现教训（Phase 1 实测）
- skfem Functional 签名是 form(w)，字段经 w['T'] 取（不是 form(v,w)）
- CellBasis 无 intorder 属性，构造 Basis 时的 quadrature 要自存
- 应力恢复 dN 链法则：invA.T @ B（写 invA @ B 会让 V-FE2/3 全错——
  位移解是对的、应力全错，这种"静默错误"只有解析解能抓住）
- 均匀温升验证约束：z=0 面 u_z=0（与解析位移一致）；单点约束引入
  点力奇异假应力
- V-FE3 旋转盘：薄盘网格 nz=2 + nr=80，检验域避开孔边应力集中区
  （r>10a）与外缘（r<0.95b）

## 验证命令（yzysas 目录）
```
python pyfem\test_fe.py               # 3 解析解（3/3 通过）
python pyfem\test_fe_convergence.py   # 收敛阶（PASS，≈2.0）
python pyfem\demo_fe.py --plot        # demo + 云图到 pyfem\out\
```
