# Object publication core 与固定 R2 SDK 独立审查

结论：原 core d362 存在一个已实证的 P1；作者窄修至 d11c 后，独立反例及新增上下文检查通过。固定 R2 factory d266 未发现本次审查范围内的新阻断。**这只是本地协议/实际 SDK 离线测试，不是 MinIO/R2、云 ACL、公开交付或性能验收。** reviewer 没有修改 core/provider、生产入口或共享状态，没有连接公网、创建云账户、读取凭证或调用生产控制。

## 精确版本与结果

| 项目 | SHA256 / 结果 |
| --- | --- |
| 原 core（保留） | d362529ba56f2283a0308c10103abe55b27e8feff9bdcf75513b42cc2f21d8dc |
| 作者修复 core | d11c801a90142d4b7142b8f67c422c341b30b84201999dd297c186c3582fa7d0 |
| 固定 R2 factory | d26685bf8f95db48ef3db8d0c8d4716aaac56d68963fd2c302fa9b5c62c16202 |
| 原 core baseline | 39 passed / 0 skipped |
| 原 core 独立期望合同 | 8 passed / 1 failed / 0 skipped，真实 DID NOT RAISE |
| 修复 core 独立检查 | 15 passed / 0 skipped |
| SDK 41 作者用例 + 原6反例 inverse 独立重放 | 47 passed / 0 skipped |
| 新独立实际 SDK 用例 | 5 passed / 0 skipped |
| 精确修复 core45 + SDK41 + 独立core15 + 新SDK5 + inverse6 | **112 passed / 0 failed / 0 skipped** |

最终组合 XML：`final-fixed-core-sdk-combined.xml`。原失败 XML `independent-desired-contract-v1.xml`、原源码 `object_publication.py` 与原测试全部保留；没有将旧失败改为成功，也没有把旧39项结果用于修复版 hash。作者只修改 core；reviewer 只写本临时目录。

## 已关闭的阻断：reconcile 没有绑定 manifest 身份

原版 `publish()` 重建当前对象并检查 `sha256(manifest)==publication_id`，但公共 `reconcile()` 缺同一检查。独立用例先提交合法 generation，再生成另一个合法 manifest（source_git_commit 从 c40 改 d40），仅把后者 dataclass 的 publication_id 换为已提交 ID：它的 head 与真 head 相同，但 manifest SHA 不同。原 `reconcile()` 仍返回 `COMMITTED_RECONCILED`，因此声称提交了错误 manifest 的上下文。用例期望拒绝，真实出现 DID NOT RAISE。相同伪造对象经 `publish()` 则正确拒绝 `CURRENT_SOURCE_CHANGED`。

作者 d11c 增加 `_prepared_context`：先核 manifest SHA/ID、source Git、aware/非未来创建时刻、parent/generation、summary validator/contract；匹配 head 后再次取得实际 manifest 并核原始 bytes 等价，再核全部远端资产。独立原反例现被 `PREPARED_MANIFEST_ID` 拒绝；五类 dataclass 上下文篡改及 parent 读取后 manifest 改变也被拒绝。未改日志、原失败或生产数据。

## 协议检查

- ETag 是不透明 CAS token，非 SHA。测试在父 head bytes 完全相同的 ABA 重写中换 ETag，子 generation 仍正确被 CAS 拒绝。非空父 ETag 的竞争不能只靠前置 GET 防范，最终 `If-Match` 是原子边界；首代用 `If-None-Match:*`。
- 不可变对象与 manifest 都是完整 SHA256 内容寻址；当前本地 reader 的 index/catalog/quality 与每个 shard occurrence 重新执行原始验证，远端完整 bytes/长度/SHA 再校，不以 receipt/mtime/ETag 代替内容。原索引同 SHA 被不同 entry 引用的关系验证仍保留。
- immutable PUT 已提交但 ACK 丢失，重开相同 journal 后靠完整 GET 收敛，不重 PUT。CAS 已提交、ACK 丢失且首个读回断线，后续调用仍只有一次 latest PUT；靠只读 reconciliation 确认，不盲重发。
- journal 写入使用同目录临时文件、file fsync、create-only link、directory fsync。独立模拟 link 后 fsync 失败，未发生任何远端 PUT，已留 intent 导致后续 fail closed。Windows 测试明确 instrument 目录 fsync syscall；不能据此证明 POSIX/NFS 崩溃持久性。
- manifest body 即使 store 内部 checksum 自洽、ETag 不变，也不能跨过内容 ID 检查。最后一次对象验证后发生外部删除时，不会返回假成功；但 head 可能已 CAS 到一个缺对象的 generation。这验证了代码明示的 **服务端必须禁止外部 delete/overwrite** 假设，客户端没有跨多个对象和指针的服务端事务。

