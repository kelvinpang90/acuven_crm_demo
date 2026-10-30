# demo 号多客服第 3 阶段「补齐」：设计

> 状态：**已批准**（Kelvin 2026-10-30，原话「都按照你建议执行」，回复的是第十节 Q1–Q14）。第 3 阶段任务的设计闸门引用本文件所在的 PR kelvinpang90/acuven_crm_demo#249；设计一变，批准作废。
> 登记另开 PR，按「登记任务」规则跑设计预审；预审补的内容另起一节，不改动已批准的各节（第一批 CRM-TASK-091 至 095 的补充见第十四节）。
> 上位设计：`acuven_hub` 的 `CHATBOT_DEMO_CRM_MULTIAGENT_2026-10-12.md`（下称 MULTI）第五节「3 补齐」，现行索引 `DECISIONS.md` 的 J-04、A-18。
> 前两阶段契约：[chatbot-events.md](chatbot-events.md)（第 1 阶段，事件）、[chatbot-replies.md](chatbot-replies.md)（第 2 阶段，发送与交还）。本文件只在它们上面**增补**，不改已定的规则；增补处集中在第六、七节。
> 这里是两个仓库共用的接口契约：`ai_chatbot_demo` 的模板与媒体接口按本文件实现。
> 依据（2026-10-30 只读核对，**都没有运行**）：本仓库 master `a3f897d`；`ai_chatbot_demo` master 的 `backend/app/` 与 `tasks/todo.md` 批次 08–10。
> 实施顺序（Kelvin 2026-10-30 认可）：先做只涉及 CRM 的 1、2，不等 chatbot；3、4 两边一起推。
> 第 3 项（窗口外模板消息）Kelvin 2026-10-30 审阅时确认保留（原话「保留」）。

## 一、第 3 阶段做到什么

| # | 项 | 涉及 | 一句话 |
|---|---|---|---|
| 1 | 收件箱实时刷新 | 只有 CRM | **主体已上线**（2026-10-21，PR #137、#138）；本阶段只补「联系人的接管、负责人、认领变化也推送」 |
| 2 | 会话认领与转派 | 只有 CRM | 收件箱里能认领、释放、接手、转派；客服回复前校验认领，挡住两个人同时回同一个访客 |
| 3 | 窗口外模板消息 | 两边 | 24 小时窗口关闭后，客服能从 chatbot 白名单里选一个已获 Meta 批准的模板、填参数、发出 |
| 4 | 媒体 | 两边 | 访客发来的图片、文件、语音在 CRM 里能看能下载；客服能发图片与 PDF |

**不做**：位置、联系人卡片、贴纸、视频的展示（仍是占位文字）；模板的 header / 按钮参数；从 CRM 管理模板或向 Meta 提交模板；病毒扫描引擎（见 Q11）；
Director 控制台受 CRM 认领约束（控制台不在 CRM 里，见第八节）。

## 二、现状侦察（读代码得出，未运行）

### 1 实时刷新

- 后端 [inbox_events.py](../backend/app/services/inbox_events.py)：进程内广播；`GET /api/messages/stream` 推一个不带数据的 `messages_changed` 信号，
  客户端按原有接口与权限重新拉取；每 20 秒心跳，连接最长 15 分钟后重连换 token。
- 触发条件：Session 钩子只看 **`Message`** 的增改删（含批量 `update(Message)`）。
- 前端 [inboxStream.ts](../frontend/src/services/inboxStream.ts) 全页一条连接，[useInboxChanges.ts](../frontend/src/hooks/useInboxChanges.ts) 被三处订阅：
  收件箱列表、当前对话（同时重新读联系人，[ConversationView.tsx:187](../frontend/src/pages/Inbox/ConversationView.tsx)）、新消息弹窗。
- **缺口**：只改联系人、不写消息的变化不推送。具体是 `handover_changed` 事件（[chatbot_event_service.py:305](../backend/app/services/chatbot_event_service.py) 只改 `contacts` 的三个接管列）——
  控制台交还、访客发 `menu`、空闲到期时 chatbot 推来的接管变化，打开着的会话头部要等下一条消息或手动刷新才变。
  转派会批量改 `Message.assigned_to`（[contact_service.py:231](../backend/app/services/contact_service.py)），所以已经会推送，但仅限这个联系人有消息时。
  第 2 项新增的认领状态也只在 `contacts` 上，不补就推不出去。

### 2 认领与转派

- 归属只有 `contacts.assigned_to` 一个字段；`may_access_contact`（[access_service.py](../backend/app/services/access_service.py)）：管理员不限、经理到团队、销售只到自己。
  能读就能回（D7）：发送、重试、交还都只校验这一条（[messages.py:259](../backend/app/routers/messages.py)）。
- 转派只能在联系人编辑页改负责人（`PUT /api/contacts/{id}`，`may_assign_to` 限制目标人）；收件箱里没有转派入口，也没有认领接口、「他人正在处理」标记。
- 已有的「谁回的」：出站消息记 `sent_by`（第 2 阶段），收件箱显示「客服 · 名字」。
- 活动记录（`activities`）的 `deal_id` 必填（迁移 006），挂不上「联系人被转派」这类不属于商机的记录。
- 并发：负责人与经理同时回，两条都发出、访客收到两条（第 2 阶段 R6 接受，留到本阶段）。

### 3 模板（chatbot 侧）

- 有发模板的函数：`build_template_message(to, name, language, params)`（`services/whatsapp.py:139`），**只支持正文的位置参数**；Graph API v20.0。
- 现有两个模板 `order_confirmed`、`order_shipped`：Utility 类、英文 `en`、各 3 个参数；名字与语言写死在 `services/notify.py:47-53`。
  唯一用途是 ERP 演示下单后的定时推送（窗口外才用模板），`order_shipped` 没有调用方，两个模板都还没有真实流量。
  它们是订单通知，**不适合 CRM 客服的跟进**（内容是「您的订单已确认」），本阶段要另申请模板（见 Q7）。
- `/internal/crm/send` 只有 `text` 字段（1–4096 字符），没有 `type`，没有为模板或媒体预留字段；多余的键直接忽略。
- 接管状态机里没有模板相关逻辑；24 小时窗口只由 CRM 在发送前检查（第 2 阶段 R2）。
- 每日条数上限只计入站（默认 100，进程内存），出站不计。

