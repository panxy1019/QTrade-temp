# 本地 bounded shallow base + incremental bundle 实验

结论：metadata-only prerequisite 方案不能重建当前完整 tree/blob，已明确证伪。完整、独立、带 shallow 边界的 depth=1 base 仓库归档，加 `old..target` bundle，在本夹具中可离线恢复全部当前文件。`bundle verify=0`、`unbundle=0` 不能代替 base blob 完整性验收。

范围仅为 Windows Git 2.53.0.windows.3 / Python 3.14.6 的合成 fixture。所有写入均在新建 E:/QTrade-temp 目录，无生产仓库、网络、GitHub、主分支 push 或 STEP2 文档变动。本实验的 frozen HEAD 是合成目标，不是当前 P0 发布 HEAD；5.358 秒是本地小夹具运行时间，不能外推真实传输或 90 秒恢复期限。

最终目录：`E:/QTrade-temp/p0-bounded-git-fixture-20261028T140010Z-76ef46bf`。

## 实测结果与原始证据

最终 `results.xml` 为 **25 checks / 0 failure / 0 error / 0 skip**；77 条 Git 命令及每条 stdout/stderr 原字节在 `commands.json` 与 `commands/`。`checks.json`、`result.json` 保存逐项结果，`independent-evidence-review.json` 复核所有原始流 SHA、XML 计数、两个输入归档和逐个当前文件。

| 接收端 | bundle verify | unbundle | 当前 blob | fsck --full --no-reflogs | 判定 |
| --- | --- | --- | --- | --- | --- |
| 手工仅 copy exact old commit + 3 trees，并设 shallow old | 0（命令048） | 0（049） | 6 个中缺 4 个：README + 3 reuse | 2（058） | REJECT_INCOMPLETE_BASE_BLOBS |
| 独立完整 depth=1 base.tar + bundle | 0（059） | 0（060） | 6 个全读出，753719 bytes，SHA256 全等 | 0（070） | PASS 当前完整 tree/blob |
| 空仓库，无 prerequisite old commit | 1（074） | 1（075） | 不接收 | 未作为有效 target 安装 | 拒绝 |

metadata-only 是明确标注的手工 fixture，不冒充真正 partial clone。另做了真实 `file:// --filter=blob:none --depth=1` 控制；本地服务端输出 `filtering not recognized by server, ignoring`，实际携带了全部 base blobs，因此没有拿该 clone 冒充缺 blob 接收端。此观察不推断其他服务端或已启用 filter 的 file upload-pack。

源 old 包含 3 个大 reuse blobs、README、旧 changed blob 及随后删除的 base public blob。中间 commit 更新 changed 并加入一个 98304-byte public blob；target 删除该中间 public blob 和旧 base public blob，加入一个 new blob。target root tree、路径、mode、OID、blob 长度/SHA 及实际 checkout 字节均逐一对照。

* base commit：`f0ed92e43fe3ac419cb849aa4068f10922adfc9b`
* frozen target：`dd3999c9103a282cdc8628c2ea7cad148a9cf513`
* frozen target tree：`aa4a884531681524a52aba7f6aa29104ffad5a33`
* exact advertised ref：`refs/heads/frozen-head`
* `base.tar`：747520 bytes；SHA256 `e46e7344cf10a55b5a8427d5efde44f54f703f22dc1b9bff456a7bbe64dbd409`
* `increment.bundle`：427114 bytes；SHA256 `05ac34724ccf90d920fa17aab3ae039ce34ad9a1355643381f41a633e7549110`

Base 有 10 个物理对象，只含 old 的完整提交/tree/blobs；old 的 parent 历史提交及其独有 blob 未进入归档。bundle 只有 **1 个 prerequisite=old、1 个 ref=target**，145-byte header，9 个 pack 对象。`verify-pack -v` 实证 9 个对象都无 delta；它仍省略全部 3 个 reused base blob。bundle 同时含 target 已删除的中间 public blob `1ef1c790863520f223ffb02fd6fa52d94312d985`，说明 range 历史负载也必须纳入边界和资料检查。

命令带了 `pack.window=0`、`pack.reuseDeltas=false`、`pack.reuseObjects=false`。本机 `git help --config` 列出 pack.window，但未列出后两项（原始输出及 SHA 在 `local-git-config-help.*`）。本实验不把 Git 接受未知配置名视为生效证据；无 delta 结论仅来自实际 pack 验证，源对象最初为 loose。不能把这一结论外推到具有既存 delta 的任意生产源仓库。

