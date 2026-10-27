# 午后新增行情与统一发布读取验收（2026-10-27）

## 当前结论与证据边界

午后新增行情已经进入公开仓库，网页单只股票 API 的实际行内容也已独立核对。完整生产链路的新鲜度验收仍需最终读取修复上线后的新凭证；不能以午休的不变数据、普通 Git 推送成功或单只样本的内容正确代替。

| 实际检查时间（上海） | 已核实结果 | 凭证 |
| --- | --- | --- |
| 12:53 | 旧生产 `4cee136`，目录与索引均新鲜；午休不变数据 | [完整抽查](evidence/qlib-publication-live-probe-4cee136-20261027.json) |
| 13:06 | 当前应到 13:05，队列当时尚未完成该网格，公开索引仍为 11:30 | [首次午后观察](evidence/qlib-afternoon-runtime-before-new-bars-20261027.json) |
| 13:11 | 公开仓库已有 1,496 只证券到 13:05，恰好 1,496 组分钟序列相对午休基线改变；当时下一网格已到 13:10 | [真实远端与队列观察](evidence/qlib-afternoon-runtime-1311-20261027.json) |
| 13:20 | 生产 `a53aa03` 单只 API 的 74 行均与公开不可变对象相符，数据到 13:10，应到 13:15；索引为 `STALE_PUBLICATION` | [单只内容核对](evidence/qlib-publication-afternoon-one-series-a53aa03-20261027.json) |
| 13:23 | 六个午后远端提交有六个不同的实际行情索引，最近导出 35.16 秒；没有据此认定所有公网边缘新鲜 | [导出、提交与发布时钟](evidence/qlib-afternoon-publication-latency-20261027.json) |
| 13:30 | 固定公开 Git 版本后，独立读取的目录/索引年龄为 109.13/117.40 秒；1,496 只分钟任务达到应到的 13:25 | [固定版本真实 HTTP 读取](evidence/qlib-publication-immutable-ref-transport-20261027.json) |

后三种证据的范围不同：远端元数据、单只生产接口内容、固定版本公网传输分别验收。尚未将它们组合成未经实际执行的完整生产通过记录。所有凭证保留真实时钟、源码/公开版本、哈希和权限边界，不改写早先失败或过期记录。

## 需要修复的读取关系

旧读取分别访问 GitHub raw 的可变 `main` 路径，再单独请求 Vercel summary。即使有每分钟查询参数和进程缓存，不同边缘仍可能返回不同的有效目录。API 响应的 `no-store` 不等于下游来源共享同一版本。13:06、13:07、13:09 三轮完整抽查因此拒绝 `CATALOG_DRIFT_RETRY_REQUIRED`，没有生成通过凭证。

另一个独立问题是行情索引的实际发布时间：外层发布 `SUCCEEDED` 可以伴随保留上次成功索引，故必须同时查看 `market-export-state.json`、索引 ID、Git 提交时间和发布状态文件修改时间。本轮随后捕获的六个实际远端帧确有新的索引，不能把早先过期结果直接归因为导出失败、推送超时或数据损坏。

最终读取修复复用原公开仓库：

1. 仅通过固定 HTTPS Git 引用发现请求读取公开 `HEAD` 与 `main`，要求两者为同一合法提交；按实际 UTC 分钟缓存，失败不回退到旧版本。
2. 用这个不可变提交读取 `unified.json` 和 `unified-market.json`。目录与其真实原字节的 SHA256、Git blob 标识及长度一起缓存，`summary.catalog_source` 返回这组来源身份。
3. 完整抽查以 summary 的版本独立下载目录和索引，对五个 API 样本同时传 `publication_revision` 与原有 `version`，逐行核对不可变公开对象。保留实际时钟、全目标身份、签名、字节界限、180 秒索引与 1,200 秒目录门限。

