# Publisher v5交接与分模式门禁：独立非生产审查

结论：**保持READONLY/PREPARATION；生产交接接线仍BLOCKED。** wrapper变换与同attempt
TERM不可重放保护基本合理，但新分模式收据尚未从真实原report逐项派生，且未接入
实际交接入口。不能将结构检查的`GATES_PASS_PREPARATION_ONLY`当作真实测量通过。

本轮不读取私有wrapper正文，不建立SSH连接，不构造真实candidate、不调用生产RPC，
不写生产文件。只冻结本地工具源码、运行合成mock反例、读取已有XML并新增本文/证据。
审查使用Karpathy准则：最小改动、精确来源、可复现失败和明确完成边界。

## 被审精确字节

| 文件 | SHA256 |
| --- | --- |
| publisher_handover_v5_20261028.py | 980d288602f35160f9904a164893b40ef147b2ee882e2a91a0f973043824ebb4 |
| publisher_handover_bridge_v5_20261028.py | f60b83490b7ef6ba6d46b4ba728120a847e38b6a40aca0ff136e2f8409ddb1fb |
| publisher_incremental_gates_v4_20261028.py | 4f05e880c19096ea78d2a8b392d463348d9b955d39f8a7a8b75ee5dd344d2954 |

本地原路径均位于`E:/QTrade-temp/`，完整副本及原有测试依赖保存在
[审查证据包](evidence/publisher-handover-v5-independent-20261028/manifest.json)。

## 阻断问题

### P1：真实原report内容尚未形成门禁信任边界

`publisher_incremental_gates_v4_20261028.py:111`的`refs()`仅验证引用字节存在且hash匹配；
`:197`与`:215`没有把原measurement/oracle字段与normalized字段逐项比较。该限制在
工具说明中已承认，当前仍缺实际适配器，不能接生产。

独立合成反例把第一个原measurement替换为明确的
`state=FAILED / elapsed=999 / wall=999 / source_revision=000... / filesystem=tmpfs`，
normalized run仍填`SUCCEEDED/38s`，更新引用hash后结构检查继续返回PREPARATION_ONLY。
这不是实际测量通过，也不是SHA256被破解：两份互相矛盾的内容没有被语义比较。

最小接线：适配器仅接受白名单原report schema/来源与受审脚本hash；从保留的原始
bytes派生每一项source、freeze、clock、elapsed/wall、模式、初始DB/sidecar状态、
资源、实际输出和oracle计数。独立复验派生值必须与normalized receipt完全相等。
原report缺字段即PENDING，不手填、不从别轮借值。tests XML、source-import和fault
证据也应解析实际结果，不能仅引用一段自称PASS的opaque bytes。

### P1：新mode合同尚未接入交接入口，旧gate不能替代

`publisher_handover_v5_20261028.py:738`的`advance()`及bridge的`:281/:343`仍只调用旧
`check_gates()`。独立反例用原合成4200-batch legacy gate通过，完全没有提供20 normal、
5 restart、5 cold、5 DB-loss或完整fault bundle。此处是尚未实施的接线，不是本轮
生产已遭绕过；CLI当前仍无apply。

不能通过伪造45秒cold字段、将normal伪装cold、monkeypatch check_gates或仅靠外层root
脚本先检查一次后丢弃结果来接线。应新增明确版本的组合审查入口，将实证适配结果、
旧交接的控制/配置/binary/owner安全检查和精确manifest共同绑定；实际mutation入口
必须重新校核同一bundle。新增代码仍需独立review及相应阶段的明确生产授权。

原v5强制cold/warm均45秒。新cold<=120及DB-loss<=120只是隔离诊断，不能直接满足旧
cold45 gate，也不允许把生产export_seconds改为120。若采用新的部署前提合同，须明确
版本化审查这一差异，保留第一次生产cold45超时/失败，不能把诊断成功改签成生产成功。

### P1：样本独立性、当前负载和NFS尚缺强绑定

