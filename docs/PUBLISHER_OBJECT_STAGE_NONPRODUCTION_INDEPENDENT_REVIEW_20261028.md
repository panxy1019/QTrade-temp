# Object-stage 非生产原型独立审查

结论：**BLOCKED。原型与metadata-only图可见策略均未证明普通main push避免重传；不得据此声称解决冷Git交付。** 只测试全新Windows本地bare，不连接SSH或公网，不改生产仓库、owner、配置或凭据。

原source `ae0937027b993aa2390269817adfa5c3a379e88ae594956917a4d885c18cf707`、原tests `92a6c0ff203c0fe2e577e2f66bc5c75babdc9f3695ed1d5c113c55524edc726f`已冻结。[证据manifest](evidence/publisher-object-stage-independent-20261028/manifest.json)保存原件、所有独立失败/反例和源码，没有复制fixture仓库进Git。

## 阻断发现

1. **普通push重传，metadata图可见仍未解决。** 独立本地bare先用成熟Git把受限aux commit推到新remote；main保持旧完整commit、fsck正常。普通source不知道aux commit时，实际final main pack有9对象、含全部5个新blob，确认原失败。随后另一个新fixture把逐Git OID和SHA256核验的aux commit/tree打成705字节metadata pack，使用原生 `index-pack --strict --stdin` 只新增source对象库pack/idx/rev，所有既有source文件字节保持不变。`cat-file`已可解析aux，main在stage时仍不变；实际普通 `git -c pack.useSparse=false push HEAD:main` **仍**发9对象及相同5个新blob。图本地可见不足以推出协商排除。实际记录见[metadata反例](evidence/publisher-object-stage-independent-20261028/independent-metadata-graph-visibility-proof.json)，其state明确为BLOCKED_ORDINARY_PUSH_RETRANSMITS，不能因文件名含proof而解释为方案通过。
2. **Git环境可突破请求仓库。** `environment()`继承整个os.environ，只关闭global/system配置。实际fixture设置GIT_DIR后，`git(requested_repo, config --local …)`修改了另一个自有bare的config；GIT_OBJECT_DIRECTORY使hash-object把新对象只写到另一个自有对象库。均未触碰非fixture路径。未来必须显式环境allowlist，拒绝继承GIT_DIR/WORK_TREE/OBJECT_DIRECTORY/ALTERNATE_OBJECT_DIRECTORIES/CONFIG_*等覆盖，关闭replace对象与未审hooks；只忽略global config不构成隔离。
3. **NO_RETRY未被意图记录强制执行。** 原send没有持久或内存单次消耗记录。ACK丢失且aux已存在会被ref检查挡住，但失败后ref尚不存在时，同一plan仍可再次send。独立模拟协议流程先截断失败、保持refs不变，再用同一plan第二次成功，实际进入两次Popen。该项是模拟控制流反例，没有冒称真实SSH失败。需要在I/O前create-only、flush/fsync的UPLOAD_INTENT，跨实例/崩溃也禁止不确定结果重放，不能以远端ref暂不存在作为重试授权。

## 已有保护及未完成边界

目标精确文件集合、每文件SHA256、Git特殊mode拒绝、目标不可达对象拒绝、闭包object allowlist、完整预pack物理字节<=2MiB及发送前重派生，对本地fixture有约束作用。aux命令只使用old=zero创建固定namespace，main不作为目的；成功要求精确unpack/ref ACK及after refs集合。重复/部分ACK不能当两条完整预期状态处理。

证书source/inventory仍是外部可信输入，不是源内容自动认证。路径allowlist和少数秘密key检查不能替代实际public发布合同；README和任意字符串没有完整秘密扫描。未来source certificate必须来自已复核的公开输出字节，不能由调用者自签布尔“approved”取得权限。

手写pkt-line只有非生产实验地位：capability按substring检查，广告/响应读取、超时、输出界限、并发变化和异常状态尚未有完整独立Linux证明。每个Git命令90秒不等于整个certification/planning总成本90秒。POSIX真实send使用新session+owned group清理；本机6个POSIX协议case明确跳过，未测试真实SSH、host key、用户/端口、认证、ProxyCommand或网络超时。

## 实际测试与失败保留

原16项在Windows独立重现：10通过、6 POSIX跳过。独立v1为4通过1失败：Git自动生成合法`.rev`索引，测试只允许pack/idx过窄；原源码/XML保留。v2允许这个合法新增对象库索引后，仍4通过1失败，真实失败在期望普通push不含stage blob的断言，不能删除该失败。

v3把该项明确命名为“图可见仍重传”的反例，5项通过：4项真实本地Git行为、1项模拟不确定重放控制流。**这些通过表示已复现阻断，不表示原型或生产交付通过。** 源码和XML分别保存，未修改原工具/旧case使其宣称正向成功。

后续若审新传输方案，应优先复用现有成熟Dulwich/Git实现，固定endpoint/host key/用户/端口/远端路径和90秒owned cleanup；凭据留原执行端，仅在内存或已存在受限配置中使用，禁止日志/证据包出现认证正文。需要新实际协议/协商/超时/丢ACK/清理证明，并单列完整冷传输成本。即使辅助传输成功，仍须原自主publisher普通90秒成功及真实public freshness链，不能把operator辅助结果替代自主恢复。

根任务已转向独立relay设计审查；本证据不授权metadata安装、aux生产适配、公网push或预算变化。原型保留作为失败实验，当前没有生产SSH入口通过本审查。
