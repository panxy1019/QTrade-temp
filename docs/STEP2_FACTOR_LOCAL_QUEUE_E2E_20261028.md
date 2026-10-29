# S2-D 实际 ASGI / 本地 GitQueue / Worker 隔离闭环

2026-10-28。PASS_ISOLATED_ASGI_LOCAL_GIT_OWNED_WORKER。使用 exact Git
45de5bd2455b9ed6bdaddc1646d69e148846e3da 的完整 3,001 文件，零 overlay；
原项目源码没有改动。此结果只认证非生产研究登记，没有认证 GitHub SSH、
TLS、公网发布、浏览器或 STEP2 的因子执行/组合回测。

## 隔离范围与真实输入

local operator root：
/root/qtrade-local/operator-evidence/factor-api-queue-e2e-20261028T132322Z-3bcecd4a

独立 NFS base：
/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/step2-factor-api-queue-3bcecd4a

NFS base 位于生产 qtrade 目录之外。完整历史 READY 09a67b13… 镜像为
18,829 文件、786,448,967 bytes，逐文件 SHA256 与原档相同；没有 symlink/
hardlink，也没有伪造磁盘空间。workflow/output 在该 NFS base；workflow、
StrategyRegistry、空模型 DB、本地 bare queue 均为独立新目录/数据库。
data catalog 来自 mode=ro 一致性 SQLite backup，所选 READY row/event 与
生产只读原件逐项相同，没有改 head 或重建准入时间。

镜像前后 NFS free 为 282,324,893,696 / 281,481,838,592 bytes，local free 为
18,242,129,920 / 18,065,768,448 bytes。Worker 对实际 base 的 100 GiB 检查
真实执行，local 始终高于 2 GiB。没有把 local 约 18 GB 伪装成 100 GiB。

批准 factor archive 是上一轮 S2-D 的真实四因子 archive 逐字节复制：原
FactorRegistry 精确 refs、S2-B 891 股/16 日报告与 READY binding 不变。此轮
不重算 IC、不选择新因子、不替换原报告，仍是已看历史/未认证 PIT/毛标签诊断。
当前新 head b82/ACTIVE68 的背景事实不被改回旧状态；09a67 是显式历史研究输入。

## 实际运行的边界

临时 harness 只将 GitQueue 的 _client 传输改为官方 Dulwich LocalGitClient，
把固定 remote 字符串映射至自己的 bare Git 路径；checkout/append/read、
canonical/HMAC、immutable ID、Git ref CAS 使用原项目实现。SSH vendor 被显式
禁止。它是真实 Git 对象/pack/ref 操作，不能冒充 GitHub SSH host-key 验收。

HTTP 经过 FastAPI TestClient/ASGI 的真实 router、登录、origin、cookie、body
上限、preview 和 submit。PUBLIC_ROOT 配置为 127.0.0.1 的临时只读文件 server；
read_public 原实现仍执行有界读取、签名与 authority 检查。API 主进程观测到的
19 次 socket connect 都只到该 loopback 地址；这不是 TLS 或真实浏览器验证。

真实 Worker.once() 运行：HMAC ingest、原容量门槛、真实 NFS 空间检查、原
subprocess 命令和 owned child、Registry 事务、workflow 发布、完成 GET。没有
patch Worker.execute/publish、Registry、磁盘空间、因子/报告 validator 或指标。
独立模型 DB 没有 model/ACTIVE，rolling 为 OFF，没有模型激活或控制操作。

## 请求、回执与负例

14 个 HTTP 交互的状态序列：

    catalog200, anonymous-preview401, wrong-origin-login403, login200,
    preview200, changed-catalog409, illegal-field422, oversized413,
    submit202, identical-retry202, alternative-preview200,
    same-id-conflict409, completion-GET200, old-catalog-after-completion409

同 payload 重试没有新 Git commit；同 ID 不同 spec 被原 GitQueue 拒绝。
队列另注入两个负例：坏 HMAC 和已签名未知 schema；真实 worker 没有执行它们。
最终 workflow DB 只有 1 个合法 COMPLETE，Registry spec/contract/job/transition
各 1，无 lineage，无重复 transition。

- job：1cece3d0695616f629f540391fde7479
- strategy：03639b67de9d3644ecb03d950a63a1cef4eafee8ce15fca506af436e8ca6058a
- contract：9242b344c16bca68c78bd894ec41e3565e26d20117d73f607465320fc11e89e8
- receipt：77aa8fac6ebf764d52d204b8b1663c88cc1f7664d45a3311c6f81a3449ae82fa
- registered_at：2026-10-28T13:29:27.657130+00:00
- final local queue head：e9e04df7e2390474c8dd4336702466074cb62e2d