### 4 媒体（chatbot 侧）

- `fetch_media`（`services/whatsapp_media.py:65`）：先查元数据、再带 token 下载，**只在内存里、不落盘**；按元数据的大小拦截（图片 5 MB、PDF 16 MB）。
- 入站：图片（jpeg/png/gif/webp）当轮交给模型；PDF 交给模型并存进进程内的 `doc_store`（最多 5 份）；语音下载后转写成文字；
  视频、贴纸、位置、联系人卡片没有专门分支，回一句固定提示。
- 推给 CRM 的只有占位文字（`_customer_message_body`，`routers/whatsapp_webhook.py:517`）：`[image] 说明`、`[document] 文件名 说明`、`[voice] 转写文字`、其余 `[类型名]`。
  事件里**没有** Meta 的 media id、MIME、大小。
- 出站：有 `upload_media`（上传到 Meta 的 `/media`，唯一调用方是发票 PDF）与 `build_document_message`；**没有**发图片、音频、视频的函数。
- chatbot 没有任何可供 CRM 拉取文件的端点。
- CRM 侧：`messages.body` 只有文字；没有附件表；后端容器没有挂卷（`docker-compose.yml`），所有数据只在共享 MySQL / Redis 里。
  CRM 的事件请求模型没设 `extra`（pydantic 默认忽略多余字段），所以 chatbot 先加可选字段不会让 CRM 返回 422。

## 三、取舍（建议；需要拍板的单列在第十节）

| # | 问题 | 建议 | 理由 |
|---|---|---|---|
| R1 | 实时刷新补什么 | 同一个信号、同一条连接；钩子再看 `Contact` 上「接管三列、负责人、认领两列」的改动 | 前端收到信号本来就会重新拉列表与联系人，不用改前端；信号仍不带数据，不新增暴露面 |
| R2 | 认领是不是锁 | **会过期的认领**：回复即自动认领，15 分钟滑动；认领期间别人回复被拒（409），可以一键「接手」覆盖 | 只提示不拦挡不住两个人同时点发送；硬锁没有接手又会让人走开后会话卡住 |
| R3 | 认领与负责人的关系 | 分开：负责人是长期归属，认领是「这一刻谁在回」；认领不改负责人，转派清掉认领 | 经理临时帮回不应改业绩归属；转派之后原认领没有意义 |
| R4 | 认领存哪 | `contacts` 加两列，用条件更新原子地抢 | 与接管状态同一种「以到期时间为准」的写法；列表查询直接带出；Redis 带 TTL 也行，但列表要再查一次 Redis |
| R5 | 转派入口 | 收件箱会话头部加转派，调用现有 `PUT /api/contacts/{id}`，不新增接口 | 后端规则（`may_assign_to`、消息随行）已经齐了 |
| R6 | 模板清单由谁定 | chatbot 配置一个**给 CRM 用的白名单**（名字、语言、参数个数、正文样式），CRM 经内部接口读取 | chatbot 是唯一持有 Meta 凭据的一方；不让 CRM 把任意模板名发给 Meta |
| R7 | 模板发送算不算客服回复 | 算：开始或续期接管 24 小时，记 `sent_by` | 访客回了模板，接着由发模板的客服跟进，AI 不该插话 |
| R8 | 入站媒体怎么到 CRM | 事件只带元数据（含 Meta media id）；CRM 收到事件后**经 chatbot 拉取**文件存自己那里 | chatbot 不落盘、事件队列在 Redis，不宜塞几 MB 的文件；拉取失败不影响消息本身入库 |
| R9 | 出站媒体怎么发 | CRM 存好文件后，把文件放在签名请求体里交给 chatbot；chatbot 上传到 Meta 再发 | 与发送文字同一套幂等、接管、结果分类 |
| R10 | 模板与媒体的接口形状 | 各开新接口（`send-template`、`send-media`），与 `send` 共用 chatbot 内部的发送流程 | 每个接口请求体严格校验；不给 `send` 加 `type` 默认值做兼容 |

## 四、流程

```
1 实时刷新（补缺口）
  任一提交里改了 Message，或改了 Contact 的接管 / 负责人 / 认领列 → 提交后广播 messages_changed → 打开的页面照旧重拉

2 认领
  客服打开会话 → 头部显示「X 正在处理，到 HH:mm」或「无人认领」
  客服点「认领」或直接发送 → CRM 条件更新抢认领（空 / 已过期 / 本来就是自己）→ 成功则续 15 分钟
  别人已认领时发送 → 409 CLAIMED（带认领人与到期时间），不存、不调用 chatbot → 前端提示，可「接手」后再发
  转派（改负责人）→ 清掉认领
  点「释放」→ 清掉认领（只有认领人本人或能接手的人）

3 模板（窗口外）
  回复框显示窗口已关 → 「发模板」→ CRM 经 chatbot（POST /internal/crm/templates）读白名单 → 选模板、填参数、看预览
  → CRM：归属、认领、资格、频率校验 → 先存一条「未确认」的出站消息 → POST chatbot /internal/crm/send-template
  → chatbot：白名单校验 → 渲染正文 → Graph 模板消息 → 开始或续期接管 → 推 agent_reply（kind=template）→ 返回
  → CRM：同第 2 阶段发送的结果处理；访客回复后窗口重开，照常文字回复

4 媒体
  入站：访客发图 → chatbot 推 customer_message（body 仍是占位文字，另带 media 元数据）
        → CRM 存消息、建一条 pending 附件 → 后台任务调 chatbot POST /internal/crm/media → 校验 → 存卷 → stored
  出站：客服在回复框选图片或 PDF → CRM 校验、存文件、先存「未确认」消息与附件
        → POST chatbot /internal/crm/send-media（multipart，签名）→ chatbot 上传到 Meta → 发 image / document → 同发送文字
  查看：GET /api/messages/{id}/attachment（按联系人归属校验）→ 前端带 token 取回、生成 blob 地址显示或下载
```

## 五、各项方案

### 5.1 实时刷新：补联系人变化的推送

- `inbox_events` 的 flush 钩子：`session.dirty` 里的 `Contact` 若改了 `wa_handover_active`、`wa_handover_expires_at`、`wa_handover_updated_at`、
  `assigned_to`、`reply_claimed_by`、`reply_claimed_until` 中任一列（用 SQLAlchemy 的属性历史判断），同样置标记；批量 `update(Contact)` 也置。
  新建联系人不单独推（新访客总伴随一条消息）。
