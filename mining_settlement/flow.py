"""最小费用流求解器（精确 Fraction 费用，整数容量），用于费用分摊优化。"""
from __future__ import annotations

from fractions import Fraction


class MinCostFlow:
    """支持边下界 [lo, hi] 与 Fraction 单位费用的最小费用可行流。

    通过节点平衡量把下界转化为超级源汇的可行流问题，再用
    连续最短路（Bellman-Ford）增广。要求初始残量网络无负费用环
    （本库构造的分层网络天然满足）。
    """

    def __init__(self, num_nodes: int):
        self._n = num_nodes
        self._edges = []  # (u, v, lo, hi, cost)

    def add_edge(self, u: int, v: int, lo: int, hi: int, cost: Fraction) -> None:
        if not 0 <= lo <= hi:
            raise ValueError("边容量下界必须满足 0 <= lo <= hi")
        self._edges.append((u, v, lo, hi, Fraction(cost)))

    @property
    def num_edges(self) -> int:
        return len(self._edges)

    def solve(self):
        """返回 (最小费用, {边序号: 流量})；不可行返回 None。"""
        n = self._n
        ss, tt = n, n + 1
        graph = [[] for _ in range(n + 2)]
        balance = [0] * n
        base_cost = Fraction(0)

        def add_residual(u, v, cap, cost, orig=None):
            graph[u].append([v, len(graph[v]), cap, cost, orig])
            graph[v].append([u, len(graph[u]) - 1, 0, -cost, None])

        for idx, (u, v, lo, hi, cost) in enumerate(self._edges):
            if hi > lo:
                add_residual(u, v, hi - lo, cost, idx)
            balance[u] -= lo
            balance[v] += lo
            base_cost += lo * cost

        total_supply = 0
        for v in range(n):
            if balance[v] > 0:
                add_residual(ss, v, balance[v], Fraction(0))
                total_supply += balance[v]
            elif balance[v] < 0:
                add_residual(v, tt, -balance[v], Fraction(0))

        pushed = 0
        flow_cost = Fraction(0)
        while True:
            dist = [None] * (n + 2)
            prev = [None] * (n + 2)
            dist[ss] = Fraction(0)
            for _ in range(n + 2):
                updated = False
                for u in range(n + 2):
                    if dist[u] is None:
                        continue
                    for ei, edge in enumerate(graph[u]):
                        if edge[2] > 0:
                            cand = dist[u] + edge[3]
                            if dist[edge[0]] is None or cand < dist[edge[0]]:
                                dist[edge[0]] = cand
                                prev[edge[0]] = (u, ei)
                                updated = True
                if not updated:
                    break
            if dist[tt] is None:
                break
            bottleneck = None
            v = tt
            while v != ss:
                u, ei = prev[v]
                cap = graph[u][ei][2]
                bottleneck = cap if bottleneck is None else min(bottleneck, cap)
                v = u
            v = tt
            while v != ss:
                u, ei = prev[v]
                graph[u][ei][2] -= bottleneck
                graph[v][graph[u][ei][1]][2] += bottleneck
                v = u
            flow_cost += dist[tt] * bottleneck
            pushed += bottleneck

        if pushed < total_supply:
            return None

        flows = {idx: lo for idx, (_, _, lo, _, _) in enumerate(self._edges)}
        for u in range(n + 2):
            for edge in graph[u]:
                if edge[4] is not None:
                    idx = edge[4]
                    lo, hi = self._edges[idx][2], self._edges[idx][3]
                    flows[idx] = hi - edge[2]
        return base_cost + flow_cost, flows
