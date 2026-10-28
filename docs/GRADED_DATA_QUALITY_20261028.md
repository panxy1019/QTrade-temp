# 分级数据质量：诊断接入与默认关闭的审批变更

本增量继续 STEP1，并将完整历史 PIT、退市、复权和多年连续数据作为独立长期
任务。缺少不相关证券或频率的数据不应被解释为整个平台不可研发；同时，异常
模型输入和未知交易状态仍不能被诊断标签替代或放行。

## 已审计的原合同

| 环节 | 原合同和本次保持的边界 |
| --- | --- |
| 采集 | 主键含 universe / frequency / symbol / trading_day，失败记录和原始响应保留；日线与分钟任务独立 |
| Qlib 输入 | 原日版本已允许失败证券隔离、保留历史、排除不合格当日输入；仍须验证原始批次、收盘、来源、数据版本和全局完整性 |
| 额外整池等待 | `daily_tasks` 在存在当前主表时要求全部1500目标的日任务终态，即使数据集实际需要990股；这是待审批修改点 |
| 资源协调 | `collection_idle` 是活跃采集 owner 的资源协调，不是“每个证券数据必须完整”；两处检查继续保留 |
| 模型 | 新正式 serving head 会被当前已批准模型的自动预测使用；因此新的数据范围批准必须明确包括 PREDICTION，而非仅因子研究 |
| PAPER | 继续使用原始 portfolio → risk → OMS → execution → gateway，以及实际已知交易约束、对账、暂停和恢复规则 |

## 诊断通道

`build_data_quality_summary` 只读采集 SQLite、已验证近期 vendor 记录和发行人
PDF原字节。输出按证券、频率和交易日分组的不可变版本，并更新独立公开指针。
缺失、损坏的可选状态来源独立隔离；质量摘要故障不能停止行情发布或隐藏行情目录。

- `SUSPENDED`：同日 vendor tradestatus=0，加发行人停牌起始公告的原始哈希。
  这是明确标注的逐日公共源佐证，不等同完整交易所事件链或历史PIT认证。
- `DATA_GAP`：存在不完整行情窗口，或 vendor 同日报告交易但行情不完整。
- `UNKNOWN`：缺少同日状态、来源冲突或停牌与已接受行情矛盾；不推断复牌。
- `COMPLETE`：仅表示该证券该频率该日要求的价格内容完整，不授予交易权限。

002827、600621、605258 的公告只支持10月26日起始事实。10月26/27日的 vendor
状态实际获取于10月28日；历史可得时间仍为 null。10月28日不会仅根据此前公告
被延伸标记为停牌，也不会从一根新行情反推出一个未经核验的复牌日期。

API `GET /api/unified/quality` 单独绑定实际公开 Git commit、正文SHA256、
获取时钟、摘要年龄；网页数据页分开展示状态窗口、实际获取时间、队列覆盖与摘要
已逐文件校验的数量。所有输出 `production_admission_effect=NONE`、
`execution_admitted=false`。它不回写旧PIT或过去的PAPER决策。

## 待人工审批的两项规则（默认关闭）

没有真实人工回复和操作员封存审批文件时，以下代码保持关闭；单元测试内的
`test-only-human` 不是生产批准。本地自哈希用于完整性，非人类身份的密码学签名。

1. **数据集必需日频范围**：`approved-scope-policy.json` 明确批准固定数据集
   股票集合哈希、当前 universe、起效日期，以及 DAILY_RESEARCH、FACTOR_EVALUATION、
   PREDICTION 三个用途。仅这些必需日任务参与终态等待；额外ETF日任务及分钟失败
   不构成研究范围数据质量否决。必需任务缺行/非终态仍等待，失败证券仍排除不合格
   当日输入。模型恢复、原批次和来源校验、benchmark、资源锁、预算均保持。
   新请求和版本绑定审批哈希，旧冻结请求不会被改写；若已有当天旧请求则继续拒绝
   不同上下文，需要单独解决，不能重置或冒用旧 revision。