- 信号名仍是 `messages_changed`，前端不改。
- 其他列（名字、电话、标签……）不推：收件箱不显示它们，推了只会多拉几次。
- 设计闸门：**不适用**——信号不带数据，展示仍走原接口与权限；不改表结构。

### 5.2 认领与转派

**状态**（`contacts` 新增两列，见第九节）：`reply_claimed_by`（员工 id）、`reply_claimed_until`（UTC）。
有效认领 = `reply_claimed_by` 非空且当前时间早于 `reply_claimed_until`；过期的按「无人认领」显示，不依赖清理任务。

**谁能做什么**（都先过现有 `may_access_contact`）：

| 动作 | 条件 | 结果 |
|---|---|---|
| 认领 `POST /api/contacts/{id}/claim` | 无有效认领，或认领人就是自己 | 认领人 = 自己，到期 = 现在 + 15 分钟 |
| 接手 同上，带 `takeover: true` | 有别人的有效认领 | 覆盖为自己；返回被接手的人（前端提示） |
| 释放 `DELETE /api/contacts/{id}/claim` | 认领人是自己；或有效认领是别人的、且自己能接手 | 清空两列 |
| 发送文字 / 模板 / 媒体、重试 | 无有效认领或认领人是自己 → 自动认领或续期；否则 409 `CLAIMED` | 抢认领与校验在同一条条件更新里，先于存消息 |
| 转派（改负责人） | 现有规则 | 同一事务里清空认领 |
| 交还 AI | 不看认领 | 交还不给访客发消息，与「谁在回」无关 |

- 「能接手」= 能访问这个联系人的人（负责人、其经理、管理员）。同一层级也能互相接手（例如两个都能访问的经理）；接手会提示原认领人（下次他发送时 409 里看得到）。
- 条件更新（MySQL 原子）：`UPDATE contacts SET reply_claimed_by=:me, reply_claimed_until=:until WHERE id=:id AND deleted_at IS NULL AND (reply_claimed_by IS NULL OR reply_claimed_by=:me OR reply_claimed_until<=:now)`；
  影响行数为 0 即被别人认领，读出认领人返回 409。两个客服同时点发送，只有一个抢到。
- 第 2 阶段的「先存再调」不变：认领在校验链里放在归属之后、号码与窗口之前，错误码顺序：422 → 403 → **409 `CLAIMED`** → `NO_PHONE` → `WINDOW_CLOSED` → `NOT_CONFIGURED`。
- 重试（第 2 阶段 `retry`）也过认领：别人已接手时，原发送人的重试被拒，避免接手后旧消息又补发出去。
- 联系人与会话列表接口多返回 `reply_claimed_by`、`reply_claimed_by_name`、`reply_claimed_until`（已过期时三项都返回空）。

**前端**：会话头部显示认领状态与「认领 / 释放 / 接手」；收件箱列表每行显示「X 正在处理」小标；发送遇到 409 时保留输入框内容，提示「X 正在处理（到 HH:mm）」，
给「接手并发送」按钮（先接手、再用同一段文字发送）；头部加「转派」下拉，候选人用现有 `GET /api/users`（已按 `assignable_users` 收窄），销售角色不显示。

**个人数据**：新增存储的只有员工 id 与时间，不涉及顾客数据。**表结构变了、且是状态机** → 过设计闸门与设计预审。

### 5.3 窗口外模板消息

**chatbot 白名单**（新配置，例如 `CRM_TEMPLATES`，JSON）：每项 `{name, language, category, params: [{label, max_len}], body}`；
`body` 是 Meta 批准的正文原样，带 `{{1}}`… 占位，用于 CRM 预览与写对话历史。不在白名单里的模板 CRM 发不了。
白名单为空时接口返回空列表，CRM 显示「没有可用模板」。

**资格与频率**（CRM 发送前校验，任一不过就不存、不调用 chatbot）：

1. 归属、认领：同 5.2。
2. 号码：能规范化（同第 2 阶段 `NO_PHONE`）。
3. **资格**：该联系人曾有过至少一条 WhatsApp 入站消息，否则 409 `NO_OPT_IN`——不从 demo 号向从没来过消息的人（手工新建、表格导入、AutoCount 同步的）主动发消息（Meta 的商业消息政策要求对方同意接收，未逐条核对现行条款）。
4. **频率**：窗口关闭后、访客回复前，同一联系人最多发 1 条模板（已存在一条窗口关闭之后创建、`external_id` 非空或仍未确认的模板消息就 409 `TEMPLATE_PENDING`）。
   防反复打扰，也控制费用（模板消息由 Meta 按条向 demo 号所属账户计费，单价按 Meta 当时价目，未核实）。
5. 窗口开着时：后端也接受（发模板不违规），但前端只在窗口关闭时给入口——窗口内用文字就够了。
6. 参数：个数与白名单一致；每个去掉首尾空白后 1–`max_len` 字符；不含换行、制表符、连续 4 个以上空格（Meta 对正文参数的限制，未核实现行条款），否则 422。

**存储**：出站消息 `author_kind=agent`、`sent_by`、`template_name` = 模板名；`body` = chatbot 返回的渲染后正文（先存时用 CRM 按白名单渲染的正文，结果以 `agent_reply` 为准不再改正文）。

**前端**：窗口关闭的提示条旁加「发模板」：弹窗里选模板（显示类别与语言）、逐个填参数（第一个参数预填联系人名字，可改）、实时预览、发送。
结果处理（已发出 / 明确没发出 / 未确认送出 + 重试）完全沿用第 2 阶段。

**个人数据**：参数可能含访客名字，经 chatbot 发给 Meta；这与发文字回复同等（发送本身必需）。新增存储只有 `template_name`。
**碰钱**（Meta 按条计费到 demo 号账户）**且改表结构** → 过设计闸门与设计预审。

### 5.4 媒体

**入站（访客 → CRM）**

- chatbot：`customer_message` 事件的 `message` 里加可选的 `media`（第六节）；`body` 照旧是占位文字（CRM 不认识 `media` 时照旧显示占位）。
  类型只推 `image`、`document`、`audio`（含语音）；其余类型照旧只有占位文字。语音的 `body` 照旧是转写文字，另带音频文件。
