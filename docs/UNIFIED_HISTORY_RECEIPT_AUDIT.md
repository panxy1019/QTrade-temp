# 长期历史回补：离线收据验收

2026-10-26。本次只在独立 worktree 同步 `origin/main` 344a6bf 并保留历史分支提交，
未 SSH、部署、请求来源、修改主工作区或真实采集产物。主代理报告部署源 5d4e7bf 已通过
977 项 Linux 检查；该现场结果由主代理持有，本地不把它当本次新代码已经部署的证据。

## 审计发现与修复

| 边界 | 审计结果 |
|---|---|
| 新月份让行后由 retry 实际完成 | 发现 `before` 只在非 retry 时推进，下一次 new 会重复查询该月份；现改为完成当前 frontier 就推进，重试旧月份不能倒退游标 |
| 已部署旧游标滞后 | 恢复时重放原始、Parquet 和绑定收据后修正；不发来源请求，不新增或覆盖历史对象；缺收据或错绑定则拒绝修复 |
| 月份端点及空响应 | 一年两频率的全部 26 个日历片段恰好请求一次，含起止碎片；空响应仍为零覆盖、有独立重试，不会变为合格历史 |
| receipt 已封存、检查点未完成 | 汇总列为待恢复，实际观测不计 accepted 覆盖；正常恢复后才计入，原始对象字节保持不变 |
| retry 扩充、回退和价格冲突 | 只计当前 accepted head；完整超集不重复计数，旧收据保留为 superseded；回退或冲突仍保留旧 head |
| 存储/进度统计 | 进度属于收据记账，不是新的一次全量重读；新增离线工具实际重放并核对 accepted_quality/行数，损坏或虚增计数拒绝计入 |

恢复返回值增加 `repaired_frontiers`，与已重放/中断次数分开。只有实际已有、正确绑定且完整
重放通过的收据能推进旧 frontier；不凭计划、文件名或服务存活推断数据已取得。
额外复核 receipt 的阶段计数与 raw、一致的原始价格基准以及未认证/PIT 权限字段。

## 主代理可执行的汇总命令

在已检验源码目录、原持久卷根 B 中运行，使用现有 root，命令不访问来源：

```sh
export PYTHONPATH="$PWD/src" LIVE_TRADING_ENABLED=false
REVISION=$(git rev-parse HEAD)
HISTORY_ROOT="$B/unified-data/backfill/execution-v1"
"$B/venv/bin/python" -m scripts.audit_history_backfill \
  --root "$HISTORY_ROOT" --source-revision "$REVISION" \
  --seconds 60 --maximum-receipts 10000 --maximum-details 5
```

需要保存证据时加 `--output <新的审计JSON路径>`；输出采用不可变写入，已有文件不会覆盖。
工具取得同一个本地 history writer 非阻塞租约，固定检查点快照；不持有 BaoStock 租约，
不会影响当前行情/PIT 取数。writer 正忙时退出 2 并输出
`HISTORY_AUDIT_YIELDED_TO_ACTIVE_WRITER`，稍后在空闲期重跑，不能把让行解读为零覆盖。
只读汇总不调用 `HistoryQueue` 的修复/写入入口，不更新游标、service-state 或 execution-latest。

关键字段：

- `frequency_coverage.day/5min.verified_accepted_rows`：实际 raw/Parquet/质量重放后，检查点
  当前 accepted 收据的行数；日线与五分钟分开。`qualified_instrument_count`、
  `verified_observed_sessions`、真实起止日期同样来自这些收据，起止跨度不代表连续覆盖。
- `latest_window_states` 和 `gap_examples`：空响应、部分会话、失败、隔离及重试；
  `planned_scope_count_metadata_only` 等仅为计划元信息，永远不进入合格覆盖。
- `verified_receipt_attempts`、`verified_query_attempts` 与
  `downloaded_rows_including_reobservations`：实际被验证的尝试/查询/下载量，包含重试，
  不当作去重后的合格覆盖。`producer_code_revisions` 保留实际来源执行代码身份。
- `pending_sealed_claims` / `unreferenced_sealed_receipt_states`：已封存但尚未被检查点接纳的
  证据，不计 accepted 覆盖。`superseded_archived_receipt_states` 是正常旧重试归档，
  不伪报为需要恢复或额外覆盖。
- `checkpoint_counts.integrity_errors`、`error_examples`：检查点、作用域、checksum、
  Parquet、收据计数或游标遗漏问题。详情被截断时，错误总数仍然有效。
- `checkpoint_inputs_sha256`、`verified_receipts_sha256`、`accepted_receipts_sha256`：
  绑定本次实际检查点、收据集合和 accepted heads；实现文件及观察/代码/主表/计划身份也在输出。

`VERIFIED` 表示本次指定 root 的收据验收完成且无完整性错误，**不表示请求历史已经补齐**。
全空窗口也可以完成收据验收，但 accepted 行数仍为零。`VERIFIED_WITH_PENDING_RECOVERY`
表示需按已有有界执行器恢复；`INVALID_EVIDENCE` 不可作为正式覆盖验收；
`PARTIAL_AUDIT_LIMIT` 表示达到时间/收据上限，只提供已验证子集，不能外推整体覆盖。
除 VERIFIED 退出 0 外，其余状态退出 2。范围绑定错误、缺少原冻结输入等直接失败。

所有结果继续保持原始 BaoStock `adjustflag=3` 未认证基准，与旧 qfq/模型/Qlib 版本独立。
历史 available_at、独立来源、完整 PIT 和十年认证均未建立；不生成交易信号或修改 LIVE。
工具当前只接受原 observed master/plan，不把其他待执行计划自动扩为已取得覆盖。

## 本地证据与待集群验证

新增针对性检查包括完整窗口序列、yield→retry 游标、旧 frontier 的无下载恢复、旧重试不倒退、
漏月份检测、空响应零覆盖、半提交收据、superseded 去重、虚增计数/损坏文件/错阶段拒绝、
只读性、收据预算、writer 让行、不可变报告。专项 106 项通过，1 项 Linux 专属检查跳过；
相关 169 项通过、1 项跳过，五份脚本 Ruff/mypy 通过。Linux 相关命令应执行全部 170 项。

```sh
export PYTHONPATH="$PWD/src" MYPYPATH="$PWD/src" LIVE_TRADING_ENABLED=false
python -m pytest tests/test_history_backfill.py tests/test_unified_backfill_plan.py \
  tests/test_probe_unified_history.py tests/test_unified_store.py \
  tests/test_collect_unified_data.py tests/test_unified_queue.py tests/test_pit_collection_queue.py
python -m mypy --explicit-package-bases --platform linux --follow-imports=silent \
  scripts/history_backfill_queue.py scripts/history_backfill_store.py \
  scripts/audit_history_backfill.py scripts/run_history_backfill.py \
  scripts/run_history_backfill_service.py
```

主代理在同一 root 做真实收据汇总，并核对 source backoff、当前服务心跳和异常收据。
若出现 pending/cursor repair，先保留审计报告，再由原执行器执行正常恢复；不要手动删除
失败或空窗口、改写已封存收据、创建新 root 绕过退避，或用计划数代替来源实际行数。
