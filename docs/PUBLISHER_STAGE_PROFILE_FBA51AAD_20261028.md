# exact fba51aad：5640冻结范围cold/warm分阶段诊断

结论：**这次诊断的主要优化对象是receipt反复JSON编码、结构验证和持久化；不是分片
worker的计算吞吐。** 完整cold为105.604330秒，warm为37.406530秒；两轮均使用120秒
隔离诊断预算并启用profiling，**均不作为45秒性能验收**。没有调整生产45秒合同。

## 精确范围和结果

复用现成exact Git source：`fba51aade2ee2410dda087e95cf054e53375ed3a`，完整2194文件
清单在运行前后逐一重hash，均一致。publisher文件SHA256为
`64caab0023626ef0f531f563c3a392d4a2429a9a6c004c5789af17321bbd8d8a`；receipt为
`2152eed8405ace883c2bd8de388b72ec42875a3feaffa314e616c58c1147f79a`。

输入固定为`market-current-freeze-20261028T070943Z`的5640 batches、140136 chunks，
采用原freeze的逻辑时钟；没有使用后来的5701输入替换本次范围。实际clock、freeze/
ledger/catalog hash及完整源码清单绑定见[总报告](evidence/publisher-stage-profile-fba51aad-20261028/successful/profile-report.json)。

唯一新隔离输出为`B/operator-checks/market-stage-profile-20261028T113018Z`，本地DB位于
对应新operator目录。原freeze、已有基准目录和生产public目录不写入。NFS输出st_dev=66，
与生产同设备；本地/远端空间运行前后均满足2GiB/100GiB保留要求。原始mountinfo只在
远端私有operator目录保存，Git仅包含其hash和去除源地址的设备/类型投影。

| 项目 | cold | warm |
| --- | ---: | ---: |
| 完整调用wall秒 | 105.604330 | 37.406530 |
| 父进程CPU秒，含其线程 | 87.378344 | 30.084611 |
| 已回收子进程CPU秒，含启动 | 17.374586 | 4.393470 |
| peak RSS KiB | 1384456 | 907648 |
| 初始本地DB/sidecar | 均无 | 均有 |
| 初始对象数 | 0 | 5986 |
| 当前源物理读取bytes | 237250656 | 237250656 |
| 原始batch重放 | 5640 | 0 |
| receipt写入/命中 | 5640 / 0 | 0 / 5640 |
| 复用分片 | 0 | 5986 |
| 实际sidecar读取bytes | 0 | 193567291 |

两轮都在新进程运行，warm复用cold的同一输出、DB和sidecar。两轮输出均为5986分片、
166050382 bytes、0 quarantined entries，全部分片引用逐字节相同；每轮完成时owned
children为空。cold指空输出/DB/sidecar与空进程缓存，不表示清除了操作系统页缓存或
NFS服务端缓存；本轮不进行会影响其他服务的全局drop_caches。

## 分阶段计时：包含关系必须保留

下表为各函数活动段的inclusive wall。父线程嵌套、预取线程、16个对象I/O线程和2个
分片进程均可能重叠，**不能将这些行相加为总耗时**。对象行是线程活动时间总和，
分片行是两进程job活动时间总和，均不是该阶段独占关键路径。

| 原函数/范围 | cold wall秒 | warm wall秒 | 解释 |
| --- | ---: | ---: | --- |
| SQLite选择chunk与行物化，3次 | 3.840 | 3.228 | 含其execute；后者不能再相加 |
| 当前源prefetch material | 23.680 | 19.517 | 预取线程，与主线程重叠 |
| 源physical read与计数 | 13.465 | 13.056 | 包含在material及manifest复验中 |
| blob read+hash | 18.761 | 9.925 | 与上一行有包含关系 |
| 原始市场语义replay | 14.014 | 0 | cold完整5640次 |
| material validation | 11.539 | 0 | cold含Parquet、gzip/JSON、行hash；重复调用可命中该material内已验证结果 |
| Parquet file.read | 1.932 | 0 | 包含于validation |
| Arrow验证及NumPy转行 | 2.498 | 0 | 包含于validation |
| receipt get总计 | 1.731 | 22.289 | warm含SQLite、NFS读取、decode/schema/hash |
| receipt put_success总计 | 51.242 | 0 | cold含下方编码、复验、事务与sidecar写入 |
| receipt JSON encode | 13.279 | 6.182 | cold84600次，warm39480次 |
| receipt JSON decode+schema | 14.100 | 15.366 | 包含后续canonical编码、行/manifest校验 |
| receipt有界NFS read | 0 | 1.878 | 不包含open/stat等元数据开销 |
| receipt NFS sidecar写入 | 9.688 | 0 | 含write/link/目录fsync |
| receipt SQLite execute | 13.834 | 0.459 | cold28202次、warm5642次；包含于get/put |
| 对象不可变write/link/flush，16线程合计 | 16.212 | 0 | cold5986个对象，与主流水线并行 |
| 对象fsync syscall，线程合计 | 1.798 | 0 | 包含于对象写入 |
| 子进程原始job decode+validate+encode合计 | 12.849 | 0 | cold5986jobs；两进程CPU合计12.729秒 |
| checkpoint load/验证 | <0.001 | 0.098 | warm加载绑定的原head |
| index完整schema validation | 0.102 | 0.094 | 不省略原验证 |
| generation prepare+持久化 | 0.049 | 0.048 | 包含目录fsync |
| index atomic write | 0.032 | 0.025 | 与其他阶段分开记录 |

