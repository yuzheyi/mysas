# #19 双探针重启器 — 设计思路（动手前理清）

> 状态：设计稿（2026-10-02 夜）。依据：HOMOTOPY_PROBE.md 实验 +
> 三路文献调研（见文末路径）+ 现行代码事实。

## 1. 问题陈述

`solve()` 对随机网络语料 66% 算例 clean_fail（牛顿从 default 初值
出发不收敛）。双探针实验证明：

- **flatline 探针**（边界压平→零流量=精确解→暖启动跳回原值）救 16 例
- **backpressure 探针**（20 步回拧边界压力、步步暖启）救 13 例
- 并集 23/261 = **8.8%**，救活集合高度互补（共同 6 / A 独有 7 / B 独有 10）
- flatline 耗时 0.2~1.8 s/例，backpressure 稍高但秒级
- 重型同伦证伪：96% 首段断链（λ=0→0.05 解流形折叠，路径不存在）

## 2. 机制原理（为什么这两个探针有效）

### 2.1 flatline：构造"已知精确解的退化问题"

把全部 PRESSURE_BOUNDARY 的 p0_spec 压平到均值 p̄ 后，网络无压差：
零流量 + 全网均压 + 均温**就是该退化问题的精确解**（初始层1场=解）。
牛顿从精确解出发 iters=0 直接"收敛"，拿到退化解向量 x̄ 后，把边界
改回原值、以 x̄ 为暖启动初值重解原问题——赌的是 x̄ 落在原问题解的
吸引域内。`initial_guess(strategy="zero_flow")` 已实现层1场。

文献对应（Kundert 2003, *The Designer's Guide to SPICE and Spectre*）：

> "Source stepping starts by setting all source voltages and currents
> to zero, and slowly ramping them to their full value. The solution
> is recomputed each time the source level is changed, with all but
> the final solution discarded."

我们探针①是它的两步极限版。跨域先例（Jereminov et al. 2019,
gmin-stepping 移植到潮流计算）：

> "The solution to such a modified circuit is then trivial, namely
> zero, and the operating point of the original circuit is obtained
> by sequentially relaxing the connected homotopy conductances."

### 2.2 backpressure：source stepping 的多步版

SPICE 术语：**source stepping** —— 把源值从易解水平逐步 ramp 到
目标值，步步暖启。我们的 λ∈[0,1] 线性插值 PB 压力 = 电压源 ramp
的流体翻版。20 步足够覆盖大多数"路径存在但一步跳太远"的算例。

### 2.3 为什么不能无限细分步长（重型同伦证伪的原因）

96% 失败例在 λ=0→0.05 就断——说明解支 x*(λ) 在压平端点附近就
折叠/消失（解流形不连通），不是"步长太大跟丢"。此时细化步长无
意义（路径不存在），必须换入口（多起点），这正是"修路 vs 换入口"
的裁决（开发日志想法 34）。

文献支撑（Sielemann et al. 2011, Modelica homotopy）：

> "it is shown at hand of several examples how an inappropriate
> formulation might lead to ill-posed problems."

（λ=0 边界全平起点本身可能构造病态问题）；Thurston 1969（自然参数
延拓无法穿过极限点）——不引弧长机制就不可能穿过折叠。

### 2.4 探针互补的机理解释

flatline 直接跳回原值 = 赌 x̄ 落在原问题吸引域；backpressure 沿
λ 逐步走 = 路径存在时更稳但首段折叠必死。两者失败模式几乎不相交
（共同 6 / 独有 7+10），且救活解零人工根污染（探针收敛到的是原
问题真根，不是守卫兜底钉出来的假解）。

## 3. 架构设计

### 3.1 现状代码事实（不改的部分）

- `solve(system, x0, ctx)` → `SolveResult`（report.converged 判收敛）
- `initial_guess(system, ctx, strategy, warm)`：zero_flow/warm 已备
- PB 元件 `p0_spec` 是实例属性（`__init__` 从 comp.params 解包）——
  **运行时改 `model.p0_spec` 即改边界，不重建网络**（探针脚本实证）
- 投影牛顿盒（p≥min锚压）与软壅塞终态告警在 solve 内部，重启器在
  外层包住，语义不变

### 3.2 重启器层次（三层嵌套，全部在 solve 之上）

```
solve()                          ← 原样不动（L1 默认路径）
 └─ restart_wrap(system, ctx)    ← 新增薄层（L2）
     ├─ 尝试0：default solve（现状）
     尝试0 失败(clean_fail):
     ├─ 探针1 flatline：
     │    PB 压平 p̄ → solve(x0=zero_flow 初值)
     │    成功 → PB 复原 → solve(x0=x̄ warm)
     │    成功 → 返回（report 标注 restart="flatline"）
     ├─ 探针2 backpressure：
     │    for k in 1..20: PB ← p̄+λ(p0−p̄) → solve(x0=上步解 warm)
     │    成功 → 返回（restart="backpressure"）
     └─ 全败 → 返回尝试0 的原始结果（保持 solve 语义：失败照旧）
```

