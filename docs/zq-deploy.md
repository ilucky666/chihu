# 吃乎接入自强自动部署

更新：2026-11-04。适配依据是 [zq-deploy 36a1049](https://github.com/ZiqiangStudio/zq-deploy/tree/36a10495289e3d67b59bf602b2d3e62e442c7894) 的 reusable-container、deploy-agent、service 与 backup 合同。中央模板与实际代理有一处差异：模板网络名为 edge，当前默认代理使用 traefik；本应用不自行创建这两种网络，由代理设置路由。

## 实际部署结构

业务仓库 `.github/workflows/deploy.yml` 先调用本仓库的检查，再调用组织 reusable-container。镜像逻辑名 default、镜像名 chihu、端口8000、探活路径 /healthz。平台生成带 digest 的 Deployment，服务器代理拉镜像并替换容器。仓库不保存 SSH、服务器地址、代理令牌或 `.platform/deploy.sh`。

默认镜像同时监督 Gunicorn、单个识别 worker 和一个每300秒执行的提醒调度器。首次启动先检查运行配置并执行数据库迁移，静态文件在构建时收集，由 WhiteNoise 提供。任一子进程退出会停止容器并返回失败；SIGTERM 会通知整个子进程组，最长等待25秒。应用以 UID10001 运行。默认一个 Gunicorn worker、两个线程；资源不足时先检查实际内存，不直接增加进程。

数据全部在外部：RDS MySQL 保存账号、业务记录、任务与加密个人Key；OSS 保存原图、缩略图和导出材料。私有文件仍通过 Django 的登录和项目/财务权限下载，不返回公开URL。上传强制 private ACL、禁止覆盖，按 OSS_PROJECT 隔离对象前缀。OSS Bucket 必须关闭公共访问；RAM 账号只授权该前缀所需的读写、查询和删除权限。备份使用独立账号和 Bucket，不能只给应用账号扩大权限。

## 技术部需要配置

1. 将 GitHub App 安装到实际业务仓库，允许生产 Deployment；若仍用 ilucky666/chihu，确认组织工作流和组织注册表凭据可供该个人仓库使用。迁入组织时只需更新源码文档和 App 授权，镜像名仍为 chihu。
2. 平台维护者准备注册表权限、production环境映射和服务器代理。服务器 Traefik 可到应用8000端口并传递可信的 X-Forwarded-Proto。应用没有主机端口映射需求，不需要服务器安装 Python。
3. 为吃乎创建专用 RDS MySQL 数据库和账号；推荐 MySQL8.4，至少8.0.16、InnoDB、utf8mb4与utf8mb4_bin（也接受utf8mb4_0900_bin）。不要与其他项目共用业务表。账号需有迁移所需 DDL 权限。运行检查会拒绝不符合要求的数据库排序规则。
4. 为吃乎分配私有 OSS Bucket或独立前缀，注入以下运行环境。按当前代理实现，可写入服务器 `/etc/zq-deploy/secrets/chihu.env`，权限0600；与 common.env 重名时服务专属配置覆盖。数据库与OSS信息由技术部私下填写，仓库的 `.env.example` 只提供占位值。

| 运行环境变量 | 说明 |
| --- | --- |
| DJANGO_SECRET_KEY | 独立、稳定的 Django 随机密钥 |
| DJANGO_DEBUG | 生产固定0 |
| DJANGO_ALLOWED_HOSTS | 真实域名，多个域名以逗号分隔，不填https前缀 |
| DJANGO_CSRF_TRUSTED_ORIGINS | 完整https来源，多个以逗号分隔 |
| EATFUL_BASE_URL | 对外https地址 |
| DATABASE_URL | mysql://用户:URL编码后的密码@RDS地址:3306/专用库 |
| MYSQL_SSL_CA | 可选，容器中可访问的RDS CA文件；启用时验证证书和主机名 |
| EATFUL_MEDIA_STORAGE | 生产设为oss，不能依赖容器文件层 |
| OSS_ENDPOINT / OSS_BUCKET | HTTPS地域endpoint、私有Bucket名 |
| OSS_PROJECT | 独立项目/环境前缀；默认代理会设为chihu，测试环境须隔离 |
| OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET | 限权RAM凭证，仅注入运行环境 |
| OSS_SECURITY_TOKEN | 仅STS需要；短期凭证须有外部刷新及重新注入机制 |
| EATFUL_AI_ENCRYPTION_KEYS | 独立加密主密钥，首次生成后稳定保管，详见个人AI说明 |
| EATFUL_AI_ALLOWED_HOSTS | 可选，AI服务精确域名白名单 |
| WEB_CONCURRENCY | 默认1；不要与后台任务副本数混淆 |

不要把运行密钥填进 GitHub Variables：当前上游 reusable-deploy 会把全部 Variables 合并进 Deployment payload。连接串、个人AI Key、Django密钥、OSS凭证和加密主密钥只放服务器私有环境文件/密钥系统。平台默认代理会将 payload.env 的同名值覆盖服务器配置，因此仓库 Variables 中也不能放会覆盖生产安全参数的测试值。

5. GitHub 仓库 Variables 设置 `CHIHU_DOMAIN`（真实域名）、`REGISTRY_HOST`、`REGISTRY_NAMESPACE`、`REGISTRY_PROVIDER`。不填注册表时沿用平台默认的阿里云注册表、ziqiang_studio命名空间、static认证。static凭证使用平台支持的Secrets，例如DOCKER_USERNAME/DOCKER_PASSWORD；aliyun-acr的OIDC参数按平台 registry-providers.md 配置，GitHub workflow已有id-token权限。生产主密钥不进入这些Secrets或构建阶段。
6. 在所有准备完成前保持 `ZQ_DEPLOY_ENABLED` 未设置；此时只运行检查和隔离构建，不登录注册表或创建生产部署。准备完成后将其设为字符串true，main后续push触发构建与中央部署；PR只构建并验证，不发布到生产。

工作流固定到已核对的上游SHA。上游内部registry-login仍引用@main，这是平台当前行为；平台升级后需技术部复核调用合同并更新SHA。

## 首次启动与验收

平台默认代理只给新容器约30秒达到healthy。先在隔离测试环境确认首次迁移能在此窗口内完成。新库初始化/大表迁移超过窗口时，由技术部在服务器清单中安排单独迁移和更长的健康等待；不要取消探活或让未完成迁移的容器对外接流量。维护清单放服务器，不提交业务仓库。已有非向后兼容迁移需维护窗口，不能依赖新旧容器短时并存来保证安全。

部署后以下命令在目标服务器执行，容器名是默认代理使用的 chihu；自定义清单以实际名称替换：

```bash
docker exec chihu python manage.py check_runtime
docker exec chihu python manage.py showmigrations core
docker exec -it chihu python manage.py createsuperuser
docker exec chihu python manage.py check --deploy
docker inspect --format '{{.State.Health.Status}}' chihu
```

再通过HTTPS验证两个普通账号和一个管理员：登录、项目加入、投票与确认、文案版本冲突、菜单和付款图片权限、报销导出、AI手工回退。用真实OSS上传一张获准测试图，触发一次替换部署后比对原图哈希和下载权限；核对提醒与识别任务确实运行。主密钥需要在服务器私有终端生成，可用已构建镜像：

```bash
docker run --rm -e DJANGO_DEBUG=1 --entrypoint python "$IMAGE_REF" manage.py generate_ai_encryption_key
```

生成值私下写入运行环境并独立备份；不要每次发布重新生成。更新运行环境后必须重新部署让新进程加载。

## 本地/非平台预览

`compose.yaml`不构建镜像，不运行数据库容器；它读取IMAGE_REF和私有运行文件并将应用仅映射到本机端口。示例：

```bash
docker build -t chihu:preview .
# 在专用测试目录准备私有.env，填写外置测试数据库和测试OSS，不能使用生产数据。
IMAGE_REF=chihu:preview docker compose config --quiet
IMAGE_REF=chihu:preview docker compose up -d
```

若由其他部署系统显式挂载持久media卷，可设EATFUL_MEDIA_STORAGE=filesystem、EATFUL_MEDIA_PERSISTENT=1；标准平台没有挂载，因此不得在平台使用这一配置。开发时直接使用Python仍支持本地文件和PostgreSQL，不受此容器保护限制。

## 旧版迁移、备份与恢复

0007将文案的条件唯一性和凭证的NULL不重复规则改为生成列加唯一索引，在MySQL和PostgreSQL上均由数据库强制执行。不要手动删除旧迁移；旧PostgreSQL实例可以直接应用新迁移，原数据和UUID保留。历史MySQL实例若绕过旧约束产生重复文案或凭证，迁移应失败，先人工核对而不能随意去重。

若已有PostgreSQL生产数据，改DATABASE_URL不会迁移数据。先停写与后台任务，完整备份PostgreSQL、media及主密钥，在隔离MySQL库中演练转换。可通过Django dumpdata/loaddata转换业务数据，排除contenttypes、auth.permission、sessions并使用natural-foreign；保留用户UUID、密码散列、个人AI密文及对应主密钥。关系和生成列需在隔离目标验证；核对每表数量、账号登录、金额、文案版本、任务、原图摘要后才切换。禁止直接将pg_dump导入MySQL。生产转换未在本次重构中自动执行。

旧media目录中的对象应保持相对路径上传到OSS的OSS_PROJECT前缀下，例如media/private/a.png对应chihu/private/a.png；数据库只保存相对名。原图、缩略图和exports都要迁移，不能只传照片。先复制校验再切换，不删除原件。用每个Asset/ExportRun已有sha256验证原件和导出文件，另记录缩略图清单。生产数据迁移由技术部在隔离环境完成验收。

应用发布不删除数据库、Bucket或历史对象。采用平台backup合同：RDS每日逻辑备份/快照，OSS版本控制和独立Bucket备份，主密钥独立加密保管；在新空库和隔离前缀中恢复，验证账号、UUID、金额、权限、文案版本与文件摘要。恢复先做restore-check，不覆盖生产。旧scripts/backup.py、restore_empty.py只适用于历史含db/web容器的PostgreSQL Compose，不能用于本部署。

回滚镜像不等于回滚数据库。失败部署如涉及不兼容DDL，应保持维护状态并按已演练的数据恢复方案处理，不能只重新指向旧镜像。当前默认代理仅在新镜像健康后删除旧容器；对于数据回滚、备份、较长迁移和维护窗口，技术部需在服务器服务清单中实现，不应假定平台会自动恢复数据库。

## 检查证据

GitHub检查包含MySQL8.4和PostgreSQL17全套业务回归，及实际Linux镜像构建、启动、私有下载、替换后文件摘要和后台进程退出监督测试。测试数据库、卷与网络均为临时合成资源，测试结束按精确名称删除。OSS单元测试模拟官方SDK，检查私有ACL、命名隔离、禁用公开URL和流式读取；真实OSS/RDS访问、生产Traefik及中央App调度仍需技术部实际环境验证。具体已执行结果记录在development-log.md。
