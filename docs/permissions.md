# 阶段2权限矩阵

角色与 scope 来自数据库 principal，工具参数不能声明或扩大 role。阶段2没有登录页面、Cookie 或正式 RBAC，也没有 dev principal header。

| 主体 | 允许范围 |
| --- | --- |
| `customer:self` | 自己的订单/物流/退款资格/模拟退款请求；当前知识查询 |
| `store_operator` | 自己 store 的六个 business 工具和知识工具 |
| `agent:customer_service` | 客服白名单：订单、物流、退款资格、模拟退款请求和知识工具 |
| `agent:operations` | 订单/物流、库存、销售指标、退款资格和知识工具；不能创建退款或调用媒体 |
| `agent:media` | media 工具白名单；不能调用 business 工具 |
| `admin` | 仅契约中明确列出的全部工具，可跨 store |

工具白名单拒绝、customer 越权和跨 store 访问返回 `FORBIDDEN` 并写审计。未知或 inactive principal 返回 `UNAUTHENTICATED`。
