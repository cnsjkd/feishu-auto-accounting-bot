# 飞书 Bitable 自动记账系统

## 第一步：整体技术方案

### 1. 系统目标

第一版实现一个稳定可运行的最小闭环：

`飞书机器人接收自然语言记账消息 -> GPT/qwen3.7-plus 解析结构化账单 -> 生成唯一去重 ID -> 查询 Bitable 去重 -> 写入飞书多维表格 Bitable`

第一版不引入复杂 Web 框架，Python 3.10+，外部依赖仅 `requests`。

### 2. 系统架构

```text
用户
  |
  | 在飞书机器人会话输入：今天中午美团点外卖花了38.5
  v
飞书开放平台事件订阅
  |
  | HTTP POST /feishu/events
  v
本系统事件接收服务（http.server）
  |
  | 提取文本消息
  v
GPTBillParser
  |
  | 调用 GPT/qwen3.7-plus，返回结构化 JSON
  v
AccountingService
  |
  | 标准化字段 + 生成唯一去重 ID
  v
FeishuClient
  |
  | 1. 获取 tenant_access_token
  | 2. records/search 查询唯一去重 ID
  | 3. 不重复则写入 records
  v
飞书多维表格 Bitable
```

### 3. 数据流

1. 用户发送文本消息到飞书机器人，例如：`今天中午美团点外卖花了38.5`。
2. 飞书开放平台通过事件订阅把消息推送到本系统 `/feishu/events`。
3. 事件服务解析飞书 payload，提取文本内容和 message_id。
4. 系统调用 GPT/qwen3.7-plus，将自然语言解析为账单字段。
5. 系统做字段标准化：类型、分类、支付方式、金额、日期时间等。
6. 系统生成 `唯一去重 ID`。
7. 系统用 `records/search` 在 Bitable 中查询该 ID 是否已存在。
8. 不存在则调用 Bitable `records` 创建记录；存在则跳过写入。

### 4. Bitable 字段设计

推荐在 Bitable 建表时使用以下字段名。字段名必须和代码一致。

| 字段名 | 建议类型 | 说明 |
| --- | --- | --- |
| 日期 | 文本或日期 | 第一版用 ISO 日期字符串，如 `2026-07-02`，便于兼容 |
| 时间 | 文本 | `HH:MM:SS` |
| 类型 | 单选或文本 | 收入 / 支出 |
| 金额 | 数字 | 保留两位小数 |
| 币种 | 文本 | 默认 CNY |
| 分类 | 单选或文本 | 餐饮 / 交通 / 购物 / 住宿 / 工资 / 报销 / 其他 |
| 支付方式 | 单选或文本 | 微信 / 支付宝 / 银行卡 / 现金 / 其他 |
| 商户或对象 | 文本 | 如美团外卖、滴滴、公司 |
| 备注 | 文本 | 补充信息 |
| 原始文本 | 文本 | 用户原始消息 |
| 记录来源 | 文本 | 飞书机器人或其他来源 |
| 创建时间 | 文本或日期时间 | 系统创建时间 |
| 唯一去重 ID | 文本 | 用于重复判断，建议设为唯一约束辅助字段 |

> 说明：第一版为了降低 Bitable 字段格式兼容问题，日期、时间、创建时间可以先用文本字段；后续可以迁移为日期字段并写入毫秒时间戳。

## 第二步：推荐项目目录结构

```text
auto_accounting_feishu/
  README.md
  requirements.txt
  .env.example
  src/
    config.py              # .env 读取与配置校验
    models.py              # Bill 数据模型与 Bitable 字段映射
    utils.py               # JSON 提取、字段标准化、去重 ID
    gpt_parser.py          # GPT/qwen3.7-plus 账单解析
    feishu_client.py       # tenant_access_token、Bitable 查询和写入
    service.py             # 业务编排：解析 -> 去重 -> 写入
    feishu_event.py        # 飞书机器人事件接收 HTTP 服务
    main.py                # 主程序入口
  tests/
    test_local_parse.py    # 本地轻量测试，不调用外部 API
    test_event_payload.py  # 飞书事件文本提取测试
  scripts/
    create_bitable_fields.py # 可选：创建 Bitable 字段
```

