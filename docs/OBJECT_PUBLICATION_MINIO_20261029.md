# 本地 MinIO 与对象发布协议：2026-10-29 检查点

本阶段按用户授权使用集群本地 MinIO 验证真实 S3 请求，不要求先开通
Cloudflare R2。生产入口、采集 owner、ACTIVE、PAPER/SHADOW 账本和旧实验均未
由本阶段修改。`LIVE_TRADING_ENABLED=false`、rolling OFF；生产导出45秒、源512MB、
cache128MB、普通 Git90秒的合同不变。测试服务只绑定 loopback，每次测试结束停止。

## 目前确认的结果

| 工作 | 已确认效果 | 尚未证明 |
| --- | --- | --- |
| 对象发布核心 | 不可变 SHA256 对象、完整来源校验、fsync 收据、父 ETag 条件提交、未知结果只读对账 | 生产性能、服务端权限与跨对象事务 |
| 固定 SDK 适配器 | 显式 R2 HTTPS origin、可选依赖、禁止隐式配置/插件、禁止自动写重试、完整读回 SHA | 真实 R2、最小权限、TLS 和公网 reader |
| Linux 回归 | 核心45 + SDK41，共86通过，0 skip；463文件源码逐字节前后相同 | 全平台回归或生产部署 |
| 独立审查 | 修复核心15项、SDK5项独立检查；包含原反例逆向重放的精确组合112通过 | 实际云服务行为 |
| 本地 MinIO | 真实条件写、竞争、丢失 ACK 对账、部分提交恢复、损坏检测7项通过 | 原固定行情包的完整组合发布，见下文阻断 |
| STEP2 原生回测适配器 | Linux Qlib0.9.7：33通过，0 skip；含2项真实 TopkDropout/Exchange 执行 | 真实891股回测、任务队列/网页执行接线 |
| STEP1 独立审计器 | 执行日与当前就绪分离、历史 STAGED 再验证 reader；新增80项本地测试通过 | 新 reader 的完整 Linux/真实集群验收及缺失的 later proof |

全部结果是明确范围的工程证据，**全 STEP1、公网新鲜度和生产 STEP2 均未通过**。

## 复用方式与协议边界

复用现有 index/catalog/quality、逐分片验证器、Qlib 和注册器；没有自建新的行情
语义、交易通道或基础设施框架。vn.py 的 gateway/OMS 不能提供对象存储提交证明，
本模块不访问它们。新增 `ObjectStore` 接口是存储边界，策略不能调用它连接交易网关。

`market/shards/<sha>.json` 等内容寻址对象以 `If-None-Match:*` 上传；清单记录全部
资产、来源 Git、父版本与 generation。所有对象核验完成后，单独更新
`market/latest.json`：首代使用条件创建，后续使用父 ETag 的 `If-Match`。
ETag 是不透明并发令牌，不能作为 SHA256。发现冲突不会覆盖其他写者。

上传或指针提交失去 ACK 后保留 intent；通过完整读取确认真实结果。读到旧 head
或404不能证明在途请求不会稍后提交，不盲目重发指针。收据使用 create-only、
文件 fsync 和目录 fsync；已有收据不能替代当前内容校验。

独立审查确证原核心 `d362529b` 的公共 `reconcile()` 未完整绑定 manifest 与 ID，
原失败 XML 和源码保留。修复 `d11c801a` 在入口检查 ID/SHA/source/clock/parent，
再核真实远端 manifest。SDK 原反例也保留：显式凭据原本仍会触发环境配置/插件
发现，以及 DEBUG 日志可能记录签名。固定 `d26685bf` 隔离这些默认入口。

SDK 只在安装可选 `r2` extra 时使用，boto3/botocore 均固定1.43.120。使用的私有
PluginContext hook 在缺失时拒绝运行，依赖升级必须重新审查。SDK 的 socket timeout
不等于整个发布流程的总预算，外层仍需有界 owned process。

