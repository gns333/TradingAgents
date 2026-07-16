# CloudBase 部署、双运行模式与动态任务调度设计

## 背景

TradingAgents 已提供 FastAPI Web 工作台、后台分析任务、报告归档、管理员模型配置和本地 SQLite 持久化。当前实现适合单机本地运行，但不能直接作为 CloudBase 云托管生产形态：

- 现有 Dockerfile 启动交互式 CLI，而不是 Web 服务。
- Web 与 China 可选依赖没有安装进当前镜像。
- 管理配置、用户、任务、事件和报告保存在容器本地 SQLite。
- 用户身份主要依赖本地请求头、访问邮箱和管理员 Cookie，尚未解析 CloudBase 网关身份。
- 任务执行使用进程内 `ThreadPoolExecutor(max_workers=2)`，并发数量写死在代码中。
- 云托管容器必须按无状态方式设计，且 HTTP 请求存在超时限制；长耗时分析不能绑定在一次浏览器请求生命周期内。

本设计将项目部署到 CloudBase 云托管，并保留现有本地部署能力。两种运行方式使用同一套代码、API、前端和业务逻辑，通过显式运行模式选择身份与存储适配器。

## 已确认的产品决策

- GitHub 指定分支推送后，由 CloudBase 自动拉取代码、根据 Dockerfile 构建并发布。
- 使用 Dockerfile 构建，不依赖平台对 Python 项目的隐式识别。
- CloudBase 首期固定一个应用实例，但允许多个用户同时登录、提交和查看任务。
- 云端所有用户和管理员统一使用 CloudBase Web Auth。
- 云端管理员身份由业务数据库中的 `role=admin` 决定，不保留独立的云端管理员密码。
- 首个管理员先完成 CloudBase 登录，再由运维人员在 CloudBase MySQL 控制台将其 UID 标记为管理员。
- 本地模式保留现有 SQLite、管理员密码、管理员 Cookie 和本地访问身份能力。
- 模型供应商 API Key 只允许管理员在 Web 后台人工录入，不通过部署环境变量配置。
- 模型供应商 API Key 加密后写入数据库；云端主加密密钥必须与数据库分离。
- 任务并发和排队上限只允许通过管理后台动态调整，不提供对应环境变量。
- 首期默认同时执行 2 个任务，最多排队 20 个任务，每个用户最多有 1 个排队中或运行中的任务。
- 首期不实现多实例任务协调、高可用 Worker 或运行中任务的无损故障恢复。

## 目标

1. 提供可由 GitHub 自动触发的 CloudBase Docker 部署。
2. 同一代码库同时支持本地模式和 CloudBase 模式。
3. 云端使用 CloudBase MySQL 持久化用户、角色、配置、任务、事件和报告。
4. 云端使用 CloudBase Web Auth 和网关注入身份进行登录验证。
5. 管理员可在 Web 后台安全维护模型供应商 API Key。
6. 管理员可在 Web 后台动态调整任务并发、排队上限和接单状态。
7. 多个用户可同时登录；任务执行数量受全局并发限制，其余任务持久化排队。
8. 保持现有 Web API、任务事件和报告读取体验，避免前端协议出现无必要分叉。

## 非目标

- 首期不支持 CloudBase 多实例横向扩容。
- 首期不拆分独立 Worker 服务。
- 首期不接入 Redis、消息队列或分布式锁服务。
- 首期不保证实例重启后从 LangGraph 中间节点继续运行。
- 首期不把 SQLite 数据自动迁移到云端 MySQL。
- 不在 GitHub 仓库、Dockerfile 或前端保存生产密钥。
- 不改变 TradingAgents 的 Agent 图、模型角色映射或报告内容生成逻辑。

## 总体架构

```text
GitHub push
    |
    v
CloudBase Git 构建触发器
    |
    v
Dockerfile 构建并发布 FastAPI 服务
    |
    +-----------------------------+
    | CloudBase HTTP 网关         |
    | - 登录态校验                |
    | - 注入 x-cloudbase-context  |
    +-----------------------------+
                  |
                  v
       TradingAgents Web API
        |         |          |
        |         |          +--> 任务调度器 --> LangGraph 分析
        |         |
        |         +--> CloudBase MySQL
        |
        +--> 外部模型与市场数据服务
```

本地模式使用相同的 Web API 和任务调度器，但身份实现切换为本地认证，存储实现切换为 SQLite。

## 显式运行模式

增加统一运行配置对象，支持：

- `local`：默认模式，保持当前零外部数据库依赖的本地体验。
- `cloudbase`：使用 CloudBase Auth、CloudBase MySQL 和云端密钥策略。