## 第三步：核心代码说明

核心代码已经在 `src/` 中实现：

- `config.py`
  - 从 `.env` 读取敏感信息。
  - 必填项：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`BITABLE_APP_TOKEN`、`TABLE_ID`。
  - 模型配置支持 `QWEN_*`、`OPENAI_*`，也兼容旧版 `GPT_*`。
- `feishu_client.py`
  - `get_tenant_access_token()` 自动获取并缓存 `tenant_access_token`。
  - `has_dedupe_id()` 使用 Bitable `records/search` 查询重复记录。
  - `create_bitable_record()` 写入 Bitable。
  - `save_bill_once()` 实现去重写入。
- `gpt_parser.py`
  - 调用兼容 OpenAI Chat Completions 的 GPT/qwen 接口。
  - 强制模型输出 JSON 对象。
  - 做返回结构校验和标准化。
- `feishu_event.py`
  - 使用标准库 `http.server` 接收飞书事件。
  - 支持飞书 URL verification 的 `challenge` 返回。
  - 支持文本消息解析。
- `service.py`
  - 业务闭环：自然语言 -> 结构化账单 -> 去重 -> 写入 Bitable。

### .env 配置模板

复制 `.env.example` 为 `.env`，填写：

```env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BITABLE_APP_TOKEN=bascnxxxxxxxxxxxxxxxx
TABLE_ID=tblxxxxxxxxxxxxxx
# 可选：机器人回复中附上的多维表格查看链接
BITABLE_VIEW_URL=https://xxx.feishu.cn/base/<BITABLE_APP_TOKEN>?table=<TABLE_ID>&view=<VIEW_ID>

LLM_PROVIDER=fallback
QWEN_API_KEY=sk-xxxxxxxxxxxxxxxx
QWEN_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.7-plus
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

SERVER_HOST=0.0.0.0
SERVER_PORT=18000
REQUEST_TIMEOUT=30
LLM_TIMEOUT=60
LLM_MAX_RETRIES=2
```

Bitable 参数获取方式：

- 先在飞书中新建或打开一个「多维表格」，不是普通云表格。
- 浏览器地址一般形如：`https://xxx.feishu.cn/base/<BITABLE_APP_TOKEN>?table=<TABLE_ID>`。
- `/base/` 后面的 `bascn...` 或 `base...` 填入 `BITABLE_APP_TOKEN`。
- `table=` 后面的 `tbl...` 填入 `TABLE_ID`。
- 如果地址栏没有 `table=`，先点击要写入的那张数据表，再复制完整地址。
- `BITABLE_VIEW_URL` 可直接填浏览器里能打开的多维表格链接，用于机器人记账后回复给你，方便点开核对。
- 如果链接是 `https://xxx.feishu.cn/wiki/<WIKI_TOKEN>?table=tbl...`，说明多维表格放在知识库里，不能直接从地址拿到 `BITABLE_APP_TOKEN`；请运行：

```bash
python scripts/resolve_wiki_bitable_token.py "https://xxx.feishu.cn/wiki/<WIKI_TOKEN>?table=tbl..." --write-env
```

该脚本会调用飞书 wiki 节点接口，把 wiki token 转换成真正的 Bitable app token，并写入 `.env`。

如果脚本提示缺少 wiki 权限，请在飞书开放平台「权限管理」里额外开通任一权限：

- `wiki:node:read`：查看知识空间节点信息，推荐最小权限。
- `wiki:wiki:readonly`：查看知识库。
- `wiki:wiki`：查看、编辑和管理知识库，权限较大，不建议第一版使用。

开通权限后需要重新发布/安装应用，再重跑上述脚本。

GPT/qwen API Key 获取方式：

