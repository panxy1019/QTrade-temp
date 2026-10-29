# QTrade 后续实施方案：持久化验证收据、增量视图与原子发布

状态：**APPROVED A/B/C — 2026-10-28 17:34 用户明确授权持续推进**。

以下正文保留提出方案时的审计事实与分阶段门槛。此前等待授权的描述为历史检查点，
已由用户最新授权撤销；实施/部署仍须实际满足文中测试与交付门槛。
日期：2026-10-28，Asia/Shanghai。项目目标和自动检查保持 PAUSED。

本文件是只读审计后的方案，不是实施决定、测试通过报告或部署授权。
本轮仅读取源码、现有证据、集群已保存记录及公共GET响应，写入本地审计证据和本方案。
没有运行导出基准、C/D/E验收器、Factor Evaluation测试、服务控制或数据库写入操作。
没有启动子agent开发。原工作区未提交实现全部保留，不自动采纳或回滚。

## 一、最新只读发现

17:08—17:13的读取更新了16:08暂停报告中的部分事实：

| 对象 | 实际读取结果 | 不能据此宣称 |
| --- | --- | --- |
| Oct28前向会话 | 12本现役PAPER/SHADOW均已有COMPLETE记录；公开响应也出现12条当日观察 | 完整E验收或实盘可用 |
| 6本现役PAPER | 各现金/NAV100万元；订单、成交、持仓均0；收益/换手记录为0 | 策略已实现盈利、非空持仓估值/成交链路已被实际验证 |
| 6本SHADOW | 观察存在；现金/NAV/收益为null，订单/成交/持仓0 | 将null填成0或当作PAPER收益 |
| 阻断 | 等权账户各886个，其余现役账户各50个；均UNKNOWN_TRADING_CONSTRAINTS | 为产生模拟成交解除约束 |
| 退休模型两本 | 没有Oct28会话，均保留退休证据；公开停止状态不变 | 当作缺失现役会话重开账户 |
| 模型与版本 | ACTIVE仍29589b44；正式head仍Oct27版本09a67b13；读取前后context相同 | Oct28正式Qlib版本/预测/候选验证已完成 |
| 公共forward | 17:10抓取可用且服务新鲜，但DEGRADED，错误FORWARD_SERVICE_DATA_STALE | 将服务新鲜等同于数据或全链路通过 |
| 行情发布 | publisher294214原b2386c3；最新导出FAILED，45.202204秒；外层Git仍SUCCEEDED | 发布恢复 |
| 行情索引 | 本地与发布checkout索引仍为10:36:23 | 17:13所有公网行情分片已重新核验；本轮未跑完整行情probe |
| Vercel | forward抓取前后deployment均main ae33a7c8 | 新候选已部署或浏览器视觉验收完成 |

17:08自动服务新增会话不是本轮补做。本轮只读其现成结果，没有调用账本构造器、
reconciliation/recovery/observe、重放恢复授权或修改冻结目标。
完整会计复算、原始行情与准入绑定验证仍需获得主动验收授权后执行。

本轮证据位置：

- `E:/QTrade-temp/cluster-evidence/close-readiness-20261028T090840493615Z.json`
- `E:/QTrade-temp/cluster-evidence/paused-readonly-books-20261028T091301Z.json`
- `E:/QTrade-temp/cluster-evidence/publisher-layers-20261028T090846719448Z.json`
- `E:/QTrade-temp/cluster-evidence/stock-public-20261028T091002Z-44f8edda/`

最后一项包含HTTP原body/header、真实请求时间、前后deployment和提取的publication。
capture文件SHA256为e0f0f395cdb280e65d17a585cb7369db3820ca1ba8511668f6e9e4122915c212；
内层publication文件SHA256为244265a109484b1b59ecf796956a9845ac2c88c136ffd69cb69fd77cdd8501f4。
这些是观察证据，未生成新的PASSED验收结论。