状态严格 EXPERIMENT/NONE，registration_only=true；
model_activation/paper_admission/backtest_implemented 全 false。
end-to-end span 130.177 秒，外层 child span 131.962 秒，peak RSS 1,291,940 KiB。
该时长不含前置镜像与之后原件闭合，不是发布器/serving SLA。

JUnit：6 个真实 E2E assertion groups 全通过；Linux 独立 local-Git 协议
7 项全通过，包括真实并发 ref 更新的拒绝/显式 retry、HMAC、不可变 job 和路径。
另一个 receiver 负例在 ref advertisement 之后插入 competitor commit，
原 append 得到 QUEUE_PUSH_NOT_ACCEPTED，competitor ref 保留、pending request
未发布。此时可能留下不可达 Git objects，不能把“ref 未前移”说成对象库零写入。
这些协议负例 payload 明确为合成 fixture，不是额外真实因子登记。

临时 harness 首次把 JUnit failures counter 输出成字符串 False；第一次 seal
因此中止。原 XML/SHA 保留，另提供 normalized XML：仅将该 counter 转为按
实际 failure 元素计数的数值 0，testcase 不变。转换 provenance 保留，未重跑
或重分类实际登记/测试。后续解析器应使用 normalized XML。

## 原件、HTTP 安全与离线复核

完整 Git source、生产 READY 原 18,829 文件、原 891 实验 279 文件、S2-B 19
文件、批准 factor archive、所选准入 row/event 前后相同。只读 ACTIVE snapshot
68d9287e… 前后相同；这不是重新认证所有 forward books。闭合另重验镜像本身
及完整 source，避免只证明原档未变而漏掉镜像改变。

独立离线复核重新读取四个 gzip SQLite snapshots 和 local Git bundle，确认
主 queue 有 3 个 request 文件但 workflow 仅执行合法的 1 个；unsealed request、
factor_spec hash、stored contract、公开 receipt/detail 完全相符。模型/ACTIVE
行数均 0、rolling OFF。签名密钥只在 live harness 内存中生成，没有导出；
离线复核不声称可在没有 HMAC secret 时独立重算其 MAC。

HTTP request/response JSON 记录是原执行的语义轨迹，不是网络 packet capture。
登录密码在 harness 中从未写入记录，cookie/session 值没有记录；Git 中进一步
只保存认证字段的安全投影及 private trace hash/大小/状态。临时认证材料也不
写进 Git。公开 queue HMAC、artifact checksums 可保留，不含 key。

追加只读 ASGI GET 回放没有认证提交或 queue 访问：完成 body 原 bytes 为
2,193 bytes，SHA dac557ab571643a852c94c15f43048f5ccd99cf028bba12ac2408169491b0ecc；
catalog body 120,443 bytes，SHA
5a500456daffbed6cf80f50116851477e720683999481d6bf98498e54e204927。
GET 完成 artifact 与原 signed receipt 相同，headers 为 no-store/application-json。

## 复现与剩余门槛

冻结 source.tar SHA：
3c3d924e5e9b1fbabbfbda95f6346f462480a4b994d001bf0307928ee8e79829。
临时实际 runner SHA：
26bf13cf82866b19cbad714cf2d2fa73188757b5e33af03e87b035d6cd5deaf9。
命令、source manifest、原始 XML、HTTP 安全投影/只读原 bytes、DB/bundle、
source/mirror/original inventories 和独立复核均在
docs/evidence/step2-factor-local-queue-e2e-20261028/manifest.json。

单独本地协议负例，不需要生产凭据：

    python -B factor_api_queue_e2e_20261028.py --mode negative \
      --source <exact45-source> --owned <new-empty-owned-directory>

完整闭环由 dispatch_factor_api_queue_e2e_20261028.py 创建全新 root，先从 exact
Git archive 构建零 overlay source、逐字节 staging READY、RO backup，再执行：

    python -P -B factor_api_queue_e2e_20261028.py --mode real-e2e \
      --source <verified-source> --owned <new-local-operator-root> \
      --revision 45de5bd2455b9ed6bdaddc1646d69e148846e3da \
      --base <new-NFS-base> --database <local-root>/db/workflow.sqlite \
      --archive <copied-approved-archive> --source-manifest <source-manifest.json>

production queue credentials 不用于这些命令，认证材料随机且仅驻内存。
重现必须创建新 base/root/job，不能覆盖本轮 source/output/DB。
后续独立门槛仍是批准的 GitHub SSH/真实私有队列往返、实际 API/transport 部署、
TLS/浏览器及因子 signal/portfolio/backtest 能力；本轮没有启用生产 factor archive，
没有更改部署或把局部登记能力宣称为全部 STEP2 完成。