`:198`把`run_id`等同第一份原measurement的文件hash。独立反例将同一个合成
execution_id的JSON仅追加空格形成20个不同hash，仍满足20次normal计数。该检查证明
字节不同，不证明20次不同、连续执行。schema也没有前后generation/DB/sidecar链或
单调起止时间，因此不能认证连续20轮与实际增量/跨日。

同一已通过的合成fixture中，5 cold、5 restart、5 DB-loss全部使用baseline5640；
只有normal里的real_change一轮使用current5701。schema没有任何filesystem/mount字段，
不能强制这些测量位于生产等价NFS。旧operator的单次NFS声明不能覆盖新35轮。

最小补充：在实证适配层绑定唯一实际run/进程身份、时钟和连续状态链，拒绝同一执行
不同序列化形式重复计数；将每一模式所要求的当前冻结负载明确列为验收矩阵；逐轮
绑定实际mountinfo、输出路径/挂载设备和被计时输入。保留旧5640基线，但不据此代替
已要求的当前5701模式实测。并行cold和normal共享NFS应写进各轮资源竞争范围，不能
称为独占或无竞争基准。本审查没有读取尚未完成的实测结果，更未宣布其通过。

### P1：新依赖完整性应在wrapper替换前检查

`publisher_handover_v5_20261028.py:532`的`check_source()`只遍历调用者提供的inventory。
独立mock证明两项新依赖完全不在inventory时，旧gate和source preflight仍可通过。
bridge的`:195`虽然要求新模块存在，却在新进程启动后的`runtime_probe()`才执行。
因此提供不完整inventory可能先替换wrapper/TERM，再发现新依赖没有被绑定。

最小补充：mutation前验证精确Git全清单及其数量/清单hash，包括新增
`scripts/unified_market_checkpoint.py`、`src/qtrade/research/market_validation_receipts.py`
及包初始化/传递依赖；运行无导入副作用的新release PathFinder预检。交接后再做真实PID
验证。当前主任务fba51aad的2194文件清单是应绑定的具体输入，不是工具中的默认常数。

## 已确认的保护与保留边界

- 旧wrapper trust root硬编码SHA256与623字节，manifest不能重新定义。只允许一次完整
  release路径替换及固定`--unified-market-state-root /root/qtrade-local/market-publication`
  两项tail追加；旧Call AST节点保留但不求值，candidate按全部字节精确比较。合成测试
  不能证明真实623字节candidate已构造/通过；本轮未读取正文。
- bridge绑定v5 operator hash，旧模块集合不强求新依赖；新模块集合明确增加两项依赖。
  实际argv校验拒绝重复/缩写/equals形式的state flag，要求固定pair位于tail；来源探针
  是PathFinder路径解析回放，不是读取live sys.modules，说明与实际能力一致。
- 同一attempt的TERM_INTENT在调用前落盘；响应丢失或intent已持久化时不重发。RPC实例
  还在I/O前消费唯一TERM权限。独立mock重复advance确认只发一次。
- wrapper安装后、TERM前旧owner自然退出/变更，guard_old拒绝继续，独立mock确认零次
  signal。应保留新wrapper/真实观察并转只读人工审查，不创建新attempt追发TERM，也不
  向新owner重定向。named-RPC检查到调用仍非原子expected-PID CAS，既有限制未消失。
- NEW_RUNNING只给PROVISIONAL_RUNTIME_READY，仍保留自然退出、owned-child清理、唯一
  自动重启、控制状态独立核验及export→Git→public API证据；不能以瞬时Running代替。

本轮未运行Linux真实mutation leaf；109个本地mock不证明生产fsync/owner交接成功。

## 793项Linux回归与FAULTS逐项映射

已读取并hash核验主任务精确fba51aad执行的XML：793 tests、0 failures/errors/skips。
对应execution.json为exit0，manifest声明EXACT_PUBLIC_GIT_COMMIT、2194文件。审查涉及
的4份测试源码与Git fba51aad对象逐份相同。另读取原tools102 XML并重跑冻结副本。

以下覆盖只对应所给XML与已查看断言，不将“case名字像”或布尔字段当作完整故障验收。
每个FAULT对应的完整testcase ID、XML文件hash及范围见
[逐项映射](evidence/publisher-handover-v5-independent-20261028/fault-coverage-map.json)。

