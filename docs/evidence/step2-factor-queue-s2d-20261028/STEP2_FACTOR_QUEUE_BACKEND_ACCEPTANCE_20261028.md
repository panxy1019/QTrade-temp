# S2-D 因子策略登记队列：后端与隔离验收

2026-10-28。结论为 PASS_NONPRODUCTION_REGISTRATION_ONLY。本轮实现
FACTOR_STRATEGY_REGISTER_V1，复用现有 HMAC/GitQueue、单 worker 子进程、
StrategyRegistry 原子关联及有界 public transport。没有生产 worker/队列提交、
发布、模型激活、PAPER、因子执行适配或组合回测。

## 实现及边界

- factor_strategy_catalog.py：严格 catalog、纯预览及 16 KB 请求合同。
  canonical 上限 900,000 bytes，实际 pretty wire 上限 1,000,000 bytes；至多
  4 个数据集、16 个模板、100 个父版本、每数据集 4,096 个真实日历会话。
  catalog 身份排除 heartbeat，外层 evidence 仍覆盖它。归档 GET 允许旧心跳；
  必须带时区，未来超过 60 秒拒绝。POST 的双心跳 [-60,720] 秒、worker
  source/kind、auth/origin 及共享三任务容量由 API 独立检查。
- factor_strategy_contract.py：新增 pure preview_strategy(template,spec,calendar)。
  API 请求不能提供评价上下文、源码、标签、因子公式或任意代码。原八字段
  factor spec 独立，不向旧 Qlib model spec/parameters 塞字段。EvaluationReader
  仅抽象已有 get()，原 SQLite reader 仍可用。
- scripts/factor_research_contracts.py：显式只读批准档案，无默认路径、扫描发现、
  latest 回退或公有任意路径。实际 gzip、factor SQLite、READY admission/
  manifest、20 个实际源码文件 SHA 形成身份；mtime/size 不作内容证据。
  心跳缓存只有 compact templates。实际 child 重新解压/验证报告、精确因子 refs、
  所有 READY 文件/准入及源码；一次只持有一份展开报告。
- scripts/run_factor_strategy_registration.py：先验证实际证据，后调用现有严格
  register_factor_research_experiment；Registry 单事务写 job/spec/contract/lineage。
  只登记 EXPERIMENT/NONE，无模型/收益输出。
- scripts/run_qlib_worker.py：显式 --factor-archive 启用；未配置无 capability，
  新 kind durable REJECTED，原 Qlib 路径不变。共享三任务容量、预测优先、取消、
  恢复复用旧 worker。固定 child 模块/argv、300 秒时限、LIVE false。恢复验证
  PID start_ticks、独立 process group、准确 -m 与 --output tokens；不确定旧
  job 变 INTERRUPTED，绝不自动重跑。

factor workflow row 的 engine=FACTOR_REGISTRATION、model=NONE_FIXED_FACTOR_SCORE、
spec=null、factor_spec 独立，registration_only=true。完成 detail schema 是
qtrade.factor-strategy-registration.v1。API/transport 由 root 并行实现，其确切
三文件也纳入同一 Linux 包；前端验收属于 root 独立证据，不能外推线上交付。

## 失败、恢复和旧归档

child 在 Registry 写入前发生证据验证错误，可输出 signed REJECTED receipt。
开始 Registry 写入后不得输出这种确定拒绝。Registry 已提交而 receipt/worker
DB 未提交时，保留 FAILED/INTERRUPTED 与 Registry job association，要求对账。
两个数据库不是同一个事务；取消不能撤销已完成的研究登记。显式新 job 可以
关联同一定义，保留原 clocks，不增加第二次 lifecycle transition。

完成回执重新核对请求、源码、stored contract、EXPERIMENT/NONE 及
job/actor/registered_at。自身登记会新增父选项并改变 catalog SHA，因此完成
核验不拿新的全局 catalog 冒充提交时 catalog，原提交 SHA 保留在回执中。

