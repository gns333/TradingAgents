# CloudBase 股票自动补全鉴权修复设计

## 背景与根因

股票自动补全通过前端 `apiJson()` 请求 `/api/stocks/search`。在云托管旧的
`sh.run.tcloudbase.com` 域名下，该接口允许匿名访问；在实际使用的
`app.tcloudbase.com` 域名下，网关要求 CloudBase Access Token。当前
`apiJson()` 只有在调用方已经传入 `options.headers` 时才刷新并附加 Bearer
Token，而自动补全 GET 没有传入 headers，因此被网关以
`401 MISSING_CREDENTIALS` 拒绝。`fetchTickerSuggestions()` 又会静默隐藏请求
异常，最终表现为输入股票代码后没有下拉建议。

## 目标

- CloudBase 用户登录后，所有经 `apiJson()` 发出的请求都能自动携带最新的
  Bearer Token，即使调用方没有显式传入 headers。
- 恢复 `app.tcloudbase.com` 域名下的股票代码和名称自动补全。
- 保持本地模式、登录前运行时配置请求及现有显式 headers 请求行为不变。
- 不降低后端接口或 CloudBase 网关的鉴权要求。

## 方案

修改 `apiJson()` 的请求头合并逻辑：当运行模式为 CloudBase、认证客户端已
初始化且当前存在 Access Token 时，刷新 Token，并基于
`options.headers || {}` 创建新 headers，写入 `Authorization: Bearer ...`。
调用方已有的 `Content-Type` 等请求头继续保留，Authorization 使用最新 Token。

不在 `fetchTickerSuggestions()` 中添加特例。这样修复位于统一请求边界，避免
未来其他无请求体 GET 再次遗漏 CloudBase 身份。

## 错误处理

保留现有 `apiJson()` HTTP 错误解析行为。自动补全仍可在暂时失败时隐藏菜单，
但鉴权失败不再由缺失请求头触发。此修复不改变登录失效或网络故障的产品行为。

## 测试策略

先添加一个针对前端请求契约的回归测试，证明 CloudBase 已登录时，未显式传入
headers 的 `apiJson()` 调用也会创建 headers 并附加最新 Bearer Token。确认该
测试在修改前因现有限制条件失败，再完成最小实现并运行相关 Web 静态契约测试。

最后执行 JavaScript 语法检查和与 Web 工作台相关的定向测试，不运行与本修复
无关的完整分析代理测试套件。

## 非目标

- 不修改股票目录、AKShare 数据源或搜索排序。
- 不开放 `/api/stocks/search` 的匿名访问权限。
- 不修改 CloudBase 网关、服务端口或部署流程。
- 不改动自动补全的视觉样式与交互节流时间。
