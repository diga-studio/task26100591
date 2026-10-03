"""数据类型定义：币种、硬件、方案、结算结果。"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Hashable

# 哈希速率单位 -> 每秒哈希次数
HASH_UNITS = {
    "H/s": 10**0,
    "MH/s": 10**6,
    "GH/s": 10**9,
    "TH/s": 10**12,
}


def parse_decimal(value) -> Fraction:
    """将十进制字符串/整数/Fraction 精确解析为 Fraction。"""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise TypeError("布尔值不是合法的数值输入")
    if isinstance(value, int):
        return Fraction(value)
    from decimal import Decimal

    text = str(value).strip()
    try:
        return Fraction(Decimal(text))
    except Exception as exc:
        raise ValueError(f"无法解析的十进制数值: {value!r}") from exc


def round_half_up_cents(value: Fraction) -> int:
    """将精确值（单位：分）四舍五入到整数分。"""
    if value >= 0:
        return (2 * value.numerator + value.denominator) // (2 * value.denominator)
    return -((-2 * value.numerator + value.denominator) // (2 * value.denominator))


@dataclass(frozen=True)
class Coin:
    coin_id: Hashable
    difficulty: Fraction          # D > 0
    hashes_per_block: Fraction    # K：难度1时每块预期哈希次数 > 0
    block_reward: Fraction        # R
    price_usd: Fraction           # P


@dataclass(frozen=True)
class Hardware:
    coin_id: Hashable
    pool_id: int
    hashrate_hs: Fraction  # 每秒哈希次数
    watts: Fraction


@dataclass(frozen=True)
class Plan:
    plan_id: int
    hardware: tuple[Hardware, ...]


def make_hardware(coin_id, pool_id: int, hashrate, unit: str, watts) -> Hardware:
    """便捷构造：hashrate/watts 为十进制字符串，unit 如 'MH/s'。"""
    if unit not in HASH_UNITS:
        raise ValueError(f"不支持的哈希速率单位: {unit!r}")
    rate = parse_decimal(hashrate)
    power = parse_decimal(watts)
    if rate < 0 or power < 0:
        raise ValueError("哈希速率与功耗不能为负")
    return Hardware(
        coin_id=coin_id,
        pool_id=int(pool_id),
        hashrate_hs=rate * HASH_UNITS[unit],
        watts=power,
    )


@dataclass(frozen=True)
class PlanSettlement:
    plan_id: int
    gross_revenue_cents: int
    electricity_fee_cents: int
    pool_fees_cents: dict  # pool_id -> cents，按 pool_id 升序
    total_fees_cents: int
    net_profit_cents: int


@dataclass(frozen=True)
class GroupSettlement:
    group_id: Hashable
    plans: tuple  # PlanSettlement，按 plan_id 升序
    total_gross_revenue_cents: int
    total_electricity_fee_cents: int
    total_pool_fees_cents: dict  # pool_id -> cents
    total_fees_cents: int
    total_net_profit_cents: int
