# 原生 Git 单次 loopback relay 独立审查

2026-10-28 13:18 UTC / 21:18 上海时间。结论范围为 **静态审查及离线负例通过，真实 relay 与公网交付尚未由本审查验收**。本审查没有调用 credential helper、SSH、真实 socket、服务控制或公网 push，没有修改生产 publication repository、owner、配置、账本或 ACTIVE。

## 最终冻结代码与结果

| 文件 | SHA256 |
| --- | --- |
| publication_loopback_proxy_20261028.py | f4146011c52405de87570e95e9b2cf084bb2896072453e01fe175d2c9a11aac2 |
| publication_native_relay_operator_v2_20261028.py | 91bfd970f1def55b01a4935907578206a7d91ece39ae857f3b451a99037c84c1 |
| run_publication_desktop_relay_v2_20261028.py | 303c1cc054fe7dd97c6936f69b9f95ba5efd28557c2fd1669d74848c48b5ab08 |

精确最终文件重新运行 **71 passed / 0 failed / 0 skipped**，见 [最终 XML](evidence/publication-native-relay-independent-20261028/relay-final-exact-independent-counterexamples-v4.xml) 和 [逐文件 manifest](evidence/publication-native-relay-independent-20261028/manifest.json)。其中 8 个测试记录 v1 真实存在的接收缺口；这些用例通过表示反例被复现，不表示 v1 安全通过。其余用例验证环境、v2 守卫、SFTP 与 relay attestation 拒绝路径。

审查没有读 private wrapper、SSH command 的实际私有正文或任何密钥。代码内固定的已有 command/URL 哈希被作为输入边界检查，测试使用合成公开字符串。

## v1 缺口与 v2 修复

1. v1 的 SHA→单个 `(ref, entry)` 字典让后出现的 entry 覆盖前一个。同一真实可验证 shard 被错误 entry 引用后，原始 `validate_index` 仍可通过；原始 `verify_shard_bytes` 对前一个 entry 明确返回 `SHARD_INDEX_MISMATCH`，但 v1 `prepare` 仍生成 `all_frozen_git_shards_verified=true`。v2 为每个 SHA 保存所有 occurrence，逐项验证；相同反例在生成 target 前被拒绝。
2. v1 的 attempt 只检查 SUCCESS、index id 和 `elapsed<=45`，实测接收 unsigned、坏 checksum、负数、bool、错误 schema、未来/无序/naive 时钟及非空错误。v2 复用原始 `document`，核 schema/authority/checksum、有限非 bool 的 0..45 秒、错误为空、index binding，以及 aware、有序、非未来且覆盖 index published 时刻的时钟。语义反例重新签名后仍被拒绝，避免仅由 checksum 失败遮盖语义检查。这里 `signed` 是内容 checksum，不是外部身份签名。
3. v1 只在 init 核 source raw origin，未在网络动作前检查 isolated repository 实际去向。v2 固定唯一 approved attempt，检查 bare/common-dir/真实路径、单 link regular config/alternates，禁止 commondir、额外 alternates/http-alternates（包含 dangling redirect）；fetch/push effective URL 各须唯一且匹配固定哈希。`ls-remote` / `push` 叶子在创建进程前再次检查。
4. v1 从脚本所在 ROOT 启动，动态 import 可被 ROOT 的 `scripts` / `qtrade` shadow。v2 使用 `-P` / `PYTHONSAFEPATH=1`，并在导入前用纯 `PathFinder` 检查两个动态模块的真实 release origin。测试中模块正文均故意抛异常，确认解析没有执行模块；错误 ROOT origin 被拒绝。
5. desktop v1 跟随 SFTP symlink，上传发生在 remote 路径守卫前。v2 逐祖先 `lstat`，文件须 regular/non-symlink；缺失时用 Paramiko `wx` 独占创建，再核 size/hash；既有内容改变时不覆写。直接检查本机 Paramiko 实现，`wx` 确实包含 WRITE/CREATE/EXCL 标志。
6. desktop v1 将原生 Git 返回值与真实 relay 混淆。已读取并封存其只读 ls 原始文件：结果返回 `READONLY_NATIVE_RELAY_PASS`、main `5a0925f47e541726210a8879e2f2bed4ae2a62fd`，但实际 accepted_channels=0，双向 bytes 均为 0，因此不能证明这次走过 relay。v2 移除与 ProxyCommand 竞争的 `ProxyJump=none`，并要求唯一 channel、无 rejected channel、双向非零字节、无 pump error 且所有 pump 已退出；9 类合成假成功均被拒绝并执行清理。

