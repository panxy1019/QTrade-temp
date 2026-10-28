# STEP1 独立执行日与 STAGED 再验证审计候选

状态：非生产 reader 已实现并完成本地合同/回归测试，等待独立代码审查、Linux I/O 检查与真实新 reader 运行。没有将新 reader 部署到生产入口，没有排 job、执行 inference/training、改原审计器、补旧档、恢复 PAPER、改变 policy/ACTIVE 或构造缺失会话。全 STEP1 仍未通过。

## 明确的范围

E 使用 `EXECUTION_DAY_FROZEN_ADMISSION_V1`，固定 Oct28、29589b44 与旧 approval 6f1ec61b。它核开盘前最后真实批准、唯一 aware execution instant 的首 target、原 admission、原 archive 重建 target、精确 READY/ROLLBACK epoch、真实当天 session、raw market replay、PAPER 会计及 durable fills。盘中停机仍失败；收盘后退休/停止不会抹掉已经存在的当天观察。退休 closure 不能代替 session。ROLLBACK 必须保留原 command/receipt，不能改用 READY 或当前 epoch。

`CURRENT_READINESS_V1` 使用另一组显式 expected model/approval；本轮实际是 68d9287e / 750de28e，其 CONTROL_APPLIED 发生于 Oct28 20:07:51。当前公开状态、新模型本簿缺失/未运行、PAPER readiness 与历史观察单列。报告分别给出 `frozen_execution_ledger_accepted`、`frozen_execution_public_projection_accepted`、`current_readiness_accepted`，全 STEP1 标志始终 false。提供的 publication 只是被验签的内层投影；真实 HTTP 原始字节、deployment 相邻绑定与传输验收仍由外部 capture 保留，新 reader 没有把内层自带 checksum 冒称真实 HTTP。

D 显式选择 `LEGACY_BOUND_RESEARCH_V1` + `STAGED_INPUT_REVALIDATION_V1`。训练来自真实 QLIB_RESEARCH_JOB 的 spec.data_binding、唯一 strategy/job/model 关联、固定训练 version、完整 archive 与原 fitted restore；不填 rolling_plan。原四阶段 staging 及原 proof 必须保留，其后只允许唯一、完全登记的 INPUT_VALIDATED；激活、孤立/重复 proof、refit、处理器变化均拒绝。每个已登记 proof 重新做 archive、inference contract 与 feature/score Parquet 检查；指定 later 还核 deterministic job、真实 job request/result/event、登记、raw day 与前后短只读 SQL facts。

D 还强制显式原 source manifest + SHA；完整 source 文件集合、size、SHA256、Git blob SHA1 均匹配。既有 096 manifest 580 files 有原声明 4 overrides / 2 removed，必须原样保留，不冒称纯 Git 树或新的源码审计。目标真实 later job/proof 不存在时提前 BLOCKED，不执行验证器制造输入。

## 代码与测试

- 新 E：`E:/QTrade-git/scripts/audit_qlib_execution_day_scope.py`，SHA `f1ffc80fdab0d45621bb8ff8a473e5c402e1b3c7c3d33ae4455a83811c3e2c5a`。
- 新 D：`E:/QTrade-git/scripts/audit_qlib_staged_revalidation.py`，SHA `a33c3d172735f22daecb070b24921e87502461d967ea4cb7464cacca5ab3beee`。
- 最终新测试 80 passed / 0 failed / 0 skipped，`step1-scoped-final-new-20261029-v6.xml`；ruff 四文件通过。
- `step1-scoped-final-exact-20261029-v5.xml` 为窄修前 E ded438 + 同 D a33c 的完整 154 项（新78 + 原 E/D76），全部通过。它不能充当新 E f1ffc 的精确证明；最终新增两条同 execution instant 重复 target 负例已经在 v6 的80项中通过。
- 最初缺显式 PYTHONPATH 的 collection error XML 原样保留；随后显式 src/. 路径解决测试环境问题，没有修改原 auditor。
- 原 strict E `a7634c1cbb5987ebdaa90bda06319405d8b975cb743a745048afa126aa8215f4`、D `0a4d7c85933aa4f6e6a829d5a20265cf93f70d57462b13d42d2e863cadfcd878` 均逐字节 matches HEAD；所有旧失败继续保留。

