# Ubuntu 部署与恢复操作

当前环境尚未取得两台服务器的 SSH、子域名和现有代理配置，因此这里是可执行配置与待实测步骤，不是上线记录。本地隔离 Compose 构建和健康检查已有记录，真实服务器仍需实测。建议 A 为生产，B 为测试和隔离备份；若现有业务已占用两台服务器，应先调整端口和资源配额。

## 首次部署（先在 B 测试）

1. 安装 Docker Engine 与 Compose，确认目标机的 4 核/4G、剩余磁盘、域名解析和现有 HTTPS 反向代理。将代码部署在专用目录，限制目录读写权限。
2. 复制 `.env.example` 为 `.env`，生成至少 50 位随机 `DJANGO_SECRET_KEY` 和单独的强 `POSTGRES_PASSWORD`；填写真实 `DJANGO_ALLOWED_HOSTS` 与 `DJANGO_CSRF_TRUSTED_ORIGINS`。不要提交 `.env`。需要识别时再由管理员在环境变量中配置图像服务密钥与模型。
3. 执行 `docker compose config --quiet`，再执行 `docker compose up -d --build`。数据库只在内部网络，Web 只绑定主机 `127.0.0.1:8000`；2 个 Gunicorn worker、1 个识别 worker、1 个提醒调度器。
4. 将 [Nginx 片段](../deploy/nginx-eatful.conf)合并到该子域的现有 HTTPS server block。证书由原有代理管理。检查 `/healthz/`、登录、CSRF 表单、原图鉴权下载、静态资源、上传、报销 ZIP。反向代理不得代理 `/private-media/` 到存储卷。
5. 执行 `docker compose exec web python manage.py check --deploy`，按实际域名核对 Secure Cookie、HTTPS、Debug 关闭、代理头、密钥和 HSTS。当前没有强制子域 HSTS 与 preload，因为其他子域的 HTTPS 状况未知；确定全域符合条件后再调整。
6. B 验证后，将同一镜像版本部署 A。上线前先备份 A，升级数据库迁移需与回滚策略一起审核。不要在 B 上使用 A 的真实外部通知密钥。

## 备份与隔离恢复

先停止外部写流量和 worker、调度器，确认没有上传或导出任务正在运行；数据库和媒体文件是顺序备份，业务持续写入时不能保证两者为同一时点。在 A 上从项目目录执行 `python scripts/backup.py /安全的隔离目录/本次备份`，它输出 PostgreSQL 自定义格式备份、媒体原件 tar 和 SHA256 清单。将完整目录复制到 B 的仅备份目录，限制 B 测试 Web 对该目录的访问。仅备份数据库不足以恢复图片和导出包。

在全新、空数据的 B 测试实例执行：

```bash
EATFUL_RESTORE_CONFIRM=restore-empty python scripts/restore_empty.py /备份目录
docker compose exec web python manage.py check
```

恢复脚本会验证摘要、空数据库与空媒体目录，拒绝覆盖现有项目或媒体。恢复前停止目标实例的外部写流量和 worker、调度器。恢复后用授权账号逐项比对项目、名单、文案版本、订单金额、原图 SHA256 和导出文件，并记录用时。本地隔离合成数据恢复已通过；真实服务器及真实数据恢复演练尚未执行。

## 回滚

升级前记录镜像版本、迁移版本和备份目录。应用回滚先停止写流量，评估新迁移是否可逆；不可逆迁移以隔离恢复的完整数据库加媒体包为准，不能只切回旧镜像。恢复演练通过后再切流量。旧导出包不得覆盖，旧文件继续受项目组长权限保护。
