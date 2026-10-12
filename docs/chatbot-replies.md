# CRM 客服经 ai_chatbot_demo 回复：第 2 阶段设计

> 状态：**已批准**（Kelvin 2026-10-13，原话「Q1–Q7 都按建议，批准，开 PR 登记 012 到 019」）。第 2 阶段任务的设计闸门引用本文件所在的 PR kelvinpang90/acuven_crm_demo#28；设计一变，批准作废。
> 第一至十三节如上已批准；**第十四节**是登记 CRM-TASK-012 至 020 时按设计预审补的（含把原 019 拆成 019、020，第八至十节同步改了），待 Kelvin 在登记 PR 上批准。
> 上位设计：`acuven_hub` 的 `CHATBOT_DEMO_CRM_MULTIAGENT_2026-10-12.md`（下称 MULTI，D1–D11），现行索引 `DECISIONS.md` 的 J-04、A-18。
> 第 1 阶段契约：[chatbot-events.md](chatbot-events.md)（已批准）。本文件只在它上面**增补**，不改它已定的规则；增补处集中在第四节。
> 这里是两个仓库共用的接口契约：`ai_chatbot_demo` 的发送与交还接口按本文件实现。
> 依据（2026-10-12 只读核对）：本仓库 master（CRM-TASK-006 已合并，007–011 未实施，写到的第 1 阶段行为以已批准设计与已登记验收标准为准）；
> `ai_chatbot_demo` master（批次 08 的 40.0–40.2 已实现，接管状态机、控制台、发送、事件队列的现状见第十三节）。

## 一、第 2 阶段做到什么

- 客服在 CRM 收件箱回复 chatbot 进来的访客：CRM 经 chatbot 的内部发送接口发出；发出即开始或续期接管，
  该访客的 AI 静默 24 小时，每次客服回复重新计时，期满后访客再来消息由 AI 回（D2）。
- CRM 存这条出站消息：`author_kind=agent`，并记**实际发送人**（新字段 `sent_by`，与负责人 `assigned_to` 分开）；
  送达状态沿用第 1 阶段的 `delivery_status` 事件。
- 「交还 AI」：CRM 经 chatbot 结束接管，等同控制台交还。
- 删掉 CRM 自己的 Meta webhook、Graph 直连发送、网关入站与出站及其配置；Kelvin 在 Meta 后台作废沙盒号 token（D6）。
- `is_gateway`：先去掉标记的全部用处，再删列。
- **不做**（MULTI 第五节已排进第 3 阶段）：窗口外模板消息、媒体、收件箱实时刷新、会话认领 / 转派与锁。
  只有 BSUID 的访客不能回复（D10：第一期回复只支持有手机号的访客），但可以「交还 AI」。

## 二、本文按这些取舍写（待批准；需要拍板的单列在第十二节）

| # | 问题 | 建议 | 理由 |
|---|---|---|---|
| R1 | 发送失败怎么记 | **明确没发出**：不入库，返回错误，文字留在输入框（沿用 CRM-TASK-004）。**结果不明**（超时、连接中断）：消息留在库里标「未确认送出」，可重试；重试用同一个幂等键，chatbot 去重，不重复发给访客；重试失败不删（第一次的结果仍不明） | 收件箱不显示没发出的消息；结果不明时删掉会丢掉其实已发出的消息 |
| R2 | 24 小时客服窗口 | CRM 发送前检查：该联系人最后一条 WhatsApp 入站消息在 24 小时内，否则 409 拒绝、不调用 chatbot | Meta 对窗口外的普通文本先收下、再异步报失败（131047），客服当时看不出；也挡住从 demo 号向从没来过消息的联系人（手工新建、AutoCount 同步的）主动发消息 |
| R3 | 接管状态谁写 | CRM 不自己改接管字段。chatbot 的发送 / 交还响应带上这次接管变化，与随后推送的 `handover_changed` 事件**同一个 `occurred_at`**；CRM 写入响应时要求**严格晚于**已存时间，事件仍按第 1 阶段「不早于」写入 | 页面立刻看到「接管中」（收件箱不自动刷新），又只有一条写入规则、一个时间来源 |
| R4 | 原有 2 个 `is_gateway` 联系人 | 去掉标记成为普通联系人；先停用代码、再取消模型映射、最后删列，分三次部署（第十四节第 12 条） | 网关已停用、入口在本阶段删掉，「是否经网关」不再有意义；CRM 是 demo，这 2 个与 chatbot 访客同样是主数据（P4 同理） |
| R5 | CRM 自己的 Meta webhook 吞异常返回 200、密钥未配时验签放行 | 不修，入口整体删掉 | 已定撤回 CRM-TASK-005；8 月底后零流量；入站可靠性由 chatbot 的事件队列保证 |
| R6 | 会话认领 / 转派与并发保护（任务二缺口 3） | 本阶段不做；靠现有归属校验（D7）加 `sent_by` 显示是谁回的 | MULTI 第四节已定「负责人与经理同时回第一期接受，认领与锁放第 3 阶段」；转派已能通过改负责人完成、历史消息随行（任务二 2.1） |
| R7 | 模板与媒体 | 第 3 阶段 | MULTI 第五节已定；R2 让窗口外的情况当场可见 |
| R8 | 发给 chatbot 的员工信息 | 员工 id 与显示名；chatbot 写进对话历史与审计，控制台能看出是 CRM 哪位客服回的 | 控制台与 CRM 共用接管（D8），控制台需要知道谁在接管；显示名是 CRM 用户名，不是顾客数据 |

