# 10 月 27 日收盘链路与统一研究接入

## 23:04 最新补充

以下补充覆盖后文较早的“待运行”状态，不修改任何旧凭证。

- 窄修复 `096387b8088e4361c9e5cd9cd2c165e21819fead` 已验证 580 个执行文件，
  实际 Linux **320 项全部通过、零跳过**，只切换 workflow 为 PID338258。
  data 服务仍为 c382453/PID334634，原 PAPER175982 与其他 owner 不变。
- 新 LightGBM `8377ca63d95f45b18593e23f1332ad51` 于 22:40:58 COMPLETE；
  新 Ridge `713e9f4de8bb456aa7fc906bf636dce6` 于 22:51:01 COMPLETE。
  独立只读 replay 对每份正式归档 **12,924 个文件** 逐项核验，复核冻结规格、
  数据/特征/策略/源码绑定、独立 fitted restore、真实模型注册及策略任务关联。
  [完整结果核验](evidence/qlib-close-20261027-final-verification.json)保留准确时间与路径。
- 新训练模型 `68d9287e`、`e0036a4f` 均为 TRAINED、activation_count=0；目前
  没有晚于其训练数据截止 10 月 27 日的新正式日版本，不能马上自动激活。
  两个恢复分数最大绝对差为 0 与 1.11e-16。原 restore 凭证中的旧人工批准文字
  原样保留；当前批准依据独立签名政策，不把恢复成功本身当作激活准入。
- 已获后续日验证的 `29589b44` 是当前 ACTIVE，source096 日预测 `3f633559`
  于22:30:00完成，signals任务 `04da9a51` 于22:30:23完成。
  [新 C 私有验收](evidence/qlib-close-20261027-final-C-private.json)实际通过；
  后续需与 PR33 合并后的真实公网交付核对，不能用本地通过替代。
- Terra high 按用户要求重新打开并绑定网址，取得首页/研究/模型/实验/回测/数据的真实
  DOM及截图；两项新实验归档、模型状态和研究限制可见。策略中心/历史入口的穷尽检查、
  旧版本选择变化和控制台未核验，补查会话再次无可用浏览器。
  [原始视觉报告与截图](evidence/qlib-browser-terra-retry-20261027/report.md)及先前失败
  均保留；不能把API或核心页面截图替代所有交互流程。
- 回补队列22:53累计63,204条合格观测（日1,572/五分钟61,632），独立退市探针
  22:58累计99条；均让位当期采集。剩余约272.7GiB，保留100GiB。
  [治理快照](evidence/qlib-close-20261027-final-governance.json)只证明有限队列。

两实验共同测试期为 2024-01-02 至 2026-10-27，680个市场交易日；训练/验证切分、
891只支持股票的共同规则、买8bp/卖13bp、最低5元、Topk50/n_drop5相同。
不同模型和实验名称生成不同策略版本；它们不是同一个模型版本。

| 指标 | LightGBM | Ridge |
| --- | ---: | ---: |
| IC / RankIC | 0.0243 / 0.0522 | 0.0195 / 0.0472 |
| 历史测试区间净收益 | 102.48% | 86.66% |
| 年化净收益 | 29.88% | 26.02% |
| 年化净夏普 | 1.105 | 1.201 |
| 最大回撤 | -32.34% | -32.51% |
| 平均日换手 | 17.68% | 17.71% |

这些是非PIT、事后查看过历史、日频执行近似下的诊断结果，不是未见过的前向收益。
原 FAILED/CANCELLED 任务及失败日志保留；两份正式成功由新请求产生，未改旧状态。
当前 ACTIVE 新 PAPER仍未运行并需要恢复，SHADOW的10月28日目标仍是未来观察。
本次不宣布整阶段STEP1完成，STEP2仍待其真实门禁全部通过。

## 实际收盘验收

以下均为 2026-10-27 上海时间，不将准备数量、预测数量或本地结果当作彼此的替代。