- 如果使用阿里云 DashScope：进入 `https://dashscope.console.aliyun.com/`。
- 打开「API-KEY 管理」，创建并复制 `sk-...`。
- 填入 `QWEN_API_KEY`，`QWEN_API_BASE` 使用兼容 OpenAI Chat Completions 的 `/compatible-mode/v1` 地址。
- 如果同时使用 OpenAI，把 OpenAI Key 填入 `OPENAI_API_KEY`。
- `LLM_PROVIDER=fallback` 表示优先调用 Qwen，失败后自动切换 OpenAI。
- 如果你的模型服务不支持 `qwen3.7-plus`，把 `QWEN_MODEL` 改成控制台实际可用的模型名。

## 第四步：运行命令和部署方式

### 1. 安装依赖

```bash
cd auto_accounting_feishu
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

如果是 Linux/macOS：

```bash
cd auto_accounting_feishu
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
copy .env.example .env
```

然后编辑 `.env`，填入真实凭证。

### 3. 本地轻量测试

不调用外部 API：

```bash
python tests/test_local_parse.py
python tests/test_event_payload.py
```

### 4. 直接用命令行写入一笔账

会真实调用 GPT/qwen 和飞书 Bitable：

```bash
python src/main.py --text "今天中午美团点外卖花了38.5"
```

### 5. 启动飞书机器人事件服务

```bash
python src/main.py --serve
```

服务地址：

```text
POST http://你的域名或公网地址/feishu/events
GET  http://你的域名或公网地址/health
```

### 6. 本地开发暴露公网地址

飞书事件订阅要求公网 HTTPS 地址。开发阶段可用内网穿透工具，例如：

```bash
ngrok http 18000
```

然后在飞书事件订阅中配置：

```text
https://xxxx.ngrok-free.app/feishu/events
```

生产部署建议：

- 轻量云服务器 + Docker Compose 常驻运行。
- 反向代理：Nginx/Caddy，开启 HTTPS。
- 使用环境变量或 `.env.production` 管理密钥。
- 定期查看服务日志，捕获 GPT 解析失败、Bitable 权限失败等异常。

### 7. Docker 长期运行部署

复制生产配置模板：

```bash
cp .env.production.example .env.production
```

编辑 `.env.production` 后启动：

```bash
cd deploy
docker compose up -d --build
```

服务会通过 `restart: unless-stopped` 常驻运行；容器异常退出后会自动重启。建议用 Caddy/Nginx 把固定域名反向代理到本机 `18000`，然后在飞书事件订阅里填：

```text
https://你的域名/feishu/events
```

如果使用 Caddy，可参考 `deploy/Caddyfile.example`。

### 8. Railway 部署（无自有域名方案）

如果没有自己的域名，可以用 Railway 自带的 HTTPS 域名长期运行服务。

部署前提：当前代码需要先提交并推送到 GitHub，然后在 Railway 从仓库导入。

1. 在 Railway 新建 Project，选择 `Deploy from GitHub repo`。
2. 选择仓库：`cnsjkd/feishu-auto-accounting-bot`。
3. Railway 会读取根目录 `Dockerfile` 和 `railway.json` 自动部署。
4. 在服务 Variables 中配置环境变量，至少包括：

```text
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BITABLE_APP_TOKEN=bascnxxxxxxxxxxxxxxxx
TABLE_ID=tblxxxxxxxxxxxxxx
BITABLE_VIEW_URL=https://xxxx.feishu.cn/base/xxxx?table=tblxxx&view=vewxxx
LLM_PROVIDER=fallback
QWEN_API_KEY=sk-xxxxxxxxxxxxxxxx
QWEN_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.7-plus
REQUEST_TIMEOUT=30
LLM_TIMEOUT=60
LLM_MAX_RETRIES=2
```

5. 创建 Railway Volume，并挂载到：

```text
/app/data
```

这样 SQLite 的用户绑定、月度 table 映射、事件幂等和月报记录会持久保存。也可以显式设置：

```text
ACCOUNTING_DB_PATH=/app/data/accounting.db
```

6. 打开 Railway 服务的 Public Networking，生成公开 HTTPS 域名，例如：

```text
https://feishu-auto-accounting-bot-production.up.railway.app
```

7. 飞书开放平台事件订阅请求地址填写：

```text
https://feishu-auto-accounting-bot-production.up.railway.app/feishu/events
```

8. 保存并校验通过后，在飞书机器人聊天中发送：

```text
绑定账本 <你的飞书多维表格链接>
```

注意：Railway 会自动注入 `PORT`，程序会优先监听该端口；本地仍可使用 `SERVER_PORT=18000`。

### 9. 多用户接入和数据隔离

每个飞书用户第一次使用时，在机器人聊天里发送：

```text
绑定账本 https://xxx.feishu.cn/base/<BITABLE_APP_TOKEN>?table=<TABLE_ID>&view=<VIEW_ID>
```

或发送 wiki 多维表格链接：

```text
绑定账本 https://xxx.feishu.cn/wiki/<WIKI_TOKEN>?table=<TABLE_ID>&view=<VIEW_ID>
```

系统会用飞书 `tenant_key + open_id` 作为用户身份，把该用户绑定到自己的 Bitable app。之后：

- 用户 A 的账单只写入 A 绑定的表格。
- 用户 B 的账单只写入 B 绑定的表格。
- 同一机器人服务多个飞书用户时，数据不会混在一起。
- 本地 SQLite 数据库保存在 `data/accounting.db`，用于保存用户绑定、月度 table 映射、事件幂等和月报发送记录。

其他命令：

```text
账本状态
帮助
```

### 10. 月度表和自动月报

系统按账单日期自动选择月份：

```text
2026-07 -> 写入名为 2026-07 的 table
2026-08 -> 写入名为 2026-08 的 table
```

如果当月 table 不存在，系统会在用户绑定的 Bitable app 里自动创建同名 table，并自动创建记账字段。

服务启动后会开启内置定时任务：每月 1 日自动统计上个月数据，并私聊对应用户发送月报。月报包括：

- 总收入
- 总支出
- 结余
- 支出分类统计
- 最大单笔支出
- 本月记录笔数

也可以手动验证指定月份月报：

```bash
python src/main.py --run-monthly-summary 2026-07
```

如果需要实际发送给用户，加：

```bash
python src/main.py --run-monthly-summary 2026-07 --send-summary
```

## 第五步：飞书开放平台权限配置说明

### 1. 创建飞书企业自建应用

1. 进入飞书开放平台。
2. 创建企业自建应用。
3. 获取 `App ID` 和 `App Secret`，写入 `.env`。

### 2. 开启机器人能力

1. 在应用能力中启用「机器人」。
2. 在「权限管理」中添加机器人消息权限：
   - `im:message.p2p_msg:readonly`：读取用户发给机器人的单聊消息。
   - `im:message.group_at_msg:readonly`：读取群聊中 @ 机器人的消息，群聊记账才需要。
   - `im:message:send_as_bot`：以机器人身份发送消息，用于记账成功/失败后在聊天里回复结果。
3. 进入「版本管理与发布」，创建版本并发布/安装到企业。
4. 将机器人添加到单聊或群聊中。
5. 如果飞书聊天页显示「暂时无法给该机器人发消息」，通常说明机器人能力未添加、应用未发布安装，或消息权限未开通；按以上步骤处理后重新打开机器人会话。

### 3. 配置事件订阅

1. 开启事件订阅。
2. 请求地址填写：`https://你的域名/feishu/events`。
3. 飞书会发送 `url_verification`，本系统会自动返回 `challenge`。
4. 订阅消息事件，至少需要文本消息相关事件，例如接收机器人消息事件。

