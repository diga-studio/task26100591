"""结算服务：保存当前输入与结算结果，对外提供三项能力。

- settle_group(plans)   整组结算
- replace_plan(plan)    替换（或重新加入）一个方案并调整结算
- delete_plan(plan_id)  删除一个方案并调整结算
- current_result()      读取当前结果

每次变更都基于保存的原始输入整体重算，确定性输出，
恢复原输入即恢复原结算，不累计舍入漂移。
"""

from __future__ import annotations

from fractions import Fraction

from .allocator import allocate_fees
from .models import GroupResult, PlanResult, PlanSpec

HOURS_PER_MONTH = 720
SECONDS_PER_MONTH = HOURS_PER_MONTH * 3600  # 2592000

HASH_UNITS = {
    "H/s": 1,
    "MH/s": 10**6,
    "GH/s": 10**9,
    "TH/s": 10**12,
}

# 阶梯电价（美元 / kWh）：前 1000 度、接下来 1000 度、其余。
TIER_LIMIT_KWH = 1000
TIER_RATES = (Fraction("0.10"), Fraction("0.20"), Fraction("0.30"))

POOL_FEE_RATE = Fraction("0.01")
POOL_FIXED_FEE_USD = Fraction(3)


def _parse_decimal(value: str, name: str) -> Fraction:
    try:
        return Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{name} 不是合法的十进制数: {value!r}") from exc


def _round_half_up_cents(dollars: Fraction) -> int:
    """非负美元金额四舍五入到整数分。"""
    cents = dollars * 100
    return (cents.numerator * 2 + cents.denominator) // (2 * cents.denominator)


def _tiered_cost_usd(kwh: Fraction) -> Fraction:
    first = min(kwh, TIER_LIMIT_KWH)
    second = min(max(kwh - TIER_LIMIT_KWH, Fraction(0)), TIER_LIMIT_KWH)
    rest = max(kwh - 2 * TIER_LIMIT_KWH, Fraction(0))
    return first * TIER_RATES[0] + second * TIER_RATES[1] + rest * TIER_RATES[2]


class _PlanStats:
    """单个方案的中间量，全部为精确值。"""

    __slots__ = ("usage_kwh", "revenue_usd", "pool_revenue", "pool_hw_count")

    def __init__(self) -> None:
        self.usage_kwh = Fraction(0)
        self.revenue_usd = Fraction(0)
        self.pool_revenue: dict[int, Fraction] = {}
        self.pool_hw_count: dict[int, int] = {}


def _validate_plan(plan: PlanSpec) -> None:
    if not isinstance(plan.plan_id, int) or isinstance(plan.plan_id, bool):
        raise ValueError(f"方案 ID 必须是整数: {plan.plan_id!r}")
    for hw in plan.hardware:
        if not isinstance(hw.pool_id, int) or isinstance(hw.pool_id, bool):
            raise ValueError(f"矿池 ID 必须是整数: {hw.pool_id!r}")
        if hw.unit not in HASH_UNITS:
            raise ValueError(f"不支持的算力单位: {hw.unit!r}")
        coin = hw.coin
        difficulty = _parse_decimal(coin.difficulty, "难度 D")
        diff_one = _parse_decimal(coin.diff_one_hashes, "难度1哈希次数 K")
        reward = _parse_decimal(coin.block_reward, "区块奖励 R")
        price = _parse_decimal(coin.price_usd, "币价 P")
        hashrate = _parse_decimal(hw.hashrate, "算力")
        watts = _parse_decimal(hw.watts, "功耗")
        if difficulty <= 0:
            raise ValueError("难度 D 必须大于 0")
        if diff_one <= 0:
            raise ValueError("难度1哈希次数 K 必须大于 0")
        if reward < 0 or price < 0:
            raise ValueError("区块奖励 R 与币价 P 不能为负")
        if hashrate < 0 or watts < 0:
            raise ValueError("算力与功耗不能为负")


