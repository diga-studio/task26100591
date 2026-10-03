# mining-settlement

离线矿场联合结算库（纯 Python，无外部依赖，全部状态保存在内存中）。
解决小矿场多方案共用电表与矿池账户时的联合结算问题：阶梯电价按全组
用电量统一计价后分摊，矿池固定费只收取一次后分摊，避免分别计算再相加
造成的错误。

## 功能

- 整组结算：`settle_group(group_id, plans)`
- 替换/新增一个方案并重新结算：`replace_plan(group_id, plan)`
- 删除一个方案并重新结算：`delete_plan(group_id, plan_id)`
- 读取当前结果：`get_settlement(group_id)`

每次修改都从保存的精确输入整体重算，恢复原输入即恢复原结算，
不会累积舍入漂移；不同结算组互不影响。

## 计算规则

- 数值以十进制字符串输入，内部用 `Fraction` 精确计算，仅在规定的
  金额落分处四舍五入到整数分（半进）。
- 硬件月毛收入 = 每秒哈希数 × 2592000 × R × P / (D × K)；
  月用电量(kWh) = 瓦数 × 720 / 1000。
- 全组阶梯电价：前 1000 kWh 0.10 美元/度，接下来 1000 kWh 0.20，
  其余 0.30；总电费按各方案用电量占比分摊（总用电为 0 则电费为 0）。
- 每个有硬件接入的矿池收取该池全组未舍入毛收入的 1% + 3 美元固定费；
  按各方案在该池的毛收入占比分摊，池收入为 0 时按接入台数分摊。
- 电费与每个矿池费各为一个费用项：先各自四舍五入到整数分，再求满足
  约束（每项合计等于账单、每方案总费用取理想总和的上下整数）的整数
  分摊中总绝对偏差最小者；并列时按方案 ID 升序、行内电费在前矿池费
  按矿池 ID 升序展开的金额序列字典序最小者（最小费用流 + 逐格定 tie）。
- 各方案毛收入单独四舍五入到整数分；净利润 = 收入 - 实际分摊费用，
  允许为负；组级收入/费用/净利润恒等于各方案之和。

## 用法

```python
from mining_settlement import SettlementEngine, Plan, make_hardware

eng = SettlementEngine()
eng.define_coin("BTC", difficulty="1000000", hashes_per_block="4294967296",
                block_reward="6.25", price_usd="30000")

plans = [
    Plan(1, (make_hardware("BTC", pool_id=10, hashrate="100", unit="TH/s", watts="3250"),)),
    Plan(2, (make_hardware("BTC", pool_id=10, hashrate="50", unit="TH/s", watts="1500"),)),
]
result = eng.settle_group("group-1", plans)
for p in result.plans:
    print(p.plan_id, p.gross_revenue_cents, p.electricity_fee_cents,
          p.pool_fees_cents, p.net_profit_cents)

eng.replace_plan("group-1", Plan(2, (make_hardware("BTC", 10, "60", "TH/s", "1800"),)))
eng.delete_plan("group-1", 1)
current = eng.get_settlement("group-1")
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

覆盖：小规模结果与全部合法分币枚举的最优结果一致（含 30 个固定种子
随机用例）、并列时字典序确定性、零收入矿池按台数分摊、修改/删除/恢复
方案后其他方案费用变化且恢复后逐项一致、阶梯电价边界、组间独立。