归档结构仅接受明确的原 S2-C 13 文件集合或当前 20 文件集合。
verify_runtime_source=True 与当前 Registry 入口仍要求等于当前 20 个实际 SHA；
catalog 始终要求 20。12/14/17 文件或未知路径重新 hash 后仍拒绝。

## 批准档案

显式 archive_root 下使用固定 index.json、factors.sqlite 和
reports/<factor-eval-32hex>.json.gz。index 为 AUTHORITY + signed 的
qtrade.factor-research-archive-index.v1，包含 registry_sha256、
evaluations[1..4] 和 templates[1..16]。每个 evaluation 精确字段：
evaluation_id/specification_sha256/report_manifest_sha256/data_version_id/gzip_sha256。
template 是原八字段 factor spec，parent 必须 null。

index 最多 100,000 bytes；factor DB 最多 2,000,000 bytes，拒绝非空 WAL/journal；
每 gzip 最多 8,000,000 bytes，解压最多 256,000,000 bytes。拒绝坏 gzip、重复
JSON keys、未知字段、符号链接和越界引用。不创建 evaluation DB。index/hash
是内容一致性证据，不是外部数字签名或 PIT 认证；archive 应经独立运维批准。

## 精确源码与回归

Git 基线 a89d45f96f0b89811ad07464e9ebb7ee3a9bbf12 加 11 个显式 overlay；
完整清单/字节在 evidence package-manifest/overlay.tar，未混入并发 publisher/
状态文档。owned source/test 均 LF。revision 是基线标签，合同明确不把它当完整
Git 认证；overlay manifest 描述实际源码。正式 commit 会产生新的冻结身份。

| 最终后端源码 | SHA256 |
| --- | --- |
| factor_strategy_contract.py | 92d19c483f80a00080debe1304fcdd8ec906496e86b00047d96dd52b806cce76 |
| factor_strategy_catalog.py | 3e40d5c7b9a00ae40e7fb9d5c080182e6977e5af7f972bdc408babd4ae357fad |
| factor_research_contracts.py | 114b3c24ab03a8ba8ecb2181041dc2c0470b08bc54b257b596696e31dd2d0dc6 |
| run_factor_strategy_registration.py | 72fec4144bc1b600d9471685e9e28f53e48af9ad433da3ad88006d6fc1f78aa1 |
| run_qlib_worker.py | f1b0ada2063340383316a9cf65a1e2d02a3c22e796434cb42ceb0042f059dbdb |

最终包 factor-queue-s2d-final-20261028T124314Z-e0813807，共 2,280 文件：

- Linux 377 passed / 0 skipped；pytest 95.60 秒/full 96.255 秒，child peak
  RSS 224,560 KiB。Windows 373 passed / 4 Linux/POSIX-only skipped，104.53 秒。
- 两平台完整 source before/after 相同；Ruff 和 Linux-platform
  explicit-package-bases mypy 5 source 通过。
- 新独立后端 60 项，覆盖 synthetic READY/archive、坏 JSON/gzip/扩张上限、
  auth/HMAC、容量/取消/重启、同大小同 mtime 篡改、准入/源码/ref 漂移、
  不可变 job/parent、Registry commit 后 receipt 故障、真实 owned child 与拒绝。
  原 C、registry/evaluator、旧 Qlib worker/API 同组回归。

原 candidate1 Linux371/Windows367+4、一次真实登记原样保留，不外推最终源。
初始 Windows POSIX directory-fsync 故障的 44pass/1fail/1skip XML 保留；没有修改
共用 durable writer 来制造 Windows 通过。C13 red2fail/3pass、green6 也保留。

## 真实登记和升级谱系

使用原 S2-B 891 股/16 日报告 factor-eval-dd6a5c6d61a64044975646668eef4486：

- specification df37905951d4c352b0a0397628f07158e36bd5b0edcae24339f221defb9116de
- manifest c0a86db641a9ab653d00f527bc557e5ceb84a0e7d866dd36d11df96c406453a9
- gzip 74c246d7822b4e860706dcc2c212763ede3aef62f38f5b94297237acf5457ada