| 环节 | 实际结果 | 证据 |
| --- | --- | --- |
| 正式日版本 | 20:23:26 自动完成，截止 10 月 27 日；987 只当日合格、990 只历史保留 | `evidence/qlib-close-20261027-automatic-completion.json` |
| ACTIVE LightGBM 日预测 | 20:26:09 COMPLETE，888 个分数，158 列特征 | 原 ACTIVE 与原批准绑定在 C 报告中 |
| 候选后续版本验证 | 20:30:17 COMPLETE，STAGED，未激活 | 原失败及新的 D 审计分别保留 |
| signals | 20:30:57 COMPLETE，绑定同一日版本与 admission event | C 原始归档校验 |
| C 本地产物 | 20:57 完整校验通过 | `evidence/qlib-close-20261027-C-private.json` |
| C 实际公开交付 | 21:08 通过五次实际固定端点 GET，生产 `b64b5f9` | `evidence/qlib-close-20261027-C-public.json` |
| D 独立重审 | 21:11 `VERIFIED_LATER_INPUT_CANDIDATE_NOT_ACTIVATED` | `evidence/qlib-close-20261027-D.json` |
| 股票前向 E | 20:51 PASSED，全部 12 个账本通过；使用 20:48 的实际公开输入 | `evidence/qlib-close-20261027-E.json` 与 E-capture / wrapper / inner |

正式版本：`09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407`。
预测：`fbe57315644040002d9ede9f49ecc8b07f9b5fe6de90797e96cff8a067c76f1f`。
本次准备仍使用冻结源码 `55b5b0044649a28fd84c77364ca9908df0cee7a4`，
预测、候选验证和 signals 使用实际 worker `19cbb2a1faa2f537ddb670dcbbf89d760e27acb7`。
不把后来的源码修复重新标成这些原产物的来源。

保留两类失败：集群访问生产 URL 的 C 审计发生 HTTP_FETCH_FAILED，未生成成功报告；
D 初次审计误拒绝 worker 合法写入的一次 VALIDATING。D 修复只接受精确三态或四态链，
Windows 与 Linux 各验证同样的 47 项，无跳过。另一次 D 输出位置选在 base 内，被独立
输出门禁正确拒绝；随后在 `/root/qtrade-local/operator-evidence` 新路径重审通过。
原失败报告、原始错误和数据库事件没有修改。

C 的公开验证在 Windows 上执行原固定 GET 校验器。先从集群完整校验产物导出原始
JSON，再核对完整传输字节数与 SHA256；公开捕获使用真实时钟和原 50 秒 HTTP 预算，
没有离线 HTTP 替身、时钟覆盖或新鲜度豁免。第一次 Windows 报告写入遇到 POSIX 目录
fsync 不兼容，原输出保留，之后使用新路径的 no-clobber 文件写入重新实际捕获。
这些是特定日期、版本和模型的验收，整阶段 STEP1、浏览器渲染及未来交易日另行判定。

## 用户新授权与实现

用户明确同时授权 ACTIVE 的自动日预测及通过门禁的候选自动激活。该授权记录为
`USER_EXPLICIT_INSTRUCTION_2026-10-27`，原文“都允许批准”，只覆盖研究。
自动批准政策与滚动训练政策分开；滚动训练仍为 OFF，实盘始终关闭。

自动激活要求显式启用的签名政策、唯一允许的 STAGED 候选、当前 READY 版本、晚于训练
数据的验证、真实未来研究执行窗口及最终 ACTIVE／数据事件 CAS。多候选、校验失败和
不确定 claim 均阻断；失败不通过改政策或重启重新申请。验证仅证明输入与恢复兼容，
不证明候选收益优于当前模型。每次批准、控制指令和结果都留存不可变关联凭证。