运行模式必须显式选择，不能通过“是否存在某个请求头”或“是否能连接数据库”自动推断。自动推断可能导致云端静默回退 SQLite，或本地误连接生产数据库。

运行模式本身可以由部署配置指定；任务并发配置不使用环境变量。

## 存储边界

### 统一接口

将当前具体的 `AdminStore` 拆成稳定的存储契约和两个实现：

```text
ApplicationStore
├── SQLiteApplicationStore
└── MySQLApplicationStore
```

API、身份授权、任务调度、模型运行配置和报告服务只依赖 `ApplicationStore`，不能直接导入 `sqlite3` 或 MySQL 驱动。

存储契约覆盖：

- 应用设置
- 用户与角色
- 管理员会话（仅本地实现使用）
- 白名单与用户额度
- 模型供应商配置
- 分析任务
- 分析事件
- 报告
- 任务领取与状态转换

### 本地 SQLite

- 默认数据库位置继续为 `.tradingagents/web_admin.sqlite3`。
- 保留现有增量建表和历史报告兼容逻辑。
- 保留本地管理员密码与 Cookie 会话。
- API Key 继续加密保存。
- 新增运行设置和用户角色表时使用兼容的增量迁移。

### CloudBase MySQL

- 使用连接池访问 CloudBase MySQL。
- 云托管服务与数据库通过同 VPC 内网连接。
- 数据库凭据属于基础设施配置，不在管理后台维护。
- 使用显式迁移或幂等初始化建立表和索引。
- 时间统一存储为 UTC。
- JSON 字段由存储层统一序列化，避免 SQLite/MySQL 返回形态不同。

### 活动任务唯一约束

当前 SQLite 使用部分唯一索引限制每个 `owner_key` 只能有一个 `queued/running` 任务。MySQL 不直接复用该 SQL。

MySQL 实现必须通过数据库原子约束保证相同语义，可以使用独立的活动任务占用表：

```text
active_analysis_owners
- owner_key PRIMARY KEY
- run_id UNIQUE
- acquired_at
```

创建任务时，在同一事务中写入占用记录和任务记录。任务完成或失败时释放占用记录。这样不会依赖“先查再写”的竞态检查。

## 身份认证与授权

### CloudBase 模式

前端使用 CloudBase Web SDK v2：

1. 初始化 CloudBase 环境和 Publishable Key。
2. 用户完成 CloudBase 登录。
3. 前端取得 Access Token。
4. 所有受保护 API 请求携带 `Authorization: Bearer <token>`。
5. CloudBase HTTP 网关验证 Token。
6. 网关将用户上下文写入 `x-cloudbase-context`。
7. FastAPI 解码 Base64 JSON，取得稳定 UID 和可用用户信息。

后端不能信任浏览器自行提交的 UID、邮箱或角色。CloudBase 模式下，缺少有效网关上下文的受保护请求返回 `401`。

### 本地模式

本地模式继续支持：

- 管理员首次设置密码。
- 管理员密码登录和 Cookie 会话。
- 现有访问邮箱或测试请求头身份。
- 本地管理员绕过普通用户白名单限制。

本地身份入口不得在 CloudBase 模式下生效。

### 用户与角色

新增统一用户表：

```text
app_users
- uid PRIMARY KEY
- email
- display_name
- role: admin | user
- status: active | disabled
- daily_limit
- created_at
- updated_at
```

CloudBase 登录成功只证明用户身份，不自动授予管理员权限。后端根据 UID 查询 `app_users`：

- `admin`：访问管理后台、模型配置和运行设置。
- `user`：创建并查看自己的任务和报告。
- `disabled` 或不存在：按产品策略拒绝访问或进入待审批状态。

首期延续当前白名单模式：不存在或未启用的普通用户不能发起分析。

## 前端运行时配置

新增只返回非敏感信息的公开接口：

```http
GET /api/runtime-config
```

本地响应示例：

```json
{
  "runtime": "local",
  "auth": "local"
}
```

CloudBase 响应示例：

```json
{
  "runtime": "cloudbase",
  "auth": "cloudbase",
  "env_id": "env-id",
  "region": "ap-shanghai",
  "publishable_key": "public-client-key"
}
```

Publishable Key 是客户端公开配置，不是供应商 API Key。接口不得返回数据库凭据、主加密密钥或模型供应商密钥。

工作台初始化时先读取运行时配置：

- 本地模式呈现现有本地访问与管理员登录流程。
- CloudBase 模式呈现 CloudBase 登录、退出和登录态恢复。
- 业务页面继续使用相同任务、事件和报告 API。

## 模型供应商 API Key

### 管理流程