## 三、流程

```
客服点发送
 → CRM：归属、号码、24 小时窗口校验 → 先存一条「未确认」的出站消息（消息 id = 幂等键）
 → POST chatbot /internal/crm/send（签名）
     chatbot：幂等键占位 → Graph 发出 → 记 wa_message_id
              → 开始或续期接管（推 handover_changed）→ 写对话历史与审计 → 推 agent_reply → 返回
 → CRM：补 external_id、送达状态记 sent、写入接管状态 → 页面显示已发出、接管中
之后：Meta 回执 → chatbot → delivery_status 事件 → CRM 只升不降（第 1 阶段规则）

客服点「交还 AI」
 → CRM：归属校验 → POST chatbot /internal/crm/handback（签名）
     chatbot：结束接管（推 handover_changed）→ 返回
 → CRM：写入接管状态
```

## 四、对第 1 阶段契约的增补

1. **新事件类型 `agent_reply`**（chatbot → CRM，走第 1 阶段同一个接口与队列）：附加字段
   `message: {wa_message_id, body}`、`crm_message_id`（CRM 发送请求里的 `request_id`）。公共部分照旧（`visitor` 必填，CRM 处理时不用）。
   chatbot 在 Graph 返回成功后**第一件事**就入队（见第五节第 4 步），所以它几乎总是排在这条消息的 `delivery_status` 之前；
   不能保证：回执由 webhook 的后台任务并发入队，Meta 的回执若在 chatbot 入队之前就到了，CRM 处理回执时这一行还没有 `external_id`，
   回执被 `ignored`。丢的是 `sent` 无害（同步响应会记 `sent`）；丢的是 `failed` 则这条显示「已发出」，接受（见第十三节）。
   CRM 处理规则见第六节。CRM-TASK-008 的 `type` 校验要加上它。
2. CRM 经 chatbot 发出的消息，chatbot **不推** `console_reply`（它们不经控制台），只推 `agent_reply`。
3. 接管时长：chatbot 的 `HANDOVER_IDLE_SECONDS` 改为 86400（D2）。接管只有一套状态机，控制台接管同样变成 24 小时（D8）。
4. 第 1 阶段契约第一节「第 1、2 阶段之间客服回复必然发不出去」到本阶段 CRM-TASK-015 部署为止。

## 五、接口契约（CRM → chatbot）

两个接口都在 chatbot 的 `/internal/` 下，只在共享 Docker 网络内可达。chatbot 的前端 nginx 与边缘 nginx 都不转发 `/internal/`
（读两边仓库里的配置确认，未上 VPS 核对）。

### 鉴权

- 与第 1 阶段同一种签名：请求头 `X-Acuven-Timestamp`（Unix 秒）与 `X-Acuven-Signature: sha256=<hex>`，
  签名 = HMAC-SHA256(密钥字符串的 UTF-8 字节, `<timestamp>.<原始请求体字节>`)；与对方当前时间相差超过 300 秒拒绝；常数时间比较。
- **另一把密钥**：CRM 配 `CHATBOT_SEND_SECRET`（签名用）；chatbot 配 `CRM_SEND_SECRET` 与可选的 `CRM_SEND_SECRET_PREVIOUS`（验签，任一把过即可，轮换用）。
  必须与第 1 阶段的 `CHATBOT_EVENTS_SECRET` 不同值（由 Kelvin 生成时保证，代码不比较）。
- chatbot 未配 `CRM_SEND_SECRET`：两个接口一律 503，不另设开关。

### `POST /internal/crm/send`

请求体：

```json
{
  "request_id": "CRM 消息 id（UUID），幂等键",
  "visitor": {"phone": "60123456789", "bsuid": "MY.xxxx 或 null"},
  "text": "去掉首尾空白后 1–4096 字符",
  "agent": {"id": "CRM 用户 id", "name": "CRM 用户显示名"}
}
```

`phone` 必填（D10），是 CRM 规范化后的号码；`bsuid` 只用于找访客档案。

chatbot 的处理：

1. 验签、校验请求体（不合法 422）。
2. 幂等：Redis 键 `chat:crm_send:<request_id>`。
   - 已存 `wa_message_id`：不再发送、不动接管，返回 200 `duplicate`，带同一个 `wa_message_id`，`handover` 为 `null`
     （首次之后可能已经交还，不能拿当前时间冒充那次变化的时间）。
   - 是「发送中」占位：409 `in_progress`。
   - 不存在：`SET NX` 写「发送中」占位，120 秒过期。
