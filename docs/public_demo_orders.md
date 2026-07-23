# 公共演示订单

公共演示订单由`data/manifests/public_demo_orders_v1.json`版本化管理。前端不得硬编码订单UUID，必须通过受保护接口读取。

## 接口

`GET /api/v1/demo/orders`

要求：

- 必须登录。
- 仅允许`public_demo`、授权`store_operator`或`admin`访问。
- public_demo只返回当前Principal store_scope内、manifest列出的固定订单。
- 不接受客户端传入任意`store_id`扩大范围。
- 不返回电话、地址、密码、Token、CSRF或完整客户信息。
- 稳定按manifest顺序排序。

返回字段：

- `demo_case_id`
- `order_id`
- `masked_order_id`
- `safe_label`
- `order_status`
- `logistics_status`
- `demo_scenario`
- `description`
- `scenario_description`
- `store_id`
- `ordered_at`
- `allowed_actions`

## 工作台显示规则

- `public_demo`：显示“选择演示订单”下拉框、脱敏订单号、场景说明、清除选择和示例任务按钮；不显示任意订单手动输入。
- `admin`：显示“选择演示订单”下拉框，同时保留“手动输入订单编号”。
- `store_operator`：显示当前`store_scope`内的“选择演示订单”下拉框，同时保留“手动输入订单编号”。

下拉选项只用于填充请求中的完整`order_id`，不会自动提交任务。前端不得硬编码订单ID。

## 当前场景

当前manifest含5个模拟店铺1订单：

| demo_case_id | 场景 | 能力 |
| --- | --- | --- |
| `normal_order` | 正常订单演示 | `get_order` |
| `normal_logistics` | 正常物流演示 | `get_order`, `get_logistics` |
| `logistics_delay` | 物流延迟演示 | `get_order`, `get_logistics` |
| `refund_eligible` | 符合退款资格演示 | `check_refund_eligibility` |
| `refund_ineligible` | 不符合退款资格演示 | `check_refund_eligibility` |

店铺1的固定seed没有显式`logistics_delay`标记；当前延迟演示选用店铺1中稳定存在、仍处于运输中的订单，并在演示任务中基于只读物流字段和预计送达时间解释延迟。没有修改Stage2 seed、订单、物流或Stage5数据。

## 校验

测试覆盖：

- manifest只包含模拟店铺1。
- 所有订单真实存在并属于模拟店铺1。
- 接口输出不含敏感字段。
- 输出稳定按manifest排序。
- public_demo不能读取manifest外订单。
- public_demo跨Session不能读取run和SSE事件。