2. **停牌采集重试暂缓**：独立 `approved-retry-policy.json`，仅列出的股票、
   同一频率与日期、失败且无任何已接受当日价格的任务可被暂缓。必须依赖本地验证
   构建且不超过1200秒的摘要、24小时内实际获取的逐日状态和PDF来源哈希，最多
   暂缓6小时。任务状态、attempts、backoff、raw、manifest 均不变，不产生成功记录。
   token 绑定完整失败行指纹、desired_end、来源/审批哈希和过期时间；claim事务内
   用实际时钟和当前任务再次校验，过期/损坏/新尝试/新截止时间自动恢复原重试。
   PLANNED、INTERRUPTED、部分已接受行情及未知状态不被暂缓。

两项审批均不允许模型激活、PAPER约束变更、账户恢复、历史改写或实盘。
不移除活跃采集owner的资源协调，不执行 prior-session repair apply。

## 512580 独立审计

10月27日原始新浪响应和10月28日扩大请求范围的响应，均只有45个同日标签；
缺10:00、11:30、14:30。解析及日期/完成K线过滤保持同样45行，缺失发生在解析前。
更大 datalen 不恢复这三行。真实 `collect_batch` 的无写入回放仍拒绝不完整窗口。
本机最初东财5分钟/新浪1分钟核验网络失败，原失败保留。集群03:02 UTC实际取得
东财原生5分钟HTTP200/3951字节/48行；三个新浪缺失标签均由东财报告量额为0。
45重叠close相同，新浪volume逐行是东财100倍；13行OHLC存在差异，成交额逐行亦有
小差异。这支持供应商零成交窗口表示差异的解释，不认证零真实成交或同口径。
腾讯独立请求DNS失败保留。原新浪数据仍PARTIAL，不混源补三根，不影响独立日频。

## 验证、复现与当前限制

独立审计位于[新证据目录](evidence/graded-quality-20261028/)；原始失败XML和中间回归均单独保留，成功不会覆盖
失败。最终源版本、Windows/Linux测试、实际服务升级、公开API和网页结果须分别
记录，不能用本地测试代替部署或默认域名交付。下述命令在仓库根目录运行：

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
python -B -m pytest tests/test_data_quality.py tests/test_data_quality_policy.py tests/test_collection_retry_policy.py tests/test_recent_status.py tests/test_daily_collection_binding.py tests/test_qlib_data_service.py tests/test_build_qlib_daily_version.py tests/test_prepare_qlib_incremental.py tests/test_unified_queue.py tests/test_collect_unified_data.py tests/test_unified_maintenance.py -q --basetemp=E:/qtest-quality-reproduce
python -m mypy --platform linux src
```

Windows 全扩展回归包含POSIX专属跳过项，应记录实际skip数；Linux另行实测。
网页全量首次并发运行有3项旧组件超时/异步失败，原始XML保留，串行复验另记，
不修改旧组件或测试超时预算来掩盖。前端lint及Vercel构建已通过。

上海10:54只读快照：近期状态280/990；27日日线1497、分钟1496/1500；28日分钟
1497成功、3失败。ACTIVE29589b44、原批准和27日正式版本未被本增量修改。今天
收盘尚未发生，真实28日E会话、会计、公开交付，以及收盘后新版本/预测/候选验证/
signals 的 C/D 验收仍待真实结果，不接受整个STEP1。

11:43 后续用户指令已允许 STEP2 的设计、接口审计和非生产测试准备与 STEP1
并行，执行计划见[修订计划](../plans/STEP1_STEP2_EXECUTION.md)。因子评价采用
当前合格的日频研究范围；完整十年PIT不作为准备工作的总开关。每个实验仍须
披露样本选择/幸存者偏差、历史可知性、复权和交易约束限制。未来改善PIT后
追加绑定新版本的实验并关联原结果，不覆盖旧实验或过去交易决策。