- 只有管理员可以新增、更新、启用、禁用或删除模型配置。
- 浏览器仅在新增或替换时提交明文 API Key。
- 后端收到后立即加密。
- 数据库保存密文、nonce、掩码、供应商、模型和 Base URL。
- 列表和详情 API 只返回掩码。
- 编辑时留空表示保留原 Key，不能通过读取接口恢复明文。
- 调用供应商模型目录或创建模型客户端时，后端按需解密。

### 密钥分离

模型供应商 API Key 不使用部署环境变量。

云端 AES-GCM 主加密密钥必须位于数据库之外，可以放在 CloudBase 服务密钥配置或腾讯云 KMS。数据库泄漏不能同时获得密文和解密根密钥。

本地模式可以继续使用自动生成的本地密钥以保持零配置体验，但文档需说明其安全边界低于云端密钥分离方案。

所有日志、异常、审计数据和模型目录缓存都不得包含明文 Key。

## 持久化任务队列与动态并发

### 设置项

任务运行设置只保存在数据库 `app_settings` 中，不提供对应环境变量：

```text
analysis_concurrency_limit = 2
analysis_queue_limit = 20
accept_new_tasks = true
```

数据库初始化时写入默认值。本地 SQLite 和 CloudBase MySQL 使用相同键名和语义。

允许范围：

- `analysis_concurrency_limit`：1 至 8。
- `analysis_queue_limit`：1 至 200。
- `accept_new_tasks`：布尔值。

配置缺失时初始化默认值。配置损坏时并发安全降级为 1，并在管理员状态接口和后台页面显示告警。

### 管理 API

新增：

```http
GET /api/admin/runtime-settings
PUT /api/admin/runtime-settings
```

只有管理员可以读取和修改。写入必须验证类型、范围和完整性，并记录更新时间与修改人 UID。

### 调度模型

不能继续把所有任务立即提交给 `ThreadPoolExecutor` 的内存队列。新的任务生命周期为：

1. API 验证身份、额度、接单状态和全局排队上限。
2. 在数据库事务中创建 `queued` 任务并占用用户活动任务槽位。
3. 调度器周期性或由本地通知唤醒。
4. 调度器读取当前动态并发设置。
5. 当 `running_count < concurrency_limit` 时，原子领取最早的排队任务。
6. 领取成功后才提交到固定安全上限的线程池。
7. Worker 持久化事件、报告和最终状态。
8. 完成或失败后释放用户活动任务槽位，并触发下一轮调度。

线程池可以设置固定安全上限 8，但真实并发只由数据库运行设置决定。

### 动态调整语义

- 从 2 调到 4：调度器立即允许更多排队任务开始。
- 从 4 调到 2：不终止现有任务；当运行数自然降到 2 以下后才启动新任务。
- `accept_new_tasks=false`：拒绝新任务，返回 `503`；已有排队和运行任务继续处理。
- 排队任务数达到上限：新任务返回 `429`。
- 同一用户已有排队或运行任务：返回现有的 `409 ActiveRunExists`。

### 单实例要求

首期云托管设置：

- 最小实例数 1。
- 最大实例数 1。
- Uvicorn Worker 数量 1。
- 推荐初始规格 2 核 4 GB。
- 默认分析并发 2。

只能有一个应用进程创建调度器。启动多个 Uvicorn Worker 会创建多个进程内调度器，不属于首期支持范围。

## 长任务与请求生命周期

创建任务 API 必须快速返回任务 ID，不能在同一次 HTTP 请求中执行 LangGraph。

前端使用短轮询和可重连 SSE 读取持久化事件：

- SSE 断开不取消任务。
- SSE 接近平台连接时限时允许自动重连。
- 使用事件序号 `after` 补齐断线期间事件。
- 页面刷新或关闭后，任务继续执行。

实例重启时：

- `queued` 任务可由新进程重新调度。
- 原实例中的 `running` 任务标记为 `WorkerInterrupted` 失败。
- 首期不自动重跑已开始任务，避免重复模型费用和不完整副作用。

## Docker 与 GitHub 自动部署

Docker 镜像必须：

- 使用受支持的 Python 版本。
- 安装 `.[web,china]`。
- 以非 root 用户运行。
- 启动 FastAPI Web 服务，而不是交互式 CLI。
- 监听 `0.0.0.0`。
- 使用 CloudBase 提供的端口。
- 保留 `/healthz` 作为健康检查。
- 不复制 `.env`、`.tradingagents`、测试缓存、Git 元数据或本地报告进镜像。

GitHub 自动部署流程：

```text
push 指定分支
  -> CloudBase 拉取仓库
  -> Dockerfile 构建
  -> 启动新版本
  -> /healthz 检查
  -> 切换流量
```

生产密钥只在 CloudBase 服务配置或外部密钥系统中维护。GitHub Actions 不是首期必需；CloudBase 仓库绑定负责构建和发布。

