# Qlib 在线模型控制

状态：集群验收通过；生产 API/页面验收进行中。2026-10-26。

运行源 978553a：819 项 Linux 检查、219 项前端检查通过，前端 lint/build、
src 类型检查及新增控制脚本类型检查通过。两份真实 Ridge/LightGBM 归档已
完整复验并注册为 STAGED，各有 888 只当日输入分数，无 ACTIVE。
模型库一致性备份 integrity_check=ok。证据：
`docs/evidence/qlib-model-control-cluster-20261026.json`。
扩大至所有旧 scripts 的额外 mypy 检查仍有 66 项既存问题；本次未改写这些
无关历史脚本，不能声称全仓脚本类型检查通过。

## 页面与权限

`#/models` 展示最新合格 Qlib 数据版本、当日缺口、冻结候选、输入验证和
操作回执。公开访问只读。复用研究登录后可以审阅激活或回滚；审批明确绑定
模型、当前数据版本、当前 ACTIVE 指针和输入验证身份。

展开候选或打开审批窗口可见历史样本外区间、IC/RankIC、净年化、Sharpe、
回撤、换手和成本假设。历史表现不是已观察的前向收益。当前观察池存在
幸存者偏差；历史 PIT、复权及精确交易规则尚未完整认证。

当前 AUTO_PREDICT 未接入，自动重训 OFF；激活只是研究模型选择，不会启用
实盘、自动重训或网关。LIVE_TRADING_ENABLED=false。

## 生命周期与持久化

已验收归档经完整文件哈希、冻结处理器和新数据输入验证后导入。
导入记录 TRAINED → VALIDATING → VALIDATED → STAGED，绝不自动激活。
审批通过后原 ACTIVE → RETIRED，目标 → ACTIVE。回滚只能选择曾经激活的
RETIRED 模型，且必须重新满足当前数据输入验证和时效条件。

Vercel 只将签名的 allowlist 操作写入现有私有 Git 队列。集群 worker 重新
验证实际归档和数据，重查当前交易日；最后短暂锁定数据 head，将模型状态、
ACTIVE 指针、回执和审计事件在本地 SQLite 同一事务提交。拒绝保持原指针。
同请求重试返回原回执，不会撤销后续审批；同请求标识绑定不同内容则拒绝。

本地库 `online-models.sqlite` 位于现有 workflow SQLite 同目录，不能放在
共享 NFS。不可变模型/验证映射保存于 `online-research/control`。唯一 worker
持有库；导入候选需在启动 worker 前或停机维护期间进行，不并发抢占所有权。
worker 将一致性备份保存为 `online-research/control/catalog-backup.sqlite`；
该副本不是活跃数据库。恢复须停机、核对队列回执和归档后人工执行，不能
把较早备份中的模型指针静默覆盖当前审批。

## 复现与维护

```bash
python -m scripts.stage_qlib_model \
  --base "$B" --database /root/qtrade-local/online-models.sqlite \
  --data-database /root/qtrade-local/qlib-versions.sqlite \
  --archive "$VERIFIED_MODEL_ARCHIVE" --validation "$VERIFIED_INPUT_VALIDATION"
python -m pytest tests/test_model_registry.py tests/test_online_model_control.py \
  tests/test_online_research.py tests/test_qlib_worker.py
cd web && npm test -- src/pages/OnlineModelsPage.test.tsx
```

`GET /api/research/online/catalog` 返回独立有界摘要；`POST .../actions` 需要
登录和同源请求。候选目录每 15 秒刷新，发布沿用现有五分钟通道，心跳超过
12 分钟关闭操作。浏览器排队成功不等于集群已应用，应核对 APPLIED/REJECTED
回执。缺失或损坏的可选模型目录发布 FAILED 空目录，不能阻塞行情更新。

仍待交付：自动数据版本生成、数据事件触发预测、滚动训练及自动注册候选、
完整预测历史、PAPER/SHADOW。候选当前日验证不等于每日自动预测已运行。
