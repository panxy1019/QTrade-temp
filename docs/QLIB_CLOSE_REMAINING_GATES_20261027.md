# 10 月 27 日剩余 C/D/股票前向验收

这是源码只读复核后的操作清单，**不是这些门禁已经通过的凭证**。实际进度和不可变证据见 [收盘恢复记录](QLIB_CLOSE_EXPORT_RECOVERY_20261027.md)。所有检查使用真实时钟、新输出路径和只读数据库连接，保持 `LIVE_TRADING_ENABLED=false`。原 PAPER `175982` 不重启、不恢复，不再次使用已消耗的恢复授权。

## 顺序与状态含义

1. 等现有冻结 owner 生成真正的 10 月 27 日版本：`data_heads(day)` 指向新版本，`actual_end=2026-10-27`，完整版本、provider reload、READY admission 和自动 `completed.json` 一致。准备完成数不等于预测股票数；首遍结束后构建器可以接纳已合格子集，仍需公开剩余重试/排除。
2. 原 worker 用批准的 ACTIVE 和原 approval 自动预测。新 version、当前 admission event、实际 worker revision 都必须匹配，任务为 COMPLETE。
3. 候选后续版本验证与 signals 随后才可调度。`rolling OFF` 只阻止新的滚动训练，不阻止独立候选验证，也不批准激活。当前调用顺序为 prediction tick → candidate validation → signals → rolling，因此 D 任务通常先占用 worker。
4. prediction 与 signals 归档完成后跑 C；D 单独验证候选后续版本证据。再等股票 forward 普通周期清除旧 provider/discovery 错误，分别核对本地与实际公开交付。

C 的这次真实公开验收必须在 **2026-10-28 09:30 上海时间之前**完成；其结束时下次执行开盘须仍在未来。错过窗口应保留失败，不能改时钟补验。D 后来的历史审计不要求该 later version 仍是 serving head，但验证生成时必须处于其正确窗口。

## 只读定位精确产物

实际数据库当前位于 `/root/qtrade-local/qlib-versions.sqlite`、`online-models.sqlite`、`workflow.sqlite`。连接使用 SQLite URI `mode=ro`、`PRAGMA query_only=ON`、短事务和显式关闭；不要构造会建表或恢复任务的 writer/registry 对象。

下面的命名参数由实际已验证的产物填写。单一 head、READY event、prediction、job 或 validation 身份的 lookup 保留全部状态并最多查三行：零行表示缺失，多行表示歧义，不能挑一个成功结果或直接取“最新一行”。这项唯一性要求不适用于多模型集合或完整事件链：最后的模型查询正常应为 ACTIVE/候选各一行，job events 正常应为 QUEUED → RUNNING → COMPLETE 三行。完整事件审计仍交由现有审计器。

```sql
-- DATA：正式 head 与 READY event。
SELECT h.frequency,v.version_id,v.actual_end,v.manifest_sha256,v.admitted_at
FROM data_heads h JOIN data_versions v USING(version_id)
WHERE h.frequency='day' LIMIT 3;
SELECT sequence,event_id,kind,payload FROM data_events
WHERE version_id=:version AND kind='QLIB_DATA_READY'
ORDER BY sequence LIMIT 3;

-- MODELS：预测任务不在 workflow.jobs。
SELECT prediction_id,state,updated_at,error_code,
       json_extract(request,'$.bindings') AS bindings,
       json_extract(result,'$.evidence_sha256') AS proof_sha256
FROM prediction_tasks
WHERE json_extract(request,'$.bindings.data_version_id')=:version
  AND json_extract(request,'$.bindings.data_event_id')=:epoch
  AND json_extract(request,'$.bindings.model_id')=:active
  AND json_extract(request,'$.bindings.approval_sha256')=:approval
  AND json_extract(request,'$.bindings.code_revision')=:revision
ORDER BY rowid LIMIT 3;

-- WORKFLOW：候选验证跨所有 revision 检查，禁止挑选成功者。
SELECT id,state,updated,error,request,result FROM jobs
WHERE json_extract(request,'$.kind')='QLIB_MODEL_VALIDATION'
  AND json_extract(request,'$.model_id')=:candidate
  AND json_extract(request,'$.data_version_id')=:version
ORDER BY rowid LIMIT 3;
SELECT id,state,updated,error,request,result FROM jobs
WHERE json_extract(request,'$.kind')='QLIB_SIGNAL_JOB'
  AND json_extract(request,'$.data_version_id')=:version
  AND json_extract(request,'$.data_event_id')=:epoch
ORDER BY rowid LIMIT 3;
SELECT rowid,id,at,state FROM events WHERE id=:job ORDER BY rowid LIMIT 5;

-- MODELS：候选验证注册凭证及模型状态。
SELECT validation_id,model_id,data_version_id,proof FROM model_validations
WHERE model_id=:candidate AND data_version_id=:version LIMIT 3;
SELECT model_id,state,registered_at,activated_at,activation_count
FROM models WHERE model_id IN (:active,:candidate) LIMIT 3;
```

