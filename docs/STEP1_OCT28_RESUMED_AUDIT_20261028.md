# STEP1：Oct28恢复后的只读收口审计

状态：**E整体BLOCKED；12本当日观察与2本退休关闭凭证逐本通过；新日C/D前提未齐。**

本轮遵照用户恢复后的范围，仅执行现有只读审计、公共GET和独立证据写出。最新汇总
采用18:39已有快照；18:42另仅读取固定请求的重试ledger，没有重跑18:39的SQL。
没有服务控制、模型激活、PAPER恢复、手动重试、交易或生产文件写入；冻结目标、
旧归档、恢复claim保持原样。本文不更新共享状态文件，也不构成部署授权。

## 1. 真实E结果

17:39:19—17:39:26上海时间获取默认生产域名的前后deployment及forward-books原始
HTTP字节、headers和请求时间。相邻deployment内容相同，Git版本均为
`ae33a7c8c27ea721eb3be1e4430b562a44b67f91`。外层wrapper与提取的publication一致，
文件SHA256及规范化evidence hash均复验。

固定执行日`2026-10-28`、执行模型
`29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487`，
在真实UTC时钟下运行既有`9846a7f`审计器。其文件SHA256与该Git对象一致：
`a7634c1cbb5987ebdaa90bda06319405d8b975cb743a745048afa126aa8215f4`。

| 检查范围 | 实际结果 |
| --- | --- |
| 12本现役PAPER/SHADOW的Oct28观察 | 12/12 accepted，逐本failures为空 |
| 2本退休模型关闭证明 | 2/2 accepted；不算作Oct28观察，不重开账户 |
| 完整14本E | BLOCKED |
| 唯一总失败 | `FORWARD_AUDIT_PUBLICATION_INVALID_STALE_OR_DEGRADED` |
| 公共wrapper | `publication_available=true`、`service_fresh=true` |
| 公共publication | `DEGRADED`；`FORWARD_SERVICE_DATA_STALE` |

现有E同时复验目标/准入/原始市场证据、会话与账本重算及公共投影；局部通过不消除
总publication门禁。本轮没有修改审计器以追求PASSED，也未将新日准备不足隐藏。

6本现役PAPER各现金/NAV为100万元，订单、成交、持仓均0，收益/换手为0。6本SHADOW
的现金、NAV、收益、换手保持null，订单、成交、持仓为0。等权账户每本886个阻断，
其余现役账户每本50个，均为`UNKNOWN_TRADING_CONSTRAINTS`。这是保守阻断的真实观察，
不证明盈利或非空成交/持仓跨日估值场景已获实际覆盖。

首次原operator在进入审计器前失败，stderr为`/bin/bash: Argument list too long`。
增长后的150434字节HTTP wrapper对应publication内嵌到SSH命令参数，超过系统限制。
该失败原文保留。独立本地副本仅改为通过SSH channel stdin发送相同Python审计代码，
并增加本地helper导入路径；不修改旧operator、9846审计源码或生产文件。17:40完成的
真实E使用前述仍在现有时效门槛内的HTTP包，未替换时钟或publication。

原始证据与绑定：

- [E的HTTP原文、headers和相邻deployment](evidence/step1-oct28-resumed-20261028/E-public-and-private/capture.json)
- [完整只读E](evidence/step1-oct28-resumed-20261028/E-public-and-private/E-private-20261028T094035371956Z.json)
- [HTTP/E绑定](evidence/step1-oct28-resumed-20261028/E-public-and-private/E-binding-20261028T094035371956Z.json)
- [首次传输失败](evidence/step1-oct28-resumed-20261028/E-public-and-private/E-private-20261028T093927198225Z.error)
- [仅传输改动的来源记录](evidence/step1-oct28-resumed-20261028/operator-and-readonly/operator-transport-provenance.json)

## 2. 正式新日版本与C/D前提