3. 发给 `phone`（Graph 文本消息）：
   - Meta 返回 HTTP 4xx：删掉占位，返回 502 `rejected`，带 Meta 的 `error_code` 与 `error_title`。
   - Meta 返回 HTTP 5xx、网络错误或超时（不能证明 Meta 没收下）：占位留到过期，返回 504 `unknown`。
4. 发出成功，按这个顺序（前两步紧跟 Graph 返回，后面各步各自捕获异常、只记 ERROR，不改变响应结果）：
   1. 键改存 `wa_message_id`，48 小时过期（CRM 只重试 24 小时内创建的消息，见第六节）；
   2. 入队 `agent_reply`；
   3. 找访客档案（先按号码，再按 BSUID；都没有就按号码建一个最小档案）：未在接管中（`active()` 为假）调 `begin`，否则调 `touch`；
      两者都推 `handover_changed`，`occurred_at` 用这一次变化的时间，并在响应里返回同一个值；这一步失败时响应的 `handover` 为 `null`；
   4. 写对话历史与审计（人工回复，标 CRM 客服与显示名），AI 恢复时有上下文、控制台看得到。
   - 不计入每日条数上限（现状出站本来就不计）。

响应（顶层字段，不套外壳）：

| 情况 | 状态码 | 体 | CRM 怎么办 |
|---|---|---|---|
| 发出 | 200 | `{"result": "sent", "wa_message_id", "handover": {"active": true, "expires_at", "occurred_at"} 或 null}` | 已发出 |
| 重复 | 200 | `{"result": "duplicate", "wa_message_id", "handover": null}` | 已发出 |
| 同一 `request_id` 正在发 | 409 | `{"result": "in_progress"}` | 结果不明 |
| Meta 拒绝（4xx） | 502 | `{"result": "rejected", "error_code", "error_title"}` | 明确没发出 |
| 不知道 Meta 收没收（Meta 5xx、网络错误、超时） | 504 | `{"result": "unknown"}` | 结果不明 |
| 请求体不合法 | 422 | | 明确没发出（CRM 的 bug，记 ERROR） |
| 签名或时间戳不对 | 401 | | 明确没发出（多半密钥配错，记 ERROR） |
| 未配密钥 | 503 | | 明确没发出 |

### `POST /internal/crm/handback`

请求体：`{"request_id": "UUID，只进日志", "visitor": {"phone", "bsuid"}, "agent": {"id", "name"}}`，`phone` 与 `bsuid` 至少一个非空。

chatbot：按号码、再按 BSUID 找档案；找到且 `handover_since` 非零就调 `end`（推 `handover_changed`，与响应同一个 `occurred_at`），
返回 200 `{"result": "ended", "handover": {"active": false, "expires_at": null, "occurred_at"}}`；
没有档案或本来就没接管，返回 200 `{"result": "not_active", "handover": null}`，不推事件。天然幂等，不需要幂等键。
不给访客发任何消息（与控制台交还一致）。401 / 422 / 503 同上。

## 六、CRM 的处理规则

### 发送（`POST /api/messages/whatsapp/send`，路径与请求体不变）

按顺序校验，任一不过就返回、不存、不调用 chatbot：

1. 归属：`may_access_contact`（D7，现有），否则 403 `NOT_ASSIGNED`。
2. 号码：联系人号码能被 CRM-TASK-007 的函数规范化，否则 400 `NO_PHONE`（含只有 BSUID 的访客）。
3. 窗口：该联系人最后一条 `channel=whatsapp`、`direction=inbound` 的消息，其时间（第 1 阶段存的是事件的 `occurred_at`）在 24 小时内，
   否则 409 `WINDOW_CLOSED`，带窗口关闭的时间。
4. 正文：去掉首尾空白后 1–4096 字符，否则 422。
5. 配置：`CHATBOT_SEND_SECRET` 未配，503 `NOT_CONFIGURED`。

然后：

6. 先存并提交一条消息：新 UUID 作 `id`，`channel=whatsapp`、`direction=outbound`、`author_kind=agent`、`sent_by`=当前用户、
   `assigned_to`=联系人当前负责人、发件人 `ai_chatbot_demo`、收件人为规范化号码、已读、`external_id` 与 `delivery_status` 为空。
   **先提交再调用**：chatbot 推回的 `agent_reply` 与回执一定找得到这一行。
7. 调用 chatbot 发送接口，`request_id` = 这条消息的 `id`，总时限 30 秒；超时走下面「结果不明」，由幂等键与 `agent_reply` 兜住。
8. 按结果：
   - **已发出**（200）：用条件更新补写，不按内存里的旧值判断（事件可能已先写入）：`external_id` 为空时补上 `wa_message_id`，
     `delivery_status` 为空时记 `sent`；响应的 `handover` 非空时按下面「接管写入」写入；返回 200 与这条消息（重新读取）。
   - **明确没发出**（502 `rejected`、401、422、503、连接建立失败）：删掉这条消息；返回 502 `SEND_FAILED`，`rejected` 时带 Meta 的错误码与标题。
   - **结果不明**（409 `in_progress`、504 `unknown`、其他 5xx、读超时、连接中途断开）：留着这条消息（`external_id` 为空 = 未确认送出）；返回 202 `UNCONFIRMED` 与这条消息。