### 3.3 关键设计决策

**D1 重启器不改 solve() 本体**。理由：solve 语义 = "单次牛顿尝试"；
重启器 = 策略层，二者职责分离。报表层只看 SolveResult——加一个
`restart` 字段（None/"flatline"/"backpressure"）标注救活来源，
报表可按需消费（待定#19 悬置理由"与报表告警口径联动"的落实）。

**D2 边界修改走 model 层而非重建网络**。`model.p0_spec = new` 即时
生效（residual 读实例属性）；探针全程用同一 NetworkSystem 对象，
避免重复组装开销与状态污染。**探针结束后必须复原**（try/finally）。

**D3 flatline 压平目标 = 锚压均值 p̄**（与实验一致）。zero_flow 初值
的层1场也是锚压均值——两者天然配套：压平后层1场 = 精确解。

**D4 backpressure 步数 20**（与实验一致）。失败诊断字段 lost_at
保留（报表可统计断点分布）。

**D5 不做 gmin 类第三探针、不做 deflation**。文献建议的 PTC/
deflation 是"换求解器"级改动，与"轻量重启器"定位冲突（M2 已裁决：
证伪重型手段）。留待 nn 策略（想法 7）接通后对比。

**D6 时长控制**：单探针不设硬超时（flatline 实测秒级）；重启器
总预算由调用方决定——fuzz 批量场景可传 `restart=False` 关闭。

**D7 solve() 签名不动**。新增独立函数 `solve_with_restart()` 或
`solve()` 关键字参数 `restart: bool = False`（默认关，语义不变；
fuzz/报表层显式开启）。**倾向后者**：调用点零改动，opt-in。

### 3.4 与报表告警的联动（悬置理由的落实）

SolveResult 新增 `restart: str | None`（默认 None）。收敛解照常过
软壅塞告警；restart 字段进报表 context（报表层后续消费：救活例
单独统计口径）。**本次只加字段，不改报表**——报表侧三项排队
（多口件超容量/row_owner/junction 告警）另案处理。

## 4. 代码改动清单（预计 ~150 行）

| 文件 | 改动 |
|---|---|
| `pysas/solver/__init__.py` | ① `SolveResult` + `restart` 字段（default None）② `solve(..., restart=False)` 关键字参数 ③ restart 分支调 `restart_wrap`（新私有函数） |
| `pysas/datamodel/solver.py` | SolverSettings 加 `restart: bool`（与 mode 并列的开关；若用 solve kwarg 则免） |
| `pysas/test_solver.py` | 新增 RS 段 4 项：①restart=False 时行为逐位不变 ②flatline 救活例（A0018）③restart="backpressure" 标注正确 ④全败时语义=原 clean_fail |
| `fuzz/run_probe_regress.py`（新） | 23 例并集救活集固定回归——任何求解器改动后跑一遍，救活率不许劣化 |

**不动的文件**：elements/（PB 不改——运行时改 p0_spec 是既有机制）、
assembly/、scaling/、newton/discrete 内核、报表层。

## 5. 验证方案

1. **单元**：test_solver RS 段 4 项（如上）
2. **固定回归**：23 例并集集（A0018/A0020/A0042/...）+ 10 例从不
   活例（对照：不许把死例救活成人工根——查软壅塞告警为零）
3. **全量语料**：300 例 A 层重跑（restart=True），对照旧基准
   clean_fail 244→~221（省 23），并发软壅塞告警复核零污染
4. **性能**：300 例总时长对比（预期 +10~20%——失败例才付重启成本）

## 6. 文献缺口（需人工下载）

| 文献 | 用途 | 检索状态 |
|---|---|---|
| Coffey, Kelley & Keyes 2003, SIAM JSC | PTC 奠基文全文 | ❌ 未命中，需下载 |
| Allgower & Georg 1990 专著 | 延拓方法谱系 | ❌ 只有条目无原文 |
| Keller 1977 弧长延拓原始论文 | 折叠点理论 | ❌ 需下载 |
| Brown & Walker 1996 | deflation 引用 | ❌ 需下载 |
| Kulkarni & di Mare 2023, Aeronautical J. | SAS 求解器对比工作 | ❌ 需下载正文 |
| van de Noort & Ireland 2022 | SAS 网络列式 | ❌ DOI 待核实 |
| Giustolisi et al. 2008, JHE 134(10):1545 | WDS 初值鲁棒性 | ❌ 需下载 |
| 张婷婷 2012（已有本地全文） | 国内网络法谱系 | ✅ 已读 |
| Okhuegbe 2025（已有本地全文） | 两段式 ML+同伦结构对照 | ✅ 已读 |

## 7. 调研报告路径

- 多起点/全局化：`chat-session-resources/call_f0bc1df455534282bee568db` content.txt
- 流体网络：`call_75bbd93820a7457c88f8d418` content.txt
- 同伦失败/ML 热启动：`call_fca7bfa2804f46ecaac96655` content.txt

（正式文献综述文档随后单独写，本文件只留设计。）
