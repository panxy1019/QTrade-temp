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

## 19:12最终公开Git源的真实性能与完整内容核验

最终源为公开Git `fba51aade2ee2410dda087e95cf054e53375ed3a`，publisher64caab00、
receipt2152eed8、checkpoint0f2c512f，完整SHA见归档。精确Git归档2194文件在Linux
运行前后完整比对，**793项通过、0skip**，66.12秒；最终33项独立故障检查也通过。

| 实际隔离模式 | 冻结范围 | 次数 | 整export调用秒 | 判定 |
| --- | --- | ---: | --- | --- |
| 无DB/sidecar/输出全冷诊断 |5640|5|103.102、105.434、107.474、106.908、104.075|各在非生产120秒诊断内 |
| 有效持久化检查点的新进程 |5640|5|36.085、34.959、34.356、34.782、34.232|各≤45秒 |
| 本地DB丢失、从sidecar恢复 |5640|5|51.439、55.103、63.002、49.709、50.611|非生产诊断；**不符合45秒正常路径** |
| 正常增量及后续无变化轮次 |5701|20|max38.607、median34.179、nearest-rank p95=37.133|各≤45秒；p95≤30秒余量目标未达到 |

normal首轮从真实5640检查点进入5701/141634快照，日频完成日窗口从Oct27滚到Oct28：
5640收据命中、61批次重放、1498 pair重建/1498复用。后19轮均5701命中、零重放、
2996 pair/5987 shards复用。两个冻结点都是Oct28实际捕获，不冒称已完成未来Oct29
生产跨日观察。cold诊断、全量oracle曾与normal并行，共享NFS竞争已披露，不是独占。
源512MB、估算batch缓存128MB及正常45秒不变；不将诊断预算导入生产。

正常每轮实际hash源239718748 bytes；另读sidecar约195283641 bytes及公开shard
167397950 bytes。**源字节数不是总IO**。sidecar另记账，缺/坏视miss，I/O错误传播；
没有以mtime、本地DB payload或先前PASS替代其实际耐久证据。

原458552d独立oracle禁用候选收据，不调用候选exporter/validator，读取全部raw与
Parquet、重放全部来源，再逐字节比较输出：

- baseline5640：3000 entry、5986 shard、166050382 output bytes、140422 capture、
  218376 normalized rows，77.242秒、source_failures=0，PASS。
- current5701：3000 entry、5987 shard、167397950 output bytes、141986 capture、
  219874 normalized rows，75.960秒、source_failures=0，PASS。

当前refs清单hash为d82b83fb823c284cce2d1dd1a5be7045bf740eb74b10110bb2854b59e589d75b；
基线refs仍为8b5fe618…，不能混淆两个范围的计数和字节数。

新增五项专门接口故障测试在精确fba源加单测试overlay的Linux隔离包通过，0skip：
历史窗口补入、解除隔离、ENOSPC对象写、真实延迟超预算及本地bare Git非快进拒绝。
首轮4pass/1fail因测试0.25秒低于既有最小1秒合同；保留失败，改为合法1秒预算加
真实1.15秒延迟后5pass。未改生产合同、填满磁盘、破坏生产NFS或调用公网Git。
这些是故障注入，不能冒称生产实际磁盘故障/网络断连认证。

证据：docs/evidence/p0-publisher-factor-20261028/persistent-final-fba51aa-20261028/manifest.json。
原mountinfo留在私有operator证据，Git仅存安全metadata投影和原bytes hash。

**尚未生产交接。** v5的legacy gate仍未接新测量，normalized收据未逐字段绑定
原report、样本可能按空白重计、新模块缺preflight等独立审查阻断正在v6修复。
102/109 mock通过不是生产门禁通过。实际证据适配和组合安全门禁通过独立审查后，
才按已获A/B/C授权执行单次publisher交接及真实export→Git→生产index→API新鲜度验收。

## 20:25实际交接与下游传输阻断

最终v6逐字段解析35轮原始运行、两个全量oracle、实际XML、慢NFS负例/恢复、源码
和fresh foundation；不再使用自述PASS或legacy cold45字段。独立审发现的未跟踪
shadow package已由exact fileset+临写前7origin PathFinder核验修复。最终Linux52项
0skip，独立9项反例及caller3项通过；实际--check与只读prepare通过。

113923单次attempt于20:01:57发送唯一命名TERM；旧294214在Supervisor记录中
20:02:32正常exit0，新647839在20:02:42自动启动。20:03:34 actual PID/source/argv/
2194文件与7origin通过；除publisher外各owner、ACTIVE/context、rolling及14本账本
相同。没有stop/start、重复advance、PAPER恢复或模型激活。缺少TERM前完整历史子进程
身份清单，不能声称已追溯证明任意旧reparented进程绝无残留。

真实生产首次若干45秒失败按原样保留（覆盖型latest文件不能复原未捕获轮次）。
随后两轮localexport分别44.379250秒和37.008315秒，实际SUCCEEDED，预算保持45秒。
**公网尚未恢复**：Gitpush多轮90秒TimeoutExpired，主分支仍5a0925f4，localHEAD领先。
只读对象统计约23.63MB既有压缩存储待传（非精确wirepack大小）；实际pack/SSH管道
背压与输出增长支持传输或远端消费瓶颈，不能据此区分具体网络和服务端原因。
正在隔离bare环境测试有界辅助refs预传不可变对象，最后仍由正常publisher推进main；
未作公网辅助push，未提高90秒，不把localexport或aux预传当完整公开交付。

20:07:51既有standing模型政策独立激活68d；必须与20:03交接不变证明分时点记录，
不能把之后context/books合法演进归因为publisher覆盖，也不能回滚来凑旧断言。
实际原件保存在授权cluster operator目录；Git只保留安全投影/原hash，不复制私有
wrapper、SSH环境、配置正文或rawmount。

交接证据：production-handover-20261028/manifest.json（32payload，7b69b9c3…），
post-handover-readonly-20261028T122511Z/README.md，及
docs/PUBLISHER_HANDOVER_V6_INDEPENDENT_REVIEW_20261028.md。