17:43:58一次只读快照确认ACTIVE、approval、data epoch读取前后相同。正式day head
仍为Oct27的`09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407`；
当时只存在3个登记版本，没有Oct28正式版本，Oct28自动`completed.json`不存在。

18:39:02由主任务获取的独立快照再次确认同一head和ACTIVE，并记录以下实际准备量。
本审计只复验该既有文件及其内层签名，不重新运行那轮SQL：

| 时点/分母 | 数量和含义 |
| --- | --- |
| 冻结研究数据集 | 990证券 |
| 固定Oct28请求`fdd7836d...` | 988个合格当日日线batch绑定；不是1500全行情池 |
| 17:43 latest | 已准备348/988；638未尝试，640未解决，2等待重试 |
| 18:38:17 latest，18:39读取 | 已准备623/988；363未尝试，365未解决，2到期重试 |
| 请求外2证券 | 002827、605258，`NO_QUALIFIED_CURRENT_DAILY_BATCH` |
| Provider状态 | `PREPARATION_ONLY_NOT_A_READY_QLIB_VERSION`，`provider_published=false` |
| 自动日流程 | `PREPARING_DAILY_INPUTS`，Oct28 `completed.json`仍不存在 |

完整固定request为
`fdd7836de313bb9520c1755e162a539ce7b54276a085b971855b3b6a86778bb2`。
源代码版本为`458552d`，原数据集、universe、日、政策、collection snapshot与batch
身份绑定在不可变request中；本轮没有扩范围、重写request或把更新后的来源混进冻结件。

17:43的online publication为`auto_predict.state=BLOCKED_STALE_DATA`，
`current_prediction_id=null`。数据库保留已完成的旧预测，其中最新`3f633559...`仍绑定
Oct27数据和ACTIVE29589b44，预期执行时间为Oct28 09:30；它不是面向Oct29的新预测。
18:39数据库快照仍仅显示同一批旧预测。旧签名/档案存在不等于新的current serving资格。

signals的17:43签名publication仍为`61d36f4f...`，data_end为Oct27，生成于
Oct27 22:30:15上海时间，预期执行Oct28 09:30。五个基线家族齐全，不能改称Oct28
收盘后生成的下一日signals。18:39工作流的最近signal job也仍绑定Oct27。

候选Ridge`98d27455...`在17:43仍STAGED、activation_count=0。保留的自动验证任务
`1ed2fd215098f7e64421f9419584662f`已COMPLETE，验证输入是Oct27版本09a67b13；
18:39任务快照相同。它证明此前已有输入的兼容性，不是Oct28 later-version验证。
捕获的model_validations及prediction request/result内层签名均已独立复验。

因此本轮**不运行新的C/D验收器**：真实Oct28 READY版本、对应自动完成凭证、批准模型
的新预测/signals及候选新日验证均未出现。旧C/D通过只保留其旧绑定范围。

- [17:43完整C/D前提与真实进程](evidence/step1-oct28-resumed-20261028/operator-and-readonly/cd-prerequisites-20261028T094358686085Z.json)
- [18:39主任务只读快照](evidence/step1-oct28-resumed-20261028/checkpoints/abc-readonly-progress-20261028T103902Z.json)

## 3. 两条retry与等待合同

18:42:34仅从同一固定请求读取`symbol-attempts.json`、request、progress与目标文件清单，
没有抓取新因子数据或调用prepare。ledger的dataset_identity与988个request binding
完整匹配。真正的两条retry是：

| 证券 | ledger状态/类型 | 已尝试 | 实际尝试上海时间 | 不早于上海时间 |
| --- | --- | --- | --- | --- |
| 300846 | FAILED / URLError | 1 | 18:07:55.206902 | 18:37:55.206902 |
| 300852 | FAILED / URLError | 1 | 18:08:05.423991 | 18:38:05.423991 |