原始按线程inclusive/exclusive wall及thread CPU、调用数都保留于
[分阶段汇总](evidence/publisher-stage-profile-fba51aad-20261028/successful/stage-summary.json)，
可以区分同线程嵌套。exclusive只扣除同线程被instrument的子调用；未包裹的工作、
线程调度/GIL等待与外部I/O等待仍留在包围它的阶段，不能据wall-threadCPU差值就断言
全是磁盘或网络延迟。进程CPU另含未instrument的Arrow等native线程。

## 热点与最小后续方向

cold的receipt put为51.242 wall/31.421 thread CPU秒。receipt JSON encode独占CPU
13.234秒，result schema/row hash独占5.309秒，decode/schema独占4.994秒，canonical
tree验证独占2.632秒；这些exclusive数字按其边界理解，不与上层put再相加。另有原始
语义replay约13.881 CPU秒。SQLite事务和NFS sidecar持久化也占可见wall时间。

warm的receipt get为22.289 wall/17.017 thread CPU秒，其中encode独占6.114秒、
decode/schema独占4.954秒、result/row hash独占2.646秒。仅JSON encode就平均每个
receipt调用7次；cold平均15次。这提示先审查一次调用内相同canonical bytes/hash和
已验证结构结果的重复计算；任何优化都必须保留每轮源物理hash、sidecar真实性、
结构/时钟/lineage/行hash条件及别名变化防护，不能以历史PASS替换当前验证。

cold两个分片worker分别只有约6.36秒job CPU，多数时间在pipe等待主进程。warm
完全没有新job，仍启动两个worker，握手约0.942 wall秒、子进程启动CPU合计4.393秒。
可另审查“无新shard时延迟创建worker”的小改动；它不是本轮已实施的优化，也不能
替代receipt热点治理。generation/index尾部在本轮不构成主要耗时。

## 方法、开销和真实失败

没有cProfile、逐行trace或修改原source文件。只在新诊断进程中包裹原函数调用；
生成器仅计next/close活动段，不计暂停在yield后的时间。SQLite仅用行为等价的
Connection/Cursor子类计execute/fetchall，所有原查询、事务、物理校验仍执行。

只拦截完全相等的owned `python -B -m scripts.publish_unified_market
--private-shard-worker`命令，替换为新profile_worker入口；其他Popen原样透传。新入口
调用相同`_shard_worker`并保持二进制pipe协议与2进程。其原/诊断argv及helper hash逐
进程记录。受保护的`_validated_shard_bytes`及code filename不变；job计时从完整J帧
读取后，到原O/E帧编码前，因此含原JSON decode、完整验证及编码，不含pipe等待。

no-op标定的单span额外成本约2.08/2.04微秒；父进程cold记录595629 spans、warm194876。
这只估计基本计时调用，不能量化所有wrapper、cursor代理、路径分类、进程入口、
调度/GIL和并发竞争影响，未从结果扣除。本轮共享NFS与其他已授权服务/基准可能竞争，
不声称资源独占，也不从profiled37.4秒外推unprofiled45秒门禁稳定通过。

首个独立attempt实际cold2.046541秒失败：wrapper包裹受保护validator后被原
`UNIFIED_MARKET_WORKER_IMPORTED_SOURCE_MISMATCH`拒绝。保留失败helper和report；没有
绕过guard或伪造co_filename。新attempt保持validator原函数，只在pipe消息边界计时。

首轮SFTP准备曾延迟，基于较早目录观察写出“未开始/已中止”的临时元记录；核验时
runner已自然结束，Stop-Process分支未执行。随后获取的完整报告证明它实际运行并在
来源门禁失败，故另存[状态纠正](evidence/publisher-stage-profile-fba51aad-20261028/source-guard-failure/startup-status-correction.json)。
原错误元记录保持原文，不能当作真实中止凭证。第二attempt用stdin transfer和新文件
xb/hash核验，避免重用第一轮输出。

## 证据与复现限制

最终[不可变manifest](evidence/publisher-stage-profile-fba51aad-20261028/manifest.json)绑定
完整2194源清单、helper before/after hash、初始条件、原始父/子profile、输出refs及
失败/纠正记录。仅有计时、hash和公开源字节进入Git；private raw mount不进入Git。

脚本副本已封存；复现必须换新的operator和output目录，继续使用原source/freeze，
不能重跑同名runner覆盖旧证据。该profile是诊断，不包含独立原始oracle重放，也不
替代主任务分模式真实性能或生产交付验收。全过程未控制生产owner、未修改生产
数据/模型/账本或LIVE=false，未创建commit。
