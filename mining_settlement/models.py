"""数据模型：结算输入与输出的类型定义。

所有数值输入均为十进制字符串，内部以 Fraction 精确计算。
所有金额输出均为整数分（cents）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CoinSpec:
    """币种参数。

    difficulty:        难度 D（>0）
    diff_one_hashes:   难度 1 时每块预期哈希次数 K（>0）
    block_reward:      区块奖励 R（>=0）
    price_usd:         美元币价 P（>=0）
    """

    difficulty: str
    diff_one_hashes: str
    block_reward: str
    price_usd: str


@dataclass(frozen=True)
class HardwareSpec:
    """单台硬件：指定币种、矿池、算力与功耗。"""

    coin: CoinSpec
    pool_id: int
    hashrate: str
    unit: str = "MH/s"
    watts: str = "0"


@dataclass(frozen=True)
class PlanSpec:
    """一个挖矿方案：稳定整数 ID + 多台硬件，整月运行 720 小时。"""

    plan_id: int
    hardware: tuple[HardwareSpec, ...] = ()


@dataclass(frozen=True)
class PlanResult:
    """单个方案的结算结果，金额单位：整数分。"""

    plan_id: int
    gross_revenue_cents: int
    electricity_cents: int
    pool_fee_cents: dict[int, int] = field(default_factory=dict)

    @property
    def total_cost_cents(self) -> int:
        return self.electricity_cents + sum(self.pool_fee_cents.values())

    @property
    def net_profit_cents(self) -> int:
        return self.gross_revenue_cents - self.total_cost_cents


@dataclass(frozen=True)
class GroupResult:
    """整组结算结果。组级各项总额恒等于各方案之和。"""

    plans: tuple[PlanResult, ...] = ()

    @property
    def total_gross_revenue_cents(self) -> int:
        return sum(p.gross_revenue_cents for p in self.plans)

    @property
    def total_electricity_cents(self) -> int:
        return sum(p.electricity_cents for p in self.plans)

    @property
    def total_pool_fee_cents(self) -> dict[int, int]:
        totals: dict[int, int] = {}
        for p in self.plans:
            for pool_id, amount in p.pool_fee_cents.items():
                totals[pool_id] = totals.get(pool_id, 0) + amount
        return totals

    @property
    def total_cost_cents(self) -> int:
        return sum(p.total_cost_cents for p in self.plans)

    @property
    def total_net_profit_cents(self) -> int:
        return sum(p.net_profit_cents for p in self.plans)