## MinIO 构建和服务隔离

集群没有 Docker/Podman/Go/MinIO。使用官方固定源码
`07c3a429bfed433e49018cb0f78a52145d4bedeb` 和官方 Go1.24.13 构建；
源码与 Go 归档分别核验 SHA256。首个600秒构建失败保留。集群无法访问官方
GOPROXY 的实测随后保留，改用单一模块传输镜像，保持 `GOSUMDB=sum.golang.org`、
原 go.sum、readonly 依赖和 Go 模块认证；没有关闭 checksum。

第二次编译94.982秒、`go mod verify`7.722秒通过。二进制 SHA256 为
`ea71aa0735f6930c3ae3fcc27f0dc2d670f941043085d9a501cad323a393db2d`。
版本字符串是 DEVELOPMENT.GOGET，身份来自固定源码与构建记录，不能冒称上游签名
预编译包。这些时间是**构建时间**，不是行情发布 SLA。

MinIO 只使用独立本地目录和 `127.0.0.1` 临时端口，随机测试凭据只保存在进程内存；
启动 banner 丢弃，不向 Git、配置默认值或日志写入凭据。不创建云账户或公网服务。
真实 `/proc` listener 与 owned pid 核验仅有 loopback。外层 owned group 包含
测试 Python 与 MinIO；正常结束和异常清理均有界，并将服务确实停止纳入成功条件。

测试使用独立 `MINIO_NONPRODUCTION_V1` scope。正式 R2 factory 仍固定 HTTPS
账户域名；没有为了 localhost 放松该工厂。root 测试凭据不能证明最小权限。
单机 MinIO 不证明 R2 行为、断电持久性、HA 或服务端禁止覆盖/删除。

## 真实协议测试与完整负载阻断

7项实际 S3 测试已通过：条件创建/旧 ETag 拒绝、双写者仅一人成功、真实提交后的
应用层 ACK 丢失、延迟提交与重开 journal 不重发、不可变上传 ACK 丢失后读回、
第二代提交前失败保留旧已发布 head、特权修改对象后 SHA 检测拒绝。
ACK/延迟场景是明确标注的应用层故障注入，**不是 TCP 丢包实验**。

首轮完整负载读取因旧 cold operator 的 FBA import-origin 门禁拒绝，未开始传输；
保留原报告。新的非生产读取器固定 Git 提交，禁止 lazy fetch、全局配置、hooks
与自动 GC，只允许 show/ls-tree；30秒及 owned TERM2/KILL2。它继续复用原738c
guarded physical reader 的实际分片 SHA1(tree OID)+SHA256，以及逐 occurrence
语义校验。独立离线检查11通过，不能替代实际负载结果。

随后固定 `abe2986cd6e3f3faeb4984454498e4c67f1646d3` 原包，在3,000条索引、
5,987个分片、167,397,950字节的组合检查中被
`OBJECT_PUBLICATION_INDEX_CATALOG_LINEAGE_MISMATCH` 拒绝。索引引用的 master
与同提交证券目录的 master 不同。原包不重签、不拼造时间，不放松新门禁，
不把7项通过改称完整发布通过。匹配的真实旧目录或新冻结一致组合仍须独立绑定来源。

01:21有界只读调查进一步确认：index master 为 `38f5d706…`，同 abe 的 catalog
master 为 `c4eddb82…`，二者自身 checksum 均合法，universe 和1,500目标集合相同。
分别检查 abe 祖先与明确当前本地 HEAD 终点的最近20次目录变化，都没有找到匹配原件；
public 顶层只有当前 unified.json。没有广扫其他历史/操作员档案，不能外推全盘不存在。
这提示后续生产者需要在导出时保留同次实际输入目录，再原子提交组合；当前原包保持拒绝。

这167MB是当前公开行情窗口的 JSON 分片，不是全部原始行情、多年历史或 PIT 数据量。

## 公网交付仍是独立未通过项

