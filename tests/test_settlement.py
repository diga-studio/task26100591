"""矿场联合结算库的自动化测试。"""

from __future__ import annotations

import random
import unittest
from fractions import Fraction
from itertools import product

from mining_settlement import CoinSpec, HardwareSpec, PlanSpec, SettlementService

HOURS = 720
SECONDS = 720 * 3600
UNITS = {"H/s": 1, "MH/s": 10**6, "GH/s": 10**9, "TH/s": 10**12}

BTC = CoinSpec("1000000", "4294967296", "6.25", "30000")
ZERO_COIN = CoinSpec("1000000", "4294967296", "0", "30000")


def hw(coin, pool_id, hashrate, unit="TH/s", watts="0"):
    return HardwareSpec(coin, pool_id, hashrate, unit, watts)


# ---------------------------------------------------------------------------
# 独立参考实现：按题面公式重算账单与占比，再枚举全部合法分币结果取最优。
# ---------------------------------------------------------------------------

def _round_cents(dollars: Fraction) -> int:
    cents = dollars * 100
    return (cents.numerator * 2 + cents.denominator) // (2 * cents.denominator)


def reference_settlement(plans):
    """返回 {plan_id: (revenue_cents, elec_cents, {pool_id: fee_cents})}。"""
    plan_ids = sorted(p.plan_id for p in plans)
    usage = {}
    revenue = {}
    pool_rev = {}
    pool_cnt = {}
    for p in plans:
        u = Fraction(0)
        r = Fraction(0)
        pr = {}
        pc = {}
        for h in p.hardware:
            d = Fraction(h.coin.difficulty)
            k = Fraction(h.coin.diff_one_hashes)
            hr = Fraction(h.hashrate) * UNITS[h.unit]
            rev = hr * SECONDS * Fraction(h.coin.block_reward) * Fraction(h.coin.price_usd) / (d * k)
            r += rev
            u += Fraction(h.watts) * HOURS / 1000
            pr[h.pool_id] = pr.get(h.pool_id, Fraction(0)) + rev
            pc[h.pool_id] = pc.get(h.pool_id, 0) + 1
        usage[p.plan_id] = u
        revenue[p.plan_id] = r
        pool_rev[p.plan_id] = pr
        pool_cnt[p.plan_id] = pc

    total_u = sum(usage.values(), Fraction(0))
    first = min(total_u, 1000)
    second = min(max(total_u - 1000, Fraction(0)), 1000)
    rest = max(total_u - 2000, Fraction(0))
    elec_bill = _round_cents(first * Fraction("0.1") + second * Fraction("0.2") + rest * Fraction("0.3"))

    pool_ids = sorted({pid for p in plans for h in p.hardware for pid in [h.pool_id]})
    bills = [elec_bill]
    weights = []
    weights.append(
        [usage[pid] / total_u if total_u > 0 else Fraction(0) for pid in plan_ids]
    )
    for pool in pool_ids:
        total_rev = sum((pool_rev[pid].get(pool, Fraction(0)) for pid in plan_ids), Fraction(0))
        bills.append(_round_cents(total_rev / 100 + 3))
        if total_rev > 0:
            weights.append([pool_rev[pid].get(pool, Fraction(0)) / total_rev for pid in plan_ids])
        else:
            total_cnt = sum(pool_cnt[pid].get(pool, 0) for pid in plan_ids)
            weights.append([Fraction(pool_cnt[pid].get(pool, 0), total_cnt) for pid in plan_ids])

    n, m = len(plan_ids), len(bills)
    ideals = [[bills[j] * weights[j][i] for j in range(m)] for i in range(n)]
    floors = [[x.numerator // x.denominator for x in row] for row in ideals]
    fracs = [[ideals[i][j] - floors[i][j] for j in range(m)] for i in range(n)]
    free = [(i, j) for i in range(n) for j in range(m) if fracs[i][j] > 0]

    best = None
    for bits in product([0, 1], repeat=len(free)):
        up = dict(zip(free, bits))
        actual = [[floors[i][j] + up.get((i, j), 0) for j in range(m)] for i in range(n)]
        if any(sum(actual[i][j] for i in range(n)) != bills[j] for j in range(m)):
            continue
        legal = True
        for i in range(n):
            t = sum(ideals[i])
            lo = t.numerator // t.denominator
            hi = lo if t.denominator == 1 else lo + 1
            if not (lo <= sum(actual[i]) <= hi):
                legal = False
                break
        if not legal:
            continue
        dev = sum(abs(actual[i][j] - ideals[i][j]) for i in range(n) for j in range(m))
        seq = tuple(actual[i][j] for i in range(n) for j in range(m))
        key = (dev, seq)
        if best is None or key < best[0]:
            best = (key, actual)
    assert best is not None
    actual = best[1]

    result = {}
    for i, pid in enumerate(plan_ids):
        fees = {
            pool: actual[i][1 + c]
            for c, pool in enumerate(pool_ids)
            if pool in pool_rev[pid]
        }
        result[pid] = (_round_cents(revenue[pid]), actual[i][0], fees)
    return result


def assert_matches_reference(testcase, svc_result, plans):
    expected = reference_settlement(plans)
    testcase.assertEqual(
        sorted(p.plan_id for p in svc_result.plans), sorted(expected.keys())
    )
    for p in svc_result.plans:
        rev, elec, fees = expected[p.plan_id]
        testcase.assertEqual(p.gross_revenue_cents, rev, f"plan {p.plan_id} 收入")
        testcase.assertEqual(p.electricity_cents, elec, f"plan {p.plan_id} 电费")
        testcase.assertEqual(p.pool_fee_cents, fees, f"plan {p.plan_id} 矿池费")


# ---------------------------------------------------------------------------

class BruteForceConsistencyTest(unittest.TestCase):
    """小规模结果与全部合法分币结果中的最优结果一致。"""

    def test_fixed_group_matches_reference(self):
        plans = [
            PlanSpec(1, (hw(BTC, 1, "100", "TH/s", "3000"), hw(BTC, 2, "40", "GH/s", "120"))),
            PlanSpec(2, (hw(BTC, 1, "55.5", "TH/s", "1777"),)),
            PlanSpec(3, (hw(BTC, 2, "900", "MH/s", "65"), hw(BTC, 1, "2", "GH/s", "33"))),
        ]
        svc = SettlementService()
        result = svc.settle_group(plans)
        assert_matches_reference(self, result, plans)

    def test_random_groups_match_reference(self):
        rng = random.Random(20261005)
        for _ in range(30):
            plans = []
            for pid in range(1, rng.randint(2, 4)):
                units = []
                for _ in range(rng.randint(1, 3)):
                    coin = CoinSpec(
                        difficulty=str(rng.randint(1, 10**6)),
                        diff_one_hashes=str(rng.randint(1, 10**9)),
                        block_reward=str(rng.choice([0, rng.uniform(0, 50)])),
                        price_usd=str(rng.choice([0, rng.uniform(0, 60000)])),
                    )
                    units.append(hw(
                        coin,
                        rng.randint(1, 3),
                        str(rng.uniform(0, 500)),
                        rng.choice(list(UNITS)),
                        str(rng.uniform(0, 4000)),
                    ))
                plans.append(PlanSpec(pid, tuple(units)))
            svc = SettlementService()
            result = svc.settle_group(plans)
            assert_matches_reference(self, result, plans)

    def test_tie_break_is_deterministic(self):
        # 三个完全相同的方案：电费理想值 17000/3，两个向上一个向下，
        # 偏差并列时按方案 ID 升序取字典序最小 -> 方案 1 取较小值。
        plans = [PlanSpec(pid, (hw(ZERO_COIN, 7, "10", "TH/s", "625"),)) for pid in (1, 2, 3)]
        svc = SettlementService()
        result = svc.settle_group(plans)
        elec = [p.electricity_cents for p in result.plans]
        self.assertEqual(elec, [5666, 5667, 5667])
        self.assertEqual(sum(elec), 17000)
        # 重复结算结果完全一致
        again = SettlementService().settle_group(plans)
        self.assertEqual(result, again)
        self.assertEqual(svc.current_result(), result)


class ZeroRevenuePoolTest(unittest.TestCase):
    """零收入矿池：固定费按接入硬件台数分摊，未接入者不承担。"""

    def test_split_by_hardware_count(self):
        plans = [
            PlanSpec(1, (hw(ZERO_COIN, 9, "10"),)),                       # 1 台
            PlanSpec(2, (hw(ZERO_COIN, 9, "10"), hw(ZERO_COIN, 9, "20"))),  # 2 台
            PlanSpec(3, tuple(hw(ZERO_COIN, 9, "10") for _ in range(4))),   # 4 台
            PlanSpec(4, (hw(BTC, 10, "100", "TH/s"),)),                   # 不接入 9 号池
        ]
        svc = SettlementService()
        result = svc.settle_group(plans)
        fees = {p.plan_id: p.pool_fee_cents for p in result.plans}
        # 9 号池账单固定 300 分，按 1:2:4 分摊，偏差最小 -> 43, 86, 171
        self.assertEqual(fees[1][9], 43)
        self.assertEqual(fees[2][9], 86)
        self.assertEqual(fees[3][9], 171)
        self.assertNotIn(9, fees[4])
        self.assertEqual(sum(f[9] for f in fees.values() if 9 in f), 300)
        # 全部功耗为 0，电费为 0
        self.assertTrue(all(p.electricity_cents == 0 for p in result.plans))
        assert_matches_reference(self, result, plans)


class ModifyDeleteRestoreTest(unittest.TestCase):
    """修改、删除、恢复方案导致其他方案费用变化；恢复后逐项一致。"""

    def setUp(self):
        self.plans = [
            PlanSpec(1, (hw(BTC, 1, "100", "TH/s", "3000"),)),
            PlanSpec(2, (hw(BTC, 1, "50", "TH/s", "1500"), hw(BTC, 2, "10", "TH/s", "800"))),
            PlanSpec(3, (hw(BTC, 2, "80", "TH/s", "2400"),)),
        ]

    def test_modify_delete_restore(self):
        svc = SettlementService()
        original = svc.settle_group(self.plans)

        # 替换方案 2：功耗大增，其他方案的电费分摊随之改变
        bigger = PlanSpec(2, (hw(BTC, 1, "50", "TH/s", "15000"), hw(BTC, 2, "10", "TH/s", "8000")))
        modified = svc.replace_plan(bigger)
        self.assertNotEqual(
            original.plans[0].electricity_cents, modified.plans[0].electricity_cents
        )
        self.assertNotEqual(
            original.plans[2].electricity_cents, modified.plans[2].electricity_cents
        )

        # 删除方案 2：剩余方案的电费与矿池费都重新分摊
        deleted = svc.delete_plan(2)
        self.assertEqual([p.plan_id for p in deleted.plans], [1, 3])
        self.assertNotEqual(
            modified.plans[0].electricity_cents, deleted.plans[0].electricity_cents
        )
        # 方案 1 只接入 1 号池，不承担 2 号池费用；方案 3 反之
        self.assertNotIn(2, deleted.plans[0].pool_fee_cents)
        self.assertNotIn(1, deleted.plans[1].pool_fee_cents)

        # 恢复原方案：结果逐项一致，无舍入漂移
        restored = svc.replace_plan(self.plans[1])
        self.assertEqual(restored, original)
        self.assertEqual(svc.current_result(), original)

        # 整组重新结算同样一致
        self.assertEqual(SettlementService().settle_group(self.plans), original)

    def test_restore_after_delete_and_readd(self):
        svc = SettlementService()
        original = svc.settle_group(self.plans)
        svc.delete_plan(3)
        svc.delete_plan(1)
        svc.replace_plan(self.plans[0])
        svc.replace_plan(self.plans[2])
        self.assertEqual(svc.current_result(), original)


class GroupTotalsTest(unittest.TestCase):
    """组级收入、费用及净利润分别等于各方案之和。"""

    def test_totals(self):
        svc = SettlementService()
        result = svc.settle_group([
            PlanSpec(1, (hw(BTC, 1, "100", "TH/s", "3000"),)),
            PlanSpec(2, (hw(BTC, 1, "50", "TH/s", "1500"), hw(BTC, 2, "10", "TH/s", "800"))),
            PlanSpec(3, (hw(BTC, 2, "80", "TH/s", "2400"),)),
        ])
        self.assertEqual(
            result.total_gross_revenue_cents,
            sum(p.gross_revenue_cents for p in result.plans),
        )
        self.assertEqual(
            result.total_electricity_cents,
            sum(p.electricity_cents for p in result.plans),
        )
        pool_totals = result.total_pool_fee_cents
        for pool_id, total in pool_totals.items():
            self.assertEqual(
                total, sum(p.pool_fee_cents.get(pool_id, 0) for p in result.plans)
            )
        self.assertEqual(
            result.total_cost_cents, sum(p.total_cost_cents for p in result.plans)
        )
        self.assertEqual(
            result.total_net_profit_cents,
            result.total_gross_revenue_cents - result.total_cost_cents,
        )
        # 净利润允许为负：零收入高功耗方案
        svc2 = SettlementService()
        r2 = svc2.settle_group([PlanSpec(1, (hw(ZERO_COIN, 5, "10", "TH/s", "5000"),))])
        self.assertLess(r2.plans[0].net_profit_cents, 0)


class ValidationTest(unittest.TestCase):
    def test_invalid_inputs(self):
        svc = SettlementService()
        with self.assertRaises(ValueError):
            svc.settle_group([PlanSpec(1, ()), PlanSpec(1, ())])
        with self.assertRaises(ValueError):
            svc.settle_group([PlanSpec(1, (hw(CoinSpec("0", "1", "1", "1"), 1, "1"),))])
        with self.assertRaises(ValueError):
            svc.settle_group([PlanSpec(1, (hw(CoinSpec("1", "0", "1", "1"), 1, "1"),))])
        with self.assertRaises(ValueError):
            svc.settle_group([PlanSpec(1, (hw(BTC, 1, "1", "PH/s"),))])
        with self.assertRaises(ValueError):
            svc.settle_group([PlanSpec(1, (hw(BTC, 1, "-1"),))])
        with self.assertRaises(ValueError):
            svc.settle_group([PlanSpec(1, (hw(BTC, 1, "abc"),))])
        with self.assertRaises(KeyError):
            svc.delete_plan(99)


if __name__ == "__main__":
    unittest.main()
