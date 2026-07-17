# 部署到腾讯云 CloudBase

本指南将 TradingAgents 部署为 CloudBase 云托管 Web 服务，并保留本地 SQLite、管理员密码和 Docker Compose CLI 用法。

当前云端架构限定为：

- CloudBase 云托管单实例，最小实例数和最大实例数都为 `1`。
- 单个 Uvicorn 进程；不要覆盖镜像 `CMD`，也不要增加多 worker。
- CloudBase Web Auth 负责登录，业务数据库中的 `app_users.role` 负责管理员授权。
- CloudBase MySQL 持久化用户、模型配置、任务、事件和报告。
- 模型供应商 API Key 只在 Web 管理后台录入，不放入 GitHub 或部署环境变量。

CloudBase 支持从 GitHub/GitLab 等 Git 仓库构建 Dockerfile，并配置推送后自动触发部署。参见 [CloudBase 部署方式](https://docs.cloudbase.net/run/deploy/deploy/introduce)。

## 1. 准备 CloudBase 环境和 MySQL

1. 在上海地域创建 CloudBase 环境。
2. 初始化 CloudBase MySQL，并记录数据库名称、内网主机、端口、用户名和密码。
3. 为云托管服务启用到数据库所在 VPC 的网络连接。
4. 确认云托管服务能通过内网地址访问 MySQL `3306` 端口。

应用启动时会幂等创建所需表和索引。首次发布前不需要手工导入 schema。

## 2. 创建云托管服务

在 CloudBase 控制台进入“云托管”，创建 WEB 公网服务：

| 配置项 | 值 |
| --- | --- |
| 部署方式 | Git 仓库 |
| 仓库 | 本项目的 GitHub 仓库 |
| 分支 | 生产分支，例如 `main` |
| 自动部署 | 开启分支推送触发 |
| Dockerfile 目录 | 项目根目录 |
| Dockerfile 名称 | `Dockerfile` |
| 启动命令 | 使用镜像默认 `CMD` |
| 服务端口 | 与平台注入的 `PORT` 一致 |
| 最小实例数 | `1` |
| 最大实例数 | `1` |

镜像默认执行：

```text
tradingagents-web --host 0.0.0.0
```

程序会从 `PORT` 读取监听端口，未提供时默认为 `8000`。CloudBase 的 Git 仓库部署会拉取代码、构建 Dockerfile 并创建新版本；Dockerfile 必须位于所选目标目录中。参见 [版本配置说明](https://docs.cloudbase.net/run/deploy/version-setting)。

单实例和单进程是首期的必要约束：任务调度器位于应用进程内，多实例或多 worker 会造成重复领取任务的风险。任务并发数量应在 Web 管理后台调整，而不是通过增加实例数或 worker 数调整。

## 3. 配置基础设施环境变量

在云托管服务配置中添加：

```text
TRADINGAGENTS_RUNTIME=cloudbase
TRADINGAGENTS_DATABASE_URL=mysql+pymysql://USER:PASSWORD@HOST:3306/DATABASE
TRADINGAGENTS_CLOUDBASE_ENV_ID=your-env-id
TRADINGAGENTS_CLOUDBASE_REGION=ap-shanghai
TRADINGAGENTS_CLOUDBASE_PUBLISHABLE_KEY=your-publishable-key
TRADINGAGENTS_MASTER_KEY=URL_SAFE_BASE64_32_BYTE_KEY
```

说明：

- 数据库密码若包含 `@`、`:`、`/` 等字符，必须进行 URL 编码。
- Publishable Key 是允许出现在浏览器端的 CloudBase 客户端配置，不是模型供应商 API Key。
- `TRADINGAGENTS_MASTER_KEY` 必须是恰好 32 个随机字节的 URL-safe Base64，且应与数据库分离保存。
- 不要设置 OpenAI、DeepSeek、Anthropic 等模型供应商 API Key 环境变量；部署完成后由管理员在 Web 后台录入。

PowerShell 可生成主加密密钥：

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
```

保留输出末尾的 `=` 填充字符。密钥丢失后，数据库中已加密的模型 API Key 将无法恢复。

## 4. 配置 Web Auth、HTTP 网关和安全域名

1. 在 CloudBase“身份认证 → 登录方式”中启用“用户名密码登录”和“邮箱验证码”。
2. 在 HTTP 网关中将域名路由到该云托管服务。
3. 下列路径保持公开访问：

   - `/`
   - `/assets`
   - `/api/runtime-config`
   - `/healthz`
   - `/readyz`

4. 为 `/api` 路径开启 HTTP 身份认证；精确的 `/api/runtime-config` 公开规则应优先于 `/api` 前缀规则。
5. 将正式访问域名加入 CloudBase Web SDK 安全来源列表；本地联调时再按需加入 `localhost`。
6. 若不希望绕过 HTTP 网关，部署验证完成后可在云托管“服务设置 → 网络访问”中关闭不带鉴权的直接公网入口。

HTTP 网关开启身份认证后，前端需要把 Web SDK 获取的 Access Token 放入 `Authorization: Bearer ...`；网关验证后再把用户上下文交给应用。参见 [CloudBase 身份认证](https://docs.cloudbase.net/service/authentication) 和 [HTTP 访问说明](https://docs.cloudbase.net/run/develop/access/client)。

页面中的“注册”使用 CloudBase 邮箱验证码流程创建身份账号。CloudBase 不支持无需验证的用户名密码自助注册；注册接口依次调用 `getVerification`、`verify` 和 `signUp`。参见 [CloudBase 用户名密码登录与注册](https://docs.cloudbase.net/authentication-v2/method/username-login)。

## 5. 初始化首个管理员

1. 先通过部署后页面的“注册”完成邮箱验证和账号创建。
2. 应用会自动把网关验证得到的 CloudBase UID 写入 `app_users`，默认角色为 `user`、状态为 `disabled`，并提示等待管理员审核。
3. 在 CloudBase 用户管理中取得该账号的稳定 UID。
4. 等 `/readyz` 返回 `200`、应用表已创建后，在 MySQL 控制台执行：

```sql
INSERT INTO app_users (
    uid, email, display_name, role, status, daily_limit, created_at, updated_at
) VALUES (
    'CLOUDBASE_UID',
    'admin@example.com',
    'Administrator',
    'admin',
    'active',
    100,
    UTC_TIMESTAMP(6),
    UTC_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
    role = 'admin',
    status = 'active',
    updated_at = UTC_TIMESTAMP(6);
```

退出并重新登录后，侧边栏会显示管理员身份和“后台管理”入口。后续用户自行注册后会自动出现在“用户管理”中；管理员将状态从 `disabled` 改为 `active` 后，该用户即可登录并发起任务。

不要根据浏览器提交的邮箱或 UID 授予权限；注册同步只以 HTTP 网关注入的 UID 创建待启用记录，云端角色和启用状态只以数据库记录为准。

## 6. 在后台完成应用配置

进入“后台管理”：

1. 在“模型管理”中新增供应商、模型和 API Key。读取接口只返回掩码；编辑时 Key 留空表示保留原值。
2. 在“用户管理”中审核新注册用户，并设置 `user/admin`、`active/disabled` 与每日次数。
3. 在“运行设置”中调整：

   - 同时执行任务数：`1` 至 `8`。
   - 最大排队任务数：`1` 至 `200`。
   - 是否接受新任务。

运行设置保存于数据库并立即通知调度器，无需修改代码、环境变量或重新部署。调低并发不会中断已经运行的任务。

## 7. 验证 GitHub 自动部署

向绑定分支推送一个提交，然后依次确认：

1. CloudBase 自动拉取仓库并完成 Dockerfile 构建。
2. 新版本启动，`GET /healthz` 返回 `{"ok": true}`。
3. `GET /readyz` 返回 `{"ok": true}`；若数据库不可用应返回 `503`。
4. 未登录调用受保护的 `/api/*` 被网关拒绝。
5. CloudBase 用户可登录，管理员与普通用户权限正确。
6. 管理员保存模型 API Key 后，列表和响应中不出现明文。
7. 将并发设置为 `2`，使用不同用户提交三个任务，确认两个运行、一个排队。
8. 实例重建后，模型配置、用户、报告和排队任务仍保存在 MySQL。

## 8. 本地运行仍然可用

本地 Web 默认使用 SQLite 和本地管理员密码：

```bash
pip install -e ".[web,china]"
tradingagents-web --host 127.0.0.1 --port 8000
```

不要设置 `TRADINGAGENTS_RUNTIME=cloudbase` 即可保持本地模式。

原有 Docker Compose CLI 入口也保留：

```bash
docker compose run --rm tradingagents
```

如果要用镜像在本地验证 Web 入口：

```bash
docker build -t tradingagents-cloudbase .
docker run --rm -p 8000:8000 -e PORT=8000 tradingagents-cloudbase
```

最后一个命令未设置 CloudBase 环境变量，因此会以本地 SQLite 模式启动；容器删除后其中的本地状态不会保留，除非挂载 `/home/appuser/.tradingagents`。
