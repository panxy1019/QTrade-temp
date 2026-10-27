# 新模型 PAPER 恢复与冻结执行窗口修复

时间：2026-10-27，上海时间；不同捕获保留各自实际时钟。

## 已实际完成

- PR33 合并后的生产版本为 `4b86145d314e66deba2fda111bbe6ee4b1464605`。
  23:13 新 ACTIVE/source096 的完整实际公开 C 验收通过，完整1500公开探针也通过。
  日线1497、分钟1496的覆盖仍是PARTIAL，不是完整全天SLA。
- 用户明确回复了指向新账户的恢复问题。新 PAPER
  `137ff4cdfc5c201ff2f733893073fff2beda156156ae5f39b45245a9692e9ac0`
  使用请求 `9ea345992d804e16aadd570ebeef805f` 在23:15:08消费一次并成功对账恢复。
  原 owner175982未重启、原消费授权未复用、实盘保持关闭。
- 23:27 真实公网响应确认该新账户 running=true / recovery_required=false。
  会话及成交仍为零，10月28日执行目标尚在未来。

原始请求、消费凭证、恢复结果、只读日志核验，以及生产发布前后相邻deployment/
HTTP响应与哈希均保留在 [原始证据目录](evidence/qlib-forward-slot-20261027/capture-files.json)。
恢复前的HUMAN_RECOVERY_REQUIRED捕获继续保留，不追改为新状态。

## 为什么服务仍是 DEGRADED

当前14账本保留历史退休模型及当前模型。5组基线及当前模型各2个模式，共12项新
提案注册触发SQLite `targets.execution_at UNIQUE` 约束。它们在同一个执行窗口
已有合法冻结目标，新的worker源码产生了新的信号/预测身份。不能因此覆盖原目标。

原测试替身没有真实唯一约束，未揭露这一问题。新增真实 `ForwardPaperBook`
回归先复现原始 IntegrityError，再验证保留首个已准入目标的修复。原失败XML保留。

修复在原data/model guard内验证旧目标身份、签名准入、data epoch、当前批准和未来
时钟。之后只写后续提案的不可变“未准入”收据，保留原目标、准入、archive-link、
会话及成交日志。收据限256份，损坏或改变上下文则阻断，幂等重试不改原字节。
这不授予任何恢复、模拟成交或实盘权限。

## 核验与部署边界

Windows相关150项测试通过，无跳过；Ruff通过，设置MYPYPATH=src的scoped mypy通过。
首次默认Windows临时路径超过系统路径长度，7项夹具初始化失败；原XML保留，改用短
临时路径后54项全通过，再补边界后运行150项相关套件。没有删用例或增加跳过。

```powershell
$env:PYTHONPATH='src;.'
python -m pytest tests/test_forward_service.py tests/test_forward_paper.py tests/test_forward_paper_adapter.py tests/test_forward_publication.py tests/test_forward_prediction.py tests/test_forward_transition_audit.py tests/test_reconciliation_recovery.py -ra --basetemp E:/QTrade-temp/fs-review-new
python -m ruff check scripts/run_qlib_forward_service.py tests/test_forward_service.py
$env:MYPYPATH='src'
python -m mypy --follow-imports=silent scripts/run_qlib_forward_service.py
```

当前修复**尚未部署**，原forward仍为e107源码/PID175982。下一步先核验隔离Linux
源码及真实账本副本，保存副本前后目标/准入/会话/成交指纹；不得把副本通过当成生产修复。

若必须切换owner，应先给出可复核的定向交接：只停止forward owner、保全账本/目标/
准入/恢复凭证，禁止启动参数复用初始化actor；新owner恢复前执行对账，重新签发与
新instance绑定的短期一次请求。原用户问题明确保留原owner不重启，故当前新账户的
单次恢复批准不自动解除这项约束；不能热改已加载代码或谎报其revision。

## 仍未通过的门禁

- 生产12项注册错误消失及新的真实公开14账本交付，不能由隔离测试替代。
- 退休模型账本的生命周期与退出规则；固定12本auditor不能静默忽略实际14本。
- 新账户10月28日的跨日持仓/阻断/估值及公开交付，未来事件不得提前认证。
- 策略中心旧入口穷尽及完整登录点击视觉检查，历史PIT/复权/多年分钟连续性。
- STEP1全通过后才进入STEP2；rolling OFF，LIVE_TRADING_ENABLED=false，资源预算不变。