原 PAPER 175982 启动参数仍含初始账户初始化 actor。为使研究模型替换不沿用这个授权，
自动激活先为新模型的 PAPER 目录写入签名的“禁止初始化／恢复”保留标记，不打开账户、
不运行 recover、不修改旧账本。未修改的旧 owner 因目录已存在而按 non-pristine 打开，
新账户保持未运行，需要独立的人工恢复与对账。真实旧 `_open` 与真实持久化账本的测试
覆盖此边界；原 PAPER 进程不重启，旧一次恢复授权不复用。

## 统一研究规格

复用已有 workflow、不可变 DataVersionCatalog、StrategyRegistry 和模型控制器。vn.py
交易网关不承担这些研究身份与档案关联职责，不引入新的交易网关路径。

网页研究目录发布最多十个已实际核验、具有唯一 READY admission 的历史日版本，同时
保留原 10 月 23 日固定档案。数据选择明确冻结 version、manifest、dataset、来源快照、
READY 事件及支持股票池哈希；历史复现不要求该版本仍是当前 serving head。

目前只开放 `qlib-0.9.7-Alpha158-v1`、Ridge／LightGBM 与 TopkDropout。完整策略版本由
这些输入、模型和预处理配置、标签与切分、组合、成本、执行近似及代码 revision 生成。
表单修改得到新版本；目录轮询不改写正在编辑的选择。

`POST /api/research/specification` 要求研究登录，只从发布目录冻结规格。
`POST /api/research/jobs` 的新规格必须附 `expected_strategy_version`。
集群 ingest 和独立 runner 再次完整核验本地实际 READY 档案，不能用客户端自己签名的
描述绕过本地字节验证，也不能把任务静默重新绑定到最新版本。

新任务开启 fitted processor/model 恢复验证，重建的完整 Provider 字节必须与所选版本
一致，完整策略定义写入归档并进入 checksums。成功后注册真实训练模型；下一合格日的
独立验证与批准政策决定其是否可激活。旧任务规格和旧不可变实验不重签、不补默认字段。

策略注册器将被接受的版本与 job 原子关联为 EXPERIMENT。网页显示信号来源、数据／特征／
策略／代码版本、组合和执行规则、任务及实际训练工件。后续 CANDIDATE、VALIDATED、
PAPER 等策略晋级不因模型自动批准而自动发生；独立因子注册与评价仍属于 STEP2。

目录缓存只复用已核验的不可变描述，并检查 admission／manifest 变化；每个实际任务
仍重读完整档案。目录心跳不宣称每五秒重新认证所有历史文件。

## 旧工具退役与历史证据

删除旧五模板目录、模板参数化日线计算入口及客户端调用；原三个日线 POST 及 current
对应路径返回 HTTP 410，保留原鉴权并在解析数据之前退役。浏览器本地历史记录保留为
只读查看与下载；分钟研究、旧不可变实验、统一七系列对照及等权基准保留。

旧 120 日固定因子报告绑定原 API／因子源码的完整字节。移除模板后 API 文件变化会使
“必须等于当前源码”的旧校验失效。现将原 Git 源码按原报告哈希存入该历史档案的
`implementation-v1`，只验证固定原实现，不把旧结果标成当前实现重跑。原报告字节不变。
为避免 Windows 换行破坏档案引用，部署证据以 `-text` 保留原始字节。

## 验证与发布门禁（截至22:22的历史检查点）

已验证：Windows 针对性后端集成、研究登录／规格冻结／队列签名、Registry 幂等、
PAPER 初始化隔离、epoch ABA、失败恢复与审计链；网页构建、lint 与全部 304 项网页测试。
集群 c382453 的 580 个执行范围文件逐项 Git blob 验真，实际 Linux 313 项全部通过、
零跳过。PR32 合并后的生产 main40db6db 已通过真实全池身份／固定行情样本公开核验；
只切换 workflow/data 服务，其他采集、发布与原 PAPER 进程 PID 不变。

