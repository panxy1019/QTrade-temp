# Publisher v6：真实证据适配与交接门禁接线设计

状态：**ACTUAL_COMPOSITE_CHECK_PASS / PREPARATION_ONLY / NO_MUTATION**。
旧v4/v5工具、旧反例和原始失败均保留。本文不宣布生产门禁、部署、实际wrapper
candidate或完整故障矩阵通过。

当前实现、47项本地正反例和实际62份原件组合结果见
[最终安全归档](evidence/p0-publisher-factor-20261028/handover-v6-composite-20261028T114221Z-0eec8c94/README.md)。
原件已齐备并通过operator实际只读内存检查入口；旧草稿的组合PENDING已解除。
文件路径Linux检查和独立复审仍由主任务分别记录，不据此执行apply。

## 已修复的适配边界

新增隔离原型`E:/QTrade-temp/publisher_actual_evidence_v6_20261028.py`。
`derive_benchmark(...)`不接受normalized PASS字段作为事实，只接受原始：

- `qtrade.market-export-worker-benchmark.v1` report bytes；
- 逐轮真实child stdout bytes；
- source manifest与freeze原bytes，以及独立取得的完整Git清单；
- report前后原mountinfo bytes，仅在内存解析；
- 精确受审benchmark driver bytes。

原型硬限定现有两个driver SHA256：

| driver | SHA256 |
| --- | --- |
| final-modes benchmark | d4215b08294bba939061ba83edae7a7ed1bd7e38501477310fe0dfa6e4a5e982 |
| final-continuity benchmark | 04966f3bb1a978417779b0ed09dcfc3d6b057f96d335ba6c24b3fba16de5a1d4 |

修改driver须重新审查、记录新hash并明确原格式，不静默兼容未知schema。
report和freeze按现有规范化内容hash重算；这属于完整性校验，不是数字签名或
对拥有全部写权限者的身份认证。

适配器从原report校验source revision/scope、完整manifest hash及文件数、freeze
内容身份/采集时钟、受审candidate与worker源码、production45秒及原资源上限。
child stdout的每一个字段须与对应report item逐字段且逐类型相同。实际FAILED先
拒绝；实际999秒不能被外层38秒覆盖。预算按实测wall与export_seconds判断，不能
信任`within_budget=true`代替计算。

source preflight比较完整独立Git清单，明确要求
`scripts/unified_market_checkpoint.py`和
`src/qtrade/research/market_validation_receipts.py`。二者缺失在任何新release路径
读取/替换/TERM之前拒绝，不能等重启后的origin探针才发现。

## 明确采用的实际测量矩阵

按当前明确批准的测量合同登记：

| scope | 模式 | 要求 |
| --- | --- | --- |
| baseline5640/140136 | full cold diagnostic | 5轮，每轮≤120秒，仅非生产诊断 |
| baseline5640/140136 | valid checkpoint/new process | 5轮，每轮≤45秒 |
| baseline5640/140136 | local DB loss/sidecar diagnostic | 5轮，每轮≤120秒，仅非生产诊断 |
| current5701/141634 | normal continuity | 20轮，每轮≤45秒，包含真实新增与日频窗口滚动 |
| 两个scope | 独立全量oracle | 各自完整source、index、全部shard与lineage |

baseline各模式不能标成current5701。current5701未重复各5轮诊断模式，应保持该
限制，不扩大解释为所有负载/恢复情形均已实测。当前normal与cold并行共享NFS的
竞争范围需从真实report保留，不能声称独占基准。

原真实记录缺少child start_ticks、逐轮UTC起止时间及精确初始sidecar存在性，
原型将它们保持null，并列出限制。`initial_local_database_exists`和
`initial_object_count`只取真实report；`receipt_hits`及
`receipt_sidecar_bytes_read`只能证明期间读取/验证的durable sidecar，不能追认
启动前存在性。统计中的cache当前值也不改名为未测量的peak。

## 执行唯一性与状态连续性

唯一执行身份从实际字段规范化派生：

```text
parent_pid + int(parent_start_ticks) + pass_name + child_pid
+ freeze_evidence_sha256 + source_manifest_sha256
```

report或stdout的空白/键序/序列化变化不会新增一个execution。
`assert_unique_executions()`按该语义身份去重。原受审driver的顺序标签必须完整且
不重排；parent-start身份保留进程边界，原driver的同步subprocess顺序保留顺序
范围。这个证据不等于未记录的逐轮UTC或完整前后generation链。

后续组合器须另读实际generation文件和index，核验normal第一轮相对于真实5640
seed的新增/滚窗，随后每轮parent/input-set/refs/index链。不能仅根据手填scenario
标签或以一份输出从report到report自我引用证明连续性。

