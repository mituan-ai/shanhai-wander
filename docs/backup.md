# 备份与恢复

备份应同时包含数据库和应用密钥。数据库里有账号的密码哈希、私人行程、会话及缓存；自动生成的密钥在 `.secret_key`。请将整个归档作为敏感文件保存。

## 建立备份

容器内可以在线备份：

```bash
docker compose exec -T app python scripts/backup.py
```

生产环境使用 `docker compose -f compose.yaml -f compose.production.yaml exec -T app python scripts/backup.py`。

脚本对主库使用 SQLite 原生 backup API，能正确包含 WAL 中已提交的数据，然后运行 `PRAGMA integrity_check`。归档还会保存数据目录中的其他普通文件及隐藏密钥文件，不跟随符号链接，不复制锁文件、临时文件、SQLite 日志或旧备份。数据库快照与其他文件是分别复制的；备份过程中不要主动更换应用密钥或修改持久化文件。

输出类似 `/app/data/backups/shanhai-wander-20260922T120000_123456Z.tar.gz`，时间采用 UTC。目录权限为 `0700`，归档为 `0600`。文件包含：

```text
manifest.json
data/
  db.sqlite3
  .secret_key
  ...其他持久化普通文件
```

将实际输出文件复制出来：

```bash
mkdir -p backups
docker compose cp app:/app/data/backups/实际备份文件.tar.gz ./backups/
chmod 600 ./backups/实际备份文件.tar.gz
```

在服务器上安排每日备份，并定期复制到异机存储；定时命令应使用你实际的项目路径与 Compose 参数。按自己的保留策略删除旧备份，脚本不会自动删除数据。

本地 Python 方式：

```bash
.venv/bin/python scripts/backup.py --data-dir ./data --output-dir ./backups
```

如果 `SECRET_KEY` 来自 `.env` 或服务器环境变量，脚本不会复制外部环境变量；必须另行安全保存原值。高德凭证、域名和自定义部署配置也应独立备份。

## 恢复到当前 Docker 数据卷

恢复会替换当前账号和行程，必须先停止应用；不要在 Gunicorn 或开发服务器仍运行时执行。

1. 确认归档是你自己的、可读取的备份，并记下完整文件名。
2. 停止服务，但保留卷：

```bash
docker compose down
```

生产部署使用同一套双文件命令：

```bash
docker compose -f compose.yaml -f compose.production.yaml down
```

3. 如果归档已在数据卷的 `backups` 里，启动一次性维护容器恢复：

```bash
docker compose run --rm --no-deps --entrypoint python app \
  scripts/restore.py /app/data/backups/实际备份文件.tar.gz --yes
```

如果归档在宿主机 `./backups`，以只读方式挂载：

```bash
docker compose run --rm --no-deps \
  -v "$PWD/backups:/restore:ro" --entrypoint python app \
  scripts/restore.py /restore/实际备份文件.tar.gz --yes
```

Linux 下归档需可由 UID 10001 读取；可针对该备份目录设置合适的所有权，保持其非公开状态。不要为了读取方便将含密钥的归档改成全员可读。

脚本先在临时目录校验文件路径、格式和 SQLite 完整性，拒绝符号链接及目录穿越。若数据卷中已有数据库，会额外生成恢复前备份；随后替换业务数据并保留 `backups` 目录。原 `.secret_key` 一并恢复。自定义环境变量密钥仍需恢复到原值。

4. 重新启动并确认账号与行程：

```bash
docker compose up -d
# 生产使用：
# docker compose -f compose.yaml -f compose.production.yaml up -d
```

启动脚本会执行当前代码版本的数据库迁移。优先用备份对应版本的代码恢复，再按正常流程升级；不要假定任意新旧版本之间可以自由回退。

## 本地恢复

停止 `runserver` 后执行：

```bash
.venv/bin/python scripts/restore.py ./backups/实际备份文件.tar.gz \
  --data-dir ./data --yes
```

建议首次上线前做一次完整的“创建行程 → 备份 → 在临时目录恢复 → 检查行程”演练，之后也定期抽样恢复。恢复验证比只检查归档文件存在更可靠。