实际生产登录、规格预览、提交及同请求重试成功，匿名预览 401、旧日线入口 410。
真实目录提供 10 月 27 日默认版本及历史 READY 选择；旧 10 月 23 日档案保留。
浏览器 DOM 与截图接口持续超时，视觉与浏览器点击流程尚未通过。

22:17 首个真实 LightGBM 任务 `32f54fc08c9a4b488910f71100f7674e` 暴露最终归档错误：
训练、回测与独立恢复已经运行，恢复差异为零；但回测的 portfolio 字典覆盖冻结策略
变量，结果封存时出现 `KeyError: strategy_version`。任务保持 FAILED，原 partial、
完整日志与事件保留，不作为正式完成实验。通过生产 API 提交的 Ridge 任务
`79b67daa7abe4d62a1d65e0c68c18dbf` 经签名取消，保留 CANCELLED 状态。

窄修复分开两个变量，并在封存前重读实际持久化策略，复核签名、spec、revision 与
expected strategy version；旧规格不补造策略身份。60 项相关 Windows 测试、Ruff、
mypy 通过。首次新增测试因测试自身修改同一 expected 对象而失败的 XML 也保留；
修正测试为深拷贝后另存通过结果。修复尚需同版 Linux、真实新请求及公开结果验证。

签名自动审批政策已实际启用，允许唯一合格 LightGBM；实际批准 `fdb7526b` 将
`29589b44` 设为 ACTIVE，原 `2d78cc4c` 退休。新 PAPER 账户 `137ff4cd` 预先放置
禁止初始化／恢复标记，原 PAPER175982 未重启；新预测仍需完成及另行验收。
生产 revision、集群 worker revision 与每次实验来源分别记录，不能互换。

复现入口：

```powershell
$env:PYTHONPATH='src;.'
python -m pytest tests/test_qlib_bound_research_integration.py tests/test_model_promotion_policy.py tests/test_qlib_strategy_contract.py tests/test_online_research.py tests/test_candidate_transition_audit.py -ra
npm --prefix web test -- --maxWorkers=2
npm --prefix web run build
npm --prefix web run lint
```

C、D、E 实际重审命令与固定产物定位沿用
[剩余门禁手册](QLIB_CLOSE_REMAINING_GATES_20261027.md)，输出使用新路径；
实际公开 body、headers、时钟及来源哈希保存在本次 evidence 文件中。
原有限八次 C/D/E 心跳已删除：旧快照和旧授权限制已经过时，当前交互仍继续执行。

## 仍然存在的限制与后续

- 当前数据版本的三个排除为 002827、600621、605258。987 准备、888 预测与 1,500
  目标池是不同范围；市场全池日线 1,497、五分钟 1,496 的旧覆盖检查不等于全天 SLA。
- 当前池仍有幸存者与流动性选择偏差；历史 PIT、ST／停牌、精确涨跌停、独立公司行为
  复权及多年连续分钟覆盖未全部认证。新实验继续逐项显示这些限制。
- 新模型的研究激活不授予新 PAPER 风险；新模型的前向账户验收必须单独做，不能借用
  本次原 LightGBM 的十二账本凭证。零成交和纯现金记录不证明收益或交易规则完备。
- ETF 的既有当日 SHADOW 验收独立保留；不重跑已消耗的修复 continuation，不宣称存在
  ETF 成交、持仓或收益。PAPER 175982 不重启、不自动恢复。
- 数据采集和公共源回补持续运行；原分钟缺口的 apply 在现有维护 owner 活跃时仍不执行。
  维持原导出 45 秒、512 MB、128 MB cache、Git 90 秒及 100 GiB 空间保留预算。
- 下一阶段先完成新接入的 Linux／真实任务／生产网页验证，再推进因子 Registry、独立
  因子评价、统一策略研发对照、实验预算及文本事件数据时点治理。完整历史认证不能
  通过增加界面组件或把模型训练结果展示出来替代。