新80项含实际本地 SQLite/raw/Parquet/accounting，真实历史 legacy metadata（只作合同 fixture）、重签名坏 metadata、初始 proof+追加 proof 全关系、双 hash/full fileset、mode=ro/query_only 拒绝写入，以及 scope/clock/zero fill/未知约束等拒绝。没有真实新 Oct28 Ridge proof，因此没有宣称真实 D 完整正向运行，也没有宣称这个新 E reader 已经通过真实集群验收。

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
python -B -m pytest -q tests/test_execution_day_scope_audit.py tests/test_staged_revalidation_scope_audit.py --junitxml=E:/QTrade-temp/scoped-new-rerun.xml
```

## 一次真实短只读事实采集

实际 2026-10-29 00:28:33 上海 / Oct28 16:28:33Z，原件 `facts.json` / `facts.stdout` 来自唯一一次短 RO SQL/文件读取，helper 仅导入 connect()，没有读取 helper 正文、凭证或环境配置。raw stdout SHA `da7fa2ff370f535a10c08ed2f7a97db6b3ca97036ca35e68dfdd3ba020dcd791`。

- roster 16 本；Oct28 COMPLETE 12 本：10 baseline + 295 PAPER/SHADOW。
- 旧 2d78 PAPER/SHADOW 有当日冻结 target/admission、没有 Oct28 session，按既有退休 closure 核，不补会话。
- 新 68 PAPER/SHADOW 没有 Oct28 target/admission/session，属于收盘后新模型 readiness，不列作原295当天新模型缺失观察。
- ACTIVE 仍 68，activation_count=1，actual activation 为 Oct28 12:07:51Z。
- 指定 98d STAGED、activation_count=0、activated_at=null；只有原 Oct27/09a67 proof f7619722，事件恰好原四阶段；Oct28/b82 validation job=0，INPUT_VALIDATED=0。D 缺项继续真实 BLOCKED，不主动提交验证。

这次是事实快照，没有执行新 reader 的完整真实 archive/raw 重放，不能作为新 E/D PASS。

## 审查后只读运行参数

使用独立新 output 名称；没有 `--now` 或 mutation/repair/enqueue/activate/recover 模式。先冻结运行源码/原 helper hashes。PUBLIC_CAPTURE 必须来自新的真实 HTTP+deployment capture；无需浏览器。以下仅复现参数，不表示已执行：

```bash
python -B -m scripts.audit_qlib_execution_day_scope \
  --base "$B" --local-root /root/qtrade-local/forward-research \
  --model-database /root/qtrade-local/online-models.sqlite \
  --workflow-database /root/qtrade-local/workflow.sqlite \
  --data-database /root/qtrade-local/qlib-versions.sqlite \
  --published-forward "$PUBLIC_CAPTURE" --day 2026-10-28 \
  --frozen-model 29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487 \
  --frozen-approval 6f1ec61bda1a28d918dd331ad0e0ae6ea7ebcc39320f8e4dade424bf726bd238 \
  --expected-current-model 68d9287efc49c24280ab8c6e4df5c69724c0e6e9876396e7312a6b771405a81c \
  --expected-current-approval 750de28e05933013e4cacf828a10ee28f638861d7d3adbc6ef7566f0d09cd510 \
  --output "$NEW_OPERATOR/E.json"

python -B -m scripts.audit_qlib_staged_revalidation \
  --base "$B" --model-database /root/qtrade-local/online-models.sqlite \
  --workflow-database /root/qtrade-local/workflow.sqlite \
  --data-database /root/qtrade-local/qlib-versions.sqlite \
  --strategy-database /root/qtrade-local/strategies.sqlite \
  --training-contract LEGACY_BOUND_RESEARCH_V1 \
  --validation-contract STAGED_INPUT_REVALIDATION_V1 \
  --model-id 98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795 \
  --training-job 578bb6107577406185782a08e1c499d7 \
  --training-version d33a74663373b1d4a0d7cf1ecf1622b34657a6fda08aacc7bacadb70d47ec4fa \
  --later-version b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5 \
  --validation-source 096387b8088e4361c9e5cd9cd2c165e21819fead \
  --expected-current-model 68d9287efc49c24280ab8c6e4df5c69724c0e6e9876396e7312a6b771405a81c \
  --expected-current-approval 750de28e05933013e4cacf828a10ee28f638861d7d3adbc6ef7566f0d09cd510 \
  --training-source-manifest /root/qtrade-local/operator-evidence/archived-version-578bb610-20261028/source-verification.json \
  --training-source-manifest-sha256 927a94559103916de0eacfda382634d1fd8586e63cbc4336f78707f52c15fb03 \
  --output "$NEW_OPERATOR/D.json"
```

预期 ACTIVE、approval、source 或实际 head 随时间变化时，不静默追踪新值；先记录旧 scope 失败，再明确新 scope。该 reader 不授权补任务或恢复新 PAPER。

已知限制：未在 Linux 执行本次新 reader；尚无真实新 D proof；未为新 reader 做完整真实 E I/O 运行；当前只有 supplied-publication 投影检查，没有自建 HTTP/deployment capture；SQLite 前后校核不是跨库事务锁；内容 checksum/记录时钟不是外部时间戳或独立 HMAC 身份验证。所有原失败、旧 gate、PIT/交易状态限制与 browser/全STEP1未完成状态保留。
