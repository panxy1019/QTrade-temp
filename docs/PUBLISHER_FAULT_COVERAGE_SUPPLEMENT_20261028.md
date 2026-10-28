# Publisher故障覆盖补充：新增5项Linux测试

本补充仅读取新增测试源码与已有Linux证据，未执行生产操作。原
[v5独立审查](PUBLISHER_HANDOVER_V5_INDEPENDENT_REVIEW_20261028.md)、原FAULT映射和失败
XML保持原字节。新增证据不追改旧审查时点。

新增`tests/test_market_incremental_failure_boundaries.py`实际SHA256为
`a362654162f85cd5c5f375f03fdabadd475c5f5cb6d69ea12cdd3d30cf177c53`，
与该Linux执行包manifest及verify.log中的候选身份相同。原日志核验2195文件。
`market-fault-boundaries-tests-20261028T110931Z/tests.xml`记录5项通过，0失败、0错误、
0跳过，测试时间3.590秒。本文审查了实际断言，不仅按测试名称映射。

| FAULT | 本次新增实际断言 | 新覆盖级别与边界 |
| --- | --- | --- |
| historical_backfill | 先导出09:40，再加入同窗口更早09:35；新shard时间有序且保留两batch来源；旧receipt命中1、新重放1 | 直接fixture；不外推为多年历史回补覆盖 |
| quarantine_release | 在临时ledger加入exclusion再解除；恢复pair等于原pair，原generation字节不变，reused_pairs=0 | 直接fixture；未修改真实隔离记录 |
| disk_full | 新对象临时文件创建明确抛`OSError(errno.ENOSPC)`；拒绝并保留旧head原字节 | 直接故障注入fixture；没有实际填满磁盘 |
| slow_nfs | 原prepare执行后真实sleep1.15秒，以1秒deadline运行；TIME_BUDGET拒绝且旧head不动 | **PARTIAL**：实际单调时钟上的人工延迟注入；不是慢NFS挂载或真实网络故障 |
| git_non_fast_forward | 两个真实临时clone向本地bare remote形成分叉；真实push失败，remote head和旧publication成功凭证不变 | 直接fixture；仅destination身份查询被mock，实际Git写入均为本地临时remote |

新增后，25项FAULT映射为**13项直接fixture、10项部分、2项工具mock**。这意味着此前
5项缺专门案例均新增了相关证据，其中slow_nfs仍仅部分覆盖；不表示25项真实故障
全验收。进程crash矩阵、真实并发writer/collector、完整跨日链和真实慢NFS等原限制
继续保留，`all_faults_real_acceptance=false`。

完整testcase ID、原XML与源码hash、每项变化前后的级别见
[新的逐项映射](evidence/publisher-fault-coverage-supplement-20261028/fault-coverage-map-supplement.json)。
原5项Linux[XML](evidence/publisher-fault-coverage-supplement-20261028/tests.xml)、
[测试源码](evidence/publisher-fault-coverage-supplement-20261028/test_market_incremental_failure_boundaries.py)
及[不可变清单](evidence/publisher-fault-coverage-supplement-20261028/evidence-manifest.json)分开封存。

## 随后新增：同NFS的人工延迟negative probe

另独立复验主任务11:28UTC完成的原report SHA256：
`dc837c767ef00ad26f01a467637b9939bec165872fc243faee73504e76a0f83f`。
report内层签名、execution/helper hash相符，helper源码确认在原prepare/fsync完成后
人为sleep16秒，仍以45秒预算导出。实际53.911292秒返回TIME_BUDGET，旧head原字节
保持；去掉延迟后同45秒合同导出34.234317秒并推进新head。该实验为exact fba源、
5701-batch输入、真实隔离NFS dev66，与本轮另做的5640 profiling范围分开。

slow_nfs因此新增`DIRECT_NFS_LATENCY_INJECTION_WITH_LIMIT`，最新映射为13直接fixture、
9部分、1同NFS人工延迟、2工具mock。它验证阻塞返回后的postguard拒绝与恢复，**不是
45秒时硬中断阻塞I/O，不是真实server outage或慢NFS认证**，全FAULT实际验收仍false。

完整原report及raw mountinfo保留在`E:/QTrade-temp/cluster-evidence/market-same-nfs-latency-20261028T112633Z/`，
没有将原report中的私有mount行复制进Git。Git仅保存来源hash、
[去除原始mount行的独立摘录](evidence/publisher-fault-coverage-supplement-20261028/same-nfs-latency-sanitized.json)
和[最新增量映射](evidence/publisher-fault-coverage-supplement-20261028/fault-coverage-map-v2.json)。

复现需要在已核验source的隔离Linux目录执行，输出使用新路径：

```bash
LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. \
python -B -m pytest -q -p no:cacheprovider -o addopts= \
tests/test_market_incremental_failure_boundaries.py --basetemp /ISOLATED/NEW/fixtures \
--junitxml=/ISOLATED/NEW/tests.xml
```

本文未将原report→normalized receipt适配器、样本独立性/NFS绑定、legacy gate接线或
mutation前依赖预检标为已修复。新增5项不是生产交接或120秒生产预算授权。
