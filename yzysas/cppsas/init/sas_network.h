// sas_network.h — 网络拓扑与几何定义（第一版：只有"谁连谁、多大"，无流动状态）
// 拓扑层是求解方程组的骨架：
//   Comp.ports[i].nodeId  →  分支动量方程连接的两端
//   Node.links            →  节点质量/能量方程汇聚的分支
// 几何层：面积（接口）、controlVolume（容腔）、params（齿高/齿数等，按 ElemType 约定顺序）
#pragma once

#include <vector>

namespace sas {

// ---------- 节点 ↔ 组件的连接记录 ----------
// isOutflowOfComp = true：该口是组件的出口（流体流入节点）
//                  false：该口是组件的入口（流体从节点流出组件）
// 方向由用户建网时指定；若元件支持倒流（如管道），求解中自会翻转，拓扑方向只作初值/约定。
struct NodeLink {
    int  compId     = -1;   // 连接的组件 ID
    int  portIndex  = -1;   // 连接的是组件第几个口（0..nPorts-1）
    bool isOutflowOfComp = true;  // 建网时的方向约定
};

// ---------- 节点（拓扑 + 几何）----------
struct Node {
    int  nodeId   = -1;      // 唯一索引
    bool isBoundary = false; // true: 边界节点（p0/T0 已知）；false: 内部节点（p0/T0 是未知量）

    double totalPressure     = 0.0;  // 总压 Pa（边界=给定；内部=初值/迭代值）
    double totalTemperature  = 0.0;  // 总温 K（同上）
    double volume            = 0.0;  // 节点自身容积 m³（若节点本身带容腔效应；0 = 无）

    std::vector<NodeLink> links;  // 与该节点相连的所有组件端口
};

// ---------- 接口几何（面积 + 连接）----------
struct Port {
    double area          = 0.0;  // 流通面积 m²
    int    nodeId        = -1;   // 连接到的节点 ID
};

// ---------- 组件（拓扑 + 几何）----------
// elemType 决定 params 数列的解读方式（顺序约定写在各元件方程文档里）
struct Comp {
    int compId = -1;              // 唯一索引
    // 物理种类（Orifice=孔板, Seal=篦齿, Pipe=圆柱直管, PreswirlNozzle=预旋喷嘴, Volume=纯容腔）
    int elemType = 0;
    // 数学类型（0定常 1零维容腔 2一维 3耦合）——与 elemType 正交
    int compType = 0;

    std::vector<Port> ports;      // 接口几何+拓扑，下标即 portIndex
    std::vector<double> params;   // 物理参数数列（齿高/齿宽/齿数…，按 elemType 约定顺序）
};

// ---------- 网络总装（拓扑容器）----------
// 只存"结构"，不存"解"。求解状态（p0/T0/ṁ 历史等）放别的结构，按需重建。
struct Network {
    std::vector<Node> nodes;  // 下标与 nodeId 一一对应（要求 nodeId = 0..N-1 连续编号）
    std::vector<Comp> comps;  // 下标与 compId 一一对应（同上）
    // 校验后的派生信息（拓扑检查通过后填）：
    int nInterior = 0;  // 内部节点数（= 方程组未知量个数 p0）
    int nBoundary = 0;  // 边界节点数
};

}  // namespace sas
