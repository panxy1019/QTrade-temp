# 10 月 27 日收盘链路与统一研究接入

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

## 验证与发布门禁

已验证：Windows 针对性后端集成、研究登录／规格冻结／队列签名、Registry 幂等、
PAPER 初始化隔离、epoch ABA、失败恢复与审计链；网页构建、lint、旧档案与新版表单测试。
Linux 同版本测试、实际新任务及生产交付需在真正完成后补充证据，不能以 Windows 测试
替代。生产 revision、集群 worker revision 与每次实验来源分别记录。

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
