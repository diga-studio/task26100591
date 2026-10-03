"""核心结算计算：收入、费用项、理想分摊与最优取整分摊。"""
from __future__ import annotations

from fractions import Fraction
from math import ceil, floor

from .flow import MinCostFlow
from .models import GroupSettlement, PlanSettlement, round_half_up_cents

SECONDS_PER_MONTH = 2592000  # 720 小时
HOURS_PER_MONTH = 720

# 阶梯电价：(档位上限 kWh, 单价 分/kWh)，最后一档无上限
_TIERS = ((Fraction(1000), Fraction(10)), (Fraction(1000), Fraction(20)), (None, Fraction(30)))


def _tier_price_cents(kwh: Fraction) -> Fraction:
    """全组用电量对应的阶梯电费，单位：分（精确值）。"""
    cost = Fraction(0)
    remaining = kwh
    for cap, price in _TIERS:
        if remaining <= 0:
            break
        amount = remaining if cap is None else min(remaining, cap)
        cost += amount * price
        remaining -= amount
    return cost


def _optimal_allocation(ideals, bills):
    """求满足约束的最优整数分摊。

    ideals[i][j]：方案 i 在费用项 j 上的理想分摊（Fraction，单位分）。
    bills[j]：费用项 j 的账单（整数分）。
    返回 alloc[i][j]（整数分）。目标：总绝对偏差最小；并列时按
    （方案行、费用项列）展开序列字典序最小。行列顺序即传入顺序。
    """
    n = len(ideals)
    m = len(bills)
    src = 0
    sink = 1 + n + m
    total_bill = sum(bills)
    plan_totals = [sum(row) for row in ideals]
    fracs = [[q - floor(q) for q in row] for row in ideals]

    def build(fixed):
        mcf = MinCostFlow(sink + 1)
        for i in range(n):
            total = plan_totals[i]
            mcf.add_edge(src, 1 + i, floor(total), ceil(total), Fraction(0))
        cell_edge = {}
        for i in range(n):
            for j in range(m):
                q = ideals[i][j]
                lo, hi = floor(q), ceil(q)
                if (i, j) in fixed:
                    lo = hi = fixed[(i, j)]
                cost = 1 - 2 * fracs[i][j]  # 向上取整相对向下取整的偏差增量
                cell_edge[(i, j)] = mcf.num_edges
                mcf.add_edge(1 + i, 1 + n + j, lo, hi, cost)
        for j in range(m):
            mcf.add_edge(1 + n + j, sink, bills[j], bills[j], Fraction(0))
        mcf.add_edge(sink, src, total_bill, total_bill, Fraction(0))
        return mcf, cell_edge

    def solve_with(fixed):
        mcf, cell_edge = build(fixed)
        result = mcf.solve()
        if result is None:
            return None
        cost, flows = result
        alloc = {cell: flows[idx] for cell, idx in cell_edge.items()}
        return cost, alloc

    first = solve_with({})
    assert first is not None, "分摊问题必然可行"
    best_cost = first[0]

    fixed = {}
    for i in range(n):
        for j in range(m):
            q = ideals[i][j]
            lo, hi = floor(q), ceil(q)
            if lo == hi:
                fixed[(i, j)] = lo
                continue
            trial = dict(fixed)
            trial[(i, j)] = lo
            res = solve_with(trial)
            if res is not None and res[0] == best_cost:
                fixed = trial
            else:
                fixed[(i, j)] = hi

    final = solve_with(fixed)
    assert final is not None and final[0] == best_cost
    alloc = final[1]
    return [[alloc[(i, j)] for j in range(m)] for i in range(n)]


