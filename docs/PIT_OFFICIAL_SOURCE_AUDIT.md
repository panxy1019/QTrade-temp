# 独立交易所历史股票池来源核验

核验时间：2026-10-26。本记录是来源探测，不是完整历史 PIT 验收。
原始文件保存在集群 `pit-independent-sources/`；不替换 BaoStock 原文，
不加入当前 Qlib 来源快照或回测，也不改变任何认证标记。

## 已执行的接口探测

复用集群已有 AKShare 中的官方接口定义，以有限超时和响应大小直接保存原文。
本次未使用第三方整理的退市列表或搜索结果摘要作为股票池认证输入。

| 来源 | 本次结果 | 限制 |
| --- | --- | --- |
| [上交所终止上市列表](https://www.sse.com.cn/assortment/stock/list/delisting/)的 `query.sse.com.cn/commonQuery.do` | 保存 161 行；返回分页总数也是 161，日期字段范围为 2001-04-25 至 2026-10-19 | 请求 STOCK_TYPE 为 1,2,8；161 行不能直接称为 161 只 A 股或全市场历史股票池 |
| [深交所暂停/终止上市列表](https://www.szse.cn/market/stock/suspend/index.html)的 `api/report/ShowReport` | HTTPError，没有取得可验证表格 | 不将失败解释为零条记录，也不以其他网站列表替代认证 |

上交所原文 SHA256：
`d5e5dec58882aef4aba8be707087e9c3483ee1be9c2d1fcb6070e1ae9abc8990`。
本次返回包括 `601198`，其日期字段与
[上交所终止上市公告](https://www.sse.com.cn/disclosure/announcement/listing/stock/c/c_20261012_10834560.shtml)
所列 2026-10-19 对应；这只是一个交叉核对点，不证明所有记录都正确。
完整探测身份及失败状态见
[采集队列验收记录](evidence/pit-queue-acceptance-20261026.json)。

AKShare 本地函数将上交所的 `DELIST_DATE` 显示为“暂停上市日期”，本轮保留
原始字段，不直接信任展示名称。公告发布日期、退市整理期开始日、最后交易日和
实际摘牌日须分开核对；不能从其中一个字段补造其他日期。

## 为什么尚不能据此认证 PIT

本次接收的是现在查询到的列表。即使包含历史上市/退市日期，也没有保存
过去每个决策时点看到的版本，更没有补齐退市股票价格、公司行为与状态变化。
`historical_available_at` 仍为空，`historical_pit_certified=false`。

下一步需要核对证券身份及重复记录、A/B 股与板块类型、原始日期语义、历史
公告版本和可得时间，再与供应商主表及退市前行情逐项对账。
深交所来源失败也尚未解决，不能宣称全沪深股票池已独立覆盖。
完整历史来源的接入条件仍需确认；当前扩容不会消除原 2026 年流动性样本的偏差。

## 复查入口

集群已安装的 AKShare 函数为 `stock_info_sh_delist(symbol="全部")` 和
`stock_info_sz_delist(symbol="终止上市公司")`。复查前检查其源码及官方接口，
为网络请求设置超时，原文、查询参数、接收时间和校验和一并保存。
接口结构可能变化；不能把重新运行后不同的结果冒充本次快照。

所有探测保持 `LIVE_TRADING_ENABLED=false`，不连接券商或真实交易网关。