- CRM 事件处理：存消息照旧；有 `media` 时同一事务建一条附件，状态 `pending`。**不在事件处理里拉文件**（事件按顺序投递，拉文件慢会卡住整条队列）。
- CRM 后台任务（沿用 `backend/app/tasks/` 的轮询器写法，例如每 30 秒）：取 `pending` 附件，调 chatbot `POST /internal/crm/media`：
  - 200：按第 5.4 节「校验」通过就写卷、记 `stored`；不通过记 `rejected`（附原因），文件丢弃。
  - 404 / 410（Meta 已取不到）：记 `expired`。
  - 413（超过上限）：记 `too_large`。
  - 其他错误：退避重试，最多 5 次或 24 小时，之后记 `failed`。
  - 多实例并发：用条件更新把 `pending` 改成 `fetching`（带开始时间，超时 10 分钟回到 `pending`）再拉，单进程现状下也不重复。
- 收件箱：`stored` 的图片显示缩略图（点开看大图），文件显示名字、大小、下载；`pending` 显示「正在取回」；其余显示占位文字加原因。

**出站（客服 → 访客）**

- 只支持图片（jpeg、png）与 PDF；说明文字可选，≤1024 字符（Meta 的说明上限，未核实现行条款）。
- `POST /api/messages/whatsapp/send-media`（multipart：`contact_id`、`caption`、`file`）：校验链同文字发送（含认领、窗口），再做文件校验；
  通过后存文件、先存「未确认」消息（`body` = 说明，或 `[image]` / `[document] 文件名`）与附件（`stored`），再调 chatbot `send-media`；结果处理沿用第 2 阶段。
  明确没发出时删消息**也删文件**。
- 前端：回复框加「附件」按钮，选文件后显示预览与大小，超过上限当场提示；前端发送超时沿用第 2 阶段 45 秒。

**校验（两边都做，CRM 为准）**

- 类型以**文件头魔数**判断，不信声明的 MIME 与扩展名；白名单：入站 jpeg / png / webp / gif / pdf / ogg(opus) / mp3 / m4a(aac)，以及 docx / xlsx（Q12）；出站 jpeg / png / pdf。
- 大小上限见 Q10。
- 记 `sha256`，同一联系人同一 `sha256` 的文件只存一份（访客反复转发同一张图常见）。

**存储与查看**

- 存在 CRM 后端容器挂的命名卷（Q9）里，路径 `<附件 id 前两位>/<附件 id>`，文件名不进路径（防路径注入），原文件名只存在库里。
- `GET /api/messages/{message_id}/attachment`：过 `may_access_contact`（与读会话同一规则，消息不存在与无权同一个 403）；
  响应头 `X-Content-Type-Options: nosniff`、`Cache-Control: private, no-store`；只有 jpeg / png / webp / gif 用 `inline`，其余一律 `attachment` 下载。
  前端用带 token 的请求取回、生成 blob 地址显示，**token 不进 URL**（与推送流同一做法）。
- 保留：附件文件按保留期删除（Q11），删后附件记 `purged`、消息与占位文字保留；联系人被软删除不提前删文件（与消息一致），到期照删。

**AI 功能**：收件箱的 AI（摘要、起草、分诊）仍只读 `body` 文字，不读附件内容；chatbot 交给模型的做法不变。

**个人数据**：**新增存储顾客发来的照片、文件、语音**（可能含证件、地址等），是本阶段最重的个人数据改动。
只存在 CRM 自己的卷里，查看权限沿用联系人可见范围，不新增导出，按保留期删除。**过设计闸门与设计预审**；部署要改 compose 加卷（见第十一节）。

## 六、对第 1、2 阶段契约的增补（chatbot → CRM 事件）

1. `customer_message` 的 `message` 加可选字段 `media`：

   ```json
   "media": {
     "kind": "image | document | audio",
     "media_id": "Meta media id",
     "mime_type": "image/jpeg",
     "file_name": "原文件名或 null（只有 document 有）",
     "caption": "说明或 null",
     "size": 123456,
     "sha256": "Meta 元数据里的 sha256，或 null"
   }
   ```

   `body` 规则不变（占位文字）。CRM 未升级时忽略此字段，行为同现状；所以 chatbot 可以先部署。
2. `agent_reply` 的 `message` 加可选字段 `kind`（`text` / `template` / `image` / `document`，缺省视为 `text`）与 `template_name`（仅 `template`）。
   CRM 处理规则不变（按 `crm_message_id` 补 `external_id`），不据此改正文。
3. 其余事件不变。`ai_reply` 带媒体（chatbot 自己发的发票 PDF）本阶段仍只推占位文字。

## 七、接口契约增补（CRM → chatbot）

鉴权全部沿用第 2 阶段：`X-Acuven-Timestamp` + `X-Acuven-Signature`，CRM 用 `CHATBOT_SEND_SECRET` 签名，chatbot 用 `CRM_SEND_SECRET`（及 `_PREVIOUS`）验签；
未配密钥 503。签名覆盖**原始请求体字节**（multipart 也一样）。两个读取类接口也用 POST 加 JSON 请求体，签名规则与现有完全相同，不新增 GET 的签名变体。

### `POST /internal/crm/templates`

请求体 `{}`。返回 200 `{"templates": [{"name", "language", "category", "params": [{"label", "max_len"}], "body"}]}`。白名单为空返回空列表。CRM 每次打开「发模板」时读取，不缓存。

### `POST /internal/crm/send-template`

请求体：

```json
{
  "request_id": "CRM 消息 id（UUID），幂等键",
  "visitor": {"phone": "60123456789", "bsuid": "MY.xxxx 或 null"},
  "template": {"name": "crm_followup", "language": "en", "params": ["Mei", "Acuven"]},
  "agent": {"id": "CRM 用户 id", "name": "CRM 用户显示名"}
}
```

chatbot：验签 → 校验（模板名 + 语言在白名单、参数个数与长度、第 5.3 节第 6 条的字符限制；不过 422）→ 与 `send` **同一套**幂等键（`chat:crm_send:<request_id>`，同一个键空间）、
发送、结果分类、接管、写历史与审计、入队 `agent_reply`（`kind=template`，`body` 为渲染后正文）。响应同 `send`，成功时多一个 `body`（渲染后正文）。

