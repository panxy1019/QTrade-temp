# QTrade 后续研究、实验持久化与发布操作

## 1. 三类改动分别如何生效

| 操作 | 应改哪里 | 生效方式 |
| --- | --- | --- |
| 调整已有因子的周期、Top K、成本、调仓周期 | `configs/experiments/*.json` 的新版本 | 在集群显式运行正式实验；结果经校验后进入只读目录 |
| 新增数学因子 | `src/qtrade/research/` 的实现、输入契约和必要接线 | 测试、提交、部署研究代码，然后显式运行实验 |
| 制定新组合/入退场策略 | 组合模型/受限策略实现及冻结规格 | 使用已有回测、成本和时钟规则验证；不直接接 gateway |
| 修改网页或只读 API | `web/src/`、`src/qtrade/api/` | 合并并推送 `main` 后由 Vercel 构建发布 |
| 更新最新数据 | 集群采集器 | 快照定时发布，网站读取；不需要反复重建 Vercel |

<https://vercel.com/richman3/q-trade-temp> 是 Vercel 管理页面；
<https://q-trade-temp.vercel.app/> 才是访问网站的地址。

## 2. 先复用已有能力

只改窗口和权重时优先新建配置，不复制整个策略类。
目前 `PriceFactorSpec` 明确支持 momentum、reversal、low_volatility；
`MomentumAllocation` 和 `RankedFactorAllocation` 已有仓位、调仓和过滤逻辑。
日内方向规则也有受限参数接口。新的配置必须使用新的 experiment ID，不能覆盖旧结果。

网页“保存到本地实验”使用浏览器 localStorage。清空浏览器或换设备可能丢失。
导出 JSON 用于留档和复核，不等于已经在集群运行并保存了正式实验。
网页生成的日线 run-spec 请求可作为配置输入，但必须由下面的集群命令重新校验并运行。

## 3. 新增一个因子

先写下定义，再写代码：

1. 因子 ID/版本、公式、方向、缺失值与极值处理；
2. 必需数据、复权/分红口径、股票池和可用性条件；
3. 因子何时可知、最早何时执行、标签用哪段未来收益；
4. 研究假设、固定候选预算、训练/验证/测试划分；
5. 基准、手续费、滑点、容量与拒绝条件。

价格因子的接线通常涉及：

- `src/qtrade/research/factors.py`：公式与输入检查；
- `src/qtrade/research/multi_asset.py`：已有组合模型能否复用；
- 因子搜索脚本：显式加入候选网格，记录所有尝试；
- `src/qtrade/research/preview.py` 和 API 请求模型：若希望网页调整，需更新受支持分支；
- `web/src/types/index.ts`、选择器与证据目录：添加对应显示与校验；
- 相关测试：时钟、缺失输入、因子方向、因果执行与已知反例。

**仅在仓库放一个 Python 文件不会被自动执行或自动加入网页。**
`factorEvidenceCatalog.ts` 是证据导航，不是任意 Python 插件加载器。
只有新数学行为才需要新增实现；同一因子的参数变化优先保留在配置。

公告因子另需先建事件数据契约，尤其是 `published_at`、`received_at`、`revision_at`、
`known_at`、原文 hash 和模型/prompt 版本。没有这些时钟，不能把文本输出用于可信历史回测。

## 4. 正式实验命令

下列 `/PERSIST/qtrade` 是持久卷位置占位符。当前实际部署路径记录在集群的 Supervisor
配置中。`/root/qtrade-local` 是本容器本地目录，供活动 SQLite 使用，不能换成 NFS。
操作不需要交易账户，不要配置真实券商凭据。

```bash
export LIVE_TRADING_ENABLED=false
cd /PERSIST/qtrade/releases/REVIEWED_COMMIT
PY=/PERSIST/qtrade/venv/bin/python
```

先生成合并后的新日线版本，不覆盖旧数据：

```bash
$PY -m scripts.persist_research_experiment prepare \
  --observer-root /PERSIST/qtrade/runtime \
  --dataset-root /PERSIST/qtrade/research/datasets
```

命令会输出真实的 `manifest.json` 路径。使用该路径运行一个新的配置：

```bash
$PY -m scripts.persist_research_experiment run \
  --request configs/experiments/continuation-momentum-v1.json \
  --manifest /PERSIST/qtrade/research/datasets/DATA_VERSION/manifest.json \
  --output-root /PERSIST/qtrade/research/experiments \
  --catalog /root/qtrade-local/research-metadata.sqlite \
  --git-commit REVIEWED_40_CHARACTER_SOURCE_COMMIT
```