### 重试（`POST /api/messages/{message_id}/whatsapp/retry`，新增）

- 只对 `author_kind=agent` 且 `external_id` 为空的消息；不存在、不满足、或无权访问其联系人，一律按现有「不存在 / 无权」的响应处理（不泄露存在与否）。
- 号码、窗口、配置按上面第 2、3、5 条再查一次；另外消息创建时间须在 24 小时内（chatbot 的幂等键 48 小时过期，超过就可能重发），否则 409 `RETRY_EXPIRED`。
- 用**同一个** `request_id`（消息 id）、同样的正文、`agent` 为原发送人（`sent_by`）调用 chatbot。
- 已发出按上面第 8 条处理；**其他任何结果都保留这条消息**，只回报错误：重试的失败只说明这一次没发出，第一次的结果仍然不明，
  删掉会让其实已发出的消息在 CRM 里消失。
- 谁能重试：能回复这个联系人的人（同第 1 条），不限原发送人。

### `agent_reply` 事件（第 1 阶段第五节的增补）

- 按 `crm_message_id` 找消息；找不到，或它不是 `author_kind=agent`：返回 `ignored`。
- 它的 `external_id` 已等于 `wa_message_id`：`duplicate`，不改。
- 它的 `external_id` 为空：若别的消息已占用这个 `wa_message_id`，`duplicate`、不改；否则补上 `external_id`，`delivery_status` 为空时记 `sent`，返回 `stored`。
- 它的 `external_id` 是另一个值：`ignored`，打 WARNING（不应发生）。
- 不新建联系人、不改接管、不改正文。

### 接管写入

发送与交还响应里的 `handover`（非空时）写入三项，条件是 `occurred_at` **严格晚于**已存的更新时间；事件仍按第 1 阶段「不早于就写」。
理由：时间只精确到秒，第 1 阶段「同一秒按到达顺序生效」靠的是只有队列一个写入方；同步响应不走队列，同一秒内可能晚于后发生的变化到达。
响应先到时，随后的同一次变化的事件 `occurred_at` 相同、值相同，重写不改变结果；事件先到时，响应不写。

### 交还 AI（`POST /api/contacts/{contact_id}/whatsapp/handback`，新增）

- 归属同发送第 1 条；联系人号码与 BSUID 都没有，400；未配密钥，503。
- 调 chatbot 交还接口：`ended` 写入接管状态；`not_active` 不改；返回联系人当前接管状态。chatbot 其他错误或超时，502，不改。
- 不存消息、不记活动。

### 接口返回的新字段

- 消息：`sent_by` 与 `sent_by_name`（用户显示名；用户已删除时为空）。
- 联系人：`wa_reply_window_until`（最后一条 WhatsApp 入站消息时间 + 24 小时，没有入站为空），按查询算、不存，前端据此禁用输入框并提示。

## 七、删掉 CRM 自己的 WhatsApp 入口（D6）

代码（CRM-TASK-017）：

- 路由：`/api/webhooks/whatsapp`（GET 订阅校验、POST 回调）整个 `webhooks` router；`/internal/whatsapp/inbound`（网关入站）整个 `internal_whatsapp` router；`main.py` 里的两处挂载。
- `whatsapp_service.py` 整个删掉：Meta 验签（含密钥未配时放行）、回调处理（含吞异常）、入站解析与建联系人、回执只写日志、网关回执文案、
  网关出站与 Graph 直连发送。发送已由 CRM-TASK-013 的新服务接替。
- 配置：`WHATSAPP_PHONE_NUMBER_ID`、`WHATSAPP_ACCESS_TOKEN`、`WHATSAPP_WEBHOOK_VERIFY_TOKEN`、`WHATSAPP_APP_SECRET`、
  `INTERNAL_SHARED_SECRET`、`WHATSAPP_GATEWAY_BASE_URL`。
- 测试：`test_internal_whatsapp.py`、`test_whatsapp_service_send.py`。
- 文档：删 `WHATSAPP_GOLIVE.md`（CRM 自己的 Meta 号上线指南）；`DEPLOY.md` 的环境变量清单换成 `CHATBOT_SEND_URL`、`CHATBOT_SEND_SECRET`（第 1 阶段两项另由人补，见 CRM-TASK-008 记录）。
  `backend/.env.example` 是凭据形状的文件名，Worker 改不了，人工另补。`requirements.md` 是早期需求，不改。

Kelvin 手工（不在仓库里）：

