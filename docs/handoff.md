# 吃乎：部署交接与账号、AI 配置

更新：2026-10-04。源码仓库：https://github.com/ilucky666/chihu 。真实 Ubuntu 部署、实际 AI 识别与用户试点仍需验收，模拟接口测试不代表真实识别效果。

## 管理员批量开通账号

应用只提供登录，关闭自行注册；旧 `/register/` 地址 GET/POST 均返回 403，不会创建用户。现有账号保留，停用账号请在后台取消“有效”。

首次部署执行 `docker compose exec web python manage.py createsuperuser`。管理员登录 `/admin/`，在用户列表点击“批量导入账号”，i自强也提供管理员入口。

上传 UTF-8 CSV（支持 BOM），每批 1–200 人、最多 1MB。必须包含 `username,password`，可附加 `display_name,email`。[空白 CSV 模板](accounts-template.csv) 仅含表头，填写后需另存到仓库外。密码遵循 Django 强度规则。CSV 含初始密码，应通过私有渠道发给管理员与本人，不上传 GitHub，不放在公开网站目录，导入后清理临时文件。

后台默认“仅检查，不写入”。检查通过后取消该选项，重新上传才会导入。任一行无效拒绝整批；默认拒绝覆盖已有用户名。明确勾选更新才会重置已有普通账号的密码和指定资料，保留权限和有效状态；不能批量覆盖管理员。

维护者也可将 CSV 私下放到容器临时位置后运行：

```bash
docker compose exec web python manage.py import_users /tmp/accounts.csv --dry-run
docker compose exec web python manage.py import_users /tmp/accounts.csv
# 需要更新已有普通账号时，显式追加 --update-existing。
```

成员在 i自强修改登录用户名、称呼、邮箱和密码。改密验证旧密码，用户名检查重名；稳定 UUID 不变，历史记录和项目关系保留。忘记密码由管理员重置。邮箱仅作为资料，本版没有邮箱验证或自动邮件找回。

## 项目大厅与随时加入

所有已登录成员都能看到未结束项目的主题、说明、发起人和状态，打开介绍并自行加入，无需邀请或审批。自行加入只授予组员角色；重复点击不会重复建成员。

项目结束的标志为组长选择“结束并归档”。之前，即使进入测评、总稿或报销节点，也可加入；之后拒绝新加入和旧邀请，原成员保留查看权限。工作流显示材料已完成与组长归档分开处理。

加入项目与参加到店分开：新人可以参与进行中的投票，在确认截止前登记参加并受名额限制。已完成节点不会退回，新人不会自动获得历史参加记录；未参加已结束到店的人可共同编辑刊发稿、上传材料和参与后续活动，但不能认领该次到店测评任务。

加入前仅能查看基本介绍，不能读内部活动、投稿、账单和图片。加入后，财务原图仍只允许提交者与组长读取。API 保留“我的项目”的权限边界，新增 `/api/v1/project-directory/` 和 `/api/v1/projects/{pk}/join/`。

## 图片识别与手工录入

管理员在后台“图片识别设置”中选择手工录入、Responses API 或 Chat Completions 兼容 API，并填写完整 HTTPS 地址与支持图片的模型。后台设置优先；地址或模型留空时使用部署环境。没有后台记录时使用全部环境配置。

密钥只从 `EATFUL_VISION_API_KEY` 环境变量读取，后台仅显示是否配置；不存入数据库、不显示密钥。两种协议分别使用对应请求格式，服务必须支持 Bearer 鉴权、base64 图片输入和文字 JSON 输出；仅支持纯文本或其他专有协议的服务需要另外适配。

在服务器私有 `.env` 中填写：

```dotenv
EATFUL_VISION_MODE=responses
EATFUL_VISION_API_URL=https://可信服务地址/v1/responses
EATFUL_VISION_MODEL=服务商的图片模型名称
EATFUL_VISION_API_KEY=在服务器私有配置中填写真实密钥
```

Chat Completions 使用 `chat_completions` 模式及完整 `/v1/chat/completions` 地址；手工方式用 `manual`。服务商和模型可自由更换，但具体兼容性、费用和识别效果需实测。

修改 `.env` 后执行 `docker compose up -d --force-recreate web worker scheduler`。后台设置立即影响后续任务。识别需运行 worker；配置不完整时可手工建菜、订单、付款，不会假装识别成功。

手工模式不向 AI 发图。AI 可用时每张菜单、订单或付款图都可取消“启用 AI”勾选，选择只保存图片、手工录入；勾选后会将该图片内容发送给所配置服务。结果进入候选校对，付款不会自动入账。失败后有限重试，仍可手工继续；切手工后待处理任务可能显示未启用的失败状态，但不再发送图片。

请求格式依据：[OpenAI 官方 Images and vision](https://developers.openai.com/api/docs/guides/images-vision)。这证明兼容结构的来源，不证明任意服务商都可用或已完成真实识别验收。

## 技术部验收

按 [部署及恢复](deployment.md) 先 B 后 A，创建生产 `.env`、密钥、数据库密码和真实域名。复测账号导入与改密、各阶段加入、重复加入、归档拒绝、权限、AI 与手工回退；验证实际手机/微信上传、Excel/WPS 打印、容器重启、worker 恢复、数据库与媒体一起备份恢复。

仓库不含本机环境、账号 CSV、演示账号、数据库、真实图片、样例和原型；空白报销模板和社团 Logo 随源码交付。生产账号、密钥与备份另走私有渠道，执行负责人、版本和实际验收证据记入 [验收记录](development-log.md)。