两证券均在988 binding内，目标目录当时没有产物。它们与request之外的002827、605258
不相交。ledger仅记录URLError，没有底层DNS/连接/超时原因；读取的有界service.log
尾部未找到对应失败明细，因此不能声称已确定某一种网络根因。按已核验源码推断，
失败可能来自prepare_stock中读取Sina `hfq.js`的既有公开请求；本轮没有重发请求。

运行源中prepare、pit_collection_queue、data_service三份文件的SHA256均与Git458552d
对象相同。既有合同如下：

- 每次先持久化RUNNING与next_attempt_at，再处理；失败保留FAILED与错误类型。
- 首次等待30分钟，随后60、120、240、360分钟，最长6小时；不能改deadline提前重试。
- 每批选择最多25个未尝试证券，另加最多5个已到期重试；prepare还有180秒单次处理界限。
- 日流程先持久化5分钟not_before；服务按300秒周期复查。到期表示可选择，不保证在
  deadline瞬间完成或已实际重试。18:42该快照仍是attempts=1，不能填成第2次成功。
- 缺失的新日原始batch与已冻结请求内的适配失败是不同问题。若后续来源改善，必须走
  合法的新版本/请求审查，不改写本次冻结输入或旧失败。

证据：[固定请求、完整ledger和只读诊断](evidence/step1-oct28-resumed-20261028/operator-and-readonly/retry-ledger-20261028T104233569838Z.json)。
代码合同见[选批及退避](../scripts/pit_collection_queue.py#L60)、
[原始因子读取](../scripts/prepare_qlib_incremental.py#L233)、
[自动日流程](../scripts/run_qlib_data_service.py#L140)；包中同时封存精确Git源字节。

## 4. 下一可行动点与限制

继续让已运行的自动准备流程按既有合同推进；下一次有意义的只读核验是出现真实
Oct28 READY版本和自动完成凭证，或出现新的明确失败/停滞原因。届时固定新的版本、
实际时钟、批准模型预测/signals和candidate validation后运行现有C/D，候选只验证不激活。

新日正式数据与公共forward降级解除后，重新捕获真实HTTP和deployment，再运行现有E。
17:40原BLOCKED结果和当日执行模型/目标保持原身份，不由后来的数据或模型覆盖。
本次不轮询、不恢复PAPER、不消费旧claim、不控制owner，也不改LIVE=false/rolling OFF。

17:39只读/proc证据确认forward383859及实例74972257不变；17:43的workflow338258、
data493083、publisher294214仍为既有owner，各过滤读取的LIVE值为false，rolling政策为OFF。
18:39确认ACTIVE/head未推进。这些是各自时点的事实，不把17:40公开新鲜度外推到18:42。

浏览器视觉、最新全行情公网索引/分片时效、非空成交/持仓跨日估值、独立历史PIT、
复权与交易约束认证仍未由本轮覆盖。整STEP1未完成。

## 5. 校验、证据清单与复现

复用既有Qlib只读审计和行情/会计适配器，不新增网关或交易逻辑；本轮是取证和文档，
不为可逆文档增加测试。已执行HTTP body SHA256、外层/内层绑定、相邻deployment、
审计签名、捕获的预测/验证证明签名、request/ledger身份和精确Git源hash核验。
[独立校验结果](evidence/step1-oct28-resumed-20261028/verification.json)与
[不可变文件manifest](evidence/step1-oct28-resumed-20261028/manifest.json)记录精确范围。

在授权仍有效、使用新输出路径并按实际时钟复现E：

```powershell
python -B E:/QTrade-temp/cluster-evidence/step1-oct28-resumed-20261028T094017503329Z/capture-close-E-stdin.py
```

该独立operator只通过既有授权connect helper建立只读连接，HTTP自动创建新目录；
不能重用本包内的过时HTTP来声称当前时效。不得把已完成handover/recovery当作复现步骤。
本文与证据包均为新文件；未提交Git，未改CURRENT_STATE、MASTER_PLAN或DECISIONS。
