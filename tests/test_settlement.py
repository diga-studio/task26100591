"""矿场联合结算库自动化测试。"""
import random
import unittest
from fractions import Fraction
from itertools import product
from math import ceil, floor

from mining_settlement import Plan, SettlementEngine, make_hardware
from mining_settlement.core import _tier_price_cents
from mining_settlement.models import round_half_up_cents

SECONDS = 2592000


def hw_revenue(coin, hashrate_hs):
    d, k, r, p = coin
    return Fraction(hashrate_hs) * SECONDS * Fraction(r) * Fraction(p) / (Fraction(d) * Fraction(k))


def independent_ideals(coins, plans):
    """按规格独立计算 (bills, ideals, pool_ids)，用于与引擎对照。"""
    plans = sorted(plans, key=lambda x: x.plan_id)
    n = len(plans)
    pool_ids = sorted({hw.pool_id for p in plans for hw in p.hardware})
    m = 1 + len(pool_ids)
    usage = [sum((h.watts for h in p.hardware), Fraction(0)) * 720 / 1000 for p in plans]
    rev = [{pid: Fraction(0) for pid in pool_ids} for _ in plans]
    cnt = [{pid: 0 for pid in pool_ids} for _ in plans]
    for i, p in enumerate(plans):
        for h in p.hardware:
            rev[i][h.pool_id] += hw_revenue(coins[h.coin_id], h.hashrate_hs)
            cnt[i][h.pool_id] += 1
    bills = []
    ideals = [[Fraction(0)] * m for _ in range(n)]
    total_usage = sum(usage, Fraction(0))
    bill_e = round_half_up_cents(_tier_price_cents(total_usage)) if total_usage > 0 else 0
    if total_usage > 0:
        for i in range(n):
            ideals[i][0] = usage[i] * bill_e / total_usage
    bills.append(bill_e)
    for j, pid in enumerate(pool_ids, start=1):
        pool_rev = sum((rev[i][pid] for i in range(n)), Fraction(0))
        bill_p = round_half_up_cents(pool_rev + 300)
        if pool_rev > 0:
            for i in range(n):
                ideals[i][j] = rev[i][pid] * bill_p / pool_rev
        else:
            total_cnt = sum(cnt[i][pid] for i in range(n))
            for i in range(n):
                ideals[i][j] = Fraction(cnt[i][pid] * bill_p) / total_cnt
        bills.append(bill_p)
    return bills, ideals, pool_ids


def brute_force_best(ideals, bills):
    """枚举全部合法分币结果，返回 (总偏差, 字典序序列) 最优的分摊矩阵。"""
    n = len(ideals)
    m = len(bills)
    plan_totals = [sum(row) for row in ideals]
    ranges = []
    for i in range(n):
        for j in range(m):
            lo, hi = floor(ideals[i][j]), ceil(ideals[i][j])
            ranges.append([lo] if lo == hi else [lo, hi])
    best = None
    for combo in product(*ranges):
        alloc = [list(combo[i * m:(i + 1) * m]) for i in range(n)]
        if any(sum(alloc[i][j] for i in range(n)) != bills[j] for j in range(m)):
            continue
        if any(
            sum(alloc[i]) not in (floor(plan_totals[i]), ceil(plan_totals[i]))
            for i in range(n)
        ):
            continue
        dev = sum(abs(alloc[i][j] - ideals[i][j]) for i in range(n) for j in range(m))
        seq = tuple(combo)
        key = (dev, seq)
        if best is None or key < best[0]:
            best = (key, alloc)
    return best[1]


def engine_alloc(result, pool_ids):
    return [
        [p.electricity_fee_cents] + [p.pool_fees_cents.get(pid, 0) for pid in pool_ids]
        for p in result.plans
    ]


BTC = ("1000000000", "4294967296", "6.25", "27123.45")


def make_engine():
    eng = SettlementEngine()
    eng.define_coin("BTC", *BTC)
    eng.define_coin("ZERO", "1", "10368000", "1", "1")  # 1 H/s -> 0.25 美元/月
    return eng


