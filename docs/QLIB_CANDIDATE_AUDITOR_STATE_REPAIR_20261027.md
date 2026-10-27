# 候选模型验证事件链审计修复

实际 2026-10-27 20:48 的 D 审计没有通过，错误为
`CANDIDATE_AUDIT_JOB_EVENT_CHAIN_INVALID`。原失败报告保存在集群
`/root/qtrade-local/operator-evidence/close-20261027T124635Z-3d14e77c/D.json`；
不得覆盖原报告或修改数据库事件以满足审计器。

只读查询显示任务 `6e256470efc5ea2f68e51bc016436027` 的真实事件为：

| 状态 | 实际 UTC |
| --- | --- |
| QUEUED | 2026-10-27T12:26:09.829713+00:00 |
| RUNNING | 2026-10-27T12:26:09.876110+00:00 |
| VALIDATING | 2026-10-27T12:26:30.757843+00:00 |
| COMPLETE | 2026-10-27T12:30:17.673210+00:00 |

源码与缺口审计：`run_qlib_candidate_validation.py` 写入 `VALIDATING` 状态，
worker 轮询到它时追加一次对应事件；子进程在两次轮询之间结束时则不会追加。
旧审计器仅接受三态序列，误拒绝了合法四态序列。vn.py 的交易网关不处理这个
研究任务审计边界，直接复用现有 worker、不可变档案及审计器。

修复仅允许两种完整精确序列：`QUEUED → RUNNING → COMPLETE`，以及
`QUEUED → RUNNING → VALIDATING → COMPLETE`。首尾、时钟、任务身份、来源版本、
唯一验证档案、原 ACTIVE 和批准凭证等校验保持不变；未知、重复、错序及失败状态
仍拒绝。审计仍是只读操作，不会执行训练、验证或激活模型。

复现：`PYTHONPATH=src python -m pytest tests/test_candidate_transition_audit.py -ra`。
Windows 47 项通过，无跳过；Ruff 与脚本 mypy 通过。回归测试使用真实 SQLite、
原始响应及 Parquet 测试档案验证四态路径，并检查数据库字节与模型激活状态不变。
覆盖坏链、异常时钟及改动来源身份的拒绝。

此处记录的是源码修复与测试，不是实际 Linux 重审或 D 门禁通过。实际重审需要
新的输出文件，并保留旧失败证据；整阶段 STEP1、公开交付和浏览器验收另行判定。