## NFS安全投影

原mountinfo bytes按原hash核验，在内存解析最长匹配挂载，要求输出和production
实际路径由同一NFS/nfs4挂载覆盖。报出的selected mount须与原line全部一致；Linux
st_dev的major/minor、output/production设备以及前后挂载身份必须一致。

Git归档只保留safe projection：原mountinfo hash、selected line hash、mount id、
parent id、major:minor、fs_type、st_dev与路径hash。不得新增原NFS source、原line
或原mountinfo正文到Git；原始件仅留private operator证据或内存。

## v6生产入口连接方案

新增`E:/QTrade-temp/publisher_handover_v6_preparation_20261028.py`保留v5精确wrapper
变换和旧TERM/no-retry状态机，但实际`check_gates()`只允许
`qtrade.publisher-handover-gates.v6`，无cold/warm/4200或normalized旧gate回退。
actual adapter按精确SHA加载。`advance()`调用新版入口；真实adapter的
`_mutation_guard()`也重新调用该入口，避免绕过advance直接install/TERM。

缺少实际原件时组合器抛出`V6_COMPOSITE_INTEGRATION_PENDING_NO_MUTATION`；提供完整
且匹配的原件时会重新派生全部事实并返回`GATES_PASS_PREPARATION_ONLY`。
schema拼写正确或单个measurement适配通过均不能授权变更。历史gate保留为未调用
的历史函数，不能用它给v6 mutation兜底。

最终组合器与独立复审检查以下步骤：

1. 使用可信manifest固定source Git tree/count/hash、两freeze、每份原report、
   每轮stdout、driver、oracle、execution receipt及XML的精确hash。原stdout必须
   全部保留；如果现有本地归档只拷贝combined stdout，需从原operator证据补取
   真实逐轮文件，不能从report重建一份声称原stdout的文件。
2. 逐份调用真实适配器并按语义执行身份合并去重。检查15+20真实轮次与明确矩阵。
   固定raw hash与语义身份是两层检查；不能仅用hash不同代表执行不同。
3. 解析原独立oracle实际schema和真实execution exit/source绑定，核对两个scope
   的source/3000 targets/全部refs/hash/lineage；不能仅引用opaque PASS bytes。
4. 解析实际pytest XML中的testcase、failure/error/skipped及真实execution状态，
   将被审Git测试源码/runner hash与每个故障具体case ID绑定。保留直接、部分、
   mock各范围，不创建自述`fault_suite.complete=true`。
5. 当前5个专门缺口为historical_backfill、quarantine_release、disk_full、
   slow_nfs、git_non_fast_forward。新增测试尚未Linux实测前保持PENDING；普通
   OSError不能冒充ENOSPC，时间schema拒绝不能冒充真实慢接口注入。
6. 复用旧交接的program/path/control/binary/config/owner安全核验，重新绑定同一
   v6 evidence bundle。旧cold/warm≤45部分由明确v6分模式合同取代，不生成假的
   cold45字段来调用旧gate。
7. mutation前核验完整Git文件清单的每份实际字节，执行无导入副作用的PathFinder
   origin预检并解析其原输出；必须包含两新增模块及完整传递依赖。重启后真实PID
   origin/argv探针仍保留，不能用preflight替代。
8. WRAPPER_INTENT与唯一TERM_INTENT之前分别重新检查同一bundle、source、owner、
   control与父代。任何不确定转只读复核，保持no retry/no retarget/no kill。

生产export仍45秒。首次生产全冷可明确失败并保留已验证不可变收据；不做未计时
预热、不偷偷使用120秒，也不把后续checkpoint成功改签为首次cold成功。

## 当前测试与限制

`test_publisher_actual_evidence_v6_20261028.py`共24项本地反例/边界测试通过：
FAILED/999秒包装、report/stdout矛盾、tmpfs、前后设备变化、同执行空白变体、
baseline冒充current、缺新模块、Git清单子集、legacy4200入口、直接mutation绕过、
opaque fault自述、实际XML失败/skip、缺stdout和未知driver。

```powershell
python -B -m pytest -q E:/QTrade-temp/test_publisher_actual_evidence_v6_20261028.py --junitxml=E:/QTrade-temp/publisher-actual-evidence-v6-counterexamples-20261028.xml
```

这些通过证明原型拒绝所列反例，**不是实际35轮报告、两份oracle、fault suite、
完整组合门禁或生产交接已通过**。本轮未读取private wrapper正文、构造真实candidate、
执行部署、发真实RPC/TERM或修改原工具。
