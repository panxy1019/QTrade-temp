# 单次 DIRECT SSH cold recovery：实施准备，未获发送批准

当前只完成源码与离线测试。本轮没有集群连接、部署、prepare、advertisement 或 main push，没有修改生产配置、源仓库、原声明或 publisher owner。**180 秒是待用户单独批准的提议，不由 ABC 持续授权自动涵盖。**

候选源码 `publication_cold_recovery_operator_20261028.py` SHA256：
`7ec53cc558c62c56dade0cf390ba274ff34de2c97c80ddb664a6103e6ce79f09`。

从 v3 `76bf3e2653ab51491b966db9f62c415c0d7161b4d642e7f89cefc30b687a61fc` 派生，原 v3 文件未改。新的 source diff 在 `source-diff.patch`。guard_scope、guard_repository、runtime_origins、save/create-only+fsync、export45 合同、guarded public-directory walk、current shard SHA1+SHA256/业务合同和 cleanup helper 来源校验均沿用；测试逐函数 AST 对照防止顺手改动。

## 固定范围

* attempt root：`/root/qtrade-local/operator-evidence/publication-cold-recovery-20261028T143000Z`，只能新建，已有任何 send/preflight 声明不复用。
* source publication、fba release、647839/start-ticks owner、remote URL SHA 及 SSH command SHA 均保留 v3 固定值，不改 owner/config。
* expected remote main：`5a0925f47e541726210a8879e2f2bed4ae2a62fd`。
* old attempt：`/root/qtrade-local/operator-evidence/publication-relay-cold-20261028T130000Z`。
* 旧 INTENT SHA：`e890b57749ef3cf849ec317acab2b83febb437968c948df0d2df8fcc4107429f`；旧 UNCERTAIN SHA：`1ad55b8098aba6f1532191373d818b9615b453fb30f94ae7bad77f9210a75859`。
* 旧 target `dde33b30a54d46beb0197a80ddbcb35afb5830da` / plan `f33c80814b18cc6e77150535b68b93e6525a1ccdadfaa0bd021707d4cc87416a` 被新 plan/INTENT 跨绑定。旧文件只读，绝不覆盖。

DIRECT SSH 使用原 command 的 exact `91e5fd67a08092e3ff795305eb4a0618a5755bf5a80a76f6c43f6a892c06125d` SHA 和 13-token 结构，严格只接收一个 `-i` 和一个明确的 UserKnownHostsFile，IdentitiesOnly/StrictHostKeyChecking 必须 yes。不存在、none、多路径或未知选项一律停止，无 proxy/fallback/multiplexing。私钥只检查文件元数据，不读取或计算其内容 hash。known_hosts 是公开 hostkey 文件，prepare 有界读取，plan 只保存 bytes/content SHA/path SHA，send 重核；不保存原 command、路径配置或 hostkey 正文。禁用全局 hostkey 文件，固定 SSH `-F /dev/null`。这是本地 SSH hostkey 配置冻结，不是外部身份签名。

## 模式与预算

| 模式 | 操作 | 预算/写入 |
| --- | --- | --- |
| init | 新 operator bare repo + 只读 source object alternates | 仅新 attempt root，无网络；普通 Git <=90s |
| prepare | 当前 HEAD、export 成功/双时钟 <=45s、3000 entries、5987 current refs 全部校验，expected main/旧 target ancestry | 总审计 <=90s；create-only target.json，明确 PENDING_NEW_EXPLICIT_USER_AUTHORIZATION |
| ls | DIRECT SSH 读取 main advertisement | 普通 Git <=90s；无公开写入 |
| send-cold | 新授权校验、再次 DIRECT advertisement、单次 nonforce push | 普通 LS <=90s；仅该 push <=180s，原 owned helper TERM2s + KILL/wait2s；不自动重试 |

180s 只围住实际 cold Git 子进程；审计/LS 仍按原 90s，export 仍必须 <=45s。没有修改生产 publisher 的 45/90。cold 路径用显式 Popen，cleanup 失败后不会触发 Popen context manager 的隐式无界 wait。内核不可终止状态、文件系统 fsync 阻塞不构成硬实时保证；遇 cleanup failure 必须停止并人工对账。

## 发送授权与一次性声明

prepare 生成 target.json 并给出**实际 prepared plan SHA**后，root 才能把冻结 target/预算/范围/限制提交给用户审阅。当前包里没有真实 target.json、USER_AUTHORIZATION.json 或发送 INTENT；测试工作目录中的同名文件全是显式合成 fixture，不能用于操作。

未来实际用户明确批准 exact prepared plan SHA 后，root 在固定新 attempt root 用独占 create-only 文件创建 USER_AUTHORIZATION.json，包含以下精确 keys（以下只是结构示意，placeholder 不能通过校验）：

```json
{
  "schema_version": "qtrade.one-shot-cold-user-authorization.v1",
  "plan_sha256": "<EXACT_PREPARED_PLAN_SHA256_FROM_USER_APPROVAL>",
  "authorization_reference": "<NEW_EXPLICIT_USER_MESSAGE_REFERENCE>",
  "authorized_at": "<ACTUAL_AWARE_UTC_TIME_AFTER_PLAN_CAPTURE>",
  "authorization_kind": "EXPLICIT_NEW_USER_APPROVAL_OF_EXACT_PREPARED_PLAN",
  "one_shot_cold_push_seconds": 180,
  "ordinary_budgets_unchanged": true,
  "automatic_retry": false,
  "force_push": false
}
```