### `POST /internal/crm/send-media`

`multipart/form-data`：`meta`（JSON 字符串：`request_id`、`visitor`、`agent`、`caption` 或 null）与 `file`（文件字节）。

chatbot：验签 → 校验（类型魔数与出站白名单、大小、说明长度；不过 422）→ 幂等键占位 → `upload_media` 到 Meta 得 media id
（上传失败按 Meta 4xx / 其他分别归入 `rejected` / `unknown`）→ 发 `image` 或 `document` 消息 → 其余同 `send`，`agent_reply` 的 `kind` 为 `image` / `document`、`body` 为说明或占位。
同一个 `request_id` 重试时 chatbot 的去重先于上传，所以不会重复上传。上传成功、发送前崩溃：占位过期后重试会再上传一次，Meta 端多一份未用的媒体，无害。

### `POST /internal/crm/media`

请求体 `{"media_id": "Meta media id"}`。chatbot：验签 → 用自己的 token 调 `fetch_media`（先元数据、再下载）→ 按入站白名单与大小上限校验 → 200 返回文件字节，`Content-Type` 为校验后的类型，
另带 `X-Acuven-Sha256`。Meta 说这个 id 不存在或已过期：404；超过上限：413（不下载）；类型不在白名单：415；Meta 其他错误或超时：502。
chatbot **不缓存、不落盘**。Meta 对入站媒体可取回多久没有核实，按「拿不到即 `expired`」设计；CRM 应在事件到达后几分钟内拉取。

## 八、CRM 的处理规则（汇总）

- 发送类接口（文字、重试、模板、媒体）的校验顺序：422（请求模型）→ 403 `NOT_ASSIGNED` → 409 `CLAIMED` → `NO_PHONE` →（模板）409 `NO_OPT_IN` → `TEMPLATE_PENDING`
  →（文字、媒体）`WINDOW_CLOSED` →（媒体）文件校验 413 `FILE_TOO_LARGE` / 415 `FILE_TYPE` → `NOT_CONFIGURED`。错误沿用 `fail` 外壳，认领人与到期时间放 `fields`。
- 先存再调、结果三分类（已发出 / 明确没发出 / 结果不明）、重试 45 秒保护、`agent_reply` 补 `external_id`：全部沿用第 2 阶段第六节与第十四节。
- Director 控制台的人工回复不受 CRM 认领约束（控制台没有坐席身份，也不在 CRM 里）；控制台回复照旧以 `console_reply` 进 CRM，推送会刷新页面，客服看得到。

## 九、表结构（迁移号登记时定；019 已预留给 CRM-TASK-020，下面暂按 020 起）

| 迁移 | 表 | 改动 | 属于 |
|---|---|---|---|
| 020 | `contacts` | 加 `reply_claimed_by` 字符串 36 可空（不加外键，与 `assigned_to` 一致）、`reply_claimed_until` 时间可空 | 第 2 项 |
| 021 | `messages` | 加 `template_name` 字符串 100 可空 | 第 3 项 |
| 022 | 新表 `message_attachments` | 见下 | 第 4 项 |

`message_attachments`（一条消息至多一个附件，WhatsApp 一条消息只带一个媒体）：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | 字符串 36 主键 | 也是卷里的文件名 |
| `message_id` | 字符串 36，唯一 | |
| `contact_id` | 字符串 36，可空，索引 | 去重与按联系人清理用 |
| `kind` | 枚举 image / document / audio | |
| `mime_type` | 字符串 100 | 校验后的类型 |
| `file_name` | 字符串 255，可空 | 原文件名，只用于显示与下载 |
| `size_bytes` | 整数，可空 | |
| `sha256` | 字符串 64，可空，索引 | |
| `wa_media_id` | 字符串 200，可空 | 入站才有 |
| `status` | 枚举 pending / fetching / stored / expired / too_large / rejected / failed / purged | |
| `attempts` | 小整数，默认 0 | |
| `error` | 字符串 300，可空 | |
| `stored_path` | 字符串 100，可空 | 卷内相对路径；去重时多个附件可指向同一文件 |
| `created_at`、`updated_at`、`purged_at` | 时间 | |

同一文件被多条附件引用时，删除按引用计数（最后一个引用到期才删文件）。

## 十、需要 Kelvin 拍板（2026-10-30 已定：Q1–Q14 全部按建议）

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| Q1 | 实时刷新的补法（5.1） | A 同一信号，钩子加联系人的几列；B 新增 `contact_changed` 信号带联系人 id，前端只刷新对应会话 | **A**：改一个后端文件、前端不动；B 省几次拉取，但信号带 id 就要再想权限 |
| Q2 | 推送通道本身（SSE、轮询、WebSocket） | 已在 2026-10-21 定为 SSE 并上线 | 确认不变（只单进程；以后多进程换 Redis pub/sub，注释里已写） |
| Q3 | 认领拦不拦（R2） | A 会过期的认领，拦别人发送、可一键接手；B 只显示「X 正在处理」，不拦；C 不认领，发送时带「我看到的最后一条消息 id」，有更新的就拦 | **A**。B 挡不住同时点发送；C 只能发现「有新消息」，挡不住两个人同时回同一条 |
| Q4 | 认领时长 | 5 / 15 / 30 分钟，每次发送续期 | **15 分钟**：够写一条回复和查资料；人走开最多卡 15 分钟，且可接手 |
| Q5 | 谁能接手 | A 能访问该联系人的人都能；B 只有负责人、经理、管理员能接手别人，同级销售不能 | **A**：销售本来只能访问自己的联系人，A 实际就等于 B 加上「经理之间互相接手」，规则少一条 |
| Q6 | 收件箱里的转派（R5） | A 会话头部下拉，复用现有接口；B 不做，仍去联系人页改 | **A** |
| Q7 | CRM 用的模板 | A 新申请 1 个跟进模板（例如参数为访客称呼、公司名，正文类似「您好 {{1}}，关于您之前咨询的事，{{2}} 想继续跟进，方便时回复即可」），英文与中文各一版；B 先用现有 `order_*` 模板 | **A**：订单通知发给 CRM 访客是错的内容。由 Kelvin 在 Meta 后台提交，类别由 Meta 审核决定（跟进类大概率被判 Marketing），审核需要时间，建议设计批准后马上提交 |
| Q8 | 模板选择界面 | A 窗口关闭时显示「发模板」弹窗：选模板 → 填参数（预填名字）→ 预览 → 发送；B 回复框里一个下拉直接切换文字 / 模板 | **A**：窗口外只有模板一种选择，弹窗把参数与预览放在一起，不和文字回复混 |
| Q9 | 媒体存哪 | A CRM 后端挂 VPS 命名卷；B MySQL BLOB；C 对象存储（S3 / R2） | **A**：不引入新服务与新凭据；代价是卷不在数据库备份里，需要另备份或接受丢失（demo，建议接受）。B 让共享 MySQL 膨胀；C 多一套凭据与费用 |
| Q10 | 大小上限 | 入站图片 5 MB、文件 16 MB、语音 16 MB；出站图片 5 MB、PDF 16 MB；CRM nginx 现有 `client_max_body_size 20M` 不变 | 按这个（与 chatbot 现有上限一致，也在 Meta 各类型的上限之内） |
| Q11 | 保留多久 | 30 / 90 天 / 永久；到期只删文件，消息与占位文字保留 | **30 天**：demo 不承载真实客户，但访客是真人、照片可能含个人信息，少存为好 |
| Q12 | 病毒与类型检查 | A 不上扫描引擎：魔数白名单、非图片一律下载、`nosniff`；B 加 ClamAV 容器扫描 | **A**：ClamAV 常驻要约 1 GB 内存、病毒库要更新，对 demo 不划算；白名单不含可执行与脚本类型。docx / xlsx（可带宏）是否进入站白名单，建议**不进**，只收图片、PDF、音频 |
| Q13 | 发模板的频率限制（5.3 第 4 条） | A 窗口关闭后访客回复前每个联系人 1 条；B 不限；C 每天 N 条 | **A** |
| Q14 | chatbot 侧怎么做 | 沿用 P5：人工会话做，不登记进 OpenClaw | 沿用 |