- VPS 上 CRM 的环境文件删掉上面六项，重启 CRM。
- Meta 开发者后台：作废 CRM 那个 App 沙盒号的 token、取消 webhook 订阅（或直接删 App）。
- nginx 里 CRM 域名的 `/api/webhooks/` 段（本仓库 `nginx/conf.d/` 与 `vps_infra` 都有）留着无害（后端 404），要删另开运维改动，不在本阶段。

## 八、`is_gateway`

现状：`is_gateway=true` 的联系人 2 个（8 月经网关进来的演示访客）；第 1 阶段起新访客一律 false（P4）；
`demo_scope` 的过滤用在客户列表、商机、看板、分析、管道 5 处，把它们挡在外面。本阶段删掉网关入站后，没有任何代码会再写 true。

- CRM-TASK-018：删 `utils/demo_scope.py` 与 5 处调用。2 个联系人从此和 chatbot 访客一样出现在列表与统计里（各带一条 0 元 lead 商机）。
- CRM-TASK-019：模型取消 `is_gateway` 映射（列与索引留在库里，列有数据库默认值 false，插入照常）。
- CRM-TASK-020：迁移删列与索引。`downgrade` 重建列、默认 false，**不还原**那 2 个 true（迁移注释写明）。
- 为什么分三步：部署时迁移先于换容器，删列那一刻在跑的是上一版，失败时也回滚到上一版；所以删列之前的那一版必须连模型都不再映射这一列。

## 九、表结构

- 迁移 014（CRM-TASK-012）：`messages.sent_by`，字符串 36，可空，不加外键（与 `assigned_to` 一致）、不加索引（不按它查询）。旧数据留空。
- 迁移 015（CRM-TASK-020）：删 `contacts.is_gateway` 与 `idx_contacts_is_gateway`。
- 013 号已给 CRM-TASK-009 的存量号码迁移。

**个人数据**：新增存储的只有 `sent_by`（员工 id）。客服写的正文发给 chatbot 与 Meta（发送本身必需），chatbot 把它连同员工显示名
写进它的对话历史（Redis，7 天滑动）与审计库（永久），与控制台回复同等对待。删掉 CRM 自己的 Meta 凭据后，CRM 不再直连 Meta。不新增导出。

## 十、任务拆分（编号为暂定，登记时以实际为准）

| 编号 | 层 | 内容 | 依赖 |
|---|---|---|---|
| CRM-TASK-012 | 数据库 | 迁移 014 加 `sent_by` 与模型 | 006 |
| CRM-TASK-013 | 业务规则 | 新的经 chatbot 发送服务：签名请求、第六节发送第 2、3、5–8 条与重试（归属第 1 条与正文第 4 条在接口层） | 012、007、011 |
| CRM-TASK-014 | 业务规则 | `agent_reply` 事件规则；交还 AI 的调用与写入 | 013 |
| CRM-TASK-015 | 接口 | 发送接口改用新服务、重试与交还接口、事件接口接受 `agent_reply`、消息与联系人新字段、两项配置；`test_message_permission.py` 里打桩的旧发送函数改成新服务 | 014、008 |
| CRM-TASK-016 | 前端 | 收件箱：窗口关闭提示、发送失败原因、未确认送出与重试、「交还 AI」按钮、「客服 · 名字」；中英文文案 | 015、010 |
| CRM-TASK-017 | 接口与服务 | 第七节：删 webhook、网关入站与出站、Graph 直连、旧配置、旧测试与文档 | 015 |
| CRM-TASK-018 | 业务规则 | 删 `demo_scope` 与 5 处调用；第 1 阶段事件处理新建联系人时写的 `is_gateway=false` 去掉（列有默认值）；删 `test_demo_scope.py`，`test_contact_unassigned.py` 里「demo 联系人被排除」的断言改掉 | 017 |
| CRM-TASK-019 | 数据库（模型） | 模型取消 `is_gateway` 映射；测试构造联系人时去掉这个参数；删 018 新增的 `test_gateway_contacts_visible.py` | 018 已部署 |
| CRM-TASK-020 | 数据库 | 迁移 015 删 `is_gateway` 列与索引 | 019 已部署 |

`ai_chatbot_demo` 那边（人工会话，沿用 P5，待第十二节 Q6 确认）：

- C3：`/internal/crm/send`——验签、幂等、发送、开始 / 续期接管且响应与事件同一个 `occurred_at`、写历史与审计、入队 `agent_reply`。
- C4：`/internal/crm/handback`；`HANDOVER_IDLE_SECONDS` 改 86400。

上线顺序：第 1 阶段全部上线且事件开关已开（发送依赖入站消息算窗口、依赖事件回填送达）→ 012 → chatbot C3、C4 部署（未配密钥，一律 503）
→ 013 → 014 → 015 部署（CRM 未配密钥，发送 503；此前本来就 502）→ 016 → Kelvin 生成第二把密钥，同值写进 CRM 的 `CHATBOT_SEND_SECRET`
与 chatbot 的 `CRM_SEND_SECRET`，两边重启 → 真机验收（回复、接管中与到期时间、续期、交还、送达对勾、窗口外被拒）→ 017
→ Kelvin 清 VPS 旧变量、作废沙盒号 token → 018 → 019 → 020。

