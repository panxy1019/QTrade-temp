# Qlib 数据版本目录与受保护回退

日期：2026-10-27。状态：核心实现和本地验证完成；生产接入验收由 STEP1
总计划记录，本文不声称执行过生产回退。

## 复用审计与范围

复用 `DataVersionCatalog` 的 immutable Provider 验证、单进程 SQLite 所有权、
CAS head 和事件日志。回退并非交易操作，不使用 vn.py gateway 或另造版本数据库。
新增请求回执与原 head/event 在同一事务提交；进程崩溃后的相同请求重放返回原回执，
即使之后 head 再次变化，也不会再次回退。同一 request_id 改写命令会失败。
`LIVE_TRADING_ENABLED=false`，不触发订单或模型激活。

## 控制契约

`qtrade.research.data_version_control.command` 只接受以下字段：

| 字段 | 约束 |
| --- | --- |
| request_id | 32 位小写十六进制；浏览器重试保持不变 |
| version_id | 已准入的回退目标，64 位小写十六进制 |
| expected_head | 用户提交时看到的当前版本，必须与目标不同 |
| expected_event_id | 用户看到的当前 head 事件摘要，防止 A → B → A 重用旧授权上下文 |
| actor | 服务端认证主体映射的 `[A-Za-z0-9._-]{1,60}` 标识 |
| reason | DATA_QUALITY_REVIEW、PROVIDER_REGRESSION 或 RESEARCH_REPRODUCTION |

网页入口必须复用现有登录、同源验证和 HMAC 队列。`signed()` 是证据摘要，不是
用户认证或授权。命令里禁止路径、脚本、任意 SQL、实盘开关。服务端决定数据库及
归档根目录；用户不能指定它们。CLI 只用于可信集群操作与验证过队列的消费者。

`apply(catalog, command, now=aware_datetime)` 完整验证目标归档中的 Provider 文件，
再检查已准入身份和 expected_head。损坏、未知、同版本或过期 CAS 均无法改 head。
成功回执含 event_id、previous_head 和命令摘要，可独立归档。有效格式但过期或目标
校验失败的命令在相同事务中保存 REJECTED 回执；重放返回同一拒绝结果。
CLI `--output receipt.json` 可将回执写入不可变文件，供 worker 可靠读取。

## 单写者与自动链路

调用 `scripts.data_version_control rollback` 的短生命周期子进程使用与自动构建一致的
`qlib-provider-build.lock`；持锁后再实例化版本目录。若上一目录所有者仍存活，操作
拒绝抢占。不得在长期 workflow worker 中实例化另一个 DataVersionCatalog。

自动构建在获得相同锁后还须重新校验准备阶段读取的 head 和事件身份；否则准备期间
发生的人工回退可能被旧构建覆盖。旧 daily request 与新输入冲突时保留 REVIEW_REQUIRED，
不静默重写其历史身份。

每次回退创建新事件。预测与信号必须绑定 `head_event_id`，不能只比较 version_id：
A → B → A 也需要新的输入有效性判断。旧结果保留为历史；禁止通过回退使其重新成为
当前预测。版本回退不会自动认证历史 PIT、更新数据时间或赋予 PAPER 执行权限。

## 只读目录

`read_catalog(database, archive_root, limit=20, before_sequence=None)` 使用 SQLite
`mode=ro`，不创建数据库、不获取单写者身份。一次快照返回 head、head_event_id、
最近版本及事件；limit 范围 1–100。事件用 next_before_sequence 获取更早页。
版本列表仅提供最近 limit 个，versions_truncated 明示是否截断。
同时返回最近最多 100 条回退回执；尚未迁移回执表的旧目录返回空列表。
`validate_public_catalog()` 检查传输清单的摘要、权限声明、精确字段、数量上限、
head/event 引用关系、版本/历史事件和回执格式，可供 online.json 发布与 API 共用。

目录校验清单签名摘要、数据库所存清单身份和事件摘要；为避免一次网页读取扫描整个
Provider，明确标记 `MANIFEST_IDENTITY_ONLY_FULL_BYTES_CHECKED_BEFORE_ROLLBACK`。
回退前会重新验证全部文件，不能把目录列出解释为字节校验或完整 PIT 认证。

## 复现

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
python -m pytest tests/test_data_version_control.py tests/test_data_versions.py -q
python -m ruff check src/qtrade/research/data_versions.py src/qtrade/research/data_version_control.py scripts/data_version_control.py tests/test_data_version_control.py
```

只读集群目录示例（无需停止数据服务）：

```bash
PYTHONPATH=src python -m scripts.data_version_control catalog \
  --database /root/qtrade-local/qlib-versions.sqlite \
  --archive-root "$B/unified-data/qlib-versions" --limit 20
```

测试覆盖事务故障回滚、幂等重放、请求 ID 冲突、失效 CAS、Provider 损坏、目录摘要
损坏、目录读者不夺写者身份，以及有界列表与事件分页。真实生产回退不是测试要求，
应在隔离版本目录验证写入。生产目录的只读可达性、登录提交入口和旧预测失效由集成
层另存验收证据。
