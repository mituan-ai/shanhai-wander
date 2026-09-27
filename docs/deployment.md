# 部署与运维

## 单机结构

一个 Django / Gunicorn 进程组负责页面、API、账户和静态资源，默认 2 workers × 4 threads。SQLite 使用 WAL 与写入超时；数据库缓存同样保存在 SQLite，不需要 Redis。生产环境由 Caddy 提供 HTTPS，容器之间通过内部网络连接。

| 配置 | 说明 |
| --- | --- |
| `DJANGO_ENV` | `development` 或 `production`，生产关闭 DEBUG 并启用 HTTPS 跳转、安全 Cookie 和 HSTS |
| `SECRET_KEY` | 留空自动生成并持久化；自定义须至少 50 个随机字符 |
| `DATA_DIR` | 数据目录，本地默认 `data`，Docker 固定 `/app/data` |
| `ALLOWED_HOSTS` | 逗号分隔主机名，不写协议 |
| `CSRF_TRUSTED_ORIGINS` | 逗号分隔完整来源，如 `https://trip.example.com` |
| `TRUST_PROXY` | 仅当应用被可信代理隔离且代理覆盖协议头时设置 `true` |
| `DOMAIN` | 配套 Caddy 所用域名；生产覆盖文件自动派生主机与 CSRF 配置 |
| `AMAP_*` | 高德凭证，详见 [高德说明](amap.md) |

## 正式部署

1. 安装 Docker 和 Compose 2.24.4+。
2. 将域名 DNS 解析到服务器，开放 80/443 入站，服务器能联网申请证书。
3. 在项目中复制 `.env.example` 为 `.env`，设置真实 `DOMAIN`；需要在线地图时填写高德配置。
4. 执行以下命令：

```bash
docker compose -f compose.yaml -f compose.production.yaml up -d --build
docker compose -f compose.yaml -f compose.production.yaml logs --tail=80
docker compose -f compose.yaml -f compose.production.yaml exec app python manage.py createsuperuser
```

登录 `https://你的域名/admin/` 可维护模板和管理用户。没有默认管理员、默认密码或公开注册后的管理员权限。

容器数据卷初始所有者是 UID/GID `10001:10001`。优先使用默认命名卷；如替换为宿主机目录，需要先赋予该目录正确所有权及限制读取权限。不要把数据目录、备份目录映射到公开静态文件路径。

生产端口只由 Caddy 发布。`!reset []` 用于移除开发模式下 app 的本机端口；旧版 Compose 会报不支持此标签，应升级 Compose。

## 已有反向代理

如已有 Nginx / Caddy，可仅使用 `compose.yaml`，将 `.env` 改为生产模式并填写主机及 CSRF 来源，由宿主机可信代理转发至 `127.0.0.1:8000`。设置 `TRUST_PROXY=true` 时，代理必须覆盖 `X-Forwarded-Proto` 和 `X-Real-IP`（后者填真实连接客户端 IP，用于登录与接口限速）；不要将 app 端口开放给不可信客户端。可以用默认健康检查中的 `X-Forwarded-Proto: https` 验证应用存活。

HSTS 会使浏览器记住该域名必须使用 HTTPS，因此正式域名不要在 HTTP 与 HTTPS 之间来回切换。

## 升级

先备份。拉取你所用项目代码的新版本后：

```bash
docker compose -f compose.yaml -f compose.production.yaml up -d --build
docker compose -f compose.yaml -f compose.production.yaml logs --tail=80 app
```

启动脚本会依次迁移、创建缓存表、更新内置模板、收集静态文件并启动 Gunicorn。模板 seed 不修改用户行程；同 slug 的内置模板会按代码更新。

不要同时启动多个共享 SQLite 的独立应用服务执行迁移；本配置按一个 app 容器设计。升级中的数据库迁移不保证可以逆向回滚，回退版本前参考对应版本变更并准备恢复升级前备份。

## 日常管理

```bash
# 健康检查，不含私人数据
curl --fail https://你的域名/health/

# 重置遗忘密码（交互式输入，不要把密码写进命令历史）
docker compose -f compose.yaml -f compose.production.yaml exec app python manage.py changepassword 用户名

# 清除到期会话，可由服务器定时任务定期执行
docker compose -f compose.yaml -f compose.production.yaml exec -T app python manage.py clearsessions

# 查看日志
docker compose -f compose.yaml -f compose.production.yaml logs --tail=100 app
```

应用不预设邮件服务、支付、酒店预订、短信验证、内容自动审核或实时车辆遥测。公开社区内容由站点管理员负责管理，可以在后台删除不合适的行程、禁用账号。

## 常见故障

- **首次启动失败**：查看 app 日志；检查 `.env`、依赖下载、数据卷写权限以及本机 8000 端口是否被占用。
- **HTTPS 证书失败**：检查 DNS、域名对应 IP、80/443、防火墙及服务器出站网络。
- **CSRF 403**：确认访问域名与 `CSRF_TRUSTED_ORIGINS` 一致，代理正确转发 HTTPS 协议。
- **重启后登录失效**：检查持久化卷及密钥是否更换。备份应包含自动生成的 `.secret_key`；自定义 `.env` 中的密钥须另存。
- **SQLite locked 持续发生**：检查是否在并行迁移、长事务或高频写入。持续高写入流量时迁移 PostgreSQL 与共享缓存，再考虑横向扩容。
- **备份缺少最近上传的原始文件**：应用只将解析后的地点存到 SQLite，不保存原始上传文件；这属于预期行为。