class BruteForceTest(unittest.TestCase):
    def test_matches_brute_force(self):
        eng = make_engine()
        plans = [
            Plan(1, (make_hardware("BTC", 10, "85.5", "TH/s", "3200"),
                     make_hardware("BTC", 11, "40", "TH/s", "1400"))),
            Plan(2, (make_hardware("BTC", 10, "120", "TH/s", "3300"),)),
            Plan(3, (make_hardware("BTC", 11, "60", "TH/s", "2000"),
                     make_hardware("BTC", 10, "10", "TH/s", "500"))),
        ]
        result = eng.settle_group("g", plans)
        coins = {"BTC": BTC}
        bills, ideals, pool_ids = independent_ideals(coins, plans)
        expected = brute_force_best(ideals, bills)
        self.assertEqual(engine_alloc(result, pool_ids), expected)

    def test_group_totals_are_plan_sums(self):
        eng = make_engine()
        plans = [
            Plan(1, (make_hardware("BTC", 10, "85.5", "TH/s", "3200"),)),
            Plan(2, (make_hardware("BTC", 10, "120", "TH/s", "3300"),)),
        ]
        r = eng.settle_group("g", plans)
        self.assertEqual(r.total_gross_revenue_cents,
                         sum(p.gross_revenue_cents for p in r.plans))
        self.assertEqual(r.total_fees_cents, sum(p.total_fees_cents for p in r.plans))
        self.assertEqual(r.total_net_profit_cents,
                         sum(p.net_profit_cents for p in r.plans))
        self.assertEqual(r.total_electricity_fee_cents,
                         sum(p.electricity_fee_cents for p in r.plans))
        for pid, bill in r.total_pool_fees_cents.items():
            self.assertEqual(bill, sum(p.pool_fees_cents.get(pid, 0) for p in r.plans))
        for p in r.plans:
            self.assertEqual(p.net_profit_cents,
                             p.gross_revenue_cents - p.total_fees_cents)


class TieBreakTest(unittest.TestCase):
    def test_lexicographic_tie_break_and_determinism(self):
        # 两台 1 H/s 硬件分属两个方案，接入同一零价池：
        # 池收入 0.5 美元 -> 费用 300.5 -> 301 分，理想各 150.5，偏差并列。
        eng = make_engine()
        plans = [
            Plan(1, (make_hardware("ZERO", 7, "1", "H/s", "0"),)),
            Plan(2, (make_hardware("ZERO", 7, "1", "H/s", "0"),)),
        ]
        r1 = eng.settle_group("g", plans)
        self.assertEqual(r1.total_pool_fees_cents, {7: 301})
        # 字典序最小：ID 较小的方案取向下取整 150
        self.assertEqual(r1.plans[0].pool_fees_cents, {7: 150})
        self.assertEqual(r1.plans[1].pool_fees_cents, {7: 151})
        # 确定性：重复结算结果完全一致
        r2 = eng.settle_group("g", plans)
        self.assertEqual(r1, r2)


class ZeroRevenuePoolTest(unittest.TestCase):
    def test_zero_price_coin_pool(self):
        eng = SettlementEngine()
        eng.define_coin("DEAD", "1", "1", "1", "0")  # 币价 0 -> 收入 0
        plans = [
            Plan(1, (make_hardware("DEAD", 5, "100", "TH/s", "0"),)),
            Plan(2, (make_hardware("DEAD", 5, "50", "TH/s", "0"),
                     make_hardware("DEAD", 5, "50", "TH/s", "0"))),
            Plan(3, (make_hardware("DEAD", 6, "10", "TH/s", "0"),)),
        ]
        r = eng.settle_group("g", plans)
        # 池 5：收入 0 -> 费用恰为 300 分固定费，按台数 1:2 分摊
        self.assertEqual(r.total_pool_fees_cents, {5: 300, 6: 300})
        self.assertEqual(r.plans[0].pool_fees_cents, {5: 100})
        self.assertEqual(r.plans[1].pool_fees_cents, {5: 200})
        # 方案 3 未接入池 5，不承担其费用
        self.assertNotIn(5, r.plans[2].pool_fees_cents)
        self.assertEqual(r.plans[2].pool_fees_cents, {6: 300})
        # 全部方案毛收入为 0，净利润为负（仅费用）
        for p in r.plans:
            self.assertEqual(p.gross_revenue_cents, 0)
            self.assertEqual(p.net_profit_cents, -p.total_fees_cents)

    def test_zero_total_usage_no_electricity_fee(self):
        eng = SettlementEngine()
        eng.define_coin("DEAD", "1", "1", "1", "0")
        plans = [Plan(1, (make_hardware("DEAD", 5, "100", "TH/s", "0"),))]
        r = eng.settle_group("g", plans)
        self.assertEqual(r.total_electricity_fee_cents, 0)
        self.assertEqual(r.plans[0].electricity_fee_cents, 0)

    def test_zero_revenue_pool_matches_brute_force(self):
        eng = SettlementEngine()
        eng.define_coin("DEAD", "1", "1", "1", "0")
        eng.define_coin("BTC", *BTC)
        plans = [
            Plan(1, (make_hardware("DEAD", 5, "100", "TH/s", "300"),
                     make_hardware("BTC", 9, "10", "TH/s", "800"))),
            Plan(2, (make_hardware("DEAD", 5, "50", "TH/s", "200"),
                     make_hardware("DEAD", 5, "50", "TH/s", "200"))),
            Plan(3, (make_hardware("BTC", 9, "20", "TH/s", "1500"),)),
        ]
        result = eng.settle_group("g", plans)
        coins = {"DEAD": ("1", "1", "1", "0"), "BTC": BTC}
        bills, ideals, pool_ids = independent_ideals(coins, plans)
        expected = brute_force_best(ideals, bills)
        self.assertEqual(engine_alloc(result, pool_ids), expected)


