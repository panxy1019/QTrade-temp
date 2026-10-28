# Oct28 晚间固定范围 C/D/E 只读审计

**原29589/Oct28 E与指定Ridge98d D均保持BLOCKED；C真实执行因旧signals admission epoch不匹配而失败。** Oct28正式版本和29589新预测已经完成。20:07既有自动政策把ACTIVE切换到68d9287e，因此切换前后必须分开，不能拿新模型替代旧账本或旧验收。

本次只读数据库、归档和HTTP，使用原9846 auditor及真实系统时钟。只新增独立证据：C运行创建一个新operator输出目录，报告和原始HTTP存本机；没有重启、恢复、排队、激活、改政策、改目标、改旧归档或账本。LIVE始终false。证据封存在[27文件manifest](evidence/step1-oct28-evening-fixed-scope-20261028/manifest.json)，SHA `ba510eb70a6d5c5e938ea9f42737c7863a364fd97098853fb7019248a545c6f2`；旧17:40证据和失败继续保留。

## 真实时点

| 上海时间 | 实际证据与范围 |
|---|---|
| 20:03:42 | 正式head `b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5`，actual_end Oct28，11:57:40Z入库；988当日合格、990历史、002827/605258被排除。自动completed为11:57:59Z。ACTIVE/approval仍29589/6f1ec61b，读取前后context一致。 |
| 20:03:42 | 29589预测 `f9a443b2…` COMPLETE，889分数、158特征，绑定Oct28与epoch `3591a430…`，预期执行Oct29开盘。signals实际仍 `61d36f4f…` / Oct27 / epoch `69c34309…`。先前聊天中“新signals已生成”的判断有误，此处以完整bindings和原auditor失败纠正。 |
| 20:03:52 | 固定29589 E：12/12当日观察、2/2退休关闭逐本通过，无book失败；整体仍BLOCKED，public DEGRADED `FORWARD_SERVICE_SIGNAL_UNAVAILABLE`。这不同于17:40的DATA_STALE。 |
| 20:07:51 | 既有standing policy自动应用68d9287e，approval `750de28e…`，previous_active为29589。本文没有执行激活。 |
| 20:11:25 | 再跑同一固定29589 E：整体BLOCKED；当前逐本11/12，原退休分类0/2。新增LOCAL_ROSTER_INVALID、PUBLIC/LOCAL_BOOK_COVERAGE_MISMATCH，29589 PAPER为LOCAL_PAPER_NOT_RUNNING。未改用68d取得通过。 |
| 20:11:28 | 原C实际失败 `SESSION_ACCEPTANCE_ADMISSION_EPOCH_MISMATCH`。旧signals与新预测epoch不同，尚未到公开检查，HTTP capture数量为0；没有C私有PASS。 |
| 20:15:09 | 原D指定98d、Oct28、旧29589/6f1ec61b真实BLOCKED：`CANDIDATE_AUDIT_INPUT_OR_ARCHIVE_VERIFICATION_FAILED`。 |
| 20:16:25 | 纯只读诊断确认原D首个失败为 `KeyError('rolling_plan')`，并再次看到signals仍Oct27。 |

对应原件分别在[C/D只读包](evidence/step1-oct28-evening-fixed-scope-20261028/readonly-C-D)、[切换前E](evidence/step1-oct28-evening-fixed-scope-20261028/E-before-auto-promotion)、[切换后E](evidence/step1-oct28-evening-fixed-scope-20261028/E-after-auto-promotion)。C捕获器仅观察原始private结果和原HTTP getter返回值，原main参数、assertions、归档验证和返回值均未改；本次在epoch检查即失败，没有触发观察hook。

## 自动promotion来源

[独立校核](evidence/step1-oct28-evening-fixed-scope-20261028/readonly-C-D/promotion-and-scheduler-independent-check.json)验证17份signed文档的内容hash与事件/命令关联。当前policy `656f643b…` 于Oct27 14:07:59Z启用，保存的用户授权来源为 `USER_EXPLICIT_INSTRUCTION_2026-10-27`，允许模型族lightgbm。新68d的policy approval、ACTIVATE command、CONTROL_APPLIED与receipt `750de28e…`一致，actor为 `standing-policy-656f643bf547556b`。实际应用时间以CONTROL_APPLIED/receipt的12:07:51Z为准。

`scripts/run_qlib_worker.py` 与 `model_promotion_policy.py` 的096 Git文件hash和9846实际只读文件一致。这里证明保留记录之间的来源一致性，不冒称重新验证秘密HMAC或发出新的人类授权。没有改变原policy，也没有自动恢复新PAPER的权限。

## Ridge98d 为何不会自然再排 Oct28 验证

目标仍为 `98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795`：STAGED、activation_count=0、training_end Oct26。只读SQL显示唯一该模型任务是旧Oct27 `1ed2fd21…` COMPLETE；没有Oct28任务，也没有FAILED/INTERRUPTED的新binding占用。

`run_qlib_worker.schedule_candidate_validation()` 的616行只接受TRAINED；STAGED直接跳过。因此即使其它任务结束，98d也不会自然再排。每tick最多新建一条、有pending/predictor时等待，是附加条件。20:11正在执行e003 Ridge的首次Oct28验证；candidate调度先于signals，signals继续等待有既有源码依据。lightgbm限制用于promotion，不是validation调度的族过滤。

若未来另行授权提交，既有binding计算得到新id `a2b0cdd3cce8df2649b13b96014d4e80`，绑定98d、b82baaac与source096。现有GitQueue ingest不接收外部 `QLIB_MODEL_VALIDATION`，现有 `run_qlib_candidate_validation` CLI只产生proof，不能独立补足workflow job/event/import链。不能调用Worker/ModelRegistry构造器抢owner，也不能重置STAGED、删失败记录或手工宣称COMPLETE。本轮没有提交任务。

## 原D合同与legacy/revalidation差异

98d归档为 `workflow/jobs/578bb6107577406185782a08e1c499d7/output/runs/archived-oct26-ridge-binding-check-20261028-95bb73b441089e26`。header真实completed为Oct27 17:44:38Z、data_end Oct26、模型Ridge；没有rolling_plan。原D在206行硬取 `header['rolling_plan']['data_version_id']`，在ACTIVE校验前即失败；直接verify异常栈见[诊断原件](evidence/step1-oct28-evening-fixed-scope-20261028/readonly-C-D/D-header-diagnosis-20261028T121624Z.json)。不能把这个错误写成已验证旧ACTIVE不变。

即使增加真实Oct28 proof，原D在324–335行要求候选事件严格恰好TRAINED→VALIDATING→VALIDATED→STAGED，而registry给已STAGED模型的新验证追加INPUT_VALIDATED，仍不符合原首次验证合同；原D还要求真实rolling plan与相应源码清单。不能往旧header补plan、删事件或放松原auditor使其通过。需要独立legacy与revalidation只读合同、真实archive/binding/全部proof关联及负例审查；这项适配尚未完成或验收。

下一步只在新68d预测与Oct28 signals真实完成后，以独立新current scope运行原C，保留本次epoch失败。固定29589 E保持原日/原模型范围，新ACTIVE readiness另行核验。Ridge98d只读适配与真实proof仍属未完成；全STEP1不因正式版本出现、局部会计通过或自动promotion而完成。
