# 吃乎工作台部署交接报告

交接对象：社团技术部。报告日期：2026年10月4日。适用对象：未参与开发、负责测试部署、生产维护和未来平台集成的人员。

本项目已完成首版实现，当前代码自动化检查通过，建议先在测试服务器部署，由4-10名成员完成一次真实测评，再决定生产开放范围。本报告给出部署配置、操作顺序、验收条件和维护方法；实际Ubuntu部署、真实AI效果和用户试点尚未完成，不能将程序测试通过视为生产验收通过。

## 1 项目概况与交付范围

正式名称为“吃乎工作台”，源码仓库为 https://github.com/ilucky666/chihu 。本文部署基线为提交 `000b4c35329e2b071e0e80831a058042ff394e09`。技术部应记录实际发布的提交和镜像，不直接追随不断变化的main分支。包名、数据库标识及`EATFUL_`环境变量继续保留，避免因展示名改变破坏兼容。

工作流以项目为单位，每个项目可包含一次或多次到店。到店依次进行时间投票、参加确认、到店准备、菜品测评；项目随后汇总刊发稿和报销材料。页面突出当前节点，全部成员完成或截止到达时按规则推进。提醒采用站内方式，调度器每300秒扫描；用户请求也会触发部分节点同步，因此截止推进不是实时推送。

| 功能范围 | 当前实现与使用边界 |
| --- | --- |
| 账号与角色 | 管理员批量导入账号密码，仅允许登录；组长与组员权限分开，成员可改资料和密码 |
| 项目加入 | 登录成员可看到未结束项目并自行加入；组长结束并归档后停止新加入 |
| 排期与名单 | 批量生成午晚餐候选、投票、确定时间、参加确认与名额限制 |
| 菜品与图片 | 手工建卡、任务认领、投稿与照片、审核；AI识别生成候选后人工校对 |
| 协作文案 | 共享编辑、评论、修改建议、版本冲突提示、回退与总稿汇总 |
| 财务与导出 | 订单、优惠、付款、退款、金额分配、凭证关联、报销表及证明ZIP、内容包导出 |
| 后续接口 | 已有部分Session API与身份映射模型；统一登录、微信登录和完整客户端API未实现 |

新增成员不会导致已完成节点退回，也不会自动获得历史参加记录。加入项目与参加到店分开；截止前补确认受名额限制。没有参加已结束到店的成员可协作文稿和上传材料，但不能认领该次到店测评任务。

微信公众号、小红书与秀米的实际发布仍由人完成。自动微信催交、完整原生小程序、邮件找回密码与邮箱验证均不属于当前交付能力。AI可以暂不开启，手工录入能够完成核心流程。

## 2 架构与关键文件

本应用采用Python 3.12、Django 5.2、Django REST framework、PostgreSQL和服务端模板，桌面与手机共用网页。生产由Gunicorn提供服务、WhiteNoise提供静态资源、已有HTTPS代理负责证书。容器方案不需要安装Conda，也不需要Node构建前端；首次构建需能拉取镜像和Python依赖。

访问路径为：浏览器 → HTTPS反向代理 → 主机127.0.0.1绑定的Web端口 → Django → PostgreSQL与私有媒体卷。worker调用所配置的外部AI；scheduler扫描截止与站内提醒。任务使用数据库队列，当前Compose没有Redis或Celery依赖。

| 服务 | 当前配置 | 持久化与注意事项 |
| --- | --- | --- |
| db | postgres:17，内部网络，不映射公网端口 | db_data保存数据库；不要随意升级PostgreSQL大版本 |
| web | 2个Gunicorn worker，每个2线程，60秒超时 | 启动执行迁移与静态收集；挂载media_data和static_data |
| worker | 1个串行识别worker | 挂载同一个media_data；AI不用时保持空闲即可 |
| scheduler | 每300秒运行scan_reminders | 使用同一数据库；不负责发送微信或邮件 |

`internal`是Compose网络名称，不代表禁用外网。worker启用识别时必须能够通过HTTPS访问所选AI服务。Web容器健康检查仅查端口，`/healthz/`仅证明HTTP可响应；数据库、登录和业务读写需另验收。