### 4. 配置 Bitable 权限

应用需要具备多维表格相关权限，第一版最小权限建议：

- `base:app:read` 或 `base:app:readonly`：查看多维表格基本信息。
- `base:record:retrieve`：查询多维表格记录，用于根据唯一去重 ID 查重。
- `base:record:create`：新增多维表格记录，用于写入账单。

如需运行 `scripts/create_bitable_fields.py` 自动建字段，再额外添加：

- `base:field:create`：新增多维表格字段。
- `base:field:update`：更新多维表格字段。

不建议全选权限；删除记录、删除多维表格、复制多维表格、多维表格插件、自动化插件等权限第一版不需要。

配置权限后，需要发布应用版本，并在企业内重新安装或授权。旧版本安装不会自动拥有新权限。

### 5. 授权应用访问目标 Bitable

常见方式：

1. 打开目标飞书多维表格。
2. 将自建应用添加为协作者，或通过飞书开放平台授权应用访问该文档。
3. 确保应用对该 Bitable 有读取和编辑权限。
4. 如果文档在知识库 wiki 中，还要确认知识库/节点没有限制该应用访问。

可以运行下面的诊断脚本确认当前应用身份是否真正能写入：

```bash
python scripts/diagnose_bitable_access.py
```

如果输出类似：