## 十一、任务拆分（编号为暂定，登记时以实际为准）

### CRM（OpenClaw，`CRM-TASK-NNN`，暂从 091 起）

| 暂定编号 | 层 | 内容 | 预计改的文件 | 依赖 | 设计闸门 |
|---|---|---|---|---|---|
| 091 | 业务规则 | 5.1：推送钩子加联系人的接管、负责人、认领列 | `backend/app/services/inbox_events.py`、`backend/tests/test_inbox_events.py`、`tasks/todo.md` | — | 不适用 |
| 092 | 数据库 | 迁移 020 认领两列与模型 | `backend/alembic/versions/020_add_contact_reply_claim.py`、`backend/app/models/contact.py`、`backend/tests/test_migration_020.py`、`tasks/todo.md` | — | 本文件 |
| 093 | 业务规则 | 认领、接手、释放的条件更新；文字发送与重试接入认领；转派清认领 | `backend/app/services/claim_service.py`（新）、`backend/app/services/chatbot_send_service.py`、`backend/app/services/contact_service.py`、`backend/tests/test_claim_service.py`、`backend/tests/test_chatbot_send_claim.py`、`tasks/todo.md` | 092 | 本文件 |
| 094 | 接口 | 认领接口、发送与重试返回 409 `CLAIMED`、联系人与会话列表返回认领字段 | `backend/app/routers/contacts.py`、`backend/app/routers/messages.py`、`backend/app/schemas/contact.py`、`backend/app/services/contact_service.py`、`backend/tests/test_contact_claim_api.py`、`tasks/todo.md` | 093 | 本文件 |
| 095 | 前端 | 会话头部认领 / 释放 / 接手、列表小标、409 时「接手并发送」、头部转派下拉；中英文案 | `frontend/src/pages/Inbox/ConversationView.tsx`、`frontend/src/pages/Inbox/MessageList.tsx`、`frontend/src/pages/Inbox/ReplyBox.tsx`、`frontend/src/services/contacts.ts`、`frontend/src/types/index.ts`、`frontend/src/locales/en/inbox.json`、`frontend/src/locales/zh/inbox.json`、`tasks/todo.md` | 094、091 | 不适用（只展示） |
| 096 | 数据库 | 迁移 021 `messages.template_name` 与模型 | `backend/alembic/versions/021_add_message_template_name.py`、`backend/app/models/message.py`、`backend/tests/test_migration_021.py`、`tasks/todo.md` | — | 本文件 |
| 097 | 业务规则 | 读模板白名单、发模板（资格、频率、参数校验、先存再调、结果分类）、`agent_reply` 的 `kind` | `backend/app/services/chatbot_template_service.py`（新）、`backend/app/services/chatbot_send_service.py`、`backend/app/services/chatbot_event_service.py`、`backend/tests/test_chatbot_template_service.py`、`tasks/todo.md` | 096、093、chatbot C5 已部署 | 本文件 |
| 098 | 接口 | `GET /api/messages/whatsapp/templates`、`POST /api/messages/whatsapp/send-template`、事件模型接受 `kind` 与 `template_name`、消息返回 `template_name` | `backend/app/routers/messages.py`、`backend/app/schemas/message.py`、`backend/app/schemas/chatbot_event.py`、`backend/tests/test_template_api.py`、`tasks/todo.md` | 097 | 本文件 |
| 099 | 前端 | 「发模板」弹窗：选模板、填参数、预览、发送；消息上显示「模板 · 名字」；中英文案 | `frontend/src/pages/Inbox/TemplateDialog.tsx`（新）、`frontend/src/pages/Inbox/ConversationView.tsx`、`frontend/src/services/messages.ts`、`frontend/src/types/index.ts`、`frontend/src/locales/en/inbox.json`、`frontend/src/locales/zh/inbox.json`、`tasks/todo.md` | 098 | 不适用 |
| 100 | 运维 | compose 给后端挂命名卷、配置项 `MEDIA_DIR`；`DEPLOY.md` 写卷与备份口径 | `docker-compose.yml`、`backend/app/config.py`、`DEPLOY.md`、`tasks/todo.md` | — | 不适用 |
| 101 | 数据库 | 迁移 022 `message_attachments` 与模型 | `backend/alembic/versions/022_add_message_attachments.py`、`backend/app/models/message_attachment.py`（新）、`backend/app/models/__init__.py`、`backend/tests/test_migration_022.py`、`tasks/todo.md` | — | 本文件 |
| 102 | 业务规则 | 附件存储（魔数校验、去重、写卷、按引用删除）、入站事件建 `pending` 附件 | `backend/app/services/attachment_service.py`（新）、`backend/app/services/chatbot_event_service.py`、`backend/app/schemas/chatbot_event.py`、`backend/tests/test_attachment_service.py`、`tasks/todo.md` | 101、100 | 本文件 |
| 103 | 业务规则 | 后台拉取任务（状态流转、退避、`fetching` 超时回收）与保留期清理 | `backend/app/tasks/media_fetcher.py`（新）、`backend/app/main.py`（启动任务）、`backend/app/services/attachment_service.py`、`backend/tests/test_media_fetcher.py`、`tasks/todo.md` | 102、chatbot C6 已部署 | 本文件 |
| 104 | 接口 | `GET /api/messages/{id}/attachment`、`POST /api/messages/whatsapp/send-media`、消息返回附件信息 | `backend/app/routers/messages.py`、`backend/app/schemas/message.py`、`backend/app/services/chatbot_send_service.py`、`backend/tests/test_attachment_api.py`、`tasks/todo.md` | 102、chatbot C7 已部署 | 本文件 |
| 105 | 前端 | 附件显示（缩略图、大图、下载、取回中 / 失败原因）与回复框附件上传；中英文案 | `frontend/src/pages/Inbox/AttachmentView.tsx`（新）、`frontend/src/pages/Inbox/ConversationView.tsx`、`frontend/src/pages/Inbox/ReplyBox.tsx`、`frontend/src/services/messages.ts`、`frontend/src/types/index.ts`、`frontend/src/locales/en/inbox.json`、`frontend/src/locales/zh/inbox.json`、`tasks/todo.md` | 104、103 | 不适用 |