## 十一、每条改变数据的规则：操作前 → 操作 → 操作后

例子里：联系人 C1（号码 `60123456789`，负责人销售 S1「Mei」），现在 10:00，C1 最后一条入站消息 09:00。

1. **发送成功**
   - 前：C1 未接管。
   - 操作：S1 发「您好」。CRM 存消息 M1（`external_id` 空）→ chatbot 返回 200 `sent`、`wamid.A`、接管到期 次日 10:00:01、`occurred_at` 10:00:01（严格晚于已存时间）。
   - 后：M1 `external_id=wamid.A`、`delivery_status=sent`、`author_kind=agent`、`sent_by=S1`；C1 接管中，到期 次日 10:00:01，更新时间 10:00:01。返回 200。
2. **Meta 拒绝**
   - 前：同上。
   - 操作：S1 发消息 M2，chatbot 返回 502 `rejected`，错误码 131026。
   - 后：M2 被删掉；C1 接管不变；返回 502 `SEND_FAILED` 带 131026 与标题；输入框里文字还在。
3. **结果不明，事件补上**
   - 前：同上。
   - 操作：S1 发 M3，CRM 调用 30 秒超时（chatbot 其实已发出 `wamid.C`）。
   - 后（立即）：M3 留着，`external_id` 空，返回 202 `UNCONFIRMED`，页面显示「未确认送出」。
   - 再操作：`agent_reply`（`crm_message_id=M3`、`wamid.C`）到达。
   - 后：M3 `external_id=wamid.C`、`delivery_status=sent`；事件返回 `stored`。随后的 `handover_changed` 写入接管。
4. **结果不明，客服重试**
   - 前：M3 `external_id` 空（事件还没到）。
   - 操作：S1 点重试；chatbot 发现键里已有 `wamid.C`，返回 200 `duplicate`、`handover` 为 null。
   - 后：M3 `external_id=wamid.C`，接管不由响应改（由事件写）；访客只收到一条。之后到达的 `agent_reply` 返回 `duplicate`、不改。
   - 若这次重试得到 502 `rejected` 或 401：M3 仍留着、仍未确认，只把错误告诉客服。
5. **窗口关闭**
   - 前：C1 最后入站是前天。
   - 操作：S1 发消息。
   - 后：不存、不调用 chatbot；返回 409 `WINDOW_CLOSED` 带关闭时间。
6. **回执跟上**
   - 前：M1 `delivery_status=sent`。
   - 操作：`delivery_status`（`wamid.A`、`read`）到达。
   - 后：M1 `read`（第 1 阶段只升不降，不变）。
7. **接管响应与事件**
   - 前：例 1 之后，C1 接管更新时间 10:00:01。
   - 操作：`handover_changed`（`occurred_at` 10:00:01、同样的到期时间）到达。
   - 后：事件不早于已存时间，照写，值不变。
   - 同一秒冲突：10:00:01 S1 发送（开始接管）与经理交还（结束）落在同一秒，队列先送到「结束」（10:00:01），S1 的同步响应（开始，10:00:01）后到：
     不严格晚于已存时间，不写；C1 正确显示未接管。
8. **交还 AI**
   - 前：C1 接管中。
   - 操作：经理点「交还 AI」，chatbot 返回 `ended`、`occurred_at` 10:30。
   - 后：C1 未接管、到期时间空、更新时间 10:30；之后 C1 再发消息由 AI 回。再点一次：chatbot 返回 `not_active`，CRM 不改。
9. **`agent_reply` 找不到消息**
   - 前：没有 id 为 M9 的消息。
   - 操作：`agent_reply`（`crm_message_id=M9`）到达。
   - 后：返回 `ignored`，不建联系人、不建消息。
10. **加 `sent_by`（迁移 014）**
    - 前：所有消息没有这一列。
    - 后：列存在，旧消息全为空；只有本阶段起的客服回复写入。
11. **去掉 `is_gateway` 的用处（CRM-TASK-018）**
    - 前：联系人 G1、G2 `is_gateway=true`，不在客户列表、商机、看板、分析、管道里。
    - 后：列还在、值不变，但不再被读取；G1、G2 与各自的 0 元 lead 商机出现在上述页面与统计里。
12. **删列（迁移 015）**
    - 前：列存在，G1、G2 为 true，其余 false。
    - 后：列与索引不存在；`downgrade` 后列回来、全部 false。

