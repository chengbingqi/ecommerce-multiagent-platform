# 运营分析看板

开发入口为 `/operations`，通过同源 `/api/v1/runs` 创建显式 `operations` run。页面包含店铺、起止日期、主题、粒度和可选问题，并提供 loading、failed、no_data 状态。

结果严格分为：确定性 KPI/趋势、规则异常、模型解释、运营建议；证据区域展示最小必要且店铺隔离的引用。页面标注所有数据均为固定模拟数据，建议不代表已执行业务动作。

阶段4仍使用 development/test principal 注入进行本地演示。production gate 禁止该机制；该页面不构成正式登录或 RBAC。
