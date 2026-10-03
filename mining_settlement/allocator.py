"""费用分摊器：把每项组级费用账单分摊到各方案。

约束：
- 每项费用的实际分摊为对应理想分摊（Fraction）的向下或向上整数；
- 每项费用分摊之和等于该项账单（整数分）；
- 每个方案全部费用之和为其全部理想费用之和的向下或向上整数。

目标：所有方案、所有费用项的实际分摊与理想值绝对偏差总和最小；
并列时按方案 ID 升序逐行展开（电费在先、矿池费用按矿池 ID 升序），
取金额序列字典序最小者。
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction


def _add_arc(graph: list, u: int, v: int, cap: int, cost: Fraction) -> int:
    """加入一条残量弧（含反向弧），返回正向弧在 graph[u] 中的下标。"""
    graph[u].append([v, cap, cost, len(graph[v])])
    graph[v].append([u, 0, -cost, len(graph[u]) - 1])
    return len(graph[u]) - 1


def _min_cost_circulation(
    num_nodes: int,
    edges: list[tuple[int, int, int, int, Fraction]],
) -> tuple[bool, Fraction | None, list[int] | None]:
    """求解带下界的最小费用循环流。

    edges: (u, v, lo, hi, cost)，流量为整数且 lo <= flow <= hi。
    返回 (是否可行, 最小费用, 各边流量)。费用用 Fraction 精确表示。
    """
    demand = [0] * num_nodes
    graph: list[list[list]] = [[] for _ in range(num_nodes)]
    base: list[tuple[int, int, Fraction, int, int]] = []
    for u, v, lo, hi, cost in edges:
        demand[u] -= lo
        demand[v] += lo
        idx = _add_arc(graph, u, v, hi - lo, cost)
        base.append((lo, hi - lo, cost, u, idx))

    ss, tt = num_nodes, num_nodes + 1
    graph.append([])
    graph.append([])
    total = 0
    for v in range(num_nodes):
        if demand[v] < 0:
            _add_arc(graph, v, tt, -demand[v], Fraction(0))
        elif demand[v] > 0:
            _add_arc(graph, ss, v, demand[v], Fraction(0))
            total += demand[v]

    n = num_nodes + 2

    flow = 0
    while True:
        prev = [(-1, -1)] * n
        prev[ss] = (ss, -1)
        queue = deque([ss])
        while queue and prev[tt][0] == -1:
            u = queue.popleft()
            for ei, arc in enumerate(graph[u]):
                if arc[1] > 0 and prev[arc[0]][0] == -1:
                    prev[arc[0]] = (u, ei)
                    queue.append(arc[0])
        if prev[tt][0] == -1:
            break
        bottleneck: int | None = None
        v = tt
        while v != ss:
            u, ei = prev[v]
            cap = graph[u][ei][1]
            bottleneck = cap if bottleneck is None else min(bottleneck, cap)
            v = u
        v = tt
        while v != ss:
            u, ei = prev[v]
            arc = graph[u][ei]
            arc[1] -= bottleneck
            graph[arc[0]][arc[3]][1] += bottleneck
            v = u
        flow += bottleneck
    if flow != total:
        return False, None, None

    # 负环消去：可行循环流最优当且仅当残量网中无负费用环。
    while True:
        dist = [Fraction(0)] * n
        parent = [(-1, -1)] * n
        last = -1
        for _ in range(n):
            last = -1
            for u in range(n):
                du = dist[u]
                for ei, arc in enumerate(graph[u]):
                    if arc[1] > 0:
                        cand = du + arc[2]
                        if cand < dist[arc[0]]:
                            dist[arc[0]] = cand
                            parent[arc[0]] = (u, ei)
                            last = arc[0]
            if last == -1:
                break
        if last == -1:
            break
        v = last
        for _ in range(n):
            v = parent[v][0]
        cycle = []
        cur = v
        while True:
            u, ei = parent[cur]
            cycle.append((u, ei))
            cur = u
            if cur == v:
                break
        bottleneck = min(graph[u][ei][1] for u, ei in cycle)
        for u, ei in cycle:
            arc = graph[u][ei]
            arc[1] -= bottleneck
            graph[arc[0]][arc[3]][1] += bottleneck

    flows = []
    total_cost = Fraction(0)
    for lo, hi_minus_lo, cost, u, idx in base:
        edge_flow = lo + (hi_minus_lo - graph[u][idx][1])
        flows.append(edge_flow)
        total_cost += cost * edge_flow
    return True, total_cost, flows


def allocate_fees(
    bills_cents: list[int],
    weights: list[list[Fraction]],
) -> list[list[int]]:
    """分摊费用。

    bills_cents[j]  : 第 j 项费用的账单（整数分）。
    weights[j][i]   : 第 j 项费用中方案 i 的占比（Fraction，每项合计为 1；
                      账单为 0 时允许全 0）。

    返回 actual[i][j]：方案 i 承担的第 j 项费用（整数分）。
    行顺序即调用方传入的方案顺序（调用方按方案 ID 升序排列），
    列顺序即费用项顺序（电费在先、矿池按 ID 升序），与字典序展开顺序一致。
    """
    num_items = len(bills_cents)
    num_plans = len(weights[0]) if num_items else 0
    if num_plans == 0:
        return []

    ideals: list[list[Fraction]] = []
    floors: list[list[int]] = []
    fracs: list[list[Fraction]] = []
    for i in range(num_plans):
        ideal_row = []
        floor_row = []
        frac_row = []
        for j in range(num_items):
            x = bills_cents[j] * weights[j][i]
            f = x.numerator // x.denominator
            ideal_row.append(x)
            floor_row.append(f)
            frac_row.append(x - f)
        ideals.append(ideal_row)
        floors.append(floor_row)
        fracs.append(frac_row)

    # 列需求：每项费用需要“向上取整”的次数。
    col_need = []
    for j in range(num_items):
        need = sum(fracs[i][j] for i in range(num_plans))
        assert need.denominator == 1, "每项费用的理想分摊之和必须等于账单整数"
        col_need.append(int(need))

    # 行界：每个方案总费用只能取理想总和的向下或向上整数。
    row_lo = []
    row_hi = []
    for i in range(num_plans):
        s = sum(fracs[i])
        lo = s.numerator // s.denominator
        row_lo.append(lo)
        row_hi.append(lo if s.denominator == 1 else lo + 1)

    source, sink = 0, 1 + num_plans + num_items
    num_nodes = sink + 1
    plan_node = [1 + i for i in range(num_plans)]
    item_node = [1 + num_plans + j for j in range(num_items)]
    total_up = sum(col_need)

    # 自由边（frac>0）在 edges 中的下标，按 (方案, 费用项) 顺序排列，
    # 与字典序破并列的展开顺序一致。
    free_edges: list[tuple[int, int, int]] = []

    def build_edges(
        fixed: dict[tuple[int, int], int],
    ) -> tuple[list[tuple[int, int, int, int, Fraction]], list[int]]:
        edges: list[tuple[int, int, int, int, Fraction]] = []
        free_indices: list[int] = []
        for i in range(num_plans):
            edges.append((source, plan_node[i], row_lo[i], row_hi[i], Fraction(0)))
        for i in range(num_plans):
            for j in range(num_items):
                if fracs[i][j] == 0:
                    continue
                cost = 1 - 2 * fracs[i][j]
                forced = fixed.get((i, j))
                if forced is None:
                    lo, hi = 0, 1
                else:
                    lo = hi = forced
                free_indices.append(len(edges))
                edges.append((plan_node[i], item_node[j], lo, hi, cost))
        for j in range(num_items):
            edges.append((item_node[j], sink, col_need[j], col_need[j], Fraction(0)))
        edges.append((sink, source, 0, total_up, Fraction(0)))
        return edges, free_indices

    edges, free_indices = build_edges({})
    for i in range(num_plans):
        for j in range(num_items):
            if fracs[i][j] > 0:
                free_edges.append((i, j, len(free_edges)))

    feasible, best_cost, _ = _min_cost_circulation(num_nodes, edges)
    assert feasible, "分摊约束必然存在可行解"

    # 字典序破并列：按展开顺序逐位尝试取更小金额（y=0），
    # 只要仍存在达到最优偏差的补全方案就固定为 0，否则固定为 1。
    fixed: dict[tuple[int, int], int] = {}
    for i, j, _ in free_edges:
        fixed[(i, j)] = 0
        edges, _ = build_edges(fixed)
        ok, cost, _ = _min_cost_circulation(num_nodes, edges)
        if not (ok and cost == best_cost):
            fixed[(i, j)] = 1
            edges, _ = build_edges(fixed)
            ok, cost, _ = _min_cost_circulation(num_nodes, edges)
            assert ok and cost == best_cost

    edges, final_free_indices = build_edges(fixed)
    feasible, _, flows = _min_cost_circulation(num_nodes, edges)
    assert feasible

    up: dict[tuple[int, int], int] = {}
    idx = 0
    for i in range(num_plans):
        for j in range(num_items):
            if fracs[i][j] > 0:
                up[(i, j)] = flows[final_free_indices[idx]]
                idx += 1

    return [
        [floors[i][j] + up.get((i, j), 0) for j in range(num_items)]
        for i in range(num_plans)
    ]