## 固定 SDK 边界

实际 boto3 / botocore 均为 1.43.120，来自 `E:/QTrade-temp/r2-sdk-20261029`；pyproject 的两项均为精确 pin。factory 使用显式账户 HTTPS origin、path-style、TLS verify、显式凭据、无 proxy 继承，隔离 config/credentials/profile/data_path，模型仅来自 SDK 自带目录；实验插件在 context-local DISABLED 范围内构造并恢复原 context。

独立重放了作者41项与旧6反例 inverse；另设计5项实际 SDK 测试，所有 socket connect 均拒绝，HTTP send 只返回内存合成响应，凭据明确是不可授权的 SYNTHETIC：

1. AWS endpoint/CA/region/HTTP(S) proxy 环境变量不能改变固定 TLS origin、auto region、verify=True 和空 proxy。
2. 同一真实 client 的两线程请求各有独立 ContextVar 与唯一 wire attempt。
3. 嵌套 SDK 调用后恢复外层 attempt token，不误用另一次请求的 URL/计数。
4. HTTP session send 异常只发一次，清除 context，并只暴露枚举错误码。
5. 固定 SDK 私有 PluginContext hook 缺失时 fail closed，没有加载 ambient fallback。

没有保存 signed headers、任意 trace、请求 payload 或异常私有正文。DEBUG 检查在构造、调用和 dispatch 三处覆盖 SDK 及配置过的子 logger；这是稳定状态守卫，不是阻止同进程任意恶意代码动态改 logger/SDK 的 OS 锁。私有 plugin hook 升级仍需重新审查；实际部署还需要可信 venv 与源码来源核验。

## 可接受但必须保留的限制

- **已知无恢复路径：immutable unknown PUT + GET404。** 404 不排除正在途中的延迟提交；当前 journal 不重 PUT，因此会停住。此轮没有改恢复合同，也没有把 latest CAS 的 no-replay 放松为一般重试。
- **逐对象重复完整 GET，尚无性能通过。** 上传、CAS 前再检查与 committed reconciliation 都读取全对象；receipt 不能跳过 SHA。约6000 shards 的真实请求数、吞吐、SDK/服务长尾和总体内存必须实测，不能以小 fixture 通过宣告45/90秒 SLA。
- manifest 的 `source_git_commit` 当前是调用方提供的 Git40 声明，core 没有 Git 仓库查询或 source-tree attestation。独立用例确认它可与 quality 的 source_revision 不同；二者可能具有不同来源语义，不能据此自称已验证原生产 Git。正式 caller 必须把已审 source/producer/admission 和实际 bytes 绑定，不能只给看似合法的40位值。
- 可选 summary 的 validator 和 contract SHA 也由调用方提供；没有通用 DLP 或自动 schema 信任。只能接已审、明确只读、固定合同的扩展。
- SDK socket/read timeout 不是整个发布过程的硬总预算；调用方仍须 owned process 总 deadline。SQLiteObjectStore 是本机 FULL/WAL 仿真，不是 NFS/S3/R2 durability 证据。
- 固定 R2 factory 不接受任意端点。父任务实际 MinIO 只可通过独立非生产注入 client/harness，不能为测试降低正式 factory 的 origin/TLS 限制。真实 R2 账户授权、delete/overwrite ACL、缓存、TLS、公开 reader、完整数据量和自然连续轮次仍需独立服务验收。

## 复现

旧期望反例应失败一次，作为原漏洞证据，不纳入最终通过组合：

```powershell
$env:PYTHONPATH='E:/QTrade-temp/r2-sdk-20261029;E:/QTrade-git/src;E:/QTrade-git;E:/QTrade-temp/r2-sdk-independent-review-20261029T004200Z'
python -B -P -m pytest -q -p no:cacheprovider E:/QTrade-temp/object-core-independent-review-20261029/test_object_core_independent.py
```

修复版与固定 SDK：

```powershell
python -B -P -m pytest -q -p no:cacheprovider E:/QTrade-git/tests/test_object_publication.py E:/QTrade-git/tests/unit/test_r2_object_store.py E:/QTrade-temp/object-core-independent-review-20261029/test_object_core_fixed_independent.py E:/QTrade-temp/object-core-independent-review-20261029/test_sdk_independent_current.py E:/QTrade-temp/r2-sdk-independent-review-20261029T004200Z/test_r2_sdk_fixed_counterexamples.py --junitxml=E:/QTrade-temp/object-core-final-replay-new.xml
```

本包没有为 MinIO 或云服务登记通过，也不改变全 STEP1、浏览器、PAPER/ACTIVE/LIVE 的验收边界。