class ModifyDeleteRestoreTest(unittest.TestCase):
    def setUp(self):
        self.eng = make_engine()
        self.p1 = Plan(1, (make_hardware("BTC", 10, "80", "TH/s", "3000"),))
        self.p2 = Plan(2, (make_hardware("BTC", 10, "100", "TH/s", "3200"),
                           make_hardware("BTC", 11, "30", "TH/s", "1200")))
        self.p3 = Plan(3, (make_hardware("BTC", 11, "50", "TH/s", "2000"),))
        self.original = self.eng.settle_group("g", [self.p1, self.p2, self.p3])

    def test_replace_changes_others_and_restore(self):
        p2_new = Plan(2, (make_hardware("BTC", 10, "200", "TH/s", "6400"),
                          make_hardware("BTC", 11, "30", "TH/s", "1200")))
        changed = self.eng.replace_plan("g", p2_new)
        self.assertNotEqual(changed, self.original)
        # 未修改的方案 1 费用也发生变化（占比变化）
        self.assertNotEqual(changed.plans[0].total_fees_cents,
                            self.original.plans[0].total_fees_cents)
        # 恢复原方案后，结算结果逐项一致
        restored = self.eng.replace_plan("g", self.p2)
        self.assertEqual(restored, self.original)
        self.assertEqual(self.eng.get_settlement("g"), self.original)

    def test_delete_changes_others_and_restore(self):
        deleted = self.eng.delete_plan("g", 3)
        self.assertEqual(len(deleted.plans), 2)
        # 池 11 只剩方案 2，其费用占比变为 100%
        self.assertNotEqual(deleted.plans[1].pool_fees_cents,
                            self.original.plans[1].pool_fees_cents)
        # 恢复被删方案后，结算结果逐项一致
        restored = self.eng.replace_plan("g", self.p3)
        self.assertEqual(restored, self.original)

    def test_groups_are_independent(self):
        other = self.eng.settle_group("h", [self.p1])
        self.eng.delete_plan("g", 2)
        self.assertEqual(self.eng.get_settlement("h"), other)


class TierPriceTest(unittest.TestCase):
    def test_tier_boundaries(self):
        self.assertEqual(_tier_price_cents(Fraction(0)), 0)
        self.assertEqual(_tier_price_cents(Fraction(1000)), 10000)
        self.assertEqual(_tier_price_cents(Fraction(2000)), 30000)
        self.assertEqual(_tier_price_cents(Fraction(2500)), 45000)
        self.assertEqual(_tier_price_cents(Fraction(1, 2)), 5)


class RandomizedBruteForceTest(unittest.TestCase):
    def test_random_small_cases_match_brute_force(self):
        rng = random.Random(20261005)
        for trial in range(30):
            n_plans = rng.randint(1, 3)
            n_pools = rng.randint(1, 2)
            eng = SettlementEngine()
            eng.define_coin("C", "123456.789", "4294967296", "3.125", "0.6125")
            plans = []
            for pid in range(1, n_plans + 1):
                hws = []
                for _ in range(rng.randint(1, 2)):
                    hws.append(make_hardware(
                        "C",
                        rng.randint(1, n_pools),
                        str(rng.randint(0, 500)) + "." + str(rng.randint(0, 99)),
                        rng.choice(["MH/s", "GH/s", "TH/s"]),
                        str(rng.randint(0, 3000)),
                    ))
                plans.append(Plan(pid, tuple(hws)))
            result = eng.settle_group("g", plans)
            coins = {"C": ("123456.789", "4294967296", "3.125", "0.6125")}
            bills, ideals, pool_ids = independent_ideals(coins, plans)
            expected = brute_force_best(ideals, bills)
            self.assertEqual(engine_alloc(result, pool_ids), expected,
                             f"第 {trial} 个随机用例不一致")


if __name__ == "__main__":
    unittest.main()
