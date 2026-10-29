# Legacy Ridge 与已STAGED模型再验证：独立只读适配方案

状态：**方案与元数据负例已准备；真实只读适配器尚未实现/验收，D及全STEP1未通过。** 不改原auditor、训练归档、registry事件或失败记录；不排job、不执行验证、不激活、不恢复PAPER、不改政策或owner。

## 为什么需要新合同

晚间[真实固定范围审计](STEP1_OCT28_EVENING_FIXED_SCOPE_AUDIT_20261028.md)保留了原D的真实失败：指定98d Ridge训练summary没有rolling_plan，原工具206行取该字段发生KeyError；它还要求候选事件恰好为首次TRAINED→VALIDATING→VALIDATED→STAGED。已STAGED模型的新proof由既有registry追加INPUT_VALIDATED，不能删除旧事件使它假装首次验证。

Ridge98d的原训练是明确选择d33a7466/Oct26版本的 `QLIB_RESEARCH_JOB`，不是rolling任务。新工具应使用显式 `LEGACY_BOUND_RESEARCH_V1` 和 `STAGED_INPUT_REVALIDATION_V1` 模式，不自动fallback，不补造plan，不将run名当训练起源证明。旧工具与新工具输出使用不同schema；旧BLOCKED永久保留。

## 可复用依据与差异

| 已有实现或证据 | 复用范围 | 新适配必须补足 |
|---|---|---|
| `verify_archive`、`verify_bundle`、`verify_version`、`verify_validation` | 精确文件集合/hash、拟合工件、版本与验证档案 | 不能只借用旧PASS；在新只读运行中对实际字节重新执行 |
| 原 `audit_qlib_candidate_transition` 的 `snapshot`、`job_chain`、`raw_day`、`validation_rows` | 短只读SQL快照、真实job事件、原始来源、特征/分数Parquet | 保持全部检查；替换两处不适用的首次/rolling合同，不修改原函数 |
| [历史Ridge独立归档检查](evidence/qlib-archived-version-20261028/cluster/verify-archived-version.py) | 原12486文件、580源码、策略/任务/模型关联与原版binding检查流程 | 从固定旧时点改为显式输入的新时点；不能拿旧不变性证明当前不变 |
| [既有calendar-boundary检查](evidence/qlib-archived-version-20261028/calendar-boundary/operator-v2.py) | 原Ridge/Oct27 proof、真实事件、未激活、aware时钟和兼容性限制 | 支持原staging proof与新增INPUT_VALIDATED的完整关联；保留首次日期与旧失败 |
| 既有 `ModelRegistry.record_validation` | 已STAGED模型只追加INPUT_VALIDATED，保持STAGED/0activation | 仅核验此行为，不构造registry或调用写方法 |

相关source/fixture hash已记录在[reuse清单](evidence/qlib-legacy-revalidation-contract-draft-20261028/reuse-source-and-fixture-hashes.json)。本边界处理研究档案与registry，没有gateway或订单接线需求。

## 拟定的真实只读验收流程

1. 显式输入candidate、训练job、训练version、later version、expected ACTIVE/approval、两个合同模式及新输出路径。禁止默认选当前模型/同日第一个版本，CLI没有 `--now`、repair、enqueue或activate选项。
2. 以原始训练任务request/spec.data_binding、summary.data_binding、策略版本、source snapshot、模型index/definition和真实result建立唯一关联。固定训练版本必须同时匹配version ID、dataset hash、data_end与READY event。完整检查归档、原始data-manifest、精确source清单、已拟合bundle与独立restore parity。不能因为缺rolling_plan而少核对原始版本或源码。
3. 训练与later版本均执行原文件验证和raw_day重放；later必须真实完成且严格晚于训练版本。使用实际Calendar与系统UTC。历史proof在其记录的aware生成时刻重建inference contract；不得把过期旧proof当当前signals或执行订单。
4. 保留原四阶段staging事件及其唯一原proof。其后的事件只允许INPUT_VALIDATED，每条都必须绑定同一未激活模型和唯一已登记proof；重复、孤立、未知或激活事件均拒绝。目标later proof须唯一且有对应INPUT_VALIDATED，旧proof不能被覆盖或隐藏。
5. 目标必须有唯一真实 `QLIB_MODEL_VALIDATION` job：原source revision、deterministic binding/id、原request、QUEUED→RUNNING→[VALIDATING]→COMPLETE、真实输出及registry/index逐项一致。FAILED/INTERRUPTED/不存在保持BLOCKED。随后执行原 `validation_rows` 的完整Parquet schema、覆盖、排名、特征精度、session/lineage与no-refit检查。
6. 新前后SQL快照包含完整candidate/ACTIVE行、批准命令/receipt、所有相关事件、目标job/proof、版本准入与head。显式expected ACTIVE/approval必须匹配，前后事实不变；不把合法自动切换后的新基准回填旧报告。候选始终STAGED、零激活、activated_at=null。
7. 所有实际读文件与SQLite均只读，SQLite URI mode=ro/query_only/短事务；禁止Worker、OnlineModels、ModelRegistry构造器。仅写新operator/local证据，记录实际source/helper hash、原始失败、完整复现参数与限制。没有真实新proof时提前报告缺项，不执行验证器制造输入。

新current68基准只能作为另一个显式范围；原29589批准不变性仍失败。即使新兼容性适配通过，也不证明未见数据研究、收益优越、前向回报、PIT、PAPER恢复或全STEP1。

## 已执行的合同负例

[纯元数据prototype](evidence/qlib-legacy-revalidation-contract-draft-20261028/contract_draft.py)不访问文件系统、网络、进程、registry或队列。两个名义样例分别使用既有legacy metadata与明确SYNTHETIC的再验证事实；**即使输入自称PASS或real_archive_verified，输出始终是 `METADATA_CONTRACT_CHECKED_REAL_ACCEPTANCE_PENDING`，真实归档/Parquet标记为false。** 它不能作为正式gate使用。

[26项测试](evidence/qlib-legacy-revalidation-contract-draft-20261028/test_contract_negative_examples.py)实际零失败零跳过，含2项元数据名义样例与24项拒绝检查：错误训练版本/dataset/model/strategy、FAILED原job、补造rolling_plan、前后变化、新ACTIVE或approval、激活与bool计数、丢原proof、缺/重复import、未知事件、缺/失败/重复目标job、错source、跳RUNNING、未来生成/日期、同版本和允许refit。多个样例重算外层hash后仍须拒绝，避免只测试hash不一致。

这些是合同可执行样例，不是实际新Oct28 proof或集群adapter验收。仍须实现上述完整I/O reader、真实坏归档/Parquet与SQL反例，再独立审查；不以这些单元测试宣称D通过。证据见[manifest](evidence/qlib-legacy-revalidation-contract-draft-20261028/manifest.json)。

```powershell
python -B -m pytest E:/QTrade-git/docs/evidence/qlib-legacy-revalidation-contract-draft-20261028/test_contract_negative_examples.py -q -p no:cacheprovider --junitxml=E:/QTrade-temp/legacy-revalidation-contract-replay-new.xml
```