原 ACTIVE、approval 和候选身份以当前已核验冻结记录为准，不从页面显示名推断。10 月 27 日准备请求固定源 `55b5b0044649a28fd84c77364ca9908df0cee7a4`；不可为调度修复更改该请求或谎报旧源码。生产 Vercel main SHA 与 cluster worker revision 是两个不同的校验值。

归档必须从查询结果的绑定定位：

```text
prediction: $B/online-research/control/predictions/$PREDICTION_ID/output/$PREDICTION_ID
signals:    $B/workflow/jobs/$SIGNAL_JOB_ID/output/$SIGNAL_FAMILY_ID
validation: $B/workflow/jobs/$VALIDATION_JOB_ID/output/validations/$VALIDATION_ID
```

精确预测 binding 如果已为 FAILED/INTERRUPTED/SUPERSEDED，确定性 claim 不会自动回到 QUEUED；D/signals 又要求 predictor 为 COMPLETE/WAITING_FOR_APPROVAL。这是源码确认的条件阻塞点，**不是已发现真实 10 月 27 日预测失败**。出现时保留 `error_code` 与原 claim，另行审查恢复，不用重启或删除记录制造“首次运行”。

## 分别验收 C 与 D

在经审查、确实含这些审计工具的源码目录运行。下面是模板，本次文档编写没有执行这些尚未到条件的命令。

```bash
LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src:$PWD" python -B \
  -m scripts.audit_qlib_session_transition \
  --previous-version "$OCT26_ROOT" --current-version "$OCT27_ROOT" \
  --automatic-completion "$B/unified-data/qlib-schedule/days/2026-10-27/completed.json" \
  --prediction "$PREDICTION_ROOT" --signals "$SIGNALS_ROOT" \
  --database "$DATA_DB" --output "$NEW_C_REPORT" \
  --verify-production --expected-production-revision "$REVIEWED_MAIN_SHA"

LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src:$PWD" python -B \
  -m scripts.audit_qlib_candidate_transition \
  --base "$B" --model-database "$MODEL_DB" \
  --workflow-database "$WORKFLOW_DB" --data-database "$DATA_DB" \
  --model-id "$PINNED_CANDIDATE" --later-version "$OCT27_VERSION" \
  --expected-active "$PINNED_ACTIVE" --expected-approval "$ORIGINAL_APPROVAL" \
  --output "$NEW_D_REPORT_OUTSIDE_B"
```

C 做固定 origin 的 deployment/catalog/versions/signals/catalog 五次 GET，保留真实 body、headers、SHA256 和时钟，核对前后稳定身份、50 秒 HTTP 总预算、720 秒研究时效。早前的公网快照不能代替这次真实捕获。

D 要求唯一匹配的 job、归档、验证注册证据与完整候选事件链，候选零 activation，原 ACTIVE 完整行和原控制批准不变；耗时 replay 后还需复核。通过不代表自动训练来源或候选激活获准。

## 股票前向：公开包装对象必须保留

`GET /api/research/online/forward-books` 返回外层包装，股票审计 CLI 要求内层已签名 `publication`。ETF 审计可接受包装对象，不能直接照搬其输入方式。

1. 使用固定公开端点、无认证 GET，记录原始 body、实际 UTC、URL/status/headers/字节数/SHA256，并相邻捕获 `/api/deployment`，核对当时完整 main SHA。
2. 检查 `publication_available=true`、`service_fresh=true`，将内层 `publication` 写入新不可变文件，保留 raw body SHA → 提取文件 SHA → publication `evidence_sha256` 的对应关系。
3. 核对 RUNNING/无 discovery errors、十二账本齐全且在十二分钟时效内，PAPER NORMAL/running、无待恢复控制状态，然后执行下面的独立审计。过期就重新捕获。

```bash
LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src:$PWD" python -B \
  -m scripts.audit_qlib_forward_transition \
  --base "$B" --local-root "$STOCK_FORWARD_ROOT" \
  --published-forward "$EXTRACTED_SIGNED_PUBLICATION" \
  --approved-model-id "$PINNED_ACTIVE" --day 2026-10-27 \
  --output "$NEW_STOCK_REPORT"
```

必须检查 JSON 内的 `PASSED`、accepted 和全部账本结果，不能仅看 CLI 返回码。股票审计本身不证明 URL/build 来源；这部分由完整公开捕获和上述提取绑定补足。本地通过不等于公网交付，零成交不认证未知交易约束。

## 保留的限制

已通过的 ETF 只接受指定旧目标的 10 月 27 日 SHADOW 观察，不重跑修复，不推断持仓/成交/收益。任何单项通过均不代表 whole STEP1；历史 PIT、复权、多年覆盖、全池分钟缺口和浏览器渲染仍有独立限制。STEP2 等 STEP1 全部真实验收后推进。
