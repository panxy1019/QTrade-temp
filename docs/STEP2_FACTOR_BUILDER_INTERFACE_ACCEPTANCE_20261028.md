# STEP2 因子策略 Builder：接口与网页隔离验收

2026-10-28，结论为 `PASS_NONPRODUCTION_INTERFACE_TESTS`。本轮实现受保护的
因子策略登记入口，不把登记任务称为组合回测、模型训练或 PAPER 执行。
生产网页、生产 worker 和正式研究队列尚未启用本轮代码。

## 使用流程

研究页先读取有界、可核验的因子目录。用户选择已有因子、独立评价与冻结
数据版本，修改研究名称、时间切分、TopkDropout 参数和成本声明。登录后
预览合同，网页独立重算规格、目录及策略版本身份，再提交已有 HMAC 队列。
修改草稿会使旧预览失效；丢失提交响应后只能沿用同一请求标识重试。

实验中心将此类任务标为 `FACTOR_REGISTRATION`。完成回执展示不可变因子、
评价、数据、源码和策略版本，以及 `EXPERIMENT / NONE` 权限边界；不进入
Qlib 回测选择器，不展示虚构收益、成交或模型。旧 Qlib 训练回测入口保留。

API 使用已有研究登录、同源校验、共享三任务容量及任务队列。提交时同时
校验工作流与因子目录心跳、执行器源码和 capability。目录缺失或不可验证
时关闭登记，不用示例目录冒充实际档案。cluster child 对实际档案重新验证，
其 Linux 和真实隔离 Registry 结果见 `STEP2_FACTOR_QUEUE_BACKEND_ACCEPTANCE_20261028.md`。

## 测试与复现

- Windows API 回归 53 passed，含原 Qlib 登录、提交、冻结绑定、结果和市场
  时钟；原始 XML 保留。最终同源 API 也包含于后端 Linux 377/0skip 包。
- Vitest 20 passed：6 项目录/版本/权限合同、4 项 Builder 行为、10 项原研究
  页面回归。测试使用显式 fixture 和模拟 HTTP，不能证明真实生产队列交付。
- 七个相关前端文件 ESLint 通过；TypeScript/Vite build 通过。原有 bundle
  大小提示保留，尚未进行浏览器 DOM、交互或视觉验收。
- 初始导入路径错误、前端按钮断言失败及修复前结果保留，后续成功不覆盖
  原始输出。源码 SHA 和原始 XML/log 位于同目录 evidence manifest。

```powershell
Set-Location E:/QTrade-git
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git;E:/QTrade-git/tests'
python -B -m pytest tests/test_factor_research_api.py tests/test_qlib_bound_research_integration.py tests/test_qlib_workflow.py -q
Set-Location E:/QTrade-git/web
npx vitest run src/research/factorStrategy.test.ts src/components/FactorStrategyBuilder.test.tsx src/pages/QlibWorkflowPages.test.tsx
npm run build
```

## 下一道门槛与限制

需要以正式 Git 身份重新冻结合同，并完成隔离 HTTP → 实际 HMAC 队列 →
worker → Registry → signed receipt → API detail 的闭环，再做 Terra 浏览器
验收和生产部署评审。当前四因子的评价是既有合格日频范围内的回顾性
研究，包含幸存者偏差、输入可知时点未知、历史 PIT 与复权未认证等限制。
成本仅冻结为策略声明，尚未计算因子组合净收益；本轮不新增执行适配器。
未来数据改善应创建新版本、新实验与父子谱系，不覆盖既有评价。
