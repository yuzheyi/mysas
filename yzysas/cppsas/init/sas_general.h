// sas_general.h — SAS 求解设置（全局控制参数）
// 稳态/非稳态模式、时间推进参数、非线性迭代收敛控制、全局物性默认值。
#pragma once

namespace sas {

// ---------- 求解模式 ----------
enum class SolveMode {
    Steady    = 0, // 稳态：整个网络解一组非线性方程（节点质量平衡），收敛即结束
    Transient = 1, // 非稳态：时间推进，每个时间步内解一次非线性方程组
};

// ---------- 求解设置 ----------
struct General {
    SolveMode solveMode = SolveMode::Steady; // 稳态 / 非稳态

    // --- 时间推进（Transient 模式有效）---
    double dt     = 1.0e-4; // 时间步长 s
    int    nSteps = 1000;   // 总时间步数（结束时刻 t_end = nSteps * dt）

    // --- 非线性方程组迭代控制（Steady 为主迭代；Transient 为每步内迭代）---
    int    maxIterations = 50;    // 最大迭代次数
    double tolerance     = 1.0e-6; // 残差收敛容差（节点质量流量不平衡量）
    double relaxation    = 1.0;    // 亚松弛因子 (0,1]，变量更新: x += relaxation*dx

    // --- 全局物性默认值（组件可各自覆盖）---
    double gasConstant = 287.05; // 气体常数 R，J/(kg·K)，默认空气
    double gamma       = 1.4;    // 比热比 γ，默认空气
};

}  // namespace sas
