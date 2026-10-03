# task26100591

## 离线矿场联合结算库

纯 Python 实现，无外部依赖，输入与当前结算结果保存在内存中，由上层直接调用。

### 用法

```python
from mining_settlement import CoinSpec, HardwareSpec, PlanSpec, SettlementService

btc = CoinSpec(difficulty="1000000", diff_one_hashes="4294967296",
               block_reward="6.25", price_usd="30000")

svc = SettlementService()
result = svc.settle_group([
    PlanSpec(1, (HardwareSpec(btc, pool_id=1, hashrate="100", unit="TH/s", watts="3000"),)),
    PlanSpec(2, (HardwareSpec(btc, 1, "50", "TH/s", "1500"),
                 HardwareSpec(btc, 2, "10", "TH/s", "800"))),
])
svc.replace_plan(PlanSpec(2, (...)))   # 替换/重新加入一个方案并重算
svc.delete_plan(2)                     # 删除一个方案并重算
result = svc.current_result()          # 读取当前结果
```

- 数值以十进制字符串输入，内部用 `Fraction` 精确计算，仅在规定落分处四舍五入到整数分。
- 输出金额均为整数分；`GroupResult` / `PlanResult` 提供组级与方案级的收入、电费、
  各矿池费用及净利润，组级总额恒等于各方案之和。
- 每次变更都基于保存的原始输入整体重算，恢复原输入即恢复原结算，无舍入漂移。

### 结构

- `mining_settlement/models.py`：输入/输出数据模型。
- `mining_settlement/allocator.py`：费用分摊器（最小费用流求最优取整 + 字典序破并列）。
- `mining_settlement/service.py`：结算服务（账单计算、占比分摊、状态管理）。
- `tests/test_settlement.py`：自动化测试（含与暴力枚举全部合法分币结果的对拍）。

### 运行测试

```bash
python3 -m pytest tests/ -q
```