注：104 文件数与验收条目可能接近拆分线（两个接口），登记时视设计预审结果再拆成「查看」「发送」两个任务。后端测试只在 CI（`backend`）执行，不写进 `allowed_commands`。

### `ai_chatbot_demo`（人工会话，沿用 P5，待 Q14 确认）

| 暂定编号 | 内容 | 预计改的文件（读代码推断） |
|---|---|---|
| C5 | 模板白名单配置、`POST /internal/crm/templates`、`POST /internal/crm/send-template`（与 `send` 共用幂等、发送、接管、历史、`agent_reply`；`agent_reply` 加 `kind` / `template_name`） | `backend/app/config.py`、`backend/app/routers/internal_crm.py`、`backend/app/services/crm_send.py`、`backend/app/services/crm_events.py`、`backend/tests/` 下对应测试 |
| C6 | `customer_message` 带 `media` 元数据；`POST /internal/crm/media` 代取（白名单、上限、不落盘） | `backend/app/routers/whatsapp_webhook.py`、`backend/app/services/whatsapp_media.py`、`backend/app/routers/internal_crm.py`、对应测试 |
| C7 | `POST /internal/crm/send-media`：multipart 验签、上传到 Meta、新增 `build_image_message`、发 image / document | `backend/app/services/whatsapp.py`、`backend/app/services/crm_send.py`、`backend/app/routers/internal_crm.py`、对应测试 |

### 上线顺序

1. **CRM 单边，不等 chatbot**：091 → 092 → 093 → 094 → 095，真机验收（两个账号同时回同一访客、接手、转派、接管变化实时出现在头部）。
2. **模板**：设计批准后 Kelvin 立刻在 Meta 提交 Q7 的模板（审核与开发并行）→ 096 → chatbot C5 部署（白名单先空）→ 097 → 098 → 099
   → 模板获批、写进 chatbot 白名单 → 真机验收（窗口外发模板、访客回复后接管中、频率限制、未确认送出与重试）。
3. **媒体**：100 → 101 → 102 → chatbot C6 部署（CRM 102 已能接 `media` 字段；C6 早于 102 也无害，CRM 忽略）→ 103 → chatbot C7 → 104 → 105
   → 真机验收（访客发图片 / PDF / 语音、客服发图片 / PDF、超限与类型被拒、保留期清理用短期配置验证）。
   第 2、3 组之间没有依赖，可以并行；建议先模板（改动小，且 Meta 审核要提前起跑）。

## 十二、每条改变数据的规则：操作前 → 操作 → 操作后

例子里：联系人 C1（负责人销售 S1「Mei」，经理 M1「Lim」），现在 10:00，C1 最后入站 09:00。

1. **发送即认领**
   - 前：C1 无人认领。
   - 操作：S1 发「您好」。
   - 后：`reply_claimed_by=S1`、`reply_claimed_until=10:15`；消息照第 2 阶段发出。页面推送后 M1 的收件箱显示「Mei 正在处理」。
2. **别人认领时发送**
   - 前：S1 认领到 10:15。
   - 操作：10:05 M1 发消息。
   - 后：不存、不调用 chatbot；409 `CLAIMED`，`fields` 带 S1、Mei、10:15；M1 的输入框文字还在。
3. **接手**
   - 前：同上。
   - 操作：M1 点「接手并发送」。
   - 后：`reply_claimed_by=M1`、到期 10:20；消息发出，`sent_by=M1`。10:06 S1 再发：409 `CLAIMED`（Lim 到 10:20）。
4. **同时发送**
   - 前：C1 无人认领。
   - 操作：S1 与 M1 同一时刻点发送。
   - 后：条件更新只有一个影响 1 行；先到的发出并认领，另一个 409。访客只收到一条。
5. **认领过期**
   - 前：S1 认领到 10:15。
   - 操作：10:16 M1 发消息。
   - 后：条件更新命中「已过期」，M1 认领到 10:31，发出。不需要清理任务。
6. **转派**
   - 前：S1 认领中。
   - 操作：M1 把 C1 转给销售 S2。
   - 后：`assigned_to=S2`、消息随行（现有）、认领清空；推送刷新各页面。S1 之后发送：403 `NOT_ASSIGNED`（已无权访问）。
7. **接管变化推送**
   - 前：C1 接管中，S1 打开着会话。
   - 操作：Director 控制台交还，chatbot 推 `handover_changed`。
   - 后：联系人三列改变 → 提交后广播 → S1 的会话头部变成「AI 在回」，无需刷新。
