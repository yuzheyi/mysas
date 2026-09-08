// sas_solver.h — 非线性方程组求解器设置与结果（纯数据定义，无算法实现）
// 分层：元件层(流量函数) → 组装层(residual/jacobian) → 求解器层(本文件的结构)
// 算法：阻尼牛顿(内核) ⊂ 离散牛顿(J 用差分) ⊂ 同伦延拓(外层 λ 推进)
#pragma once

#include <vector>

namespace sas {

// ---------- 牛顿法设置 ----------
struct NewtonOptions {
    int    maxIter     = 50;     // 最大内迭代次数
    double tol         = 1e-6;   // 收敛判据：max|F(x)|（节点净流量，kg/s）
    double relaxInit   = 1.0;    // 初始松弛因子 α（残差不降则自动减半）
    double relaxMin    = 1e-3;   // α 下限，低于此判失败
};

// ---------- 牛顿法单次求解结果 ----------
struct NewtonReport {
    bool   converged     = false;
    int    iters         = 0;
    double finalResidual = 0.0;  // max|F|
    double finalRelax    = 1.0;  // 收敛时的 α
};

// ---------- 离散牛顿（差分雅可比）设置 ----------
struct DiscreteNewtonOptions {
    NewtonOptions newton;        // 继承内核设置
    double fdEpsRel  = 1e-7;     // 差分步长 ε_j = fdEpsRel * max(1,|x_j|)
    bool   useColoring = true;   // 距离-2 染色并行扰动（false=逐列，调试用）
};

// ---------- 同伦延拓设置 ----------
struct HomotopyOptions {
    DiscreteNewtonOptions inner; // 内层：离散牛顿
    double lambda0      = 0.0;   // λ 起点（0 = 线性问题，必有解）
    double lambda1      = 1.0;   // λ 终点（1 = 真实非线性系统）
    double dLambdaInit  = 0.1;   // 初始 λ 步长
    double dLambdaMin   = 1e-4;  // λ 步长下限（低于此触发"改变同伦"）
    int    maxSteps     = 200;   // λ 推进最大步数
};

// ---------- 同伦延拓求解结果 ----------
struct HomotopyReport {
    bool   converged      = false;
    int    nLambdaSteps   = 0;   // λ 实际推进步数
    int    nNewtonTotal   = 0;   // 内层牛顿迭代总次数
    double finalResidual  = 0.0;
    std::vector<double> lambdaPath;  // λ 轨迹（诊断收敛难易用）
};

// ---------- 求解模式汇总（挂在 General 里用）----------
struct SolverSettings {
    int mode = 0;  // 0 = 纯牛顿；1 = 离散牛顿；2 = 同伦延拓（离散牛顿内核）
    NewtonOptions          newton;
    DiscreteNewtonOptions  discrete;
    HomotopyOptions        homotopy;
};

}  // namespace sas
