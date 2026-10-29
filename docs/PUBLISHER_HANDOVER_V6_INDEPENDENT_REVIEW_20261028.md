# Publisher v6 独立审查（2026-10-28）

最终已审工具的非生产接线未留新增阻断。独立审查先证实并阻断了旧 v6 的写入前 shadow-origin 缺口，随后在新工具上复核修复。本文不授权生产操作，不代替真实发布链、C/D/E 或资本安全验收。

## 冻结范围

| 文件 | 最终 SHA-256 |
|---|---|
| `publisher_actual_evidence_v6_20261028.py` | `ef3665447333b16ff8b04f53e37562a15dae53902d21016db5de7e06adbff0a6` |
| `publisher_actual_composite_v6_20261028.py` | `bb732a46fb20ed3b148daabdeda685410f4285616732ecbd6d3b47fe1b2db7ba` |
| `publisher_handover_v6_preparation_20261028.py` | `0fd5100d029e6234039c765639b4ddb89094795918d6c7779ce199efe98f68f2` |
| `publisher_handover_bridge_v6_20261028.py` | `689af7db40b33f804283068c3f6bd7756fafcd65160af674a2937b3c66c06bbf` |
| `publisher_v6_control_call_20261028.py` | `9293cd719f398dcdd5c8603a89a6b4a9e77eb1598ae7766f1141544ce61f3e8c` |

原 operator `03e186c1…`、bridge `4cbd3017…`、旧反例和 XML 均保存在[独立证据包](evidence/publisher-handover-v6-independent-20261028/manifest.json)。没有覆盖 v5 review、故障映射或其失败原文。此次只变更交接工具；fba51aad 的 2194 文件、publisher `64caab00…`、receipt `2152eed8…` 性能源未因这次修复改变，未重跑性能测量。

## 已关闭的 P1

原 `check_source()` 仅逐清单校验 hash。独立 fixture 保留 tracked `scripts/unified_market_checkpoint.py` 字节不变，新增同名 package 的 `__init__.py`：原检查通过，原生 PathFinder 实际解析到额外 package。因此早先 origin artifact 和完整 Git 清单不能替代写入前的现场解析。

最终工具拒绝 release 中额外文件和 symlink，固定 venv、`-B`、cwd、PYTHONPATH，以 PathFinder-only 子进程重新解析七个受保护模块，并对照实际路径和源码 hash。fixture 模块正文一旦执行即抛错，clean 检查仍成功且不产生 pycache。inventory 扫描后注入 shadow 也会被现场 resolver 拒绝。

`install_wrapper()` 在临时候选文件 fsync 后、`os.replace` 前再次执行上述检查；`named_term_once()` 在最终 old-owner guard 前再次核验 source/config/controls。测试在这两个间隙注入 shadow，均拒绝 replace/RPC。独立原反例迁移到新工具后已转为期望拒绝并通过。

## 原始证据和控制接线

- 62-role 原始包在私有 temp 内存解析，没有复制 raw mount 或 private wrapper 正文进入本证据包。35 次原始 report/stdout 严格逐字段匹配，15 次固定 5640 范围与20次当前5701范围区分；原 oracle 绑定各轮 index 和完整输出统计。独立双改 report/stdout 的假 index 仍被原 oracle 拒绝；重复 JSON key 被拒绝。
- v6 入口没有 legacy 4200/cold/warm fallback。组合 gate 绑定 manifest 的同一个 canonical bundle，叶子 mutation guard 每次重新读取并验证；独立修改原始 report ref 后，guard 在文件访问前拒绝 bundle 变化。
- origin 实际时间为 11:27:29Z，foundation 为 11:39:24Z，包冻结 11:42:21Z，早于本轮独立复核。foundation 原件 SHA `a3efba4c…` 与 bundle 一致；`checked_at` 是证据元数据，没有人为 TTL。实际变更仍须现场重复核验 owner/start_ticks/native RUNNING/config/binary。
- exact 623-byte 旧 wrapper 信任 hash 与仅 release 路径、固定 state flag/root 尾追加合同保持。三个 wrapper 函数、journal 与 `advance` 的 AST 和已审 v5 相同。没有新增求值或可调 flag。
- 同 attempt 的 durable `TERM_INTENT` 先于 RPC；不确定结果不重放、不重定向。自然退出发生在 TERM 前会拒绝并保持零信号。bridge 在任何 I/O 前消费唯一 TERM；独立 socket identity 失败后也不能再次消费。named RPC 仍有已披露的非原子 check-to-call race，不能声称 PID CAS。
- 最终 caller 固定 attempt 和工具/manifest/gates/foundation/旧242文件 inventory hash；向 adapter 传入相同 gates。prepare 无 journal/变更权限；observe 只接受已有 TERM_INTENT，且不调用 advance、不创建 journal、不安装、不发信号。安全检查使用显式 require 并拒绝 optimize，兼容 `timezone.utc`。对三种 mode 的 `-O` 本地运行均在加载任何远端路径前拒绝。

## 实际验证与边界

本审查在 Windows 隔离目录独立重现原47项、修复后52项，均零失败零跳过。另有原9项边界、新9项边界和caller优化解释器3项；它们分别保留 XML，不作为额外真实生产场景计数。原9项中的 shadow case 通过表示确认旧缺口，不能解释为旧工具安全通过。新9项验证相同缺口已拒绝。现场文件/信号操作仅在本地 fixture 上模拟；本审查没有执行实际 caller、SSH 控制或生产写入。

组合故障映射实际为14 DIRECT_FIXTURE、9 PARTIAL、2 TOOLS_MOCK；额外同 NFS 的16秒人为延迟 probe 单独解析。它证明实际 dev66 上45秒 postguard 拒绝、旧 head 保留及同预算恢复，不证明真实 NFS outage、硬中断阻塞 I/O、断电或分布式 CAS。既有[故障补充审查](PUBLISHER_FAULT_COVERAGE_SUPPLEMENT_20261028.md)继续保留更细范围。

冷启动/DB-loss 的120秒只用于诊断，生产预算仍45秒；不存在自动预热授权。第一轮真实生产 cold 失败必须保留。逐轮 UTC、child start_ticks、初始 sidecar 和 cache peak 未捕获，当前5701未重复冷诊断；这些未知量不能补写成真实 PASS。即使工具返回 `GATES_PASS_PREPARATION_ONLY`，仍需真实 owner 自然退出、唯一自动重启、子进程清理、发布→Git→public API 的独立证据。

可在具备原私有62-role包的本机重放下列命令。仅输出新的 temp XML，禁止把 raw 包复制到 Git：

```powershell
$env:ACTUAL_V6_PRIVATE_PACKAGE='E:/QTrade-temp/publisher-v6-actual-bundle-20261028T114221Z-0eec8c94'
python -B -m pytest E:/QTrade-git/docs/evidence/publisher-handover-v6-independent-20261028/fixed-origin-review -q -p no:cacheprovider --junitxml=E:/QTrade-temp/v6-independent-reproduction-new.xml
```

结果和逐文件 hash 见[review-summary.json](evidence/publisher-handover-v6-independent-20261028/review-summary.json)及[manifest.json](evidence/publisher-handover-v6-independent-20261028/manifest.json)。原始报告内容的真实性依赖已复核的测量源和冻结包；外层 hash 不是任意重新签名内容的独立事实证明。