这是公开读取，不上传 Git pack、不更新远端引用，不访问研究队列凭据。固定路径只接受严格的提交/内容哈希。固定来源、大小、时限、重定向拒绝和失败不兜底是实现要求。[Git HTTP 官方协议](https://git-scm.com/docs/http-protocol#_discovering_references)规定这种引用发现使用 GET。本项目的实际响应只有 444 字节，且声明不缓存、强制重新验证。

源审计曾验证公共 Git blob REST 方案，但现场遇到匿名配额限制。最终方案使用 Git 引用 GET 和按提交固定的 raw 文件，不依赖该 REST 配额，也不需要额外服务、密钥或存储。

## 测试与生产验证

最终源 `f0e0598` 在真实 Linux 上 [354 项通过、无跳过](evidence/qlib-fixed-ref-linux-f0e0598.xml)，覆盖固定来源、引用协议、API、行情、完整抽查和已有交付检查；独立复核未发现剩余阻塞。13:47 的[本地 ASGI 集成](evidence/qlib-fixed-ref-local-integration-f0e0598-20261027.json)实际访问公开 HTTP，确认 summary 与固定版本 bars 可用。这是本地 API 加真实来源证据，不是 Vercel 生产验收。

现场 8 MB 目录在新读取器的八秒完整下载预算内偶尔无法读完。原下载器的八秒只限制 socket 无进展时间，故最终实现区分两者：socket 始终八秒，引用/索引/碎片完整响应仍八秒，只有固定 `unified.json` 完整响应允许十五秒；每次读取后及返回前仍检查总时限。冷 summary 成功路径的网络预算为 8 + 15 秒，30 秒函数剩约七秒用于处理；不声称阻塞 socket 能在同一时刻被硬取消。该修复有九秒目录成功、十六秒 EOF 拒绝及其他资源九秒拒绝的回归。导出 45 秒、Git 90 秒、抽查 180 秒和原新鲜度门限均未改变。

复现最终测试：

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
python -m pytest tests/test_unified_source.py tests/test_unified_catalog_api.py tests/test_unified_market.py tests/test_probe_unified_publication.py tests/test_qlib_public_delivery.py -q -o addopts=''
python -m ruff check .
```

早先 `294c81f` 的 205 项和 `2bd4795` 的 349 项 Linux 结果分别保留，不能取代最终修复后的测试。当前生产仍以独立部署检查和完整抽查凭证为准。

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
$env:LIVE_TRADING_ENABLED='false'
python -m scripts.probe_unified_publication --expected-production-revision REVIEWED_MAIN_40_HEX --report NEW_REPORT.json
```

将生产版本替换成真实已部署的 main 身份，报告路径必须不存在。网络、版本、源字节或完整性异常不能生成通过凭证；有效但过期的观察必须保留过期状态。固定版本只能证明读取一致性，新鲜度仍按实际时钟重算。

## 仍保留的门禁

1,500 是统一采集目标池，正式 Qlib 研究版本仍是另一组已封存的资格范围。日线缺口、三只无已准入数据证券和 `512580` 的分钟缺口不推断为停牌，也不补造行情。多年历史、复权和完整历史 PIT 仍未认证。

13:50 的[后台状态快照](evidence/qlib-afternoon-services-20261027.json)确认所有受监管服务运行、原 PAPER `175982` 与发布 `240024` 的 PID 不变；1,496 只分钟目标达到应到 13:45。历史队列已有 40,692 合格行（日线 948、五分钟 39,744），盘中按既有策略让行；这不是全湖历史总行数或多年完整覆盖。PIT 13:38 刷新了 990/990 只、2,168,788 状态行，历史可用时点与完整 PIT 仍为 false。磁盘剩余 276.80 GiB，继续保留 100 GiB 下限。该快照未捕获独立退市队列路径，因此不能用其缺项推断退市服务停用。

本轮不改变发布 writer、已恢复的 PAPER 进程、ACTIVE LightGBM 或滚动 OFF 政策，不追加 PAPER 恢复授权。只有实际完整交易日后的版本、预测、候选独立验证、真实持仓/阻断/估值及生产发布核对全部通过，才可更新 C/D/E 总验收。STEP2 保持在该门禁之后。
