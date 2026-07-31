# 运营指标定义 `operations_metrics_v1.0.0`

统一使用 UTC `[start,end)`；单次范围最多 90 天。金额使用 `Decimal`/数据库 `Numeric`，按 `ROUND_HALF_UP` 保留 2 位；比例保留 6 位。零分母返回 `0.000000`，不产生无穷或模型估算。

| 指标 | 定义 | 单位 |
| --- | --- | --- |
| `order_count` | 期间内状态为 `shipped` 或 `delivered` 的订单数 | count |
| `gmv` | 上述订单 `total_amount` 之和 | CNY |
| `average_order_value` | `gmv / order_count` | CNY/order |
| `refund_count` | 期间内退款记录涉及的不同订单数 | count |
| `refund_amount` | 期间内退款记录金额之和 | CNY |
| `refund_rate` | `refund_count / order_count` | ratio |
| `visitor_count` | `SalesDaily.sessions` 之和 | count |
| `conversion_rate` | `SalesDaily.conversions / sessions` | ratio |
| `stockout_sku_count` | 当前可用库存 `on_hand-reserved <= 0` 的 SKU 数 | count |
| `low_stock_sku_count` | 当前可用库存为 1 至 10 的 SKU 数 | count |
| `delayed_logistics_count` | 期间订单中物流状态为 `delayed` 的数量 | count |
| `delayed_logistics_rate` | 延迟物流数 / 期间物流记录数 | ratio |
| `campaign_count` | 与期间有交集的活动数 | count |
| `live_campaign_count` | 上述活动中类型为 `live` 的数量 | count |

日粒度以 UTC 自然日聚合，周粒度以周一为桶起点。`previous_period` 使用紧邻且等长的前一窗口，`change=(current-previous)/previous`；前值为零时 change 为 `null`。每个 metric 与趋势点都携带数据库 evidence refs。

库存是当前快照，而非历史库存；字段名称和证据中明确这一限制，不伪造历史库存轨迹。