`--git-commit` 必须是实际运行的完整代码 SHA，不能随意填写。Git checkout 可用
`git rev-parse HEAD` 核对；归档部署用经过校验的发布记录及 `SOURCE_REVISION` 核对。
当前命令支持三个已有网页因果模板的日线请求结构；不是通用任意 Python 实验执行器，
也不会自动把浏览器中的日内预览转换成正式日线实验。

本轮示例固定为训练 2022—2024、验证 2025、延续诊断 2026-08-24—10-23。
较早数据用于滚动预热和按时间推进的组合状态；诊断指标只统计声明窗口。
它是事后诊断，不是未见测试。下一轮应在查看结果前预注册新的、互不重叠的观察窗口。

## 5. 保存的文件与状态

每个成功实验的独立目录包含：

```text
EXPERIMENT_ID/
  run-spec.json                 # 完整冻结规格及 spec hash
  dataset-manifest.json         # 确切数据版本与 checksum
  EXPERIMENT_ID.json            # 不可变原始实验 record
  result.json                  # 运行结果与 result hash
  artifacts/                   # equity/drawdown/trades/positions/risk/logs
  benchmark/                   # 同窗口、同成本的等权基准产物
  benchmark-metrics.json
  publication.json             # 不含内部路径的只读摘要
  metadata-backup.sqlite       # 从本地数据库生成的离线备份
  archive-manifest.json        # 完成标记及全部归档文件 checksum
```

同 ID 同规格重跑会校验已有归档并返回原结果；同 ID 改参数、数据身份或代码提交会拒绝。
需要改变时使用新版本 ID。未完成或损坏的归档不会被当成成功结果发布。
失败留下的未完成目录需要诊断后显式处理，不能删除失败记录并把同一次尝试当成未发生。

旧 v4 的 11 个实验用 `archive-legacy` 命令按原身份保留，标记为
`VERIFIED_LEGACY_ARCHIVE`。新运行标记为 `FORMAL_IMMUTABLE_RUN`。
“正式”表示规格和产物持久化，**不表示策略已经有效、注册或获准实盘**。

## 6. 容器重建后的研究目录恢复

活动 SQLite 目录位于容器本地，可能随容器重建丢失；持久卷中的实验目录是恢复依据。
校验不可变归档并重建新正式实验的本地目录：

```bash
$PY -m scripts.persist_research_experiment rebuild-catalog \
  --output-root /PERSIST/qtrade/research/experiments \
  --catalog /root/qtrade-local/research-metadata.sqlite
```

原 v4 档案缺少可独立重建的完整规格，因此保持历史归档身份，不伪造新的正式运行谱系。
恢复研究目录不会自动恢复策略运行、解除 HALT 或发出订单。
持久卷还需要独立备份；当前快照仓库只保存摘要，不能恢复完整 Parquet 和研究产物。

## 7. 只读发布与排障

两个独立 Supervisor 程序分别负责采集和发布：

```bash
supervisorctl status qtrade-data-observer qtrade-research-publisher
curl -fsS http://127.0.0.1:8010/data-freshness
```

发布器使用只属于 `QTrade-observations` 的 deploy key。私钥留在集群的受限目录中，
不进入源码、Git、网页或日志。Vercel 不保存 SSH 密码，也不能反向写入集群。

数据看起来不更新时，依次检查：采集进程是否运行、源数据时间是否推进、发布是否成功、
网站发布年龄是否超限。不要只检查网页刷新时间或“请求成功”。

公开检查：

```bash
curl -fsS https://q-trade-temp.vercel.app/api/data-freshness
curl -fsS https://q-trade-temp.vercel.app/api/persisted-experiments
```

容器关机、网络故障、GitHub/CDN 延迟会影响通道。源日历覆盖至 2026-12-31，需提前续期。
当前没有邮件/聊天主动告警；网页状态与 HTTP 健康检查是监控入口。

## 8. GitHub、Vercel 和集群的更新关系

建议流程是：新分支修改 → 运行相关测试 → PR 审核 → 合并 `main` → Vercel 自动构建。
代码进入 `main` 后，网站展示的 UI/API 实现会更新；新因子若未接入目录和界面，不会自动出现。

GitHub 推送**不会自动**在集群拉取代码、运行回测、注册策略或重启服务。
研究代码需要部署经过校验的确定版本，实验由显式命令执行。这样每个结果才能对应到
确定的代码 SHA、数据版本、参数和成本。

正常数据更新与正式实验摘要则通过快照通道进入网页，不需要每 5 分钟重建 Vercel。
所有策略继续遵守 portfolio → risk → OMS → execution → gateway 的边界，
不通过新增因子文件绕过风险控制或开启 `LIVE_TRADING_ENABLED`。
