"""对外 API：整组结算、替换/删除方案并调整、读取当前结果。"""
from __future__ import annotations

from .core import compute_settlement
from .models import Coin, Plan, parse_decimal


class SettlementEngine:
    def __init__(self):
        self._coins = {}
        self._groups = {}  # group_id -> {"plans": {plan_id: Plan}, "result": GroupSettlement}

    def define_coin(self, coin_id, difficulty, hashes_per_block, block_reward, price_usd) -> None:
        """注册/更新币种参数，数值为十进制字符串。"""
        coin = Coin(
            coin_id=coin_id,
            difficulty=parse_decimal(difficulty),
            hashes_per_block=parse_decimal(hashes_per_block),
            block_reward=parse_decimal(block_reward),
            price_usd=parse_decimal(price_usd),
        )
        if coin.difficulty <= 0 or coin.hashes_per_block <= 0:
            raise ValueError("难度D与K必须大于0")
        if coin.block_reward < 0 or coin.price_usd < 0:
            raise ValueError("区块奖励与币价不能为负")
        self._coins[coin_id] = coin

    def settle_group(self, group_id, plans) -> object:
        """以给定方案列表整组结算，保存输入与结果，返回 GroupSettlement。"""
        plan_map = {}
        for plan in plans:
            if not isinstance(plan, Plan):
                raise TypeError("plans 必须是 Plan 对象")
            if plan.plan_id in plan_map:
                raise ValueError(f"方案ID重复: {plan.plan_id}")
            plan_map[plan.plan_id] = plan
        result = compute_settlement(group_id, self._coins, list(plan_map.values()))
        self._groups[group_id] = {"plans": plan_map, "result": result}
        return result

    def replace_plan(self, group_id, plan) -> object:
        """替换（或新增）一个方案并重新结算整组。"""
        state = self._require_group(group_id)
        if not isinstance(plan, Plan):
            raise TypeError("plan 必须是 Plan 对象")
        state["plans"][plan.plan_id] = plan
        return self._recompute(group_id, state)

    def delete_plan(self, group_id, plan_id: int) -> object:
        """删除一个方案并重新结算整组。"""
        state = self._require_group(group_id)
        if plan_id not in state["plans"]:
            raise KeyError(f"方案不存在: {plan_id}")
        del state["plans"][plan_id]
        return self._recompute(group_id, state)

    def get_settlement(self, group_id) -> object:
        """读取该组当前结算结果。"""
        return self._require_group(group_id)["result"]

    def _require_group(self, group_id):
        if group_id not in self._groups:
            raise KeyError(f"结算组不存在: {group_id!r}")
        return self._groups[group_id]

    def _recompute(self, group_id, state):
        # 从保存的精确输入整体重算，确定性结果，无舍入漂移累积
        result = compute_settlement(group_id, self._coins, list(state["plans"].values()))
        state["result"] = result
        return result
