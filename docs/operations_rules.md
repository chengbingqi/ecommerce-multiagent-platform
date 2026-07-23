# 运营异常规则 `operations_rules_v1.0.0`

异常只由确定性规则决定，并要求对应阶段2 scenario 记录实际存在。模型不能新增、取消或改变命中结果。

| rule_id | 场景 | 指标与阈值 | 严重度 |
| --- | --- | --- | --- |
| `OPS-STOCKOUT-001` | `stockout_spike` | `stockout_sku_count >= 1` | high |
| `OPS-REFUND-001` | `refund_spike` | `refund_rate >= 0.150000` | high |
| `OPS-LOGISTICS-001` | `logistics_delay` | `delayed_logistics_rate >= 0.100000` | medium |
| `OPS-CONVERSION-001` | `conversion_drop` | `conversion_rate <= 0.010000` | high |
| `OPS-CAMPAIGN-001` | `live_campaign` | `live_campaign_count >= 1` | low |

每个命中项包含 `anomaly_id`、rule、observed value、threshold、severity、解释和 evidence refs。固定测试覆盖五个正向场景、无异常对照、阈值边界、零分母、空数据和跨店铺证据拒绝。