| 文件或目录 | 技术部用途 |
| --- | --- |
| compose.yaml与Dockerfile | 构建、四服务启动、端口和卷配置 |
| .env.example与config/settings.py | 配置样例及实际环境变量读取规则 |
| deploy/nginx-eatful.conf | 合并到已有HTTPS站点的代理片段 |
| core/services与core/models.py | 权限、业务规则、工作流及数据结构 |
| core/api_urls.py与docs/openapi.json | 当前可用接口及合同 |
| scripts/backup.py与restore_empty.py | 数据库和媒体备份、仅空实例恢复 |
| docs/accounts-template.csv | 仅含表头的账号导入模板 |
| core/resources/reimbursement-blank.xlsx | 空白报销格式模板；运行中不修改原件 |

源码不包含本机环境、演示账号密码、真实图片、数据库、样例材料及原型。生产数据与密钥必须另外准备，不能从克隆仓库获得。仓库中的Logo及空白报销模板属于交付资源。

## 3 本地测试结果与证据

2026年10月4日在Windows、Python 3.12及本地PostgreSQL环境，对上述部署基线重新执行检查，结果如下。

| 检查项 | 本轮实际结果 | 证明范围 |
| --- | --- | --- |
| pytest -q | 65 passed in 28.34s | 业务、权限、并发和失败处理自动化回归 |
| ruff check与format --check | 全部通过，77个Python文件格式通过 | 静态检查与格式一致性 |
| manage.py check | 0个问题 | Django配置与模型系统检查 |
| makemigrations --check --dry-run | No changes detected | 模型与已有迁移无漂移 |
| showmigrations core | 0001至0005均已应用 | 本地数据库迁移状态 |
| 本地服务健康检查 | /healthz/返回status为ok | 本地HTTP服务可响应 |
| GitHub Actions | 当前基线运行成功 | Ubuntu runner上的依赖安装、检查和自动化测试 |

对应CI记录：https://github.com/ilucky666/chihu/actions/runs/37145704771 。CI使用Python 3.12与PostgreSQL 17服务，不等同于目标服务器Docker镜像构建及HTTPS部署验收。

代表场景包括：注册入口拒绝创建用户；CSV整批校验、强密码及散列存储；管理员保护与账号修改；跨项目权限；不同阶段加入、归档限制、并发加入幂等；并发任务认领；文案版本冲突；金额对账、重复凭证和公式注入防护；两种AI协议的模拟响应及手工回退。测试文件集中在`tests/`，可复查具体断言。

历史开发记录另有本地隔离Compose启动和合成数据备份恢复通过的记录，本轮未重跑容器恢复。它们不证明真实Ubuntu主机、生产数据和生产备份已经验收。

仍待执行：实际手机及微信内浏览器上传、桌面布局和断网草稿恢复；真实菜单与付款图的识别效果；Excel或WPS打开及打印；目标服务器资源负载、重启持久化与恢复演练；4-10人真实流程试点。本轮未执行手机与桌面视觉验收，程序页面请求不能替代效果检查。

## 4 部署建议与前置条件

目前已知社团有两台Ubuntu服务器，配置约4核4GB，已有域名与HTTPS。CPU、内存、剩余磁盘、Ubuntu版本、SSH权限和代理位置均需技术部现场确认。建议A承载生产，B承载测试及隔离备份；B上的备份不能直接被测试Web读取，两台服务器也不能自动形成高可用集群。

当前默认进程配置可作为小规模试运行起点，尚无目标4核4GB主机的容量测试证据。技术部应观察CPU、内存、磁盘、数据库连接、上传和导出耗时，再调整worker数量或资源限制；不能仅凭4-10人估计保证容量。