已批准的单次180秒冷 Git 发送在00:15启动，180秒未得到 ACK，清理后总182.003秒；
保留 `PUSH_INTENT`、`PUSH_UNCERTAIN` 和遥测，不重复发送。枚举/压缩约7秒完成，
之后 writing 计数进展缓慢；对象计数百分比不是字节百分比，也不能据此认定唯一网络原因。
00:21只读远端仍为 `5a0925f4`。

01:08真实 Vercel GET：网页生产源码仍 `ae33a7c8`，行情 catalog 来源仍 `5a0925f4`，
目录生成于10月28日20:07:27，API 的日频与5分钟均报告 `STALE_PUBLICATION`。
这是原始 HTTP body/hash/时钟捕获，**不是完整 C/D/E probe 或浏览器视觉验收**。
MinIO 成功不会自动更新 Vercel，也不能据此宣布公网恢复。

## STEP1、STEP2 与后续顺序

STEP1 的一次只读事实快照：16本中10月28日 COMPLETE12本，包含10本基线和旧295模型
两本；新68模型在收盘后激活，没有当天 target/admission/session，属于单独 current
readiness。98d 仍 STAGED，10月28日 later-version job/proof 均为0，D继续 BLOCKED。
新只读审计器不能替代缺失会话、公开交付或真实新 proof。

STEP2 适配器只做已登记因子 score→原生 Qlib 组合执行：复用冻结注册、版本和成本，
明确下一交易日执行与 TopkDropout 内部 previous-session signal。原标签只是诊断，
不是已实现收益。原测试与失败保留。共享 worker 的20文件来源指纹仍是正式接线前的
兼容性门槛；此次没有修改共享 worker 或旧合同。

接下来按独立门槛推进：

1. 修复新发布输入的原子组合与来源绑定，保留原不一致包；再做完整 MinIO 负载和两代复用。
2. 单独设计 immutable unknown PUT+404 的可恢复协议；当前首版会安全停住，尚不能自动恢复。
3. 分析约6,000对象的重复完整 GET、请求长尾和内存；保留独立全量审计，不宣称当前原型
   达到45秒增量合同。真实服务的权限/不可变保护也必须先验收。
4. 适配公网只读 reader、旧 Git fallback 和发布来源标注；实际服务可用后再验收上传、
   head、Vercel API、新鲜度及连续普通轮次。R2 账户无需用于当前开发测试。
5. 并行完成 STEP1 新 reader Linux/集群验收与真实缺项；STEP2 再做真实已登记因子原生
   回测，之后按已审新 execution-source 兼容合同接入受保护队列/网页。

当前样本选择、未认证完整历史 PIT/复权、多年分钟连续性等限制继续随新实验披露；
未来数据改善使用新版本重验，不覆盖旧实验。

## 证据与复现

逐文件 SHA256、原失败和成功报告在
[`docs/evidence/object-publication-minio-20261029`](evidence/object-publication-minio-20261029/)。
`checkpoint-manifest-v2.json` 包含后续目录调查，并绑定保留的首个 checkpoint manifest。
大二进制、SDK/Go归档、私有原始构建日志和测试凭据不入 Git。

```powershell
$env:PYTHONPATH='E:/QTrade-temp/r2-sdk-20261029;E:/QTrade-git/src;E:/QTrade-git'
python -B -P -m pytest -q -p no:cacheprovider tests/test_object_publication.py tests/unit/test_r2_object_store.py
```

Linux精确463文件 staging、owned child、实际 MinIO server 和停止流程由证据中的
`operators/run_minio_protocol_20261029.py` 与
`operators/minio_s3_fixture_check_20261029.py` 保存。它们是固定本次目录/源码的操作员
复现记录，不是生产部署入口；重新执行需新测试 run 路径、原二进制/源码哈希和资源门禁。

原生 Qlib33、STEP1 reader80以及独立审查各有自己的 source manifest、命令与限制，
不能把这些数字相加当作全仓库或生产通过率。