def compute_settlement(group_id, coins: dict, plans: list) -> GroupSettlement:
    """根据币种注册表与方案列表计算整组结算结果（确定性）。"""
    plans = sorted(plans, key=lambda p: p.plan_id)
    plan_ids = [p.plan_id for p in plans]
    if len(set(plan_ids)) != len(plan_ids):
        raise ValueError("同一结算组内方案ID重复")
    n = len(plans)

    usage = []      # 每方案月用电量 kWh
    rev_pool = []   # 每方案在各矿池的未舍入毛收入（美元）
    cnt_pool = []   # 每方案在各矿池的硬件台数
    gross_exact = []
    pools = set()
    for plan in plans:
        plan_usage = Fraction(0)
        plan_rev = {}
        plan_cnt = {}
        plan_gross = Fraction(0)
        for hw in plan.hardware:
            coin = coins.get(hw.coin_id)
            if coin is None:
                raise ValueError(f"未知币种: {hw.coin_id!r}")
            if coin.difficulty <= 0 or coin.hashes_per_block <= 0:
                raise ValueError(f"币种 {hw.coin_id!r} 的难度与K必须大于0")
            if hw.hashrate_hs < 0 or hw.watts < 0:
                raise ValueError("哈希速率与功耗不能为负")
            plan_usage += hw.watts * HOURS_PER_MONTH / 1000
            revenue = (
                hw.hashrate_hs
                * SECONDS_PER_MONTH
                * coin.block_reward
                * coin.price_usd
                / (coin.difficulty * coin.hashes_per_block)
            )
            plan_rev[hw.pool_id] = plan_rev.get(hw.pool_id, Fraction(0)) + revenue
            plan_cnt[hw.pool_id] = plan_cnt.get(hw.pool_id, 0) + 1
            plan_gross += revenue
            pools.add(hw.pool_id)
        usage.append(plan_usage)
        rev_pool.append(plan_rev)
        cnt_pool.append(plan_cnt)
        gross_exact.append(plan_gross)

    pool_ids = sorted(pools)
    m = 1 + len(pool_ids)  # 费用项：电费 + 各矿池费
    bills = []
    ideals = [[Fraction(0)] * m for _ in range(n)]

    total_usage = sum(usage, Fraction(0))
    if total_usage > 0:
        bill_elec = round_half_up_cents(_tier_price_cents(total_usage))
        for i in range(n):
            ideals[i][0] = usage[i] * bill_elec / total_usage
    else:
        bill_elec = 0
    bills.append(bill_elec)

    total_pool_fees = {}
    for j, pool_id in enumerate(pool_ids, start=1):
        pool_revenue = sum(
            (rev_pool[i].get(pool_id, Fraction(0)) for i in range(n)), Fraction(0)
        )
        # 1% 佣金 + 3 美元固定费，换算为分：pool_revenue(美元)*1 + 300
        bill_pool = round_half_up_cents(pool_revenue + 300)
        if pool_revenue > 0:
            for i in range(n):
                ideals[i][j] = rev_pool[i].get(pool_id, Fraction(0)) * bill_pool / pool_revenue
        else:
            total_cnt = sum(cnt_pool[i].get(pool_id, 0) for i in range(n))
            for i in range(n):
                ideals[i][j] = Fraction(cnt_pool[i].get(pool_id, 0) * bill_pool) / total_cnt
        bills.append(bill_pool)
        total_pool_fees[pool_id] = bill_pool

    alloc = _optimal_allocation(ideals, bills) if n > 0 else []

    plan_settlements = []
    for i, plan in enumerate(plans):
        gross_cents = round_half_up_cents(gross_exact[i] * 100)
        elec = alloc[i][0]
        pool_fees = {
            pool_id: alloc[i][1 + j]
            for j, pool_id in enumerate(pool_ids)
            if pool_id in cnt_pool[i]
        }
        total_fees = elec + sum(pool_fees.values())
        plan_settlements.append(
            PlanSettlement(
                plan_id=plan.plan_id,
                gross_revenue_cents=gross_cents,
                electricity_fee_cents=elec,
                pool_fees_cents=pool_fees,
                total_fees_cents=total_fees,
                net_profit_cents=gross_cents - total_fees,
            )
        )

    total_gross = sum(ps.gross_revenue_cents for ps in plan_settlements)
    total_fees = bill_elec + sum(total_pool_fees.values())
    result = GroupSettlement(
        group_id=group_id,
        plans=tuple(plan_settlements),
        total_gross_revenue_cents=total_gross,
        total_electricity_fee_cents=bill_elec,
        total_pool_fees_cents=total_pool_fees,
        total_fees_cents=total_fees,
        total_net_profit_cents=total_gross - total_fees,
    )
    # 组级一致性：收入、费用、净利润分别等于各方案之和
    assert sum(ps.electricity_fee_cents for ps in plan_settlements) == bill_elec
    for j, pool_id in enumerate(pool_ids, start=1):
        assert sum(alloc[i][j] for i in range(n)) == bills[j]
    assert sum(ps.total_fees_cents for ps in plan_settlements) == total_fees
    assert sum(ps.net_profit_cents for ps in plan_settlements) == result.total_net_profit_cents
    return result