def _plan_stats(plan: PlanSpec) -> _PlanStats:
    stats = _PlanStats()
    for hw in plan.hardware:
        coin = hw.coin
        difficulty = Fraction(str(coin.difficulty).strip())
        diff_one = Fraction(str(coin.diff_one_hashes).strip())
        reward = Fraction(str(coin.block_reward).strip())
        price = Fraction(str(coin.price_usd).strip())
        hashrate = Fraction(str(hw.hashrate).strip()) * HASH_UNITS[hw.unit]
        watts = Fraction(str(hw.watts).strip())

        revenue = hashrate * SECONDS_PER_MONTH * reward * price / (difficulty * diff_one)
        stats.revenue_usd += revenue
        stats.usage_kwh += watts * HOURS_PER_MONTH / 1000
        stats.pool_revenue[hw.pool_id] = stats.pool_revenue.get(hw.pool_id, Fraction(0)) + revenue
        stats.pool_hw_count[hw.pool_id] = stats.pool_hw_count.get(hw.pool_id, 0) + 1
    return stats


class SettlementService:
    """单个结算组的联合结算服务。不同结算组各自实例化，互不影响。"""

    def __init__(self) -> None:
        self._plans: dict[int, PlanSpec] = {}
        self._result: GroupResult = GroupResult()

    def settle_group(self, plans: list[PlanSpec]) -> GroupResult:
        """整组结算：以给定方案集合整体替换当前输入并重算。"""
        new_plans: dict[int, PlanSpec] = {}
        for plan in plans:
            _validate_plan(plan)
            if plan.plan_id in new_plans:
                raise ValueError(f"方案 ID 重复: {plan.plan_id}")
            new_plans[plan.plan_id] = plan
        self._plans = new_plans
        return self._recalculate()

    def replace_plan(self, plan: PlanSpec) -> GroupResult:
        """替换同 ID 方案（不存在则加入）并重算。"""
        _validate_plan(plan)
        self._plans[plan.plan_id] = plan
        return self._recalculate()

    def delete_plan(self, plan_id: int) -> GroupResult:
        """删除指定方案并重算。"""
        if plan_id not in self._plans:
            raise KeyError(f"方案不存在: {plan_id}")
        del self._plans[plan_id]
        return self._recalculate()

    def current_result(self) -> GroupResult:
        """读取当前结算结果。"""
        return self._result

    def _recalculate(self) -> GroupResult:
        plan_ids = sorted(self._plans)
        stats = [_plan_stats(self._plans[pid]) for pid in plan_ids]

        # 电费：全组共用阶梯电价，账单四舍五入到分，按用电量占比分摊。
        total_kwh = sum((s.usage_kwh for s in stats), Fraction(0))
        elec_bill = _round_half_up_cents(_tiered_cost_usd(total_kwh))
        if total_kwh > 0:
            elec_weights = [s.usage_kwh / total_kwh for s in stats]
        else:
            elec_weights = [Fraction(0) for _ in stats]

        # 矿池费：每池收全组未舍入毛收入的 1% 加 3 美元固定费。
        pool_ids = sorted({pid for s in stats for pid in s.pool_revenue})
        bills = [elec_bill]
        weights = [elec_weights]
        for pool_id in pool_ids:
            pool_revenue = sum(
                (s.pool_revenue.get(pool_id, Fraction(0)) for s in stats), Fraction(0)
            )
            fee_usd = pool_revenue * POOL_FEE_RATE + POOL_FIXED_FEE_USD
            bills.append(_round_half_up_cents(fee_usd))
            if pool_revenue > 0:
                weights.append(
                    [s.pool_revenue.get(pool_id, Fraction(0)) / pool_revenue for s in stats]
                )
            else:
                total_hw = sum(s.pool_hw_count.get(pool_id, 0) for s in stats)
                weights.append(
                    [Fraction(s.pool_hw_count.get(pool_id, 0), total_hw) for s in stats]
                )

        actual = allocate_fees(bills, weights)

        results = []
        for idx, pid in enumerate(plan_ids):
            revenue_cents = _round_half_up_cents(stats[idx].revenue_usd)
            pool_fees = {
                pool_id: actual[idx][1 + col]
                for col, pool_id in enumerate(pool_ids)
                if pool_id in stats[idx].pool_revenue
            }
            results.append(
                PlanResult(
                    plan_id=pid,
                    gross_revenue_cents=revenue_cents,
                    electricity_cents=actual[idx][0],
                    pool_fee_cents=pool_fees,
                )
            )
        self._result = GroupResult(plans=tuple(results))
        return self._result