8. **发模板**
   - 前：C1 最后入站是前天（窗口关闭），近两天没有模板消息。
   - 操作：S1 选 `crm_followup`、参数「Mei」「Acuven」，发送。
   - 后：存消息 `template_name=crm_followup`、`body`=渲染正文；chatbot 返回 `sent` → `external_id`、`sent`、接管开始 24 小时；S1 认领到 +15 分钟。
   - 再操作：S1 再发一条模板。后：409 `TEMPLATE_PENDING`。访客回复后窗口重开，可以发文字；再次关闭后又可以发 1 条模板。
9. **向从没来过消息的联系人发模板**
   - 前：C2 是表格导入的，没有任何 WhatsApp 入站。
   - 后：409 `NO_OPT_IN`，不存、不调用。
10. **入站图片**
    - 前：无。
    - 操作：`customer_message` 带 `media`（image、`media_id=MID1`、image/jpeg、800 KB）。
    - 后：消息 `body=[image] 说明`；附件 A1 `pending`。后台任务拉到文件、魔数是 jpeg → 写卷、A1 `stored`、记 `sha256`。
11. **入站媒体取不到**
    - 前：A2 `pending`。
    - 操作：chatbot 代取返回 404（Meta 已过期）。
    - 后：A2 `expired`；会话显示占位文字加「文件已无法取回」。
12. **出站 PDF 明确没发出**
    - 前：无。
    - 操作：S1 发 `quote.pdf`；chatbot 返回 502 `rejected`。
    - 后：消息与附件行删除、卷里的文件删除；返回 502 `SEND_FAILED`。
13. **保留期到**
    - 前：A1 `stored`，创建于 31 天前（Q11 定 30 天）。
    - 操作：清理任务运行。
    - 后：没有其他附件引用同一文件 → 删文件；A1 `purged`、`purged_at` 记时间；消息与占位文字仍在。

## 十三、边缘情况

- **认领人被停用或删除**：认领照常到期；接手不受影响。名字查不到时显示 id。
- **认领与第 2 阶段「发送中不能重试」**：45 秒内的重试本来就 409 `IN_PROGRESS`，与认领无关；45 秒后的重试过认领。
- **客服时钟与服务器**：认领到期按服务器时间算；前端显示的倒计时只作参考，以接口为准。
- **推送多了**：认领每次发送都续期，会多一次推送与重拉；每次发送本来就写消息、本来就推送，同一次提交只推一次。
- **模板白名单里有、Meta 那边被停用或改了类别**：chatbot 发送得到 Meta 4xx → `rejected` 如实返回；Kelvin 从白名单移除即可。
- **模板参数里的个人数据**：只发给 Meta，CRM 存渲染后的正文（与文字回复同等）。
- **模板发出后访客一直不回**：窗口一直关闭，`TEMPLATE_PENDING` 一直挡住；接受（不能对不回应的访客反复发）。若要再发，需 Kelvin 另定规则。
- **入站媒体事件先于 CRM 升级到达**：CRM 忽略 `media`，只有占位文字；升级后不回补（事件已处理）。接受。
- **同一张图被多个联系人发来**：去重只在同一联系人内（跨联系人共享文件会让一个联系人的删除影响另一个，不值得）。
- **卷丢失或被清**：附件记录 `stored` 但文件不在 → 查看接口返回 404 `FILE_MISSING`，前端显示「文件已不在」；不自动重拉（Meta 多半已过期）。
- **大文件占后端内存**：入站与出站都在内存里过一遍（≤16 MB），单进程并发数小，接受；以后放大上限再改流式。
- **语音**：转写文字仍由 chatbot 产生、放在 `body`；音频文件另存，可播放（浏览器对 ogg/opus 的支持不一，Safari 播不了时只给下载）。
- **AI 功能与附件**：起草回复看不到图片内容，客服要自己看图；接受，本阶段不把附件送给 CRM 的 AI。

## 十四、登记 CRM-TASK-091 至 095 时的补充（2026-10-30）

登记第一批（第 1、2 项，只涉及 CRM）时补的，不改第一至十三节已批准的规则；对应任务的验收标准按这里写。

1. **编号**：第十一节的暂定编号按依赖重排——迁移先做，推送补缺口要用到认领列：
   CRM-TASK-091 = 认领迁移（原暂定 092），092 = 推送补缺口（原 091），093 至 095 不变。模板与媒体的 096 至 105 仍是暂定，登记时再定。
2. **迁移号**：认领迁移取 **019**（`down_revision` 为 018）；原预留 019 的 CRM-TASK-020（删 `is_gateway` 列）顺延为 **020**，并依赖 CRM-TASK-091。
   第九节表里模板与媒体的迁移随之暂按 020 之后的下一个空号，登记时再定。
3. **发送时什么时候抢认领**：第八节的校验顺序不变（403 → `CLAIMED` → `NO_PHONE` → `WINDOW_CLOSED` → `NOT_CONFIGURED`），
   其中 `CLAIMED` 这一步只读判断；全部校验通过、存消息之前再用条件更新抢或续认领，影响 0 行（其间被别人抢走）同样返回 `CLAIMED`。
   这样被 `WINDOW_CLOSED` 等拒掉的发送不会留下认领。抢认领与存消息在同一个事务里提交。
4. **释放别人的认领**：第 5.2 节的「释放」改写死为——没有有效认领或认领人是自己时直接清空；有效认领是别人的，请求须带 `takeover=true`，否则 409 `CLAIMED`。
   避免误点就清掉同事正在处理的标记。
5. **批量更新的推送判断**：第 5.1 节「批量 `update(Contact)` 也置」限定为 SET 里含被监听的列（接管三列、`assigned_to`、认领两列）才置标记；
   只改别的列（例如清 BSUID）的批量更新不推送。
6. **093 与 094 之间的过渡**：093 部署后、094 部署前，发送遇到别人的认领时，现有接口把未知的结果类别映射成 403 `NOT_ASSIGNED`；
   这段时间只有「15 分钟内两个人回同一个访客」会碰到，接受，094 部署后变成 409 `CLAIMED`。
7. **认领的输出**：联系人详情、联系人列表、会话列表的每一项都带 `reply_claimed_by`、`reply_claimed_by_name`、`reply_claimed_until`（带 UTC 标记）；
   认领已过期或没有认领时三项都为空。会话列表按每页的联系人一次批量查询，不逐条查库。