## 十二、需要 Kelvin 拍板（2026-10-13 已定：Q1–Q7 全部按建议）

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| Q1 | 原有 2 个 `is_gateway` 联系人 | A 去掉标记，成为普通联系人（第八节）；B 同时软删除这 2 个 | A：CRM 是 demo，它们的对话就是演示历史；软删除后同号码再来消息会新建联系人，历史反而分家 |
| Q2 | 窗口外回复（R2） | A CRM 发送前拒绝；B 照发，等 Meta 异步报「未送达」 | A |
| Q3 | 明确没发出的消息（R1） | A 不入库、文字留在输入框（沿用 004）；B 入库并标「未送达」 | A |
| Q4 | 发给 chatbot 的员工信息（R8） | A id 与显示名；B 只有 id | A |
| Q5 | 会话认领 / 转派与锁、模板、媒体、实时刷新（R6、R7） | 确认留在第 3 阶段，不前移 | 确认 |
| Q6 | chatbot 侧 C3、C4 | 沿用 P5：人工会话做，不登记进 OpenClaw | 沿用 |
| Q7 | 接管时长改 24 小时也作用于控制台接管（第四节第 3 条） | 接受；或给 CRM 回复单独一个时长 | 接受：只有一套状态机，分开要改 chatbot 的状态机设计 |

## 十三、边缘情况

- **chatbot 现状核对**（读 `ai_chatbot_demo` master 代码）：`handover.begin` 在已接管时直接返回、不续期，所以 C3 要按「未接管 begin、否则 touch」；
  控制台回复遇到 Meta 拒绝会吞掉异常仍返回 200，C3 的发送接口**不能**照搬这一点；访客档案只按「号码或 BSUID」一个键存、7 天滑动，
  所以交还要两个键都试；chatbot 目前没有基于 Redis 的幂等工具，C3 新建 `chat:crm_send:` 键。
- **Meta 侧结果不明导致重发**：chatbot 到 Meta 超时或 Meta 回 5xx，占位 120 秒后过期，之后的重试会再发一次，访客可能收到两条。
  WhatsApp 发送接口没有幂等键，只能接受；120 秒内的重试得到 409，CRM 继续显示「未确认送出」。
- **回执先于 `agent_reply`**（第四节第 1 条）：极少见；丢 `sent` 无害，丢 `failed` 时这条显示「已发出」而实际未送达，接受。
- **chatbot 发出后、写幂等键前崩溃**：键是「发送中」占位，120 秒后过期，重试会再发一次；`agent_reply` 也不会入队。与上一条同一类，接受。
- **同一联系人两个人几乎同时回**（负责人与经理）：两条都发出，各记 `sent_by`，访客看到两条（R6 接受）。
- **号码冲突的联系人**：窗口按每个联系人自己的入站消息算；入站只挂最早的那个（P2），所以只有它能回，重复的那个窗口关闭，自然不会从错的联系人发出。
- **原有 `is_gateway` 联系人**：第 1 阶段起它们若再来消息照常挂上，窗口打开即可回复，走 chatbot。
- **发送途中联系人被转派或删除**：归属在第 1 步校验；之后的转派由任务二 2.1 随行带走这条消息；联系人被软删除不影响已存的消息与回执。
- **长期「未确认送出」**：chatbot 真的没发、又一直没人重试，窗口关闭后不能再重试，这条一直显示「未确认送出」；接受（demo，罕见），不加「放弃」按钮。
- **chatbot 档案丢失**（Redis 数据丢了）：发送照样发出并新建最小档案开始接管；交还返回 `not_active`，CRM 的接管显示要等到到期时间自然消失（第 1 阶段以到期时间为准）。
- **chatbot 事件队列卡住**（第 1 阶段的顺序投递代价）：发送仍成功，CRM 靠同步响应已补上 `external_id` 与接管；回执与 `agent_reply` 等队列恢复后补到。
- **访客发 `menu` 结束接管**：chatbot 现有行为，推 `handover_changed`；客服下一次回复重新开始接管。
- **两把密钥配反或配成同一把**：配反时两个方向都 401，各自记 ERROR，修好即恢复；配成同一把代码不拦，靠上线步骤保证。
- **超过 4096 字符**：CRM 拒收（422），不靠 chatbot 截断。

## 十四、登记预审后的补充（2026-10-13，待 Kelvin 在登记 PR 上批准）

登记 CRM-TASK-012 至 020 前跑了设计预审（输出在 `tasks/todo.md`「登记 CRM-TASK-012 至 020」），下面几条照原文没写死或走不通，按这里补；
对应任务的验收标准已按这里写。

1. **`CHATBOT_SEND_URL` 是基地址**：只到端口、不含路径，发送与交还分别接 `/internal/crm/send` 与 `/internal/crm/handback`。
2. **窗口边界**：当前时间早于「最后一条 WhatsApp 入站消息时间 + 24 小时」才算开着，恰好 24 小时算关闭；没有入站消息时 `WINDOW_CLOSED` 的关闭时间为空。
3. **结果分类写死**（第六节第 8 条）：已发出 = 200 且 `result` 为 `sent` / `duplicate`、带 `wa_message_id`；明确没发出 = 502 且 `result` 为 `rejected`、401、422、503、
   请求没送出去（连接失败、连接超时、连接池超时）；**其余一切**都算结果不明（409、504、其他状态码、200 但字段不对、写超时、读超时、连接中途断开、30 秒总时限到）。
   拿不准就留着消息，可以重试，不会丢。