| FAULT | 已有证据范围 |
| --- | --- |
| unchanged | 直接fixture：restart/reuse与相同分片 |
| real_increment | 直接fixture：新batch仅重建对应pair |
| window_rollover | 部分：窗口fingerprint变化；未覆盖实际完整跨日发布 |
| historical_backfill | 未找到专门case |
| same_price_new_lineage | 直接fixture：同价新来源改变lineage |
| revocation | 直接fixture：exclusion移除已用source |
| quarantine_release | 未找到解除隔离的专门case |
| conflict | 直接fixture：新重叠冲突保留旧head |
| rule_upgrade | 直接fixture：可丢弃源码副本改policy使缓存失效 |
| source_corruption | 直接fixture：同size/mtime原始字节变化仍检出 |
| missing_shard | 部分：公网shard不可达及复用shard损坏；非本地删除后的完整恢复 |
| bad_receipt | 直接fixture：损坏sidecar及future receipt拒绝 |
| disk_full | 未找到ENOSPC专门case；普通OSError不能替代 |
| slow_nfs | 未找到实际慢NFS专门case；schema枚举/时长拒绝不能替代 |
| source_budget | 直接fixture：实际read计数边界及失败保留旧成功 |
| crash_fsync | 部分：注入fsync异常与重试；不是进程kill矩阵 |
| crash_generation | 部分：prepare异常保留旧head；不是实际进程崩溃 |
| crash_head | 部分：head提交后异常及fsync恢复 |
| crash_db_record | 部分：sidecar fsync在SQLite COMMIT前；未在DB补记点实际kill |
| collector_concurrency | 部分：回调在prepare期间修改ledger；非并行真实collector |
| publisher_competition | 部分：单进程嵌套lease拒绝第二writer；非两个独立进程竞争 |
| stale_owner | 工具mock：owner变化不重定向，仍无原子CAS |
| git_non_fast_forward | 所给XML未找到非快进push专门case |
| http_timeout | 部分：generic网络错误及RPC响应丢失mock；非正常发布端到端超时 |
| term_no_retry | 工具mock：intent后崩溃/响应丢失不重发 |

合计9项直接fixture、9项部分、2项工具mock、5项缺专门case。**全部25项真实fault_suite
complete目前不能登记为true。** 793项还包含研究/因子等其他范围，数量不能替代该矩阵。

## 本轮验证与最小后续顺序

独立新增7项mock反例/保护检查，加冻结的原102项，共**109通过、0skip**。这些测试的
“通过”表示复现了当前边界或保护，不是伪造真实NFS、性能、故障或生产PASS。
第一轮打包遗漏legacy测试依赖导致collection error；补齐后一个Windows PurePosixPath
mock失配失败。两份失败XML及原测试版保留；v2只修正合成平台路径适配，未改被审工具。

建议顺序：先完成原report→字段逐项派生/复验与样本连续性/NFS/当前负载矩阵，再补齐
明确缺失的fault cases和进程崩溃/竞争实测，随后实现版本化组合gate及mutation前完整
源码预检。最后才提交精确candidate、manifest、只读prepare与生产阶段授权审查。
现有5cold/5restart/5DBloss/20normal实测应保留每轮原文件、竞争环境与失败，不能在
缺适配器时手工填一份新的normalized全PASS收据。

复现本轮纯mock测试：

```powershell
python -B -m pytest -q E:/QTrade-temp/publisher-v5-independent-review-20261028T105905Z/test_publisher_v5_independent_review_v2.py E:/QTrade-temp/publisher-v5-independent-review-20261028T105905Z/test_publisher_incremental_gates_v4_20261028.py E:/QTrade-temp/publisher-v5-independent-review-20261028T105905Z/test_publisher_handover_v5_20261028.py E:/QTrade-temp/publisher-v5-independent-review-20261028T105905Z/test_publisher_handover_v4_20261028.py
```

本轮没有修改原三份工具、生产源码、shared state、LIVE=false、rolling OFF、ACTIVE或
不可变账本，也未创建commit。STEP1先前BLOCKED证据保持独立。