## 接线边界

- desktop 仅连接固定 `ssh.github.com:443`；remote listener 仅绑定 `127.0.0.1` 的 ephemeral port，并读取 `/proc/net/tcp{,6}` 核监听。callback 同时核 origin/server loopback、server port 与单次接收数。
- cluster SSH 私钥继续留在 cluster；desktop 只转发 SSH 加密字节。代理 stdout 只承载流，诊断不打印 payload；安全记录仅计数/错误类型/公开结果和哈希。
- 新 bare repository 仅通过严格 scope 的只读 alternates 借用已存在 source objects；原生 Git 的工作目录/refs/config 写入限新隔离目录。source Git 调用仅有 config 查询、rev-parse、show、cat-file；没有 source refs/index/worktree/config 写命令。
- SSH 强制 `-F /dev/null`、BatchMode、StrictHostKeyChecking、禁 local command/forwarding/control multiplexing，沿用固定哈希的已有身份配置；不修改磁盘 SSH 配置或服务环境。
- push 固定已冻结 commit 和 expected target-plan SHA，原生 non-force `main`；`PUSH_INTENT.json` 在联网写入前以 `xb` 创建、flush、file fsync、directory fsync，已存在即拒绝。异常保存 UNCERTAIN，没有 retry loop；即使最终 attestation 失败，也不产生再次 push 许可。
- Git 子进程维持原始 90 秒 communicate timeout 与已审计 owned-group cleanup；export gate 为实际 45 秒 SUCCESS receipt。proxy 95 秒和 desktop lease 100 秒是传输清理上限，不改变生产 Git 的 90 秒预算，也不构成硬中断 NFS/I/O 的证明。

## 测试、保留证据与限制

| 用例组 | 数量 | 性质 |
| --- | ---: | --- |
| v1 shard occurrence 与坏 attempt 接收 | 8 | 未 mock 原始 index/shard validators 的反例 |
| clean environment | 1 | 合成 inherited Git override 不传播 |
| desktop SFTP / Paramiko flags / attestation | 18 | 本地 mock 与真实 Paramiko mode 实现 |
| 最终 v2 operator | 44 | 实际新临时文件/hardlink + 合成 Git batch、origin/redirect/intent syscall mock |

输入 market fixture 有 3000 entries；只用公开合成 bar，不读取 raw mount。mock Git batch 验证 framing/size/blob/bytes/checksum/entry；未连接任何 Git remote。PathFinder/网络叶子守卫各自独立测试，fixture 在 `prepare` 单元中替换这两个不适用于 Windows 临时路径的前置守卫，不把它们的替换当实际运行证据。

首次新增 desktop 主流程夹具使用 BytesIO，而 Paramiko stdin 接收 str，导致 9 条 TypeError 夹具失败；[失败 XML](evidence/publication-native-relay-independent-20261028/relay-original-and-desktop-draft-counterexamples-v2.xml) 与失败源码原样保留。修正夹具行为后 62 条通过；最终 operator 的 lexists/attempt 文件上限加强后增加 9 条，重新跑精确最终源得到 71 条通过。a2cf operator、e14afe draft runner 及旧 v1 均保留独立 snapshot，未将旧 XML 冒充最终 hash 证明。

本地复现（只运行以下两个文件，旧失败 snapshot 不参与最终套件）：

```powershell
python -B -P -m pytest -q E:/QTrade-git/docs/evidence/publication-native-relay-independent-20261028/test_relay_independent_20261028.py E:/QTrade-git/docs/evidence/publication-native-relay-independent-20261028/test_relay_v2_operator_independent_20261028.py --junitxml=E:/QTrade-temp/relay-independent-rerun.xml
```

测试实际导入的本地 `unified_market.py` SHA 为 `64982440d5c876a58c282fb7c8ab4923d03376864c489e8224565a9aff760bb6`，`data_freshness.py` 为 `d4d53a0296181a882da419ba867572651bf2ca121f5ab869c6219772b72ff8cf`，calendar 为 `c9de9c436286a67d231a01a2c0ee9927f196b93cff326bd1bea851e2e91a24d9`。没有改这些文件。

后续真实只读 route 需另保留新的完整 attestation；本地测试不能替代它。一次 operator-assisted cold delivery 即使成功，也仅证明该次交付；恢复还要求普通 publisher 在原 90 秒预算自然成功及真实 public probe。当前 C/D/E 的历史失败、固定模型 scope 与公网未更新状态不因本审查而变化。
