# 分级质量规则部署进展与保留的失败

2026-10-28 上海12:01检查点。两项规则已获用户明确批准；审批暂存、消费者
实际运行、公开发布和完整 STEP1 验收分开记录。本页不宣告完整 STEP1 通过。

## 实际完成

| 实际时间 | 操作和证据 | 已证明的范围 |
| --- | --- | --- |
| 11:47 | 只读生产快照 | 近期状态460/990；27日日线1497/1500、分钟1496/1500；28日盘中分钟1497/1500；空闲271.06GiB |
| 11:51:19 | 已审核 operator 暂存两项批准及质量摘要 | 绑定数据集990只股票；scope/retry政策分别不可变存档；原owner、当前模型/批准/版本、账本及旧PIT保持；尚无消费者升级 |
| 11:52–11:53 | 定向更新日版本服务至458552d，owner493083 | 新argv/cwd/PYTHONPATH/LIVE=false；自动周期结束WAITING、return0；监督日志记录旧进程exit status0 |
| 11:55–11:56 | 持有现有供应商非阻塞会话锁，空闲窗口更新近期状态服务至458552d，owner493858 | 已持久保存前后控制快照；其他owner、ACTIVE/批准/head、冻结账本、rolling OFF、前向instance及旧PIT哈希一致 |
| 11:59:49 | 新服务实际自动运行 | 近期状态520/990、470未尝试、无待重试；新源码生成质量摘要；436源码文件和3独立测试文件复核通过 |

质量摘要为12条SUSPENDED、3条DATA_GAP、4条UNKNOWN。这里的单位是证券×频率×日期
记录，不是12只停牌证券。使用逐日状态与公告证据，不推定完整停牌区间或复牌日期，
原采集失败及原始行情保持。近期窗口仍为10月23/26/27，520/990不能证明历史全PIT。

审批scope哈希 `bee913e4d976e9a03acd2994d0662edf6f1e2eb0f1a12792e82b475f8cb9d195`，
retry哈希 `71a262a56839a3af7eea40d8e170a832f1c6129613ec08d8498850d30ccaff0b`。
日版本新源将在真实新日请求冻结时绑定scope政策；上午WAITING不证明28日Provider、
预测或signals已经完成。今天实际收盘尚未发生。

## 失败与未完成边界

1. **日版本部署检查过早。** 定向remove/add完成后Supervisor仍为Starting，没有
   pid文本，operator最后断言失败。保留原trace及reread的既有ValueError/exit1；
   随后只读检查确认Running493083和自动WAITING/return0，没有重做stop/add。
   原operator把部署前冻结账本快照只保存在内存，没有持久化这份比较基准，所以
   不能补写该次完整前后账本等价证明。后续状态服务operator已修正为事前归档。
2. **状态服务不是优雅退出。** 监督日志实际出现`signal=killed`和`signal: killed`；
   初始只匹配字面SIGKILL的检测为false，不能解释成正常退出。供应商会话锁与
   新鲜空闲窗口检查已执行，交接前后控制证明通过，新服务自动继续；但退出原因
   必须保持为non-graceful。旧备份和新配置均未显式设置stopsignal，实际Go
   Supervisor默认行为仍需独立核验，不能把它说成配置中明确写了KILL。
3. **重试消费者尚未升级。** 维护owner213587继续运行，后台兼容刷新线程的自然
   退出与实际停止信号尚未满足升级条件。retry政策已保存，不等于暂缓规则已生效。
   不抢占队列、不重放失败、不执行活跃owner下的缺口apply。
4. **公开行情索引过期。** 实际11:49默认域名main ae33检查为STALE_TRANSPORT：
   index年龄4373.28秒，而catalog年龄93.56秒。固定公开commit为55ce9efc；
   目录更新和外层推送成功不能代替行情导出成功。
5. **性能候选仍需真实验收。** 第一份微优化在Windows96通过/12平台跳过、
   Linux108通过后，冻结全量冷/热仍分别45.268/45.263秒失败。四个预算保持原值：
   导出45秒、来源512MB、缓存128MB、普通Git90秒。后续候选必须另留完整证据，
   不覆写本次失败、不截断、不用未提交源码冒充正式revision。

全部14本前向账本保留，前向服务383859不重启，PAPER恢复授权不重放；ACTIVE29589b44、
rolling OFF和LIVE=false保持。新的研究范围批准不授予未知交易状态的PAPER准入。

## 下一步

- 验证新的状态自动窗口、持续990覆盖与质量摘要；处理重试消费者的安全交接条件。
- 完成最终发布源的45秒冷/热全量字节与lineage验证，再做实际公开API和网页验收。
- 真实28日收盘后，先固定执行日的E账本/会计/实际public证据；并行核验新正式日版本、
  批准模型预测、候选later-version和signals，不等新C/D全部完成才取E证据。
- STEP2设计、接口审计和非生产测试继续并行，见[设计总览](STEP2_EXECUTION_DESIGN_20261028.md)。
  因子研究可用合格日频范围并披露偏差；改善PIT以后追加新版本实验，不覆盖旧实验。

## 证据与只读复现

[归档目录](evidence/graded-quality-rollout-20261028/)保存原始成功/失败和各自哈希。
`completed-status`含完整部署前控制摘要；`read-only-data`只证明其捕获时运行状态。
完整Supervisor原始日志和私有配置不进入仓库。使用新的输出路径复验公开数据：

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
python -B -m scripts.probe_unified_publication --expected-production-revision ae33a7c8c27ea721eb3be1e4430b562a44b67f91 --report E:/QTrade-temp/new-public-check.json
python -m pytest -o addopts= tests/test_data_quality_policy.py tests/test_collection_retry_policy.py tests/test_recent_status.py -q
```

已执行的stage/stop/add不是复现命令，不可直接重放。新日验收使用实际时钟与新输出路径。