4. **校验顺序**（第六节）：正文校验在请求模型里，FastAPI 先于处理函数执行，所以顺序是 422 → 403 → `NO_PHONE` → `WINDOW_CLOSED` → `NOT_CONFIGURED`。
5. **错误放哪**：沿用现有 `fail` 外壳 `{code, message, fields}`；窗口关闭时间放 `fields.window_closed_at`，Meta 的错误码与标题放 `fields.error_code` 与 `fields.error_title`。
6. **重试发给谁**：发给这条消息存的收件人（第一次发送时的规范化号码），不换成联系人现在的号码，所以重试不再查号码，只查窗口与配置。
7. **重试已确认的消息**：其间已被同步响应或 `agent_reply` 确认（`external_id` 非空）的，重试直接返回 200 与这条消息、不调用 chatbot，不再返回「无权」。
8. **前端超时**（设计缺陷）：前端请求默认 15 秒超时，后端调用 chatbot 最长 30 秒，前端会先报错、客服重发就换了幂等键，访客收到两条。
   补：发送与重试请求单独把超时设为 45 秒；收不到响应时保留输入、刷新会话，提示先看有没有「未确认送出」的消息、有就用它的重试。
9. **删配置后的启动**：配置类从本地环境文件读取时默认拒绝多出来的键，而环境变量示例文件（Worker 改不了）仍带 WhatsApp 那几项，照它复制的本地环境文件会让启动失败。
   补：CRM-TASK-017 删配置时让配置忽略环境文件里多出来的键。生产经 compose 以环境变量注入，本来不受影响。
10. **`DEPLOY.md` 的变量清单**：第七节原写「只加 `CHATBOT_SEND_URL`、`CHATBOT_SEND_SECRET`，第 1 阶段两项另由人补」；改为 CRM-TASK-017 一次补齐
    `CHATBOT_EVENTS_SECRET`、`CHATBOT_EVENTS_SECRET_PREVIOUS`、`CHATBOT_SEND_URL`、`CHATBOT_SEND_SECRET` 四项空值。
11. **测试文件范围**：CRM-TASK-007 新增的 `test_contact_phone.py` 有经旧 Meta 回调与网关入站的用例，归 CRM-TASK-017 删；CRM-TASK-018 新增的
    `test_gateway_contacts_visible.py` 用到 `is_gateway`，归 CRM-TASK-019 删（模型不再映射后它守的口径已无从构造）；「不再出现 `is_gateway`」不含迁移文件与 `test_migration_015`。
12. **删列前先取消映射**（设计缺陷）：原 019 在同一次部署里删模型字段与删列，而 `deploy.sh` 先跑迁移、再换容器，失败回滚到上一版；
    上一版（018）的模型仍映射这一列，删列那一刻起它的联系人查询全部报错，回滚目标也坏。补：拆成 019（模型取消映射，列有数据库默认值 false，插入照常）
    与 020（迁移删列），各自一次部署。任务从 8 个变成 9 个。
13. **重试的细节**：先查创建时间（当前时间不早于创建时间 + 24 小时即 `RETRY_EXPIRED`，先于窗口检查）；`visitor.bsuid` 取联系人现在的 BSUID；
    原发送人已不存在时 `agent.name` 用 `sent_by` 的 id。
14. **交还的细节**：总时限 30 秒；chatbot 返回 200 但 `ended` 却没有合法的 `handover`，按失败处理、不改，打 ERROR；CRM 接口的失败码为 502 `CHATBOT_FAILED`。
15. **结果不明的响应形状**：202 用 `ok` 外壳，`data` 是这条消息，`message` 为 `UNCONFIRMED`（`fail` 外壳没有 `data`）。
16. **前端输入上限**：WhatsApp 回复框限制 4096 字符；没有 `code` 的 422 与不带 Meta 字段的 `SEND_FAILED` 显示通用的发送失败。
17. **发送途中不能重试**（设计缺陷）：消息先存后调，第一次发送还在途中时它就显示「未确认送出」；此时重试可能与第一次的「明确没发出、删行」赛跑，
    重发一条 CRM 已删掉的消息。补：创建不足 45 秒（CRM 调用 chatbot 的 30 秒总时限加余量）的消息不能重试，后端返回 409 `IN_PROGRESS`，前端显示「发送中」、不给重试按钮。
    45 秒后第一次的结果已定：删了就找不到（403），留着才可重试。
18. **重试不碰回复框**：重试的结果只刷新会话与提示，不清空回复框里另写的草稿。
19. **交还后仍显示接管中**：chatbot 返回 `not_active`，或 `ended` 与已存状态同一秒没写入时，前端提示「已请求交还，状态以 chatbot 推送为准」并稍后刷新。
20. **交还的边角**：联系人号码无法规范化时 `visitor.phone` 为空，再没有 BSUID 即 `NO_IDENTITY`；联系人不存在或已删除（管理员也一样）返回同一个 403 `NOT_ASSIGNED`。