```text
[OK] 读取字段列表: 字段=['文本']
[FAIL] 新增测试记录: HTTP 403; code 91403
```

说明应用可以读字段，但不能新增记录。通常需要重新检查 `base:record:create`、发布/重新安装应用，以及目标文档是否把自建应用授予可编辑权限。

当前代码带有临时兼容模式：如果表里只有默认 `文本` 字段，会把完整账单压成一段文本写入 `文本` 列；但这也要求新增记录权限正常。长期建议仍按「Bitable 字段设计」补齐结构化字段。

## 第六步：后续扩展方案

### 1. 截图 OCR

扩展入口：新增 `src/ocr_parser.py`。

流程：

```text
图片/截图 -> OCR 提取文本 -> GPT/qwen 结构化账单 -> 统一 AccountingService 写入
```

建议字段扩展：

- 附件 URL
- OCR 原文
- OCR 置信度
- 票据来源

### 2. 手动输入页面

可以新增极简 HTTP 表单或命令行交互：

```text
手动表单 -> AccountingService.handle_text() 或直接提交结构化字段 -> 去重 -> 写入 Bitable
```

第一版已经把核心业务写在 `AccountingService`，后续入口可以复用。

### 3. API 回调

新增 `/api/bills` 接口，接收外部系统 JSON：

```json
{
  "text": "昨晚打车 42 元 支付宝",
  "source": "api:xxx"
}
```

或接收结构化账单后直接生成 `Bill`。

### 4. 统计报表

可以从 Bitable 拉取记录，按以下维度统计：

- 月度收入、支出、结余。
- 分类支出排行。
- 支付方式占比。
- 商户 Top N。
- 异常大额支出。

第一阶段可直接在 Bitable 视图和仪表盘中做；第二阶段可用 Python 生成日报/周报并推送飞书。

### 5. 异常提醒

规则示例：

- 单笔支出超过 1000 元提醒。
- 同一商户 10 分钟内多次支出提醒。
- GPT 解析置信度低或金额缺失提醒。
- 写入 Bitable 失败自动重试并告警。

### 6. 更强去重策略

第一版使用字段哈希生成去重 ID。后续可升级为：

- 飞书 `message_id` + 文本哈希。
- 外部支付平台订单号。
- OCR 票据号。
- 时间窗口内相似账单 fuzzy match。

## 稳定性建议

1. 先手动在 Bitable 建好字段，保证字段名完全一致。
2. 单选字段的选项要包含代码中的枚举值，否则飞书写入可能失败。
3. 如果 Bitable 写入失败，优先检查应用权限和是否已把应用授权到目标 Bitable。
4. 若 qwen 模型名不可用，可在 `.env` 中修改 `QWEN_MODEL`。
5. 生产环境不要把 `.env` 提交到代码仓库。