选择 momentum20 原确切 ref，data binding 仍为该报告历史 READY 09a67b13…，
不要求仍是 current head。期间正式 head/ACTIVE 已由独立 standing policy 变化；
本流程没有调用控制操作或回滚。

1. Candidate1 job 762688be9f7a49018cb64a63d6ab970d，strategy
   2983861cbc2e6a4891bac485028f117962e339e3594c73e11d7b93c397934711；
   spec/contract/job/transition 各 1。总 span 110.932 秒，child+对账 66.427 秒，
   child peak RSS 1,235,348 KiB。
2. 最终 candidate2 使用新 operator root，逐字节复制已对账 candidate1 Registry
   （SHA 2ea35bbce875a7912389f49003dc9d0431378f04e2168052e80d42324dae63ce），
   以其实际合同为明确 parent，记录源码兼容修复 reason/hypothesis/change_summary。
   新 job 1cfad5d88fad4f70bd7b029a57efd9d2，strategy
   469641828717ccc398e1bf07a4c272740c9d24534cddad81818e7c09224e6ca2，
   contract b34c0a91b42beb630e62bbe8e6f5f1d53d608f7631fe7c5ddf5c07713cea87cc，
   receipt ee290dad340e740d4a6c9877a7fc93ad05cbd787c8f169f015783f3b3fe5d0f6。
   新 DB 四项各 2，新增 lineage 准确指向原父；DB SHA
   73b86630cf8181c8caef66c74b4f782dc98bdef224ae902ba75f9f1fff7891b1。
   总 span 110.083 秒，child+对账 65.939 秒，child peak RSS 1,233,616 KiB。

最终 acceptance evidence SHA
79f1a8eef1a8e9846df376822b2301483338cd6aeb84e472afc16d4037a7eb72。
父/child Python audit guard 将所有写及非 RO SQLite 限制于各自 operator root；
真实准入与 ACTIVE DB 只用 mode=ro。完整源码、原 891 实验 279 文件、S2-B
19 文件、candidate1 DB/request/receipt、所选 READY admission 均前后不变。
实际 ACTIVE 68d9287e… 与 auto-train OFF 快照前后相同。两次 child 均在 300 秒内；
NFS 始终大于 100 GiB/local 大于 2 GiB。这不是发布器或实时 serving SLA。

旧 C13 fixture f728b11244661ef3682c80b0bfdbf2b9d88d666716fea3bbded0819a073cad8c
由冻结 C 源码（contract243472…、完整 2,196 文件未变）在本轮对原 S2-B 报告作
纯 freeze，不伪装为先前登记事件。最终 reader 对原 bytes 重验成功，当前
Registry runtime 拒绝，复制 DB bytes 未变。原 5 日 report 47bed3ad… 另通过最终
reader，2.221 秒，gzip 未改。

## 复现及剩余门槛

    LIVE_TRADING_ENABLED=false PYTHONPATH=src:. python -B -m pytest -p no:cacheprovider \
      tests/test_factor_research_worker.py tests/test_factor_research_api.py \
      tests/test_factor_strategy_contract.py tests/test_qlib_worker.py

完整 14 文件命令、Git archive/overlay 提取校验、双平台/真实 driver、request、
catalog/definition/receipt、RO/write guard、原件清单、压缩 DB 和所有失败 XML
均在 docs/evidence/step2-factor-queue-s2d-20261028/manifest.json。重现实验须新
operator root/output/job ID 与显式批准 archive，不覆盖本轮记录。

剩余门槛：正式 commit 后重绑实际 revision/源码；批准 archive 配置及非生产
queue/API/Builder 实际往返；public transport 部署与取回另验。本轮未访问真实
GitQueue、未部署改动。目录缓存是描述；child 当次物理重验才是登记准入。
Registry 生命周期/immutable parent 事务复用旧实现，不宣称跨 workflow DB 原子。
未来原生 factor signal/portfolio/backtest 仍需执行时点、约束、费用、净值 oracle；
当前短窗口 IC 和毛标签诊断不构成样本外或净收益证据。
