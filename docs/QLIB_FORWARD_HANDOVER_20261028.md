# 2026-10-28 forward 定向交接

核验至上海时间01:08。**定向交接、6本PAPER对账恢复及实际公网控制状态交付已完成；
完整跨日STEP1尚未通过。** 当前模型的首个完整前向日要等待10月28日真实收盘。

## 授权及范围

用户明确选择“允许按方案定向交接（推荐）”，对应PR34已归档方案。这次授权覆盖一次
forward owner175982的定向替换，以及成功逐账对账后为5个基线和当前ACTIVE LightGBM
共6个PAPER账本创建新的单次恢复请求。它取代此前对这一次操作的“不重启原进程”限制。
退休模型不恢复，原已消费claim不复用，LIVE=false、rolling OFF不变。

[签名授权](evidence/qlib-forward-handover-20261028/authorization.json)保留问题对应范围；
[实际交接凭证](evidence/qlib-forward-handover-20261028/cluster/handover-verification.json)
另行记录已执行的范围，不能将授权记录本身当执行证据。

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
- 后端Ruff与3个源文件scoped mypy通过；同一228项在实际Linux通过，零跳过。
  [Linux执行记录](evidence/qlib-forward-handover-20261028/cluster/linux-attempt.json)
  及原始XML保留实际源码9846a7f；582个文件逐一与Git blob/SHA核对。
- [真实14账本副本回放](evidence/qlib-forward-handover-20261028/cluster/copy-replay-20261027T164115Z/verification.json)
  验证6组当前提案、12份未准入收据、2份退休关闭凭证、幂等重试及原账本指纹不变。
- 原175982按本次授权退出，新383859执行9846a7f；实际进程目录、参数、PYTHONPATH、
  新instance与582份源码全部核验，不携带initialize-paper。
- 00:57的6个全新短期请求均单次消费成功；[控制事件复核](evidence/qlib-forward-handover-20261028/cluster/six-paper-control-audit.json)
  绑定各请求、成功收据、SYSTEM_START与真实owner，并确认没有不确定成交。
  原恢复历史文件逐份字节一致，原冻结目标、准入、会话及成交不变，其他8个owner不变。
- [01:00实际公网交付](evidence/qlib-forward-handover-20261028/public-running-control/control-delivery-verification.json)
  保存默认域名原始body/header/hash、相邻相同main4b86145及内层签名对应关系：14账本、
  RUNNING、service_fresh=true、errors=[]；6本当前PAPER NORMAL且运行，2本退休账本停止。
  00:58仍为STOPPED/过期的实际捕获同时保留，不能只归档成功结果。
- 10月28日真实交易日尚未完成，不认证当日成交、持仓、估值或全STEP1通过。

## 完整日审计为何仍为BLOCKED

[实际公开输入的v2股票审计](evidence/qlib-forward-handover-20261028/cluster/stock-E-v2-actual-public-oct27.json)
覆盖全部14本：12本当前研究账本与2本退休关闭账本。10本基线的10月27日观察及2份退休
关闭证明通过；当前LightGBM的PAPER/SHADOW均为`FORWARD_AUDIT_OBSERVED_SESSION_MISSING`。
原因是该模型在10月27日收盘以后激活，其冻结目标面向10月28日。不能补造10月27日
会话，也不能把退休关闭证明计作当天观察。报告状态明确保持BLOCKED、whole_step1=false。

10月28日真实收盘后按现有自动链路生成观察，再做当前12本+退休2本的完整审计和
实际公网发布。零成交时也要核验阻断原因、持仓/现金/估值与收据；未知交易状态继续阻断。

## 本次操作失败与修正

初版操作脚本没有同步修改PYTHONPATH，且假定Supervisor采用标准返回格式，导致切换
期间服务无法直接启动。这是本次操作检查不完整，原失败记录保留：

1. Go Supervisor v0.6.8的reread返回不能被标准supervisorctl拆为三个列表。
2. 目录/命令已更新但PYTHONPATH仍指向旧release，新退休模块导入失败，尚未打开账本。
3. 修正环境后，异步stop/backoff尚未退出时的start仍可能只返回“started”。等待该循环
   安静后定向启动，再用实际进程和新鲜实例证明成功；其他owner没有重启。

先保存停止后的14账本副本和私有配置备份，再只修改目标服务目录、revision、PYTHONPATH
并移除初始化动作。私有配置和数据库副本仅保存在集群权限目录，不进入Git。
原单次handover claim保持已消费，后续只继续尚未完成步骤，未重复执行handover或旧恢复。

自定义Supervisor行为参考[对应版本源码](https://github.com/ochinchina/supervisord/blob/v0.6.8/process/process.go)
及[reload实现](https://github.com/ochinchina/supervisord/blob/v0.6.8/supervisor.go)。

## 采集、回补和网页剩余项

[只读治理快照](evidence/qlib-forward-handover-20261028/cluster/post-handover-governance.json)
保留原始progress字节哈希和集群不可变副本位置。历史有限队列76,824条，其中日线1,944、
五分钟74,880；独立退市探针192条。两者本轮让位当前采集；不是进程停止或全历史完成。
完整历史PIT、复权和连续多年数据尚未认证。交接后空间272.56GiB，仍保留100GiB。

PR34的退休账本网页标注等待main合并/实际部署核对。最新Terra high尝试因浏览器扩展
`nodeRepl.fetch request failed`未取得DOM/截图，见[原报告](evidence/qlib-forward-handover-20261028/browser-terra-report.md)。
此前核心页面截图有效范围不变，不用API通过代替这次视觉验收。

## 复现检查

下面是测试命令；已消费的生产handover/recover不可重跑。只读生产日审计使用新输出路径，
实际公开输入必须重新捕获并验证十二分钟时效，模型registry显式传入以覆盖退休账本。

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
