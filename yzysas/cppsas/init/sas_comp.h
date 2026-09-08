// sas_comp.h — SAS 网络组件定义
// 组件按 CompType 分四类；本文件定义公共骨架（compId、进出口、几何）
// 与各类型自己的状态量。接口状态 PortState 与接口几何 Port 成对出现：
//     ports[i]        → 第 i 个进出口的几何/拓扑（不随时间变）
//     *.ports[i]      → 第 i 个进出口的流动状态（随时间变）
// 下标 i 一一对应，从 0 到 nPorts-1。
#pragma once

#include <array>
#include <vector>

namespace sas {

// ---------- 组件类型 ----------
enum class CompType {
    Steady  = 0, // 定常组件（孔板、管道等，只保留最新时间步）
    Volume  = 1, // 零维非定常元件（容腔效应，保留最近 N_HIST 步）
    OneD    = 2, // 一维元件（预留）
    Coupled = 3, // 耦合元件（预留）
};

// ---------- 接口流动状态（随时间变化）----------
struct PortState {
    double massFlow          = 0.0; // 质量流量 kg/s（符号约定：正 = 流入组件）
    double staticPressure    = 0.0; // 静压 Pa
    double staticTemperature = 0.0; // 静温 K
    double totalPressure     = 0.0; // 总压 Pa
    double totalTemperature  = 0.0; // 总温 K
    double machNumber        = 0.0; // 马赫数（派生量：由 P0/P 等熵关系导出）
};

// ---------- 接口几何 + 拓扑（常数）----------
struct Port {
    double area         = 0.0; // 流通面积 m²
    int connectedNodeId = -1;  // 连接到的节点 ID（拓扑，不随时间变）
};

// ---------- 定常组件状态（只保留最新一步）----------
struct SteadyState {
    std::vector<PortState> ports; // 各进出口流动状态
};

// ---------- 历史步数 ----------
inline constexpr int N_HIST = 4; // 非定常元件保留最近 4 个时间步

// ---------- 容腔状态（Volume 型组件每时间步一份）----------
struct VolumeState {
    double time                     = 0.0; // 时间戳 s（变步长推进必备）
    double controlMass              = 0.0; // 控制体流体质量 kg，m(t) = ∫Σṁ dt
    double averageStaticPressure    = 0.0; // 平均静压 Pa
    double averageStaticTemperature = 0.0; // 平均静温 K
    double averageTotalPressure     = 0.0; // 平均总压 Pa
    double averageTotalTemperature  = 0.0; // 平均总温 K
    double averageDensity           = 0.0; // 平均密度 kg/m³，ρ = m/V
};

// ---------- 非定常组件状态（最近 N_HIST 步）----------
// head 指向最新时间步；推进一步：head = (head + N_HIST - 1) % N_HIST，
// 新数据写入新的 head 位置，循环覆盖最老的一步，不搬移数据。
struct UnsteadyState {
    std::array<VolumeState, N_HIST>            volumeHist; // 容腔状态历史
    std::array<std::vector<PortState>, N_HIST> portsHist;  // 各进出口流动状态历史
    int head = 0; // 最新时间步下标
};

// ---------- 组件 ----------
struct Comp {
    int      compId   = -1;              // 组件 ID（唯一索引号）
    CompType compType = CompType::Steady; // 组件类型
    int      nPorts   = 0;               // 进出口个数（ports 与状态数组的长度）

    std::vector<Port> ports;     // 接口几何 + 拓扑（下标即进出口编号 0..nPorts-1）

    double controlVolume = 0.0;  // 控制体体积 m³（几何常数，仅 Volume 型使用）

    // 通用参数数列：元件种类相关的几何/结构参数，按约定顺序填入。
    // 顺序由元件定义处自行约定，例如篦齿：
    //   params = { 1.2e-3, 0.5e-3, 15.0 };  // {齿高 m, 齿宽 m, 齿数(取整用)}
    // 注意：CompType 是"数学类型"（定常/容腔/一维/耦合），与元件物理种类
    // （孔板/篦齿/管道…）是两个维度——同一篦齿既可建为定常组件也可建为容腔
    // 组件，所以物理参数放公共区，不放进 SteadyState/VolumeState。
    std::vector<double> params;

    SteadyState   steady;   // compType == Steady 时有效
    UnsteadyState unsteady; // compType == Volume  时有效
    // OneD / Coupled 的状态结构后续需要时再加
};

}  // namespace sas
