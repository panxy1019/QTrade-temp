# P0持久化验证收据与检查点：首轮实际证据

用户于2026-10-28 17:34明确授权方案A/B/C。当前为非生产实现和验证；尚未交接生产publisher。

已复用UnifiedBatchStore原始物理hash、_replay、质量隔离和原分片编码，在显式
--unified-market-state-root启用时增加本地SQLite成功收据与NFS不可变sidecar。
每轮仍读取并hash选中源实际字节，收据只省略已验证内容的解码/重放；get返回内容
还与当前manifest逐字段相等。指纹绑定完整规则依赖、日历、证券类型、Python/Arrow
与Decimal政策。未变化pair复用原分片bytes/hash，来源新增即使价格相同仍使其失效。

生成完整generation并fsync对象/祖先目录后才原子替换public index。实际index是提交
指针，generation按index id查找；崩溃导致状态日志落后不会发布半代。POSIX本地主机
lease与提交前head hash复核阻止竞争，SQLite拒绝配置在已识别网络文件系统。
生产启用仍需唯一旧owner退出证明，不能以lease对不遵守它的旧writer声称CAS。

## 已执行测试

Linux639项通过、0skip，包含原有市场/store/parser/公共probe回归及11项端到端
检查：重启收据复用、新批次只改对应pair、等价价格新增来源、隔离撤销、损坏原始
对象/公开分片、checkpoint失败保留旧head、提交后状态失败、重复writer与fsync重试。
Ruff及发布器/checkpoint定向mypy通过；新收据模块还需其独立测试和独立代码复核。

## 真实NFS固定5640批次首轮基准

| 模式 | 实际整调用秒 | 语义重放 | 收据命中 | 结果 |
| --- | ---: | ---: | ---: | --- |
| 无收据首次全冷，独立非生产120秒诊断 |118.804268|5640|0|通过本轮诊断；不是45秒生产通过 |
| 新进程/有效持久化检查点1 |38.598779|0|5640|45秒内 |
| 新进程/有效持久化检查点2 |37.871646|0|5640|45秒内 |
| 新进程/有效持久化检查点3 |37.773785|0|5640|45秒内 |

四轮均逐字节读取hash源237250656bytes；新进程无内存batch cache。
全部复用结果为2995 AVAILABLE pair、5 NO_DATA；完整3000索引项、5986分片、
166050382输出bytes，refs清单SHA256固定8b5fe61818b574d922de13d20732ebd6c323d1eba5ee072573e0771698229645。
RSS约650MiB的重启路径并非128MB总内存合同；128MB是原估算batch cache上限。

## 独立全量内容/来源核验

原458552d源码独立oracle于18:05执行，禁用候选缓存且不调用候选exporter/validator。
实际PASS：5640批次、140136metadata、140422原始响应、218376规范化行；全部3000
entry相等、5986原始shard byte相等，完整来源关系相等。用时74.262182秒，是独立
全量审计，不是45秒发布。source_failures=0，production_output_modified=false。

基准实际执行代码：publisher8cca31458d73cacee97ae1909c7996e92ab010ff000b46adfb26c4a7bc069cae，
checkpoint343d0a16d94b5e2f84ae7fac514a4e3b67d333e8a9011d1b4110b6c89c741bd9，
receiptffe300edd238c1c96d8f40b235fd8bea65d06e86a988ca1206295eb95a7fb31e。
完整测试包1946文件执行前后hash相同，明确为Git基线加候选overlay，不伪称已经公开提交。

## 剩余门槛

首轮通过不等于生产恢复。仍需独立代码审查/收据合同测试、正常增量连续轮次、实际
跨日5701批次新快照验证、精确Git源码再次运行及受控owner交接。生产45秒、源512MB、
缓存128MB、Git90秒预算均未改变。全冷恢复120秒只有1.2秒余量，尚不承诺恢复SLA。

首轮证据：docs/evidence/p0-publisher-factor-20261028/persistent-firstpass-20261028/manifest.json。
源完整冻结5640证据8a4ea3ab…保留；18:07新5701快照是另一个范围，不能混用其计数
或把旧source/oracle结果当成新快照已通过。原超时、质量缺口及STEP1全局阻断继续保留。

## 18:28独立审查与第二轮候选（未部署）

原首轮候选并非最终源。独立故障注入发现五项真实缺陷：sidecar目录fsync失败后
恢复索引未再次fsync；generation准备期间head变化仍可能覆盖；准备期间旧chunk
撤销未再次核对；重新摘要的未来generation时钟被复用；Bar准入依赖文件变动未使
收据失效。追加探针另发现prepare耗尽deadline和新增重叠batch未及时阻断提交。
原始5 failed/2 passed和追加失败均保留，不将修复后结果写回原报告。

修复现在在generation准备后重新检查head、选中集合、重叠新增batch、实际证券描述
及deadline；收据和generation重试补足目录fsync，未来/无时区generation视为miss，
补入实际Bar准入依赖。新增batch与旧选中时间范围重叠时保守拒绝本轮提交，下一轮
从完整新集合重新计算；不论价格是否相等，都不能遗漏新增lineage。

18:27独立25项故障/接口回归全部通过，0skip；精确候选publisher374d1467、
checkpoint0f2c512f、receiptd95d0b66。Linux原市场/store/parser/probe和检查点回归
640项通过、0skip；完整1946文件清单分别在执行前后核对。独立测试另核对25项
所用2072文件清单。源为明确Git基线加overlay，尚未冒称已公开提交。

18:11原候选在新5701/141634快照下连续三轮45秒超时，受控终止仅针对隔离基准的
已持有进程，生产owner未受影响。故障profile显示重复收据树校验和不必要的跨频率
target字段失效。新候选保持JSON边界验证，将内部已检查树的重复digest改为规范化
编码，并仅将实际消费的四项证券描述字段加入pair指纹；按频率窗口及完整chunks
继续绑定。尚未用旧源通过结果替代新源真实性能，当前正在重跑5640及5701基准。

生产publisher仍294214/b2386c3，未改wrapper、未发TERM。正常45秒、源512MB、估算
batch缓存128MB及Git90秒不变。全冷诊断120秒仅非生产；若生产首轮需要多轮积累
已验证收据，超时仍明确失败且保留旧head，不能隐去预热成本或将其称为45秒冷恢复。