send-cold 同时强制 `--expected-plan-sha256` 与 `--user-authorization-sha256`，record 必须绑定 exact plan、批准时间在 plan 之后且不晚于当前时间/不旧于1小时；ABC/旧批准种类、未知 keys、重复 JSON keys、NaN、错误 SHA/预算/时钟全部拒绝。该文件是可信本地 operator 根据新用户消息作的声明，不是密码签名或自动证明人类身份；root 不得提前生成。

实际发送前再次 rehash old claims、operator、plan、approval、known_hosts，核对 frozen index。PREFLIGHT.json 是 create-only；若 remote 已是 exact target，只确认不发；若既非 expected5a09 也非 exact target，停止，要求重新对账。然后新 create-only PUSH_INTENT.json 绑定上述所有谱系，独立 SEND_STARTED.json 阻止直接 helper 重放。超时、异常、中断、cleanup/telemetry失败均保留声明并标记 UNCERTAIN，无重试。即使 preflight 拒绝，也不循环复用该 attempt。

## CAS 的真实边界与测试

生产命令是普通 nonforce `git push --progress origin <target>:refs/heads/main`，hooks 禁用，没有 `--force-with-lease`、force、自定义 hook 或新协议。LS 和 send 之间没有跨事务锁，不能保证每个竞态都在发送前发现。

本地真实 native Git 三个测试已分别验证：

1. LS 后改成 divergent main，普通 push 拒绝并保留竞争方。
2. LS 后先合法推进到 target 的祖先，再普通 push 可继续 fast-forward；说明“LS 曾匹配”不能充当跨事务锁。
3. native receive-pack 真正发出 oldOID advertisement 后，本地控制器执行真实 concurrent update-ref，再将客户端/服务端字节原样继续转发，服务端拒绝旧 OID，竞争方 HEAD 未动，fsck 通过。Git2.53 Windows 给出 `incorrect old value provided`；兼容其他版本的 `cannot lock ref ... but expected`。这个 barrier 仅用于隔离 fixture，未生成或修改 Git packet、未装 hook、未进入 cold operator。

这些是本地 file transport/native server 的协议证据，不是 GitHub/TLS/SSH 实验，不证明公网的耗时或可达性。

## 安全进度观测与证据限制

cold stderr 仅经有界解析保存允许 phase（Enumerating/Counting/Compressing/Writing/remote Resolving）、数值计数/百分比和 monotonic 时间。raw stdout 丢弃；未知 stderr 只保留 bytes/SHA，所有 stderr 也只另存总 SHA/bytes。无 GIT_TRACE/TRACE_PACKET，最多4096条事件，pending <=4096 bytes、每次读取<=8192 bytes。

Writing100% 仅表示客户端写入进度，不能证明服务端全部收到或 refs 已提交；remote Resolving 也不是 ACK。只有 Git exit0 记为“ACKNOWLEDGED_RECONCILIATION_REQUIRED”，仍须 root 独立只读核对实际 remote target。首个可识别进度之前的时间包含多种阶段，不能仅凭 bytes 或无进度就认定网络失败。旧90秒尝试没有足够 phase logs，不能据此强认网络原因。

## 已测与尚未测

最终 Windows **61 passed / 0 skipped**，`windows-frozen-results.xml` SHA256 `58ec529e8c13c4bcbe3e00a39aad6215a03d33cb5cf63740192e87de87c6ec5a`。测试含原 v3 safety helpers AST、45/90 不变、exact approval与旧声明、未知 SSH/多路径拒绝、known host read bounds、oneshot/UNCERTAIN、受限 telemetry、180/cleanup-error 子进程 doubles、上述真实 native nonforce/CAS。最终 test SHA `93ee73337dee4260a8fdd14b2526d0989f162991623bc3446004735814cbaf5c`。

首轮57项有1个测试文案断言失败；第二轮60项有3个新增 doubles 夹具失败（OS proxy 未委托 open），原 test source/XML/raw native result 保留。修正夹具后60通过；最终 cold helper 的显式 close 改进后新增 cleanup-error 检查，61通过。原结果不外推最终源码。

**Linux 未运行**，因本轮明确不授权集群连接/部署；测试文件、barrier 与源均可在 Linux 隔离目录运行。POSIX 真正180秒 timeout/2+2进程组清理、实际 owner/config/hostkey 解析、全量 prepare 性能、DIRECT SSH/GitHub、180秒是否足够、最终用户发送批准均未认证。现阶段不能批准实际 send。

复现（当前目录；只运行测试）：

```text
python -B -m pytest -q -p no:cacheprovider --basetemp ./new-test-output --junitxml=./new-results.xml ./test_publication_cold_recovery_operator_20261028.py
```

待 persistent review 通过后，仅由 root 按已授权范围只读检查 owner/旧进程组无残留与 clocks、verify 源文件 SHA，stage 到固定新 root，再执行 init/prepare。owner/旧 PGID 无残留须 root 当前只读核对；脚本固定校验现有 publisher owner，并不声称通过扫描所有进程证明旧组不存在。真正 send-cold 命令应等新 prepared plan SHA 和用户明确批准后才给出。本文件未提供可直接执行的发送命令。