开始前需要：可写的专用部署目录；已安装且可使用的Docker Engine及Compose插件；Git、curl、OpenSSL和主机python3；可用DNS、HTTPS证书及反向代理修改权限；允许拉取镜像和依赖的网络；私有备份目录和负责人。Docker安装按官方Ubuntu说明完成，不擅自卸载现有业务使用的容器软件。[Docker官方安装说明](https://docs.docker.com/engine/install/ubuntu/)

本文默认Nginx与容器同在一台主机，代理目标为127.0.0.1。若代理在另一台服务器或另一个容器中，127.0.0.1并不指向本应用，应先由技术部调整受限网络路径。不要为了方便将数据库或Web端口直接开放到公网。

## 5 生产配置说明

Compose读取项目根目录的私有`.env`；直接运行`python manage.py`只读进程环境，不自动加载`.env`。生产必须使用PostgreSQL，`DJANGO_DEBUG=0`。每套环境使用不同密钥、数据库密码和Compose项目名。

| 配置项 | 必填情况与内容 |
| --- | --- |
| DJANGO_SECRET_KEY | 必填，生成独立高强度随机值；部署后保持稳定 |
| DJANGO_DEBUG | 生产和测试部署都设置0 |
| DJANGO_ALLOWED_HOSTS | 真实主机名，逗号分隔，不含协议或路径 |
| DJANGO_CSRF_TRUSTED_ORIGINS | 真实HTTPS源，含https前缀，不含业务路径 |
| POSTGRES_PASSWORD | 必填，独立随机数据库密码；初始化后改变.env不会自动修改已有数据库用户密码 |
| EATFUL_BIND_PORT | 可选，主机环回端口；默认8000，本文B测试用8001 |
| EATFUL_MEDIA_ROOT | Compose固定为/app/media；不要改到容器临时目录 |
| EATFUL_VISION_MODE | manual、responses或chat_completions；建议首次验收先manual |
| EATFUL_VISION_API_URL | AI启用时填写可信服务的完整HTTPS接口地址 |
| EATFUL_VISION_MODEL | AI启用时填写支持图片输入的模型名称 |
| EATFUL_VISION_API_KEY | AI启用时在私有.env设置；后台不显示或存储密钥 |
| EATFUL_BASE_URL | 样例预留项，当前业务代码未读取；不替代域名或代理配置 |

标准Compose通过PGHOST等变量指向db容器，不需要另填DATABASE_URL。若额外设置DATABASE_URL，它会优先于PGHOST生效，可能连到错误数据库。不要将本机127.0.0.1:5543的开发连接串带入服务器。

先生成密钥并在私有编辑器中填写`.env`；示例输出仅供本机配置，不能发到公开日志或仓库。

```bash
openssl rand -hex 32
openssl rand -hex 24
```

`.env`示例中的域名与占位值必须替换后才可启动。测试端口8001与生产端口8000需分别与Nginx代理目标一致。

```dotenv
DJANGO_SECRET_KEY=填写第一条随机输出
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=chihu-test.example.org
DJANGO_CSRF_TRUSTED_ORIGINS=https://chihu-test.example.org
POSTGRES_PASSWORD=填写第二条随机输出
EATFUL_BIND_PORT=8001
EATFUL_MEDIA_ROOT=/app/media
EATFUL_VISION_MODE=manual
EATFUL_VISION_API_KEY=
EATFUL_VISION_MODEL=
EATFUL_VISION_API_URL=
```

## 6 测试服务器首次部署

以下命令在B的项目目录执行，使用有Docker操作权限的部署账号。由技术部先创建并授权`/srv/chihu-test`的父目录；没有目录权限时先解决授权，不以root维护所有源码。

### 6 1 获取并固定代码

```bash
git clone https://github.com/ilucky666/chihu.git /srv/chihu-test
cd /srv/chihu-test
git checkout --detach 000b4c35329e2b071e0e80831a058042ff394e09
git rev-parse HEAD
umask 077
cp .env.example .env
chmod 600 .env
```

编辑`.env`并替换上一节的所有占位值。文件不进入Git，测试与生产不共用密钥。后文所有`-p chihu-test`必须保持一致，否则Compose会使用另一组容器和卷。

### 6 2 检查构建与启动

```bash
docker version
docker compose version
docker compose -p chihu-test config --quiet
docker compose -p chihu-test up -d --build
docker compose -p chihu-test ps
docker compose -p chihu-test logs --tail=100 web worker scheduler
```

Web启动会自动迁移数据库并收集静态资源。确认db、web可用，worker与scheduler持续运行；没有输出不一定代表后台失败，应结合容器状态和实际任务检查。`.env`含秘密，不运行或转发未脱敏的完整`compose config`输出。

### 6 3 接入已有HTTPS代理

将仓库`deploy/nginx-eatful.conf`中的location合并到测试子域的HTTPS server block，测试阶段将proxy_pass端口改为8001。该文件不是完整站点配置，不包含server_name或证书；同一server中已有location时应合并而不是重复粘贴。

保留Host、X-Forwarded-Proto、X-Forwarded-For和X-Real-IP转发头；私有媒体路径`/private-media/`保持404，不设置公开alias。当前代理上传上限16m，应用单图上限15MiB、4000万像素，支持JPEG、PNG和WEBP，不直接支持HEIC；批量账号CSV另有1MB限制。

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -fsS https://chihu-test.example.org/healthz/
```

期望健康接口返回`{"status":"ok"}`。如需直接在主机诊断环回端口，用真实Host与HTTPS代理头；普通HTTP请求在Debug关闭时可能跳转HTTPS，不应因此判定服务失败。

```bash
curl -fsS -H 'Host: chihu-test.example.org' \
  -H 'X-Forwarded-Proto: https' \
  http://127.0.0.1:8001/healthz/
```

### 6 4 检查配置并创建管理员

```bash
docker compose -p chihu-test exec web python manage.py check --deploy
docker compose -p chihu-test exec web python manage.py showmigrations
docker compose -p chihu-test exec web python manage.py createsuperuser
```

管理员访问`https://chihu-test.example.org/admin/`。部署检查中的W005与W021涉及全子域HSTS和preload，当前实现有意未开启；技术部评估后记录接受或调整理由。其他警告不能一概忽略。不能使用本机演示密码作为生产密码。[Django部署检查说明](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)

### 6 5 导入试点账号

在后台用户列表选择“批量导入账号”。CSV为UTF-8或带BOM的UTF-8，包含username,password，可附display_name,email；每批1-200人，密码需满足强度规则。先保留“仅检查”，成功后取消该选项重新提交。任一无效行拒绝整批，默认不覆盖已有账号，不能覆盖管理员。

初始密码经私有渠道发给本人，要求首次登录改密。批量更新选项会重置已有普通账号密码，只在明确需要时使用。忘记密码由管理员重置；本版无邮件自助找回。CSV放在仓库外，导入后清理私有临时文件。

完成后由管理员、组长、两名组员和未加入项目的普通成员分别登录检查。组长是项目角色，不自动等同于站点管理员。

## 7 AI配置与验证

首次部署建议先用手工模式验收，再单独开启AI。管理员后台“图片识别设置”支持手工、Responses、Chat Completions；后台记录的模式优先于环境变量，地址和模型留空时回退环境值。已有后台manual记录时，只修改.env为responses不会自动启用，必须同步后台设置。

| 方式 | EATFUL_VISION_MODE | 完整接口路径示例 |
| --- | --- | --- |
| 手工录入 | manual | 不调用外部服务，无需密钥 |
| Responses兼容 | responses | https://可信服务域名/v1/responses |
| Chat Completions兼容 | chat_completions | https://可信服务域名/v1/chat/completions |

图片模型需支持Bearer鉴权、base64图片输入及文字JSON输出。服务商可自由选择，但“兼容”需要真实请求验证，不能仅凭名称判断。密钥只放在私有环境配置，后台不存入数据库、不显示实际值；AI启用后上传者仍可逐图取消识别。被勾选的菜单、订单或付款图片将发送给配置的外部服务。

修改.env后重建应用服务，使新环境生效；单纯restart不会重新读取容器创建时的环境。

```bash
docker compose -p chihu-test up -d --force-recreate web worker scheduler
docker compose -p chihu-test logs --tail=100 worker
```

用允许发送的测试菜单和付款截图逐张验证：请求成功，候选内容与原图一致；菜单校对后只建一次卡片；付款必须人工确认，不能自动入账；取消AI勾选不排队；无密钥、错误密钥、额度不足和网络失败时仍能手工完成。后台Job列表可查看任务状态、次数和错误；不要依赖空闲worker日志判断真实识别成功。

生产密钥由技术部配置与保管。接口格式实现依据官方图片输入文档，但当前报告不提供任何服务商费用或识别准确率承诺。[图片输入官方文档](https://developers.openai.com/api/docs/guides/images-vision)

## 8 上线验收与生产发布

以下事项由技术部与业务组长共同验收，填写实际结果。测试应包含1名组长和至少2名组员；真实试点扩大到4-10人。失败项解决后重新验证，程序回归成功不能代替现场操作。

| 验收项目 | 操作与通过标准 | 记录 |
| --- | --- | --- |
| 登录与账号 | 导入、修改资料、改密、停用账号均有效；注册入口不能开通账号 | 待填写 |
| 项目与权限 | 新人看到项目并可加入；重复加入不重复；归档阻止新加入；越权访问被拒绝 | 待填写 |
| 排期与工作流 | 投票与确认正确推进；截止扫描有效；改时间处理确认版本；迟加入不退回节点 | 待填写 |
| 投稿与协作 | 两人认领同一任务结果一致；投稿与照片可审核；版本冲突不覆盖；回退保留历史 | 待填写 |
| 图片与隐私 | 手机及微信上传成功；非授权人不能下载付款原图；不公开媒体目录 | 待填写 |
| 对账与材料 | 支付、优惠、退款及金额分配一致；凭证正确；Excel或WPS打开与打印正常 | 待填写 |
| AI与手工 | 开启时完成真实识别校对；关闭或故障时手工流程可用 | 待填写 |
| 运行与恢复 | 容器和主机重启后数据、原图保留；后台恢复；备份能在隔离实例复原 | 待填写 |
| 资源与体验 | 试点人数下观察内存、磁盘、耗时；电脑、手机无影响操作的布局问题 | 待填写 |

验收通过后，在A用同一代码基线重复首次部署步骤。目录使用`/srv/chihu-prod`，所有Compose命令用`-p chihu-prod`，域名改为真实生产子域，EATFUL_BIND_PORT通常设8000。A使用独立密钥与数据库，重新创建生产管理员并导入真实成员，不将测试账号密码直接复制过去。

明确生产维护人、业务验收人和故障联系渠道，记录发布日期、提交、镜像ID、实际域名及端口、备份位置和验收结果。初次正式使用先限制到本社团已开通账号；之后根据实际问题改善UI、提醒和AI，不必等待未来统一平台完成。

本机演示数据默认不迁移到生产。若其中已有需要保留的真实业务数据，必须先单独确认范围，采用经验证的数据与媒体迁移方案，不能只复制源码。

## 9 备份恢复与日常维护

必须同时备份数据库和media_data，后者包含原图、缩略图和导出文件。static_data可通过collectstatic重新生成。源码版本、私有配置、证书及其恢复方式另做受限保存；当前备份脚本不包含.env、镜像、代理配置或证书，也没有自动定时及保留策略。

建议技术部设置每日备份及升级前备份，保留周期和频率按社团可接受的数据损失与磁盘确定，并设置失败通知。脚本顺序导出数据库再打包媒体，不自动冻结业务；需先在代理进入维护状态，停止worker和scheduler，等待正在运行的请求与任务结束，期间停止管理员写入。Web容器需保持运行供脚本exec tar。

### 9 1 创建备份

主机需有python3、可操作Docker的权限和只允许维护者访问的备份目录。以下在生产项目目录执行，备份放源码目录之外，每次使用新的子目录。

```bash
cd /srv/chihu-prod
docker compose -p chihu-prod stop worker scheduler
umask 077
backup_dir="/srv/chihu-backups/$(date +%Y%m%d-%H%M%S)"
python3 scripts/backup.py "$backup_dir" --project chihu-prod
docker compose -p chihu-prod start worker scheduler
```

期望输出Backup complete，并存在database.dump、media.tar与SHA256SUMS。成功后复制完整备份到B的受限目录，核对文件摘要，再解除维护状态；失败时不要把半份文件当作有效备份。不要执行`docker compose down -v`或删除卷，它们会删除持久化数据。

### 9 2 隔离恢复演练

在B准备新的目录`/srv/chihu-restore`及独立Compose项目`chihu-restore`，用对应源码版本和新的私有.env，将环回端口改为8002。不要接入用户流量，不创建管理员、不导入账号，先仅启动db和web，让迁移创建空结构。

```bash
cd /srv/chihu-restore
docker compose -p chihu-restore up -d --build db web
EATFUL_RESTORE_CONFIRM=restore-empty python3 scripts/restore_empty.py \
  /srv/chihu-backups/需要恢复的备份目录 --project chihu-restore
docker compose -p chihu-restore exec web python manage.py check
```

该路径是占位，应替换为实际完整备份。脚本校验SHA256清单，检查目标用户和项目数量为0及媒体没有文件，再恢复；不是向已有生产实例直接覆盖的工具。恢复期间web仍供脚本执行检查和untar，但禁止外部访问与后台写入。

恢复后用备份中的授权账号核对项目、名单、文案版本、订单与付款金额、图片摘要和导出包；确认无误再按需要启动worker与scheduler。恢复演练记录用时和结果。若恢复到较新的代码，先评估并执行所需迁移，不能假定旧备份的结构适配新代码。

### 9 3 日常观察

定期检查容器状态、HTTPS健康接口、证书有效期、失败任务、scheduler日志、内存与磁盘、备份是否产生及能否恢复。Compose尚未配置容器日志大小上限，生产建议设置日志轮转并在B验证；磁盘告警阈值由实际图片增长量确定。现有代码没有完整监控告警平台，不能只等用户反馈故障。

```bash
docker compose -p chihu-prod ps
docker compose -p chihu-prod logs --tail=100 web worker scheduler
docker stats --no-stream
df -h
```

## 10 后续升级与回滚

界面、文案、流程和功能可以部署后持续迭代。每轮先在B验证，记录目标提交及迁移，再备份A并进入维护状态更新。当前Web启动命令会自动migrate，生产启动前必须提前评估迁移；有迁移的发布可先启动db与web，验证后再启动后台，避免旧worker与新结构混用。

以下将目标提交替换为已在B验证的完整提交。镜像构建、依赖安装或迁移失败时停止开放流量，检查日志，不重复启动试图掩盖错误。

```bash
cd /srv/chihu-prod
git fetch origin
git checkout --detach 已验证的目标提交
docker compose -p chihu-prod stop worker scheduler
docker compose -p chihu-prod up -d --build db web
docker compose -p chihu-prod exec web python manage.py check --deploy
docker compose -p chihu-prod up -d --build worker scheduler
```

在执行上述命令前完成第9节备份，命令后完成HTTPS、登录、上传、导出及后台检查，再解除维护状态。仅修改.env时用force-recreate重建应用服务；不要重新初始化数据卷。

回滚不能只看Git提交：没有数据结构变更时可切回旧提交并重建应用；有迁移时先判断旧代码能否读取当前结构。不可逆或有数据转换的迁移，应按经演练的隔离恢复方案恢复数据库与媒体，再切流量。恢复至旧备份会丢失备份后的写入，需明确停写时点并由业务负责人确认。

报告按具体代码基线描述。升级PostgreSQL大版本、改数据库名、改媒体存储或轮换数据库密码属于独立维护操作，不与普通界面更新顺手合并。

## 11 常见故障排查

| 现象 | 优先检查与处理 |
| --- | --- |
| HTTPS出现502 | web是否运行、启动迁移日志、代理目标端口；127.0.0.1仅适合同机代理 |
| 页面持续跳转或登录失效 | HTTPS代理头是否正确、域名是否一致、Secure Cookie；不要用Debug=1绕过生产问题 |
| 登录后表单403 | CSRF_TRUSTED_ORIGINS是否含真实https源、Host转发是否正确、浏览器Cookie状态 |
| 页面400或DisallowedHost | ALLOWED_HOSTS填主机名，不填https或路径，补齐实际访问域名 |
| 静态资源404 | web启动collectstatic是否成功、静态卷是否挂载、代理是否误接管/static/ |
| 上传413或图片无效 | 代理16m、应用15MiB、格式与像素限制；HEIC先转JPEG，保持原图备份 |
| AI始终待配置 | 环境密钥与模型、完整HTTPS地址、后台设置优先级；web和worker都需重建环境 |
| AI任务排队或失败 | worker容器、Job状态、出网、服务认证和额度；仍可手工录入，不直接改库标完成 |
| 截止不推进或无提醒 | scheduler是否运行，scan_reminders日志、活动截止时间与服务时钟；允许一次扫描间隔 |
| 重启后数据看似丢失 | Compose项目名、卷挂载及是否误设DATABASE_URL；先找原卷，不创建新业务覆盖排查 |
| 备份恢复被拒绝 | 备份是否完整、摘要是否一致、目标用户项目及媒体是否为空；更换隔离实例，不删生产数据 |

## 12 后续集成规划

建议吃乎保留独立业务后台与数据，i自强提供入口和统一身份，吃乎继续判定项目组长、组员及财务权限。统一平台身份不自动授予管理员权限，也不自动绕过现有管理员开通账号的规则。

| 统一平台形态 | 建议接法 | 需要补充的工作 |
| --- | --- | --- |
| 网页平台 | 先添加入口，再接统一登录 | 身份绑定、单点登录、停用同步及安全跳转 |
| 微信小程序复用网页 | 条件满足时用web-view访问现有网页 | 核实主体与业务域名权限、登录衔接及微信内上传体验 |
| 原生小程序或其他App | 新客户端调用同一吃乎API | 客户端界面、登录凭证、完整业务与文件API、接口验收 |

当前`User`使用稳定UUID，`ExternalIdentity`保存provider与subject并有唯一约束；已提供项目、活动、任务、文案及导出相关的部分`/api/v1/`接口。当前鉴权为Django Session，适合现有网页；完整投票、确认、上传、财务客户端接口、微信登录及统一登录均未交付。[当前API代码](https://github.com/ilucky666/chihu/blob/000b4c35329e2b071e0e80831a058042ff394e09/core/api_urls.py)

统一身份建议由技术部按OpenID Connect对接：验证身份提供方与稳定主体标识，再绑定现有吃乎UUID；不按昵称或可改用户名自动合并账号。首次绑定需验证既有账号或管理员授权，未开通成员不能仅凭微信或平台登录自动注册。需要设计停用、解绑、注销登录、令牌有效期及历史账号迁移规则。[OpenID Connect规范](https://openid.net/specs/openid-connect-core-1_0.html)

小程序与其他客户端可新增令牌鉴权，但具体选择应随统一平台方案确定，网页Session与CSRF保护仍保留。不要把数据库密码、AI密钥或微信应用密钥放在客户端，也不直接让多个系统共同写吃乎数据库。[DRF鉴权说明](https://www.django-rest-framework.org/api-guide/authentication/)

微信web-view方案需技术部确定小程序主体后，核实实际业务域名权限及登录对接规则，并以微信后台和官方文档确认，不能把网页嵌入视为必然可行。集成前先约定身份协议、成员权限、接口合同和数据归属，再开发适配层并测试，无需为了未来集成重做现有项目数据。

## 13 技术部交接确认

本报告与仓库源码一并交付，账号初始密码、AI密钥、SSH和数据库密码另用私有渠道。以下由实际负责人填写，部署后保存到受限运维记录，公开仓库不要补入凭证或服务器访问秘密。

| 确认事项 | 负责人填写 |
| --- | --- |
| 生产维护人与业务验收人 | 待填写 |
| A和B的实际系统与资源 | 待填写 |
| 部署目录与固定Compose项目名 | 待填写 |
| 发布提交与镜像ID | 待填写 |
| 实际域名及代理位置 | 待填写 |
| 私有配置保管位置与管理员开通状态 | 待填写 |
| AI方式及真实验证结果 | 待填写 |
| 备份负责人位置频率与隔离恢复记录 | 待填写 |
| 试点成员及核心流程验收结果 | 待填写 |
| 故障联系渠道与回滚记录 | 待填写 |

配套资料：README.md为项目入口；docs/handoff.md说明账号与AI；docs/deployment.md说明部署恢复；docs/operator-guide.md供业务成员使用；docs/development-log.md记录历史检查与未验收事项。部署有疑问时先对照本报告对应提交的compose.yaml、config/settings.py及脚本，避免按较旧规划执行。
