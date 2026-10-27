# 个人 AI 配置与服务器加密保存

更新：2026年10月28日。本说明取代旧版全站共用 AI 配置。用户不再将个人 API Key 发给技术部；在“i自强 → 我的 AI 设置”（`/me/ai/`）填写自己的接口协议、完整HTTPS地址、图片模型和Key。支持Responses、Chat Completions及手工录入。

## 成员操作

首次配置填写全部字段并保存；Key采用密码框，保存后只显示是否配置，不回显、不提供明文读取接口。相同地址留空可保留原Key；更换地址须重新输入Key，避免误将原服务的凭证发给另一服务。保存后可点击“测试已保存的连接”，它只发送一次简短文字，可能消耗额度，不证明图片效果。

上传菜单、订单或付款图时，勾选AI才会以你的个人配置排队。组长识别其他成员有权查看的图片时，使用组长自己的配置，不借用图片上传者的Key。重试仅限任务发起者；其他人需使用自己的配置发起新任务。识别候选仍按项目及财务权限共享，API Key不随结果共享。

修改、删除配置或切换手工模式，会停止本人的未完成任务；旧配置版本的结果不会入库。已经发送给服务商的请求无法撤回，也可能已计费。切换手工模式会清除当前Key，重新启用时需再次输入。未配置、加密主密钥缺失或服务故障不影响手工录入。

## 技术部首次启用

服务器需要设置的是**加密主密钥**，不是某位成员的AI Key。应用使用cryptography的Fernet认证加密；密文绑定用户UUID、配置版本、协议、接口地址和模型，复制密文到其他用户或篡改地址不能正常解密。地址及模型属于配置元数据，数据库中可见；API Key为密文。

1. 按部署流程升级依赖或构建新镜像，先停止旧worker和scheduler，备份数据库与media。迁移`0006_personal_ai_credentials`会移除旧全站配置，停止没有发起者身份的旧排队任务，不将全站Key分配给任何成员。
2. 在服务器的私有终端生成主密钥。Compose已build、`.env`已有常规部署参数时，可运行：

```bash
docker compose -p chihu-prod build web
docker compose -p chihu-prod run --rm --no-deps web python manage.py generate_ai_encryption_key
```

3. 将唯一输出写入该环境私有`.env`的`EATFUL_AI_ENCRYPTION_KEYS`。不要把真实值贴到聊天、工单、公开日志或仓库；生产与测试分别生成。输出仅此一次，后续复用，不要每次重启重新生成。

```dotenv
EATFUL_AI_ENCRYPTION_KEYS=填写本环境生成的主密钥
EATFUL_AI_ALLOWED_HOSTS=api.openai.com,open.bigmodel.cn,generativelanguage.googleapis.com,api.siliconflow.cn,dashscope.aliyuncs.com,dashscope-intl.aliyuncs.com
```

4. 在维护窗口按以下次序启动，让数据库迁移及加密主密钥对所有进程一致生效；B测试时将项目名替换为固定的`chihu-test`。

```bash
docker compose -p chihu-prod stop worker scheduler
docker compose -p chihu-prod up -d --build --force-recreate db web
docker compose -p chihu-prod exec web python manage.py check --deploy
docker compose -p chihu-prod exec web python manage.py showmigrations core
docker compose -p chihu-prod up -d --build --force-recreate worker scheduler
```

应用启动仍自动执行迁移。`core.W001`表示未配置主密钥，只能手工录入；`core.E001`表示主密钥格式错误。原有`DJANGO_SECRET_KEY`和数据库密码继续独立保管，不能用它们替代AI加密主密钥。旧`EATFUL_VISION_API_KEY/MODE/MODEL/API_URL`不再读取，请从部署环境移除不再使用的全站凭证。

服务器拒绝非HTTPS、非443端口、含用户名密码、查询参数或片段的地址，以及IP地址和未获准的域名。域名解析后只允许公网IP，连接固定到已验证的地址并校验证书及主机名，不跟随重定向、不读取环境代理。新增可信的自建公网服务时，技术部可在`EATFUL_AI_ALLOWED_HOSTS`追加精确域名并重建web与worker；不要填通配符、本机、内网或云元数据地址。此变量只是服务器出站服务名单，不是社团成员账号白名单。

## 备份与主密钥轮换

数据库备份中含加密后的API Key，但**备份脚本不包含主密钥或.env**。必须将当前及必要的历史加密主密钥另行加密、限权保存。丢失主密钥将无法解密对应凭证，成员须重新配置；项目和图片可按正常备份恢复。删除当前Key不会清除已存在的历史备份，备份也应按保留策略处理。不要把生产密钥复制到普通测试环境；恢复生产备份的演练需在受限隔离环境进行。

轮换步骤：进入维护窗口，停止识别worker及设置修改；备份数据库和旧主密钥；生成新主密钥，将`.env`改为`新主密钥,旧主密钥`；重建web以加载密钥环，执行下面的重加密命令。

```bash
docker compose -p chihu-prod exec web python manage.py rotate_ai_keys
```

成功后保留仅新主密钥的`.env`，重建web、worker、scheduler，验证自己的设置可用后恢复业务。命令整批事务执行，不输出API Key，不改变个人配置版本；无法解密时整批回滚。先用B验证流程；旧备份仍需要当时的旧主密钥，应单独保存至相应备份淘汰。不能只删除旧主密钥而跳过重加密。

## 安全边界与验收

普通成员及站点管理页面没有读取他人密钥或配置的入口，后台任务管理仅查看，不允许管理员改写任务发起者和配置版本。任务只保存图片ID、发起者和配置版本，错误信息统一处理，不保存服务商原始报错、请求头或Key。AI响应意外回显的已知完整Key会被替换后才进入候选。

此方案需要信任服务器维护者：服务器必须解密才能调用，控制服务器或加密主密钥的人理论上仍可接触凭证，不属于对服务器维护者的端到端加密。生产必须关闭Debug、启用HTTPS，反向代理和监控不得记录请求体或Authorization头。成员应使用独立、限额、可随时撤销的Key，并核实所填服务地址可信。

本轮本地PostgreSQL全量检查98项通过（22.33秒），包括账号隔离、不回显、无全站Key回退、版本失效、删除在途结果、密文绑定、主密钥轮换、错误脱敏，以及域名、内网IP与重定向拦截。网络测试使用模拟响应，未以真实成员Key访问第三方AI；生产需按两个普通账号与一个管理员分别验证，并用获准的菜单图片测试识别效果。

旧版部署报告及导出PDF描述的是2026年10月4日全站配置基线；升级本版本以本说明、当前`.env.example`及迁移为准。

实现依据：[cryptography Fernet](https://cryptography.io/en/latest/fernet/)；[OWASP SSRF防护](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)。