本夹具的拒绝边界：base tar <=2000000 bytes；bundle <=2000000 bytes；header <=65536 bytes；increment pack objects <=64；exact singleton prerequisite/ref；target blob 总量 <=1000000 bytes。重复 target ref、重复 prerequisite、错误 target、错误 base、bundle 超界、pack object count 超界、错误 base tar SHA 均有拒绝检查。最终 19 个物理对象的解压总量也由独立证据复核，低于 2000000 bytes。这里的边界数值是小夹具配置，不能当作生产阈值。

冷接收只解开已经哈希绑定的 `base.tar`，再导入增量 bundle；源路径和原 clone 路径已改名为 unavailable，之后全部 Git transport 显式禁用，GIT_NO_LAZY_FETCH=1，接收端没有 remote、alternates 或 promisor。未声称操作系统防火墙断网。完整 ancestor history 不在浅仓库验收范围。

## 原失败保留

首轮目录：`E:/QTrade-temp/p0-bounded-git-fixture-20261028T135524Z-d8621924`。

首轮 12 项检查通过后，正例失败：归档 helper 只打包文件，漏掉空的 `refs/` 等目录。解压后 Git 报 `need a repository to verify a bundle`。首轮 `commands.json`、result、XML（12 checks + 1 execution error）及原 driver/hash 原样保留。最终 driver 仅补目录 tar entries 和解包目录对照，在新目录重建后通过；没有覆盖首轮原始失败，未改写或归一化 XML。冷搬运仓库归档必须保留 Git 所需目录结构，只有文件清单相等还不够。

## 给真实 cold 搬运的最小准备

1. 先冻结实际 old/target 两个完整 OID 和 target tree，确认 old 是 target 的祖先；预先确定 base 与整个增量范围的字节、对象数、解压量、当前路径数和时限上限。范围内已删除文件也要审计。若实际完整 base 超预算，不能以删 blob 或 metadata-only 代替。
2. 在可读取完整源的准备端生成 old 的完整 shallow base；确认无缺 blob，记录 shallow 边界、object format、所有 Git 必需目录及文件 SHA。归档应采用干净、无凭据的配置，禁用 hooks，去掉 remote/promisor/alternates；本夹具携带的仅 Git 默认 sample hooks，运行时 hooks 全禁用。**本正例搬运的是完整 shallow bare repo tar，不声称 Git bundle 自身编码 shallow 边界。**
3. 为 exact `old..target` 生成 bundle；独立读取 header，强制一个且仅一个 expected prerequisite old 和 expected target ref/OID。检查包 SHA/长度、对象上限及实际 pack 表示。若要求无 delta，应使用得到验证的实现/显式 pack-objects 选项并验证实际 pack；不能依赖未证实的配置名。无 delta 也不能消除 base blob 需求。
4. 冷端只写新 staging 仓库；在导入前 stat + bounded read，安全解包（禁止越界路径、symlink/hardlink、设备节点、未知元数据），校验完整 base 字节/目录与 shallow 边界。保持所有传输和 lazy fetch 禁用。然后 bundle verify/unbundle；两者成功仅是中间检查。
5. 最后解析 exact target 完整 tree，拒绝未知 modes/submodules 或按另行合同验证，按总量边界读取**每个**当前 blob 并核对 OID、length、独立 SHA，执行完整 fsck、checkout 并核对实际文件。只有所有对象/源/预算检查通过，才能作为一个独立待审候选；fixture 不包含公开 refs 更新或生产切换。

真实 P0 源包、Linux、NFS fsync、GitHub/TLS、传输丢包/断点、90 秒 deadline、生产切换与恢复控制仍须独立验收。本轮未授权也未执行任何 main push。

## 复现和封存

运行 `powershell -NoProfile -File E:/QTrade-temp/p0-bounded-git-fixture-20261028T140010Z-76ef46bf/reproduce.ps1`，只会在 E:/QTrade-temp 另建唯一目录并复制最终 driver。不要在已有证据目录重跑 `run_fixture.py`。

`review_and_seal.py` 只读复核既有两轮结果、生成独立 review 及 `sealed-manifest.json`，不会重放 Git 或注册任务。`sealed-manifest.json` 绑定两轮 driver、每条原始输出、原始失败、最终 JSON/XML、base.tar、bundle、fixture 当前文件及本文；Git object directories 不在文件清单逐项复制内，完整可恢复对象已包含于 base.tar 和 increment.bundle，物理对象清单保存在 result.json。所有产物保持在 E:/QTrade-temp，没有写入仓库 docs 或 Git。
