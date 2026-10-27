# 吃乎工作台

社团美食测评工作台。一个项目可包含多次到店；组长安排投票、确认名单、分配任务、汇总文案和报销材料，组员认领菜品测评与照片。应用默认只发站内提醒，不自动向微信或平台发布内容。

源码仓库：https://github.com/ilucky666/chihu 。账号由管理员批量导入，应用关闭自行注册；所有已登录成员可在项目大厅自行加入未结束项目。成员在 i自强修改个人资料与密码。

## 本地启动

要求 Python 3.12 和 PostgreSQL。命令行直接启动读取进程环境变量，不会自动加载 `.env`；Docker Compose 启动才读取 `.env`。推荐使用独立虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock pytest==9.1.1 pytest-django==4.14.0 ruff==0.16.8
export DJANGO_DEBUG=1
export DATABASE_URL='postgresql://用户名:密码@127.0.0.1:5432/数据库名'
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Windows PowerShell 激活方式为 `.venv\Scripts\Activate.ps1`，环境变量写法为 `$env:DJANGO_DEBUG='1'`、`$env:DATABASE_URL='postgresql://用户名:密码@127.0.0.1:5432/数据库名'`。未设置数据库时仅在 `DJANGO_DEBUG=1` 下使用本地 SQLite，并需先创建 `.work` 目录；正式部署必须 PostgreSQL。Docker 部署复制 `.env.example` 为私有 `.env` 后填写真实设置，见部署交接文档。

## 常用命令

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
pytest -q
ruff check .
ruff format --check .
python manage.py scan_reminders
python manage.py advance_workflows
python manage.py run_worker --once
```

图片识别采用每人独立配置：在“i自强 → 我的 AI 设置”填写协议、完整 HTTPS 地址、图片模型和个人 API Key。Key 在服务器认证加密保存，不回显；任务使用发起者的配置与额度。部署只需设置独立的 `EATFUL_AI_ENCRYPTION_KEYS` 加密主密钥及可信域名名单，不再读取旧 `EATFUL_VISION_*` 全站凭证。修改或删除配置会停止旧任务；任何时候都可手工录入。升级、主密钥备份和轮换见 [个人 AI 部署说明](docs/personal-ai.md)。

项目与到店页按当前节点显示任务。到店活动依次经过时间投票、确认参加、到店准备和菜品测评；全部成员投票/确认完成或相应截止时间到达时自动推进，后台提醒扫描也会推进过期节点。菜品任务都完成后，项目依次显示总稿汇总和报销材料。顶层导航为“我的项目”“测评任务”“i自强”，移动端使用底部导航。

当前开发电脑的 `.work/` 留有本地启动脚本和演示数据，它们未上传仓库；新电脑按上述命令启动、创建管理员并批量导入自己的账号。需要后台识别和截止推进时，另开终端用同一套环境变量运行 `python manage.py run_worker`，并定期运行 `python manage.py scan_reminders`。生产 Compose 已包含这两个后台服务。

## 文档

- [个人 AI 配置与升级](docs/personal-ai.md)
- [技术部部署交接报告](docs/deployment-report.md)
- [部署交接、批量账号及 AI 设置](docs/handoff.md)

- [零基础开发讲解](docs/zero-basics-development-guide.md)
- [产品方案](docs/product-plan.md)、[样例分析](docs/sample-analysis.md)、[原始 28 包计划](docs/vibecoding-plan.md)
- [实施与验收记录](docs/development-log.md)、[使用与维护手册](docs/operator-guide.md)
- [部署及恢复](docs/deployment.md)、[领域合同](docs/domain-contract.md)、[OpenAPI 合同](docs/openapi.json)

`sampal/` 仅作本地参考且不进入 Docker 镜像。`core/resources/reimbursement-blank.xlsx` 是从样表抽取样式并清空所有内容后的模板；运行时只读取它，不修改原件。导出文件、图片原件和数据库数据必须纳入备份。