## 错误处理

- CloudBase 身份缺失或上下文非法：`401`。
- 用户不存在、被禁用或未通过白名单：`403`。
- 普通用户访问管理员接口：`403`。
- 同一用户已有活动任务：`409`，返回活动任务摘要。
- 全局排队达到上限：`429`。
- 管理员暂停接单：`503`。
- 数据库不可用：健康检查返回非就绪状态；业务请求返回结构化服务错误。
- 主加密密钥缺失或无法解密模型配置：云端启动失败或模型配置明确标记不可用，不能静默生成新密钥。
- 动态运行设置非法：拒绝保存；数据库已有非法值时降级并显示告警。
- Worker 异常：保存 `run_failed` 事件、错误类型和清理后的消息。
- 日志必须屏蔽 Authorization、数据库密码和模型供应商 API Key。

## 数据库迁移与兼容

- 本地已有 SQLite 数据不得删除或覆盖。
- SQLite 初始化继续使用增量建表和加列。
- MySQL 使用独立迁移历史，禁止每次启动无条件重建表。
- 存储层测试确保两种实现返回相同领域对象。
- 云端首次上线不自动上传本地报告；如后续需要迁移，作为独立工具实现。
- 现有 API 路径尽量保持兼容，新增云端登录后不再接受浏览器自行声明 UID。

## 验证策略

### 存储层

- SQLite 和 MySQL 契约测试。
- 用户角色、白名单和禁用状态。
- 模型配置加密、掩码和更新时保留旧 Key。
- 每用户活动任务唯一约束。
- 排队上限和状态转换。
- 运行设置默认值、范围验证和损坏降级。

### 身份与 API

- 本地管理员登录和 Cookie 回归。
- CloudBase 上下文解析、无上下文拒绝和伪造头隔离。
- 管理员/普通用户权限矩阵。
- 运行设置管理员 API。
- 任务创建的 `409`、`429` 和 `503`。
- 用户只能读取自己的任务和报告。

### 调度器

- 默认并发为 2。
- 调高并发后立即领取更多任务。
- 调低并发不取消运行任务。
- 排队任务按顺序领取。
- Worker 完成或失败后释放用户占用。
- 重启后恢复排队任务并将遗留运行任务标记失败。
- 调度器不会把全部排队任务预提交到线程池。

### 前端

- 本地模式继续显示本地登录流程。
- CloudBase 模式登录、退出、Token 注入和登录态恢复。
- 管理员运行配置编辑与错误提示。
- 普通用户不能看到或调用管理员能力。
- SSE 重连与轮询恢复。

### 部署

- Docker 镜像本地构建。
- 容器使用 Web 入口启动。
- `/healthz` 和数据库就绪检查。
- 镜像不包含本地密钥和运行数据。
- CloudBase 测试环境完成 GitHub 推送自动部署。
- 单实例下至少验证两个并发任务和一个排队任务。

## 分阶段实施

### 阶段一：基础抽象和本地兼容

- 增加运行配置对象。
- 抽象存储和身份接口。
- 将现有 SQLite 与本地身份迁入适配器。
- 保持现有本地测试通过。

### 阶段二：CloudBase 数据与认证

- 增加 MySQL 存储实现和迁移。
- 增加 CloudBase 上下文身份解析。
- 增加用户角色和首个管理员初始化流程。
- 接入前端 CloudBase Web Auth。

### 阶段三：动态调度

- 增加数据库运行设置。
- 将立即提交线程池改为数据库调度器。
- 增加后台运行配置 UI。
- 验证多用户排队、并发调整和重启恢复。

### 阶段四：容器和自动部署

- 更新 Dockerfile 和 `.dockerignore`。
- 增加 CloudBase 运行配置与部署文档。
- 本地构建并验证容器。
- 绑定 GitHub 分支，在 CloudBase 测试环境发布。

## 验收标准

- 本地用户无需 MySQL 或 CloudBase 账号即可按原方式启动 Web 工作台。
- CloudBase 用户必须登录后才能访问受保护 API。
- 云端管理员由 MySQL 角色控制，普通用户不能访问管理接口。
- 管理员可在 Web 后台新增和更新模型供应商 API Key，任何读取接口不返回明文。
- CloudBase MySQL 持久保存用户、设置、任务、事件和报告。
- 多个用户可以同时登录并提交任务，每个用户最多一个活动任务。
- 管理员调整并发后无需重启或重新部署即可生效。
- 默认同时运行两个任务，额外任务持久化排队。
- GitHub 指定分支推送后可自动构建并发布 CloudBase 服务。
- CloudBase 实例重建后，已排队任务仍可恢复，已运行任务以明确失败状态结束。