17:28:42另做一次公网`GET /api/unified/summary`只读抓取（未调用完整probe）：目录
发布时间17:26:54、年龄108.10秒；日线1498/1500、五分钟1496/1500，预期端点均为
Oct28 15:00。公开commit为1e1e2f3f7abd8a2dcfa04a3926e82a627fafdaba。这是采集目录
与覆盖的观测，不是行情索引/全部分片验收，也不据1498推断任何证券复牌。
证据`E:/QTrade-temp/cluster-evidence/paused-readonly-unified-summary-20261028T092836Z.json`，
SHA256为48c98e1cad1c5ef7f490fc1145b10fbff4c29dc7aec0bf47897df8caa9401881。

## 二、采用的设计思想与本地适配

Flink把输入位置与算子状态一起保存，恢复从一致检查点继续；本项目借鉴“输入集合
与处理状态共同绑定”，不引入Flink或声称获得分布式exactly-once语义。
[Flink状态与恢复](https://nightlies.apache.org/flink/flink-docs-stable/docs/concepts/stateful-stream-processing/)。

Delta Lake先读取快照、生成数据文件，再做冲突验证和提交；本项目借鉴不可变对象、
版本化提交清单和提交前冲突检查，不安装Delta/Spark。
[Delta并发控制](https://docs.delta.io/concurrency-control/)、
[Delta协议](https://github.com/delta-io/delta/blob/master/PROTOCOL.md)。

物化视图保存查询结果以复用；本项目把“证券×频率×窗口”视为可重建的结果视图，
并自行定义失效与刷新规则，不将PostgreSQL文档当作自动增量刷新保证。
[物化视图](https://www.postgresql.org/docs/current/rules-materializedviews.html)。

上述只是适配依据。以下收据、增量与发布协议是**拟议QTrade合同**，尚未实现或认证。

## 三、现有源码审计与复用选择

| 现有组件 | 复用能力 | 必须补齐 |
| --- | --- | --- |
| `scripts/unified_store.py` | 内容地址raw/Parquet、SHA256、不可变link/fsync、批次清单、原始响应读取 | 批次验证结果的持久化身份和读取合同 |
| `scripts/unified_queue.py` | COMPLETE批次/chunks与data_ready_events同事务；隔离chunks；本地WAL/FULL及owner模式 | 事件不覆盖所有隔离/撤销/元数据变化，不能直接以“最后event_id”代替完整变化检测 |
| `scripts/publish_unified_market.py` | 原始重放、质量隔离、去重冲突检查、窗口、分片编码、索引与来源验证 | 进程内_CACHE无法跨重启；每轮仍重放大量超出缓存的批次 |
| `src/qtrade/research/unified_market.py` | 固定公开schema、分片验证、数值和时钟合同 | 保持已有消费者合同；新检查点清单先放sidecar，不悄改API语义 |
| `scripts/atomic_json.py` | 临时文件、文件fsync、原子replace/不可变link、目录fsync | SQLite与文件系统不是同一个原子事务；必须明确提交顺序与崩溃恢复 |
| `scripts/publish_research_snapshot.py`及Git发布 | 既有调度、单发布流程与公开版本 | 每层独立结果绑定；外层Git成功不能掩盖导出失败 |
| 独立原始基线oracle与公共probe | 全量内容/来源对照与固定公开提交核验 | 扩到当前5640冻结范围；保留既有失败，不用旧4200通过替代 |

vn.py现有网关/会计复用边界见已有VNPY_GAP_ANALYSIS；本任务不增加交易或网关依赖。
优先拆出小的“收据存储/校验”模块，编排留在原发布器，避免另建调度平台。
拟新增路径如`src/qtrade/research/market_validation_receipts.py`是提议，不是现存实现。

## 四、P0拟议合同

### 4.1 不跳过物理完整性检查的第一版

第一版不以mtime、文件名、stat缓存或历史PASS代替当前SHA256：每轮仍核验本轮
选中源对象与被复用公开分片的实际字节，使用现有物理源512MB预算和缓存128MB估算上限。
**增量化首先针对昂贵的解码、原始语义重放、聚合和分片重建，而不是删掉SHA256。**
没有经过实测的外部WORM/对象版本保证，不能仅凭“内容地址路径”断言字节永远不变。
若未来要减少物理重哈希，必须另提存储完整性合同、故障实验和授权；不含在本方案首版。

### 4.2 持久化批次验证收据

每个成功收据绑定下列身份，使用规范化内容hash形成不可变receipt_id：

- batch_id、实际manifest字节hash与内部evidence hash、raw包与normalized Parquet
  的实际hash/大小、captured response lineage、admitted rows hash与条数。
- parser/normalizer/quality规则schema和实际依赖源码指纹、相关库版本、数值政策。
- universe/security type、calendar、price basis、来源请求规格与时点政策指纹。
- 验证时实际时间、源数据时间范围、核验器版本、结果与原始失败证据引用。

只有“当前物理字节匹配 + 完整依赖指纹匹配 + 当前metadata/context仍合法”的收据
可跳过语义重放；不能只检查batch_id或代码的某一个文件。当前as-of限制、窗口与
新鲜度判断仍逐轮执行。新鲜度不能被冻结在一张历史PASS中。

确定性坏数据可保留失败收据，但不得作为复用通过；OSError、超时和未完成写入
不能固化成永久隔离。损坏或不完整收据视为miss，重走原校验；旧记录不覆盖。
普通SHA256证明内容一致性，不是防拥有全部写权限者伪造档案的数字签名。

### 4.3 变化识别与分片复用

第一版仍在短SQLite只读事务中取得完整有界metadata快照，计算输入集合指纹并与
前一代逐项比较。原data_ready_events作加速提示，不能作为唯一真值。
不在第一版迁移采集队列表或给活跃collector安装触发器。

脏键为`(universe_version, symbol, frequency, window)`，并保存反向依赖。
日频保持原20会话窗口分片；五分钟保持原按日分片，以便原字节合同对照。
某证券/频率的关联批次尚未全部处理或存在冲突时，仍按现有规则隔离该证券/频率，
不能只放行其中“看起来正常”的分片绕过当前pair级合同。

失效至少包括：新增/替换/删除来源，manifest/raw/Parquet变化，隔离增加或解除，
同时间戳冲突，回补较早日期，日历修正，窗口滚动，证券池/证券类型/复权口径变化，
parser/quality/输出schema或依赖源码变化。无法可靠界定影响范围时全量失效。
即使新批次价格相同，只要来源关系新增，相关分片也要更新lineage。

未变化分片复用原hash和原字节；变化分片从该脏键的**完整窗口来源**重算，不能把
新增行简单append而遗漏冲突/撤销。窗口到期从新索引解除引用，旧不可变文件保留。
重算隔离在证券/频率/窗口范围，不能拖累无关日频研究或授予PAPER交易资格。

### 4.4 检查点与原子发布

```text
完整metadata快照 S + 当前物理hash核验
    → 命中收据 / 新批次原始重放
    → 计算脏键，复用或重建不可变分片
    → 分片落盘fsync + 完整3000条索引/来源检查
    → 不可变generation清单落盘fsync
    → 复核源集合变更、发布owner及前代指针
    → 原子替换公开索引指针并fsync目录
    → 单独记录Git提交/推送/公网交付结果
```

拟议sidecar记录包含三类：batch validation receipts、分片依赖/内容索引、
publication generations（parent id、输入集合指纹、窗口、规则指纹、完整refs、
索引hash、阶段和失败证据）。本地SQLite只作协调/可重建索引；不可变文件及经验证
的公开head决定读者实际看到的代。新generation与Git commit是两个不同身份。

跨SQLite、文件系统、Git、Vercel不宣称有一个分布式事务。顺序必须先持久化
对象和generation，再更新公开head；DB状态与head若不一致，恢复时核对真实文件
后补记状态，不能凭DB的SUCCEEDED发布半成品。只有generation完整耐久且所有依赖
匹配才能复用。失败时不前移发布进度、不改旧hash、不制造新的成功时间。

发布CAS需要同一主机上受验证的唯一owner/提交互斥和父代比较；原子replace本身
不是CAS，SQLite token也不能隔空阻止旧进程写NFS指针。交接必须确认旧writer退出，
旧/新publisher不能同时有发布权。目录fsync失败、RPC/存储结果不确定时显式
UNCERTAIN，停止提交并核对实际head，不能盲重试或自动把指针退回造成混代。

第一版只做固定输入快照S的提交。提交前确认S内源文件/选中chunk/隔离状态未变化；
S之后新增且不改变S有效性的批次留到下一代，不谎报已处理。冲突则放弃候选head。

即使没有新价格，也必须经本轮真实metadata/字节检查才能产生新的验证/发布记录。
`published_at`、`validated_at`、`source_end`、`expected_end`分别保留；不能只刷新
旧索引时间来掩盖数据陈旧。公开180秒新鲜度与全天数据覆盖的含义不静默改变。

### 4.5 存储与崩溃边界

SQLite收据索引拟置于已有本地`/root/qtrade-local`，不在NFS启用新的WAL数据库。
SQLite官方说明WAL不适用于网络文件系统；对象/不可变检查点可沿用NFS，但实际
文件/目录fsync、原子命名、故障恢复仍需在同一挂载隔离目录验证。
[SQLite WAL](https://www.sqlite.org/wal.html)、
[SQLite原子提交与网络锁限制](https://www.sqlite.org/atomiccommit.html)。

本地卷丢失时，从已持久化并重验的不可变检查点恢复；无法认证的记录只作缓存miss。
新索引不得依赖唯一存在于临时本地缓存的证据。第一版不做自动GC，不删除旧源、
失败证据、冻结实验或仍被旧generation引用的对象；保留100GiB空间预留。

### 4.6 正常增量与全冷恢复分开

| 模式 | 工作内容 | 拟议门槛 |
| --- | --- | --- |
| 正常增量 | 新/变化输入语义核验，脏键重算，全metadata/物理hash复核，完整索引提交 | 保持45秒、512MB源/128MB缓存及原输出限制；超限保留旧head，明确滞后 |
| 进程冷启动但检查点有效 | 重建本地索引、重验已有收据/对象，再走增量 | 与正常增量分别记录；目标仍45秒，未通过不能当热路径通过 |
| 无有效检查点的全冷恢复 | 首次全量原始重放，建立新收据/视图/检查点 | 独立非生产计时；可提议120秒诊断上限，但不是已获准生产合同 |
| 独立全量审计 | 禁用候选收据，原始基线核验全部来源和输出 | 独立资源/耗时报告；不冒充45秒正常发布成功 |

生产仍执行旧45秒合同。全冷预算最终取值必须附同冻结负载的多轮证据、资源峰值、
失效全量范围和恢复SLA，经单独审批后才进入配置/代码/验收版本。不能通过后台
偷偷预热或先跑不计时bootstrap来假装无检查点冷启动达标。

## 五、P0测试与验收标准（均待恢复授权）

### A：非生产实现与合同测试

1. 以当前5640批次/140136条metadata冻结快照为固定基准，记录原freeze、ledger、
   catalog、源代码与时钟hash；不修改原冻结件，也不将它称为部署时最新快照。
2. 独立全量oracle不信任候选收据、不调用候选重放器：比较全部3000索引条目、
   5986分片、166050382分片字节及全部源批次关系。固定输入/时钟下要求分片hash
   逐个一致。索引只允许明确登记的实现来源/新generation元信息差异；不能泛化忽略
   时间、质量、数量或lineage差异。允许字段也必须独立校验其真实性。
3. 无变化、新增批次、跨日换窗、历史回补、重复价格新增来源、撤销/隔离解除、
   冲突、规则升级、字节损坏、缺分片、坏收据、磁盘满、慢NFS、源预算耗尽均有测试。
4. 在文件fsync前后、generation写入、head替换及DB补记各点注入崩溃；启动恢复
   必须只读到完整旧代或完整新代，不重复消费、跳过未验证批次或发布混合版本。
5. 并发collector新增、旧chunk撤销、两个publisher竞争、旧owner延迟返回、Git非快进
   和HTTP超时均拒绝错误的成功；实际owner交接测试使用隔离假服务，不控制生产。
6. 正常增量连续至少20轮，覆盖无变化和真实增量；冷启动/恢复各至少5轮，记录
   每轮max/p50/p95、阶段时间、读写字节、dirty/reused数量、RSS、缓存、重试及失败。
   正常增量每轮须≤45秒；p95≤30秒是争取的余量目标，未经测量不承诺已达到。
7. 同时验证原collector和研究owner未被影响。原先两个分片worker和解码预取只是
   候选，不先验保留；增量方案能复用更简单已审实现时优先减少运行复杂度。

### B：部署准备与单独生产授权

提交准确Git版本、完整执行源码清单、Linux/本机测试、独立审查、基准及故障证据、
迁移/回退方案和安全限制。schema采用sidecar且旧reader仍可读是目标，不能假定。
生产部署、owner交接、任何预算合同变更均需用户另外明确批准。

### C：获准部署后的实际交付验收

按顺序保留不可变证据：本地真实export成功 → 对应内容的Git commit → push远端
commit可读 → production索引引用同一代 → Vercel API固定版本分片一致 → 公网时效。
记录相邻deployment、原HTTP body/header/hash、实际上海时钟及数据覆盖分母。
至少观察正常连续轮次及一次真实跨日窗口；catchup、单次快速跑或Running不替代。
沿用现有index≤180秒/catalog≤1200秒核验，并分别报告1500池的日/分钟覆盖与缺口。
发布SLA和研究/交易资格是不同门禁，任何发布优化都不能放宽PAPER约束。

## 六、STEP1恢复后的真实验收

获得主动验收授权后，先固定Oct28实际执行模型29589b44的当日目标与准入归档，
再运行现有E工具，分别检查12本当日观察和2本退休关闭。读取记录本身不修改目标。
零成交、未知约束阻断是应如实保留的结果；不为测试而制造持仓或放宽限价/停牌规则。
非空成交/持仓跨日估值尚未被实际观察覆盖，应继续列为限制，不宣称全部执行场景通过。

核验会计重算、成交journal、现金/NAV、冻结来源、阻断分类及实际public/deployment
绑定；SHADOW的null保持null。完整E还受公开DEGRADED等现有门槛约束，不能因12本
会话出现就改成PASSED。若严格审计区分当日观察和下一日就绪不足，另提合同修订，
保留原失败，不能在本次方案中暗改。

并行核对Oct28正式版本、已有ACTIVE日预测、signals和候选later-version验证，
候选只验证不激活；保持当前ACTIVE、rolling OFF。即使后续自动状态发生变化，
旧审计仍绑定原模型/原日/原发布范围，新状态必须独立核验。
PAPER恢复或owner重启不包含在普通审计、模型授权或本方案内。

## 七、STEP2实施顺序与验收

### S2-A：先验收时间语义修复

当前未提交的factor_evaluation.py增加归档网格/双时钟/标签端点/成熟度/来源复验；
本地120项通过、0skip；原891股五日报告只读重验且字节未变。该事实来自暂停前
证据，本轮没有重新运行测试。恢复后先独立review，随后在Linux隔离执行包核验
精确源码，运行同套目标回归及原报告重验，原3个red失败与修复结果分开归档。

旧v1缺少标签退出行双时钟和资格interval原字节，不能凭补规则追认历史事实。
第一阶段保留旧v1兼容与限制；若新增完整证据bundle，明确v2 schema并重新生成
新的实验身份，绝不回填/重签旧报告。

### S2-B：四因子评价扩大历史窗口

复用momentum20、reversal5、lowvol20、multifactor_equal_rank精确版本及合格READY
日频范围，先按共同日历和预注册窗口评价。当前指标已有IC/RankIC、分组、稳定性、
换手和两两冗余；新增较长窗口、年度/滚动分段及对照时不重复实现原指标函数。
只使用实际具备足够历史和可用标签的数据，不能把短五日实验外推为多年稳健性。

分开列出研究收益标签与可执行策略收益：当前评价成本合同是GROSS_LABEL_DIAGNOSTIC，
fees/slippage=false。成本对照须用冻结策略、Qlib Exchange/TopkDropout及同一证券
资格、信号时间、交易价格、佣金/最低费用/容量/再平衡规则，不能从IC直接推算净收益。
有拟合/选参时另冻结train/validation/test和标签purge/embargo；不把已查看窗口称OOS。

### S2-C：关联StrategyRegistry

新增受限、版本化合同，将factor_ref、evaluation_ref、READY data_binding、
signal_kind、label/split、组合/执行/成本、实际源码与父策略引用共同冻结。
因子/评价/策略三个版本不能互相替代；改因子、价格基础或成本必须形成新版本。
复用已有StrategySpec/StrategyRegistry原子job link、角色和EXPERIMENT生命周期。
不得把未知新字段塞进旧parameters绕过服务端验证，也不将评价通过自动变成ACTIVE。

### S2-D：受保护Strategy Builder

复用当前登录、specification预览、版本绑定、Git任务队列、worker幂等核验和
既有网页。第一版仅四因子、合格日频版本和已支持组合/执行规则；Qlib固定0.9.7，
不增加复杂模型。现有Alpha158/Ridge/LightGBM Builder功能保留其原范围。

预览显示精确因子/数据/模型/策略版本、实际日期范围、成本和PIT/幸存者/复权限制。
提交后状态展示QUEUED/RUNNING/COMPLETE/REJECTED/FAILED及失败原因；不能把202视为
研究成功。篡改版本、未知字段、旧预览、重复request_id不同内容、未登录、取消和
失败重试均有边界测试。预览修改使原版本失效，worker再次独立核验。
新网页操作与视觉验收等待相应授权及可用的用户指定Terra子agent；DOM未取得时
不得标视觉通过。任何研究提交均不触发模型激活、PAPER恢复或实盘。

## 八、授权范围与阶段交付

| 阶段 | 需要明确授权后才能执行 | 交付物 |
| --- | --- | --- |
| 当前 | 已允许只读审计与方案准备 | 本方案和只读证据；项目保持PAUSED |
| A：恢复非生产研发 | P0收据/增量实现、隔离Linux/集群测试、STEP2修复验收；并行现有STEP1主动审计须同时明确包含 | 代码、测试、冻结基准、独立审查、失败/成功归档、限制、复现命令 |
| B：批准生产部署 | 精确候选版本、迁移、publisher定向交接及必要的新预算合同 | 已批准部署/回退清单、实际owner与源版本证据 |
| C：实际交付与后续STEP2网页发布 | 依阶段部署范围执行真实公开验收；网页接线另给可审查变更 | 各层原始证据、真实新鲜度、研究端到端及视觉结果 |

授权阶段A不等于授权B，不自动恢复定时任务；预算、服务控制、候选模型激活和
PAPER恢复不能由“继续研究”推导。当前先等待用户明确回复，不执行上述后续动作。

所有阶段保持LIVE_TRADING_ENABLED=false、rolling=OFF、当前ACTIVE和不可变账本。
历史回补/退市/PIT/复权为独立长期工程；研究使用合格范围并披露偏差，改进数据后
创建新版本复验，不覆盖旧实验。维护owner活跃时不apply缺口修复。
