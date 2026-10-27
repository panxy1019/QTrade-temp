# 2026-10-28 forward 定向交接

## 授权及范围

用户明确选择“允许按方案定向交接（推荐）”，对应PR34已归档方案。这次授权覆盖一次
forward owner175982的定向替换，以及成功逐账对账后为5个基线和当前ACTIVE LightGBM
共6个PAPER账本创建新的单次恢复请求。它取代此前对这一次操作的“不重启原进程”限制。
退休模型不恢复，原已消费claim不复用，LIVE=false、rolling OFF不变。

[签名授权](evidence/qlib-forward-handover-20261028/authorization.json)保留问题对应范围；
授权记录不等于实际交接已经执行。

## 必须保留的对象

- 全部14账本、旧模型/新模型与每份目标的真实源码、数据、信号身份。
- 原targets、sessions、admissions、archive-link与fill_journal内容；不得重写冻结窗口。
- 原recovery请求、consumed、receipt及此前成功/失败证据。
- 采集、workflow、发布、ETF与回补服务不在此次停机范围。

新owner不携带initialize-paper参数。启动后全部PAPER先保持停止，只有这次授权的
6个当前账户在成功对账和新instance短期请求后恢复。退休模型账本继续展示，保持停止。

## 补齐的退休生命周期

根据真实ModelRegistry当前状态与最近签名RETIRED事件识别退休，先停止运行，再核对
现金、历史成交、空持仓、无未决会话/订单/预留以及原目标/准入字节。持仓或任何不确定
状态不能被退休标签隐藏。未执行的未来目标保留并明确禁止执行；已错过的退休前会话
必须继续人工核对，不能被归入“已关闭”而消失。

关闭凭证保留原签名事件、stop事件和账本指纹。后续篡改、回滚或重新激活不能自动恢复。
恢复消费前后再次核对当前模型状态；已消费但中途失败的请求保持失败，不自动重试。

公开服务继续兼容v1格式，14账本全部保留。网页从新鲜模型目录标注当前基线、当前模型、
退休模型和未知状态，退休历史不显示为自动恢复待办。v2审计分别计数12本当前日观察和
2本退休关闭证明；后者不是新的日观察，也不是新收益。

## 已核验与待核验

- Windows相关228项通过，无跳过；前端50个文件310项通过，构建通过。
- 前端首次lint发现测试参数未使用，原失败保留；修正后lint通过。
- 后端Ruff与3个源文件scoped mypy通过；最终Linux源码/集成及实际账本副本核验待执行。
- 正式切换、6本恢复、退休2本隔离、实际公网发布仍待执行。
- 10月28日真实交易日尚未完成，不认证当日成交、持仓、估值或全STEP1通过。

```powershell
$env:PYTHONPATH='src;.'
python -m pytest tests/test_forward_service.py tests/test_forward_retirement.py tests/test_forward_paper.py tests/test_forward_paper_adapter.py tests/test_forward_publication.py tests/test_forward_prediction.py tests/test_forward_transition_audit.py tests/test_reconciliation_recovery.py tests/test_model_promotion_policy.py -ra --basetemp E:/QTrade-temp/handover-review-new
python -m ruff check src/qtrade/research/forward_retirement.py scripts/run_qlib_forward_service.py scripts/audit_qlib_forward_transition.py tests/test_forward_service.py tests/test_forward_retirement.py tests/test_forward_transition_audit.py
$env:MYPYPATH='src'
python -m mypy --follow-imports=silent src/qtrade/research/forward_retirement.py scripts/run_qlib_forward_service.py scripts/audit_qlib_forward_transition.py
npm --prefix web test -- --maxWorkers=2
npm --prefix web run build
npm --prefix web run lint
```

实际生产检查使用真实UTC/上海日历及新的不可变输出路径。不得改时钟、用副本结果代替
生产交付、删去退休账本使固定12本检查虚假通过，或将当前采集刷新当完整历史PIT认证。
