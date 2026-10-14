# crm_os 任务清单

## 规划（OpenClaw planning-v1）

> 下面两个标记之间的块由 OpenClaw 控制面按固定格式解析，决定下一项任务；只管「做什么、先做什么」，执行权限仍以 `.platform/tasks.yaml` 为准。格式不对整块作废，项目 fail closed。

<!-- 块内每一行只能是下面三种格式之一（空行可以有，别的内容一律不行，包括注释）：
     当前计划：`1. `<任务 id>` <标题>`，从 1 连续编号，最多 20 项；每一项都必须在 tasks.yaml 登记为 ready，第一项就是下一次「开启」的任务。
     已阻塞：  `- `<任务 id>` <标题>｜阻塞：<原因>` 或 `- 待登记：<标题>｜阻塞：<原因>`（分隔符是全角竖线）。
     后续计划：`- `<任务 id>` <标题>` 或 `- 待登记：<标题>`。
     任务 id 必须符合控制面登记的 task_id_pattern，全块不重复；tasks.yaml 里每个 ready 任务都必须出现在块里。
     「待登记」行不带 id，只给人看，不会变成可执行的东西。三个标题与两个标记逐字不改。 -->

<!-- openclaw:planning-v1:begin -->
### 当前计划

1. `CRM-TASK-016` 收件箱回复改经 chatbot，显示发送人、未确认与重试、窗口关闭和交还 AI
2. `CRM-TASK-021` 收件箱AI辅助的审计表迁移与模型
3. `CRM-TASK-022` 收件箱AI辅助的调用客户端、会话快照组装与审计写入
4. `CRM-TASK-023` 收件箱三个AI功能的提示词、输出结构与输出校验
5. `CRM-TASK-024` 收件箱AI辅助的三个接口与是否已配置的查询
6. `CRM-TASK-025` 收件箱会话顶部加AI会话简报卡片
7. `CRM-TASK-026` 回复框加AI起草与翻译，消息气泡加翻译

### 已阻塞

- 待登记：CRM AI 客户摘要接入 Billing Hub 的 AI 模块｜阻塞：等 acuven_ai_api 并入 ai_billing_hub 的 AI 模块上线与第 3 阶段验收
- 待登记：跟进记录 AI 整理成结构化记录并生成任务草稿（任务八）｜阻塞：待设计，等 AI 模块上线
- `CRM-TASK-017` 删掉 CRM 自己的 Meta webhook、网关入口与直连发送及其配置｜阻塞：等第 2 阶段真机验收通过（chatbot 侧 C3、C4 上线并配好第二把密钥）

### 后续计划

- `CRM-TASK-018` 去掉 is_gateway 的全部用处，原有网关联系人回到列表与统计
- `CRM-TASK-019` 联系人模型取消 is_gateway 映射，列先留在库里
- `CRM-TASK-020` 删除联系人表的 is_gateway 列
<!-- openclaw:planning-v1:end -->

---

# 任务一：把 demo 数据从真实业务视图里排除（✅ 已完成）

> 提出时间：2026-08-18
> 背景：`whatsapp_gateway` 的 CRM demo 上线后，每个来体验的访客都会在真实 CRM 里
> 创建 `Contact` + 一条 `status=lead` 的 `Deal` + 若干 `Message`，污染真实业务数据。
> `Contact.is_gateway` 字段已存在，且有索引 `idx_contacts_is_gateway`，过滤成本很低。

## 核心取舍

**收件箱必须保留 demo 会话。** CRM demo 的卖点就是「消息进到 CRM 后台，由真人回复」——
如果把 demo 联系人从所有地方排除掉，客服就没法回复，demo 本身就废了。

所以口径是：**排除出「统计与业务列表」，保留在「收件箱与单条会话」**。

| 视图 | 是否排除 demo | 理由 |
|------|--------------|------|
| Dashboard KPI / 漏斗 | ✅ 排除 | demo 会虚增新增线索数、商机数 |
| Analytics 图表 | ✅ 排除 | 同上 |
| 联系人列表 | ✅ 排除 | 销售不该看到一堆 demo 访客 |
| 商机列表 / Pipeline | ✅ 排除 | demo Deal 全是 amount=0 的 lead，污染漏斗 |
| **消息收件箱** | ❌ 保留 | 客服要在这里回复 demo 访客 |
| **联系人详情（按 id 查）** | ❌ 保留 | 从收件箱点进去要能打开 |

## 实施拆分（按「超 3 个文件先拆分」规则分两阶段）

### 阶段 A —— 统计口径（3 个文件）✅ 2026-08-18 完成

- [x] 新增 `backend/app/utils/demo_scope.py`，提供三个查询谓词：
  - `contact_not_demo()` → `Contact.is_gateway.is_(False)`
  - `deal_not_demo()` → `Deal.contact_id.notin_(...)`。`Deal.contact_id` 是 NOT NULL，裸 `notin_` 安全
  - `message_not_demo()` → **`Message.contact_id` 可空**，而 `NULL NOT IN (...)` 求值为 NULL 会把
    行整个滤掉，所以必须显式 `or_(Message.contact_id.is_(None), ...)`，否则无联系人的邮件会被静默丢弃
- [x] `backend/app/services/dashboard_service.py`：把 `_deal_alive()` / `_contact_alive()`
      改名为 `_deal_in_stats()` / `_contact_in_stats()` 并在body里合入谓词。
      **这个文件里 24 处统计查询全部走这两个 helper，改 helper 即全覆盖**（admin / manager /
      sales 三套看板 + 两个漏斗），不需要逐个调用点改
- [x] `backend/app/routers/analytics.py`：`_get_scoped_deal_conditions()` 加 `deal_not_demo()`
      （该 router 所有 Deal 查询都由它构造），渠道分布查询加 `message_not_demo()`

验收：新增 `backend/tests/test_demo_scope.py` 3 项测试，红绿对照通过 —— 未加过滤时
`test_admin_dashboard_excludes_demo` 和 `test_analytics_deal_scope_excludes_demo` 断言失败，
加上后全套 11 项通过。

踩到的坑：analytics 端点本身无法在测试里跑，它的趋势查询用了 MySQL 专有的 `func.date_format`，
SQLite 报 `OperationalError`。改成直接测 `_get_scoped_deal_conditions()` 构造出的条件列表 ——
那才是本次真正改动的地方。（`dashboard_service` 的 manager 看板同样用了 MySQL 专有的
`func.datediff`，目前测试只覆盖 admin 看板。）

### 阶段 B —— 业务列表（3 个文件）✅ 2026-08-18 完成

- [x] `backend/app/services/contact_service.py` 的 `list_contacts()` 加 `contact_not_demo()`
      （列表、总数、排序共用同一个 `query`，一处即全覆盖；`_bulk_deal_summary()` 拿的是已过滤的
      contact_ids，不用改）
- [x] `backend/app/services/deal_service.py` 的 `list_deals()` 加 `deal_not_demo()`
- [x] `backend/app/routers/pipeline.py`：该文件已有一个「排除已删除/已归档联系人」的子查询，
      把 `contact_not_demo()` 加进那个子查询即可 —— 两个查询都用 `*base_where`，比再套一层
      `deal_not_demo()` 子查询干净

验收：红绿对照通过 —— 未加过滤时 3 项断言失败（`assert 2 == 1`），加上后全套 **15 项通过**。
其中 `test_demo_contact_still_reachable_by_id` 专门守住那个刻意保留的口子。

### 明确不动

- `backend/app/routers/messages.py` —— 收件箱和单会话，保留 demo
- `contact_service.get_contact()` —— 按 id 查单个，保留

## ⚠️ 已知的遗留问题：`is_gateway` 语义重载（真实号码上线前必须解决）

`Contact.is_gateway` 现在**同时表示两件事**：

1. 「消息从共享网关进来的」—— 传输方式
2. 「是 demo 访客」—— 业务性质

今天这两者重合，因为只有测试号一个号码，所以本任务的过滤是正确的。

**但按多号码架构，真实业务号也会走同一个网关**（见 `whatsapp_gateway/docs/multi-number-architecture.md`）。
到那时真实客户也会带 `is_gateway=True`，本任务加的过滤会**把真实线索从联系人列表、
商机列表、管道和看板里全部藏起来** —— 与「每个新进来的顾客指派给销售跟进」的需求完全相反。

**解决方向**（属于网关设计文档的阶段 4）：网关在转发时带上消息落在哪个号码/哪条线上，
crm_os 据此区分，而不是靠「是否经过网关」。届时把 `is_gateway` 拆成
「传输来源」和「是否 demo」两个概念。

2026-08-18 与用户确认：先按当前方案上线（方案 A），真实号码落地时一并处理。

## 可选项（本次不做，需要再说）

- 前端加「显示 demo 数据」开关，把过滤做成可切换而不是硬排除
- `tasks` / `activities` / `sales_targets` 视图：当前 demo 流程不产生这些数据，暂不处理
- 给 demo 联系人在收件箱里加一个视觉标记（如「DEMO」徽章），避免客服误以为是真实客户
- 历史 demo 数据清理：现有的 demo 联系人已经带 `is_gateway=True`，加过滤后自动生效，
  不需要数据迁移；如果想彻底删除，另开任务

## 评审记录

- 2026-08-18：`dashboard_service.py` 里 24 处统计查询全部走 `_deal_alive()` / `_contact_alive()`
  两个 helper，改 helper 即全覆盖三套看板和两个漏斗，不必逐个调用点改。同理 `analytics.py`
  的所有 Deal 查询都由 `_get_scoped_deal_conditions()` 构造，`pipeline.py` 已有一个联系人子查询。
  **动手前先找 choke point，比逐处加过滤省一个数量级的改动量，也不会漏。**
- 2026-08-18：`Message.contact_id` 可空，`NULL NOT IN (...)` 在 SQL 里求值为 NULL 会把整行滤掉，
  差点静默丢弃所有无联系人的邮件。可空外键上写 `notin_` 一律要配 `is_(None)` 分支。
- 2026-08-18：analytics 端点无法在测试里跑（趋势查询用 MySQL 专有的 `func.date_format`，
  SQLite 报 `OperationalError`），改成直接测被修改的 `_get_scoped_deal_conditions()`。
  `dashboard_service` 的 manager 看板同样用了 `func.datediff`，目前测试只覆盖 admin 看板。

---

# 任务二：WhatsApp 多客服协作（规划中）

> 提出时间：2026-08-18
> 需求：「每个新进来的 CRM 顾客，指派给一个销售人员跟进」，一个 WhatsApp 号码多人共用。
> 平台层面这是可行的 —— 号码接进 Cloud API 后就没有 WhatsApp App 那一层了，
> 谁处理哪个会话完全由 CRM 决定。**注意：号码一旦注册到 Cloud API，
> 就不能再在 WhatsApp Business App 里使用**，员工必须在 CRM 里工作。

## 已经具备的能力（不用重做）

| 能力 | 位置 |
|------|------|
| 新联系人自动分派，三种策略（工作量 / 区域 / 赢率）+ 可配规则优先级 | `routing_service.assign_contact()` |
| 找不到匹配规则时兜底取第一个在职 sales | `routing_service.assign_contact()` 末尾 |
| 收件箱按角色隔离：sales 只看自己、manager 看团队、admin 看全部 | `routers/messages.py:31-38` |
| 从 CRM 后台回复并经网关发出 | 已于 2026-08-17 真机验证 |

## 核实过的缺口（以下均已读代码确认，非推测）

### 缺口 1 · 批量导入的联系人可能完全没有负责人 —— 严重度：高

`import_contacts()` 的负责人取自 `assigned_to_email` 列；该列为空时，**只有当导入者本人是
sales 角色**才回落到导入者自己。**管理员导入一批没有负责人列的线索 → 全部 `assigned_to = None`。**

后果：这些线索在销售和主管的**联系人列表和收件箱里完全不可见**（两处都按 `assigned_to` 过滤），
只有管理员看得到，且没有任何告警。而且 import 这条路径**根本不调用路由引擎**，
与 WhatsApp / 邮件 / 手工创建三条入口的行为不一致。

- [ ] 修复：`import_contacts()` 在没有显式负责人时调用 `routing_service.assign_contact()`

### 缺口 2 · 转派后历史消息留在原负责人名下 —— 严重度：高

`Message.assigned_to` 是消息创建时从 `contact.assigned_to` 拷贝的快照。
`contact_service.update_contact()` 修改 `contact.assigned_to` 时**不会同步已有的 Message 行**。

后果：把客户转给同事后，接手的人（sales 角色）在收件箱里**看不到任何历史消息**，
原负责人反而还看得到。多客服场景下这条最致命 —— 转派等于丢上下文。

- [ ] 修复：`update_contact()` 改动 `assigned_to` 时，同步更新该联系人名下的 Message
- [ ] 或者改为收件箱不按 `Message.assigned_to` 过滤，而是 join 到 `Contact.assigned_to`
      （去掉冗余字段，从根上消除不同步；需评估对邮件等无联系人消息的影响）

### 缺口 3 · 没有会话级的转派与认领机制 —— 严重度：中

只能通过改联系人的 `assigned_to` 来换人，没有会话级的转派或认领接口，
也没有「他人正在处理」的标记 —— 两个销售可能同时回复同一个客户，
而 WhatsApp 那一侧看不出是谁回的。

- [ ] 设计：认领 / 转派接口 + 简单的并发保护（如回复前校验当前归属）
- [ ] 可选：出站消息记录实际操作人，与「归属人」区分开

### 缺口 4 · 悬空会话没有兜底 —— 严重度：低（但影响面大）

因为 `assign_contact()` 有兜底逻辑，只要系统里有至少一个在职 sales，
WhatsApp / 邮件 / 手工创建这三条路径几乎总能分到人。**（这一条我最初高估了严重度，
实际核实后发现路由兜底覆盖了绝大多数情况，真正的悬空来源是上面的缺口 1。）**

剩余风险：`assigned_to = None` 的联系人对 sales 和 manager 完全不可见且无告警。

- [ ] 加一个「未分派」视图或告警，让悬空线索不至于无声无息

## 建议顺序

缺口 1 → 缺口 2 → 缺口 3。前两个是明确的 bug，各自独立、可单独验收；
缺口 3 是新功能，需要先定交互再动手。缺口 4 可以并进缺口 1 一起做。

## 实施前须知

按项目规则，每个缺口**先写能重现的测试**，并在未修复的代码上确认它确实失败。
本地无 Docker 且依赖钉死旧版本，测试在 VPS 上用 `ghcr.io/kelvinpang90/crm_os-backend:latest`
镜像挂载临时副本跑（见任务一的做法）。

## 实施拆分（2026-08-18 用户确认三条需求后细化）

用户明确的三条：
1. 批量导入时询问是否自动指派负责人，且可选择指派逻辑
2. 转派后历史消息要转给新负责人
3. 销售发消息时，判断当前客户是否在该销售名下

按「超 3 个文件先拆分」拆成四个子任务，按风险从低到高排：

### 2.1 转派带走历史消息（需求 2）—— 1 个文件 ✅ 2026-08-18 完成

- [x] `contact_service.update_contact()`：在 setattr 循环**之前**先算出 `reassigned`
      （循环会把 `contact.assigned_to` 覆盖掉，之后就比不出变化了），
      然后 `UPDATE messages SET assigned_to = ? WHERE contact_id = ?`
- [x] 测试 `backend/tests/test_contact_reassign.py` 两项：转派后历史消息归属跟着变；
      只改其他字段时不动消息（避免误伤）

红绿对照：未修复时 `test_reassign_transfers_message_history` 失败，修复后全套 17 项通过。

注意：**不动 Deal.assigned_to**。商机归属牵涉业绩归属和提成，不能顺手改，
需要单独决策。

### 2.2 发送前校验客户归属（需求 3）—— 1 个文件 ✅ 2026-08-18 完成

用户定的口径：销售发给非自己名下的客户 → 拒绝；主管可发团队成员名下的；
管理员不受限；**无负责人的客户只有管理员能发，或先分派给人之后才能发**。

- [x] `routers/messages.py` 新增 `_may_message_contact()`，`/whatsapp/send` 和
      `/email/send` 在调用 service **之前**校验，不通过返回 403 `NOT_ASSIGNED`
- [x] 测试 `backend/tests/test_message_permission.py`：9 组权限矩阵 + 1 项端点测试
      （断言 403 且下游 service 一次都没被调用）

实现要点：「无负责人只有管理员能发」不需要特判 —— `assigned_to = None` 既不等于
销售自己的 id，也不在主管的团队列表里，非管理员自动被拒。查不到的联系人同样按拒绝
处理，不泄露它是否存在。

红绿对照：部署版本上越权发送返回 **200 且消息真的发出去了**（mock 被调用），
加守卫后返回 403 且下游未被调用，全套 27 项通过。

### 2.3 导入自动指派 —— 后端（需求 1）—— 3 个文件 ✅ 2026-08-18 完成

- [x] `routing_service.py`：新增 `ASSIGN_STRATEGIES` 和 `assign_by_strategy()`。
      与 `assign_contact()` 的区别：后者跟随配置好的路由规则（策略写在规则里、
      候选人取规则的 target_users），前者由调用方指定策略、候选人是全部在职销售
- [x] `contact_service.import_contacts()`：新增 `auto_assign` / `assign_strategy` 参数。
      **在 `db.add(contact)` 之前解析归属**，这样后面创建的 Deal 能继承同一个负责人 ——
      Deal 用的是 `final_assigned` 变量，晚于 flush 再赋值就会留下 None
- [x] `routers/contacts.py`：multipart 表单加 `auto_assign` / `assign_strategy`，
      并校验策略取值（未知值返回 400）

`"me"` 由 service 层解析（只有它知道 `current_user`），其余三个走 routing_service。
`"region"` 不单独暴露 —— 它依赖路由规则里的 keyword conditions，脱离规则没有意义，
只能通过 `"rules"` 间接生效。

红绿对照：新增 `backend/tests/test_import_assign.py` 8 项（含 3 个策略的参数化），
未实现时 7 项失败，实现后全套 **35 项通过**。测试覆盖了几个容易写错的边界：
显式填了负责人的行不被覆盖（auto_assign 是兜底不是强制）、Deal 继承解析后的负责人、
系统里一个在职销售都没有时不报错只是留空。

### 2.4 导入自动指派 —— 前端（需求 1）—— 4 个文件 ✅ 2026-08-18 完成

- [x] `frontend/src/services/contacts.ts`：`importContacts(file, options?)` 带上表单字段
- [x] `frontend/src/pages/Contacts/ExcelImport.tsx`：勾选框 + 策略下拉（勾上才显示）
- [x] `frontend/src/locales/{en,zh}/contacts.json`：新增 7 个文案 key

比计划多了两个文件（i18n）。原打算沿用文件里已有的硬编码中文，但既然 contacts 命名空间
是现成的，加 key 更正规。注意 **`autoAssign` 这个 key 已被占用** —— 是 `ContactForm.tsx`
里「系统自动分配」下拉选项用的，含义不同，所以新 key 统一加 `import` 前缀。

验证：`npx tsc -b` 通过（exit 0）。

---

# 任务三：公开注册与按 id 接口越权修复（✅ 2026-10-09 完成，分支 `fix/authz-object-checks` 已合并进 master 并推送）

> **Kelvin 2026-10-09 的答复**：Q1 选 A（直接关闭注册）；Q2 demo 账号先不动（「现在没有 demo 和真实的区别，
> 都是自己用」），所以登录页的一键登录按钮保留，步骤 0 不处理账号、chatbot 不换账号；Q3 projects 是演示数据，
> 只把 `seed-demo` 限 admin，其余写操作不变；Q4 同步限 admin + manager；Q5 同意沿用原「不存在」响应；
> Q6 tasks 的 400 改 404；Q7 由我上 VPS 跑只读核对。

> 提出时间：2026-10-09
> 背景：Kelvin 决定 crm_os 接入 acuven_ai_api 做「客户摘要」，并允许把真实客户数据发给模型。
> 在那之前先堵住现有越权：任何人都能自助注册成 sales；注册后能按 id 读写别人的客户。
> AI 接入本身（ai-summary 接口、审计表、前端按钮）是另一个任务，不在本任务范围，
> 但它将来判断「能不能对这个客户生成摘要」时，应直接复用本任务抽出的 `may_access_contact()`。

## 0. 调研中新发现、比注册更严重的一项（P0，需 Kelvin 先拍板）

**登录页源码里写着三个一键登录的 demo 账号，其中一个是 admin**：
`frontend/src/pages/Login/index.tsx:7-9`，`admin@crm.com` / manager / sales 三组邮箱和明文密码
（与 `backend/seed.py` 的 seed 账号一致）。仓库是公开的，登录页本身也显示这几个按钮。

- ai_chatbot_demo 的审查记录（2026-09-02）写着「线上登录页预填的是 demo 管理员」，
  而且 ai_chatbot_demo 现在**就是用 `admin@crm.com` 这个账号**调 crm_os 的 API（`CRM_EMAIL`）。
- **我没有登录线上核实**这几个账号在生产库里是否仍是那组密码（不该拿凭据去试）。如果仍是，
  那么现在任何人点一下就是 admin，下面所有按 id 的检查都形同虚设——admin 本来就不受限。
- 所以这一项必须排在所有代码修复之前，或至少同时上线。

## 1. 核实结果（以实际代码为准，行号对应 2026-10-09 master `58ef7df`）

### 1.1 现有范围规则

| 角色 | 列表口径 | 实现位置（各自内联，`team_ids` 共有 6 份副本） |
|------|---------|------|
| admin | 不限 | — |
| manager | 自己 + 直接下属（`User.manager_id == me`，不递归） | `contact_service.list_contacts`、`deal_service._scope_conditions`、`task_service.list_tasks`、`messages.list_messages`、`pipeline`、`analytics._get_scoped_deal_conditions`、`dashboard_service._get_team_ids` |
| sales | 只看 `assigned_to == me` | 同上 |

各资源的「归属字段」不同。对象级检查必须与列表口径用**同一个字段**，否则会出现「列表里看得到、点进去 404」：

| 资源 | 列表按什么过滤 | 对象检查应按 |
|------|---------------|-------------|
| Contact | `Contact.assigned_to` | `Contact.assigned_to` |
| Deal | `Deal.assigned_to`（不是联系人归属） | `Deal.assigned_to` |
| Task | `Task.assigned_to` | `Task.assigned_to` |
| Message（收件箱） | `Message.assigned_to`（快照，任务 2.1 起转派时同步） | 单条消息按 `Message.assigned_to`；整段会话按 `Contact.assigned_to`（与发送用的 `_may_message_contact` 一致） |

唯一已有的对象级检查：`routers/messages.py:25-54` 的 `_may_message_contact()`，
不存在与无权都返回同一个 403 `NOT_ASSIGNED`。

### 1.2 公开注册

`routers/auth.py:23-47`：无鉴权、无邀请码，`role="sales"`，注册成功直接发 token。

**比「能注册」更严重的后果**：路由引擎的候选人就是「所有在职 sales」
（`routing_service.py:113-114 / 136 / 153`，兜底分支取第一个在职 sales，workload 策略取负载最低的人）。
**一个刚注册、名下零客户的陌生账号，正好是负载最低的 sales，新进来的真实 WhatsApp 线索会被自动分派给他。**
所以即使所有按 id 的接口都加了检查，只要注册不关，陌生人仍能「合法地」拿到真实客户。

### 1.3 按 id 接口逐条核实

✅ = 调研线索属实；➕ = 调研没列、这次新发现。

| # | 接口 | 位置 | 现状 | 风险 |
|---|------|------|------|------|
| C1 ✅ | `GET /api/contacts/{id}` | contacts.py:137 | 只查存在 | 读任意客户全部字段（电话、邮箱、地址、备注） |
| C2 ➕ | `PUT /api/contacts/{id}` | contacts.py:160 | 只查存在；`assigned_to` 可改成任何人 | **提权支点**：sales 把别人的客户转给自己，之后所有检查都放行，而且任务 2.1 会把整段消息历史一起转过来。前端只对 admin/manager 显示负责人下拉（`ContactForm.tsx:31`），后端没有守 |
| C3 ✅ | `GET /api/contacts/{id}/autocount-documents` | contacts.py:204 | 不查 | 读任意客户的 AutoCount 单据（金额、明细） |
| C4 ✅ | `GET /api/contacts/{id}/activities` | contacts.py:216 | 不查 | 读任意客户的跟进记录 |
| C5 ✅ | `POST /api/contacts/{id}/activities` | contacts.py:226 | 不查；`body.deal_id` 也不校验是否属于该联系人 | 往别人的客户上写记录、改 `last_contact`；能把 A 客户的记录挂到 B 的商机上 |
| C6 ➕ | `PATCH /api/contacts/{id}/archive` | contacts.py:176 | 限 admin/manager，但 manager 不限团队 | manager 能归档其他团队的客户 |
| C7 ➕ | `POST /api/contacts/import` 的 `customer_id` 列 | contact_service.py:295-304 | 按 id 给已有联系人加商机，不查归属 | 往别人的客户上挂商机 |
| D1 ✅ | `PUT /api/deals/{id}` | deals.py:38 | 不查；`assigned_to` 可改成任何人 | 改别人商机的金额/状态；把商机转给自己（业绩归属） |
| D2 ✅ | `DELETE /api/deals/{id}` | deals.py:53 | 不查 | 删别人的商机 |
| D3 ✅ | `GET /api/deals/{id}/activities` | deals.py:68 | 不查 | 读别人的跟进记录 |
| D4 ✅ | `POST /api/deals/{id}/activities` | deals.py:78 | 只查存在 | 写别人商机的记录 |
| D5 ➕ | `POST /api/deals` | deals.py:27 | `contact_id` 不查归属，`assigned_to` 可填任何人 | 给别人的客户建商机并归到自己名下，再通过商机读到对方客户的名字和公司 |
| T1 ✅ | `GET / PUT / PATCH toggle / DELETE /api/tasks/{id}` | tasks.py:52-100 | 不查 | 读、写、删任意任务 |
| T2 ➕ | `POST /api/tasks`、`PUT /api/tasks/{id}` 的 `contact_id` / `assigned_to` | task_service.py:100-127 | 不校验 | 把任务挂到别人的客户上（任务详情会回显 `contact_name`）、分派给任意人 |
| T3 ➕ | tasks 的「不存在」响应 | tasks.py:60/75/87/99 | `fail(message=..., code=404)` 实际返回 **HTTP 400**、`code="404"` | 小 bug，见步骤 5 |
| M1 ✅ | `GET /api/messages/contact/{id}` | messages.py:111 | 不查 | 读任意客户的完整聊天记录（demo 访客和真实客户都包括） |
| M2 ✅ | `PATCH /api/messages/{id}/read` | messages.py:126 | 不查 | 把别人收件箱里的未读标成已读（让同事漏看消息） |
| P1 ✅ | projects 整个模块 | projects.py | 任何登录用户全权 | **`POST /api/projects/seed-demo` 会清空 projects 两张表再重灌 demo**（project_service.py:159-174）：刚注册的账号一个请求就能抹掉全部项目数据。`Project` 表**没有任何归属字段**，做不了「只看自己的」检查 |
| A1 ✅ | `POST /api/autocount/sync` | autocount.py:19 | 任何登录用户 | 触发全量同步，改写联系人数据、消耗 AutoCount API；前端同步按钮对所有角色可见（`Contacts/index.tsx:135`） |
| S1 ➕ | `POST / PUT /api/sales-targets` | sales_targets.py:68/102 | 限 admin/manager，但 manager 不限团队 | manager 能改其他团队的业绩目标 |

核实后**没有问题**的：`/api/users`（列表已按角色收窄，写操作全是 admin）、`/api/routing`（全是 admin）、
`/api/dashboard/*`（按角色分端点）、`/api/pipeline`、`/api/analytics`、`GET /api/deals?contact_id=`、
`GET /api/messages?contact_id=`（都套了列表口径）、`DELETE /api/contacts/{id}`（限 admin）。

## 2. 方案

### 2.1 一个共用的对象级检查模块

新增 `backend/app/services/access_service.py`，所有对象级判断只在这里写一次：

```python
async def team_ids(db, user) -> list[str]                  # manager 自己 + 直接下属
async def may_access_owner(db, user, owner_id) -> bool     # 核心规则，下面几个都调它
async def may_access_contact(db, user, contact_id) -> bool # 查 Contact.assigned_to（未删除）
async def may_access_deal(db, user, deal_id) -> bool       # 查 Deal.assigned_to（未删除）
async def may_access_task(db, user, task_id) -> bool       # 查 Task.assigned_to
async def may_assign_to(db, user, target_id) -> bool       # 能否把东西分派给 target
```

`may_access_owner` 的规则就是现有 `_may_message_contact` 的规则，原样搬过来：
admin → True；`owner_id is None` → False；manager → `owner_id in team_ids`；sales → `owner_id == user.id`。
`_may_message_contact` 改成直接调 `may_access_contact`（行为不变，靠现有 9 组权限矩阵测试守住）。

`may_assign_to`：admin 可分给任意在职用户；manager 只能分给团队内的人；sales 只能是自己。
**这不是新需求**：前端本来就只给 admin/manager 显示负责人下拉（`ContactForm.tsx:31`、`DealEditModal.tsx:21`），
sales 的任务负责人下拉也只列得出自己（`/api/users` 对 sales 只返回本人）。后端只是补上前端已经表达的意图。
注意：前端编辑时可能把**没改过的**原负责人一并提交，所以只在「值确实变了」时才校验。

**未分派（`assigned_to = NULL`）的对象只有 admin 能触达**。这与 `_may_message_contact` 现有口径一致，
也与列表一致（列表对 sales/manager 本来就不显示它们）。这正是任务二缺口 4 的现状，本任务不改变它，
只让按 id 的入口与列表保持一致；「未分派视图 / 告警」仍作为缺口 4 单独做。

### 2.2 越权时返回什么：沿用该接口原本的「不存在」响应

建议规则：**越权的响应与该接口原本的「找不到」响应完全相同**。

- contacts / deals / projects 原本找不到时返回 `404 NOT_FOUND`，越权也返回 `404 NOT_FOUND`
- messages 的发送原本对「不存在 / 无权」统一返回 `403 NOT_ASSIGNED`，会话读取、标记已读也沿用 `403 NOT_ASSIGNED`
  （同一个 router 内保持一致；Inbox 的 ReplyBox 已经处理这个 code）
- 列表类子资源（activities、autocount-documents）原本对不存在的联系人返回空数组；越权时改为返回 `404 NOT_FOUND`
  （返回空数组会让人以为「这个客户没有记录」；前端只会从已可见的客户点进来，正常流程不会触发）

理由：与 `_may_message_contact`「不存在与无权同样拒绝」的原则一致（不泄露某个 id 是否存在），
而且**前端不需要新增任何错误处理分支**，它对每个接口原本的那个响应已经有处理。
代价：manager 遇到「客户刚被转出团队」时，看到的是「找不到」而不是「无权」。我认为可以接受。

备选：全部统一成 403。好处是语义直白；坏处是 contacts/deals 的前端要加 403 分支，而且 403 等于确认了这个 id 存在。
**不推荐，但这一点 Kelvin 可以改。**

### 2.3 无法按归属判断、只能按角色限制的接口

- **projects**：`Project` 表没有归属字段，只能按角色限制。需要 Kelvin 先回答「projects 里放的是真实项目，还是只给客户看的 demo」（见第 6 节 Q3）。无论哪种，`seed-demo` 都应该只允许 admin 调用。
- **autocount/sync**：建议只允许 admin（或 admin + manager，见 Q4），前端同步按钮按角色隐藏。
- **sales-targets**：manager 的 POST/PUT 要校验目标所属人在自己团队内（复用 `may_assign_to` 的团队判断）。

### 2.4 公开注册：四个选项（由 Kelvin 选）

| 选项 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **A. 直接关闭** | 删掉 `POST /api/auth/register` 和前端注册页、链接；账号只由 admin 在「设置 → 用户」里建（`POST /api/users` 和页面都已存在） | 改动最小，当天就能上线；攻击面直接归零 | admin 要给新人设初始密码再转交；**目前没有「用户自己改密码」的接口**，admin 会一直知道密码（可以另开一个小任务补上「修改我的密码」） |
| **B. 邀请制** | admin 生成一次性邀请链接（带过期时间），被邀请人自己设密码 | admin 不经手密码；能预先指定角色和上级 | 要加新表、迁移和新页面；本项目的邮件发送「暂不启用」，链接得手动转发；工作量是 A 的好几倍 |
| **C. 注册后待审批** | 注册照旧，但新账号 `is_active=False`，admin 在用户列表里启用（`PATCH /api/users/{id}/toggle` 已有） | 保留自助注册；审批界面基本现成 | 公网仍能无限刷账号（垃圾数据、占用邮箱）；admin 有误批的风险。（已核实路由只选 `is_active` 用户，`routing_service.py:113/136/153`，所以审批前的账号不会被分到线索） |
| **D. 环境变量开关** | 加 `ALLOW_REGISTRATION`，默认关闭；需要演示时临时打开 | 生产环境关闭，演示实例可以打开 | 多一个配置项；打开期间的风险和现在一样 |

**我倾向于 A**（团队小，admin 建号的界面已经有了，上线最快），但这要由 Kelvin 决定。
无论选哪个，都要配合一次**生产数据清点**：列出现有的 sales 账号，停用不认识的（它们可能已经被分到了真实线索）。

## 3. 实施拆分（每步 ≤3 个业务文件；每步先写复现测试，并在未修复的代码上确认它失败）

测试沿用任务一的做法，在 VPS 上用 `ghcr.io/kelvinpang90/crm_os-backend:latest` 镜像挂载临时副本跑；
复用 `test_message_permission.py` 的四个用户：REP、OTHER、BOSS（manager，REP 的上级）、ADMIN。

### 步骤 0 · P0 运维处置（不改代码，依赖 Q1/Q2/Q7 的答复）

- [ ] 生产库只读清点：`users` 表里所有 sales 账号及其 `created_at`（找出经注册口进来的陌生账号）；
      seed 的 demo 账号（`admin@crm.com` 等）是否仍存在、是否在职
- [ ] 按 Q2 的决定处理 demo 账号：停用或改密码。**改之前先给 ai_chatbot_demo 建一个专用服务账号，并更新它在 VPS 上的 `.env`**，
      否则 chatbot 的 CRM 工具会全部失败（它现在用的就是 `admin@crm.com`）
- [ ] 停用不认识的账号；把它们名下的客户转给真人（转派会按任务 2.1 的逻辑带走消息历史）

### 步骤 1 · 关闭或改造公开注册：后端（2 个文件，以选项 A 为例）

- `backend/app/routers/auth.py`：删除 `/register` 以及只为它存在的 import
- `backend/app/schemas/auth.py`：删除 `RegisterRequest`
- 先写测试 `backend/tests/test_auth_register.py`：用合法 body 调 `POST /api/auth/register`，断言**不是 200**
  （选 A：404/405；选 C：200 但 `is_active=False` 且返回体里没有 token）。
  在当前代码上它会拿到 200 和 token，所以会先红。
- 如果选 B/C/D，这一步的文件和测试都不同，到时再细化。

### 步骤 2 · 登录页与注册页：前端（3 个文件，以选项 A 为例）

- `frontend/src/App.tsx`：删除 `/register` 路由
- `frontend/src/pages/Login/index.tsx`：删除「去注册」链接；**按 Q2 的决定删除一键登录的 demo 账号和明文密码**
- 删除 `frontend/src/pages/Register/index.tsx`
- 验证：`npx tsc -b`
- 步骤 2b（3 个文件）：清理残留的 `register` 代码，即 `store/authStore.ts`、`services/auth.ts`、`locales/{en,zh}` 里的 `register.*` 文案

### 步骤 3 · 共用检查模块 + 收件箱（2 个文件）

- 新增 `backend/app/services/access_service.py`（见 2.1）
- `backend/app/routers/messages.py`：`_may_message_contact` 改为调 `may_access_contact`；
  M1 `GET /contact/{id}` 加上 `may_access_contact`；M2 `PATCH /{id}/read` 按 `Message.assigned_to` 调 `may_access_owner`
  （与收件箱列表同一口径，没有联系人的邮件也能覆盖到）；越权一律返回 `403 NOT_ASSIGNED`
- 先写测试 `backend/tests/test_access.py`：
  - `may_access_owner` 的权限矩阵（admin / 本人 / 上级 manager / 其他 manager / 其他 sales / owner=None）
  - 端点：OTHER 读 REP 客户的会话，期望 403（现在是 200 并返回消息）；OTHER 把 REP 的消息标成已读，期望 403 且 `is_read` 仍为 False
  - **守住 demo 流程**：一个 `is_gateway=True`、分派给 REP 的 demo 联系人，REP 能读会话、能标已读、能回复；ADMIN 也能
- 现有 `test_message_permission.py` 的 9 组矩阵必须原样通过（这是重构的安全网）

### 步骤 4 · contacts（2 个文件）

- `backend/app/routers/contacts.py`：C1/C3/C4/C5 加上 `may_access_contact`（越权返回 404）；
  C5 额外校验 `body.deal_id` 属于这个 `contact_id`；C2 加上 `may_access_contact`，`assigned_to` 有变化时再校验 `may_assign_to`；
  C6 让 manager 只能归档团队内的客户
- `backend/app/services/contact_service.py`：C7 导入时带 `customer_id` 的行先过 `may_access_contact`，越权时报的错与「Contact not found」相同
- 先写测试 `backend/tests/test_contact_access.py`：OTHER 对 REP 的客户做 GET / PUT / autocount-documents / activities 读写，期望 404（现在是 200）；
  REP 用 PUT 把 OTHER 的客户 `assigned_to` 改成自己，期望 404 且归属不变；REP 提交没改过的原负责人，期望 200；
  BOSS 能读写 REP 的客户；导入一行带 OTHER 客户 id 的数据，该行报错且没有建商机；
  任务一的 `test_demo_contact_still_reachable_by_id` 仍然通过

### 步骤 5 · deals + tasks（2 个文件）

- `backend/app/routers/deals.py`：D1/D2/D3/D4 加上 `may_access_deal`（越权返回 404）；D1 的 `assigned_to` 有变化时校验 `may_assign_to`；
  D5 的 `contact_id` 校验 `may_access_contact`，`assigned_to` 校验 `may_assign_to`
- `backend/app/routers/tasks.py`：T1 加上 `may_access_task`；T2 的 `contact_id` / `assigned_to` 校验方式同上；
  T3 把四处 `fail(message=..., code=404)` 改成 `fail("Task not found", code="NOT_FOUND", status_code=404)`。
  **这四行本来就是这一步要改的**（越权和不存在共用同一个响应），所以顺带纠正。要不要改由 Kelvin 定（Q6）：前端 Tasks 页只做通用报错，影响很小
- 先写测试 `backend/tests/test_deal_task_access.py`：OTHER 修改或删除 REP 的商机、读写它的 activities，期望 404；
  REP 给 OTHER 的客户建商机，期望 404；OTHER 读取、修改、切换、删除 REP 的任务，期望 404（现在是 200）；REP 建任务挂到 OTHER 的客户上，期望 404

### 步骤 6 · 按角色限制的接口（3 个文件，依赖 Q3/Q4）

- `backend/app/routers/autocount.py`：`sync` 改成 `require_role("admin")`（或 admin + manager）
- `backend/app/routers/projects.py`：`seed-demo` 改成 `require_role("admin")`；其余写操作按 Q3 的决定处理
- `backend/app/routers/sales_targets.py`：manager 的 POST/PUT 校验目标所属人在团队内
- 先写测试 `backend/tests/test_role_gates.py`：sales 调 `POST /api/autocount/sync` 和 `POST /api/projects/seed-demo`，期望 403 且下游没有被调用
  （mock `autocount_service.sync_all`；断言 projects 表没有被清空）；manager 修改其他团队成员的目标，期望 403/404

### 步骤 7 · 前端配合（≤3 个文件）

- `frontend/src/pages/Contacts/index.tsx`：同步按钮按 Q4 的角色显示
- `frontend/src/pages/Projects/*`：按 Q3 的决定，隐藏 sales 已经不能用的按钮（重灌 demo、增删改）
- 验证：`npx tsc -b`

### 可选项（本次不做，需要时再说）

- 把列表查询里 6 份内联的 `team_ids` 和角色过滤也收拢到 `access_service`（纯重构、不改行为；这次不顺手做，避免扩大改动面）
- 「修改我的密码」接口和页面（选 A 之后会需要）
- 未分派客户的视图或告警（任务二缺口 4）
- 给 projects 加归属字段，做真正的对象级检查（需要迁移）

## 4. 对前端的影响（哪些页面现在依赖越权读取）

结论：**正常使用路径上没有页面依赖越权读取**。所有按 id 的调用，都是从已经按角色过滤过的列表点进去的。
会受影响的只有下面几处，而且都在预期内：

| 页面 / 组件 | 调用 | 影响 |
|------|------|------|
| 联系人详情 `ContactDetailPanel` / `ActivityTimeline` | autocount-documents、activities、deals?contact_id= | 客户来自已过滤的列表，本人、上级、admin 都能打开，**无影响**。`contactsApi.getContact`（即 `GET /contacts/{id}`）**前端没有任何地方调用** |
| 收件箱 `Inbox` / `ConversationView` / `ReplyBox` | 会话、标已读、发送 | 列表按 `Message.assigned_to` 过滤，会话按 `Contact.assigned_to` 判断。任务 2.1（2026-08-18）之后两者在转派时保持同步，但**更早转派过的老数据可能不一致**：那个人在收件箱里看得到消息，点开会话却得到 403。上线前要跑一次只读核对（见下） |
| demo 访客会话 | 同上 | demo 联系人入站时走 `routing_service.assign_contact()` 分派给某个 sales，所以**按归属判断就能保住「客服能回复 demo 访客」**，检查本身不读 `is_gateway`。分派失败（系统里一个在职 sales 都没有）的 demo 联系人只有 admin 能读能回，与现在发送的口径一致 |
| Pipeline `DealEditModal` | `PUT /deals/{id}` | 商机来自已过滤的 pipeline；manager 的负责人下拉来自 `/api/users`（只列团队成员），**无影响** |
| Tasks 页 / `TaskForm` | 任务的增、改、删、切换 | 任务、客户下拉、负责人下拉都来自已过滤的列表，**无影响** |
| 联系人页「立即同步」按钮 | `POST /autocount/sync` | 非 admin 点击会得到 403，步骤 7 按角色隐藏按钮 |
| Projects 页 | 全部 | 取决于 Q3；限权后要隐藏 sales 的按钮 |
| 登录页 / 注册页 | register、一键 demo 登录 | 步骤 2 删除 |
| **ai_chatbot_demo（跨项目）** | 以 `admin@crm.com` 调 contacts / deals / activities | admin 不受对象检查影响；**但会受步骤 0 改 demo 账号的影响**，要先换成服务账号 |

上线前的只读核对（需要 Kelvin 在 VPS 上跑，或授权我跑）：

```sql
-- 消息归属与联系人归属不一致的条数（>0 时会话页会对部分人返回 403，要先补一次同步）
SELECT COUNT(*) FROM messages m JOIN contacts c ON c.id = m.contact_id
WHERE NOT (m.assigned_to <=> c.assigned_to);
-- 未分派的客户数（这些只有 admin 能触达）
SELECT COUNT(*) FROM contacts WHERE assigned_to IS NULL AND deleted_at IS NULL;
-- 商机归属与客户归属不一致的条数（新负责人看不到这些商机，见边缘情况 2）
SELECT COUNT(*) FROM deals d JOIN contacts c ON c.id = d.contact_id
WHERE d.deleted_at IS NULL AND NOT (d.assigned_to <=> c.assigned_to);
```

## 5. 边缘情况

1. **未分派的对象只有 admin 能触达**（见 2.1）。如果某段时间系统里没有在职 sales，新线索会全部悬空，而且只有 admin 看得见。
2. **商机归属与客户归属分叉**：任务 2.1 刻意不随客户转派商机（涉及业绩归属）。于是客户的新负责人能打开客户，却看不到、改不了老商机；
   老负责人仍能改老商机和它的跟进记录（而这些记录的内容是关于这个客户的）。这与现有列表口径一致，本任务不改，但 Kelvin 应该知道。
3. **manager 只看直接下属**，不递归；多级主管不在本任务范围内。
4. **负责人被停用**：客户仍挂在停用账号名下，所有 sales 都看不到，只有原上级 manager 和 admin 能触达。
5. **转派竞态**：A 打开会话期间客户被转给 B，A 再操作会得到 404/403，符合预期。
6. **PUT 时前端回传未改动的负责人**：只在值变化时校验 `may_assign_to`，否则 sales 编辑自己的客户也会被拒绝。
7. **activities POST 的 `deal_id` 与 `contact_id` 不匹配**：按「商机不存在」拒绝。
8. **导入时 `customer_id` 行越权**：报的错与「Contact not found」相同，不泄露客户是否存在。
9. 每个按 id 的请求多一次主键查询，开销可以忽略。

## 6. 需要 Kelvin 拍板的问题

- **Q1 公开注册**：选 A / B / C / D？（我倾向于 A）
- **Q2 登录页的一键 demo 账号**：生产上这几个账号怎么处理：停用、改密码，还是只留在演示实例里？
  ai_chatbot_demo 要不要换成专用服务账号？（建议要，并且给最小权限；但它的 `delete_contact` 需要 admin，可能要一起调整。）
  更根本的问题是：**放真实客户数据的 crm_os 还要不要同时当给客户看的 demo**？如果要，建议另起一个 demo 实例。
- **Q3 projects 模块**：里面是真实项目还是演示数据？如果是真实项目，写操作只允许 admin/manager（没有归属字段，做不到「只看自己的」）；如果只是演示，可以考虑在这个实例里隐藏
- **Q4 AutoCount 同步**：只允许 admin，还是 admin + manager？
- **Q5 越权响应码**：同意「沿用每个接口原本的不存在响应」（2.2）吗？
- **Q6 tasks 的 400 改 404**（步骤 5 T3）：顺带改，还是保持原状？
- **Q7 步骤 0 的生产库只读核对**：你来跑，还是授权我 SSH 上去跑只读查询？

批准后按步骤 0 → 1 → 2 → 3 依次做；步骤 4–6 之间互相独立，但都依赖步骤 3 提供的共用模块。

## 实施记录（2026-10-09）

全部在分支 `fix/authz-object-checks` 上，6 个提交，**未推送、未部署**。测试照旧在 VPS 上用生产镜像跑：
基线 54 项 → 最终 **150 项全部通过**；每一步的新测试都先在未修复的代码上跑过，确认失败。

- [x] **步骤 0** 生产库只读核对（后端容器内用应用自己的连接，会话设为 READ ONLY）：
      用户只有 6 个 seed 账号，**没有经注册口进来的陌生账号**；消息归属 ≠ 客户归属 **0** 条；
      商机归属 ≠ 客户归属 **0** 条；未分派客户 **0** 个；共 42 客户 / 43 商机 / 50 消息 / 16 任务 / 10 项目。
      所以不需要任何数据修补，会话页也不会出现「列表看得到、点开 403」。按 Q2 不动 demo 账号。
- [x] **步骤 1 + 2 + 2b** `8d35a1e` 关闭注册：删后端 `/register` 和 `RegisterRequest`，删前端路由、注册页、
      登录页的注册链接、store / service 里的 register、两种语言的 `register.*` 文案。按 Q2 **保留登录页的一键 demo 按钮**。
      计划里 1、2、2b 是三步，实际合成一个提交（都是删除，拆开反而会留下指向不存在接口的半成品）。
      测试 `test_auth_register.py`：红（200 + token）→ 绿（404/405，不建用户）
- [x] **步骤 3** `ceee292` 新增 `services/access_service.py`；`messages.py` 的发送、读会话、标已读都改为调它，
      旧的 `_may_message_contact` 删除（`test_message_permission.py` 改为直接测 `may_access_contact`，9 组矩阵原样通过）。
      测试 `test_access.py`：3 红 → 绿；demo 联系人仍能被负责的 sales 和 admin 读、标已读
- [x] **步骤 4** `57c942d` contacts：C1–C7 全部加检查。测试 `test_contact_access.py`：12 红 → 绿
- [x] **步骤 5** `80b2a47` deals + tasks：D1–D5、T1–T3。测试 `test_deal_task_access.py`：16 红 → 绿
- [x] **步骤 6** `f7c2004` 同步限 admin + manager（Q4）；`seed-demo` 限 admin（Q3）；业绩目标限团队内。
      测试 `test_role_gates.py`：5 红 → 绿
- [x] **步骤 7** `f108985` 联系人页同步按钮只对 admin / manager 显示。Projects 页**没有任何地方调用重灌接口**
      （`projectsApi.resetDemo` 无人使用），所以不用改。`npx tsc -b` 通过；**没有在浏览器里实际点过**

测试辅助：新增 `backend/tests/_people.py`（REP / OTHER / BOSS / ADMIN 四个人、三个客户、`logged_in_as()`），
四个新测试文件共用，避免每个文件各写一份。

## 评审记录

- 2026-10-09：**对象检查的依据必须和列表过滤用同一个字段**，否则会出现「列表里看得到、点进去被拒」。
  Deal 按 `Deal.assigned_to`（不是客户归属）、单条消息按 `Message.assigned_to`、会话按 `Contact.assigned_to`，
  都是照各自列表的口径定的。上线前用只读查询确认了生产上这几个字段之间没有分叉。
- 2026-10-09：**编辑表单会把没改的字段原样回传**，这是本次唯一自己踩到的回归。任务可以挂在被分派人打不开的客户上
  （主管把关于自己客户的任务派给下属），下属改个标题就会因为回传的 `contact_id` 被拒。修法是只校验**发生变化**的关联。
  客户和商机不需要这样处理：能打开这条记录，就一定够得着它当前的负责人，回传原负责人总能通过。
- 2026-10-09：「不存在」与「无权」统一成同一个响应，靠的是把检查放在查询之前、并让「不存在」的记录归属为 `None`
  （`None` 只有 admin 能过）。标已读就是这么写的：先按 `msg.assigned_to if msg else None` 判权限，再判是否存在。
- 2026-10-09：做法沿用任务一的教训，**先找 choke point**：所有按 id 的判断收进 `access_service` 一处，
  各个 router 只问一句「能不能」。列表查询里那 6 份内联的 `team_ids` 这次没有顺手收拢（见可选项）。

## 新发现、本次没处理的（留作后续）

- `POST /api/contacts` 和 Excel 导入的 `assigned_to_email` 列仍能把**新建**的客户直接分给任何人（不是越权读取，
  而是创建时的分派）。前端对 sales 不显示负责人下拉，风险低；要堵的话照 `may_assign_to` 加一行。
- 登录页的一键 demo 账号（含 admin）按 Q2 保留。只要它们在生产上有效，**任何能打开登录页的人仍然是 admin**，
  本任务的对象检查对他们不起作用。等「demo 与真实是否分开」有结论时再处理。
  - 2026-10-09 Kelvin：**保留一键 demo 账号**。做 CRM AI 客户摘要（DECISIONS E-12，真实数据）时要记得：
    demo admin 能对所有真实客户触发摘要，届时在那个任务里单独讨论。

---

# 任务四：部署后 nginx 仍指向旧容器 IP 导致全站 502（✅ 已批准 b′；线上已改，2026-10-09）

> 提出时间：2026-10-09
> 现象：部署 `f49e3e5` 时，Deploy CRM 只做了 `docker compose up -d --remove-orphans`，容器重建后 IP 对调
> （前端拿到了后端原来的 172.19.0.7）。infra_nginx 还在用启动 / 上次 reload 时解析的 IP，
> crm.acuventech.com 与 crm.kelvinpeng.com 全站 502，日志 `connect() failed (111) ... upstream: "http://172.19.0.7:8000/..."`。
> 手动 `docker exec infra_nginx nginx -t && nginx -s reload` 后恢复。

## 1. 核实结果

> ⚠️ 本次**没能登录 VPS 读取**（会话权限拦下了对生产机的只读 ssh），以下来自本地仓库与公网探测。
> 本地 `vps_infra/nginx/conf.d/*.conf` 被 `.gitignore` 排除，是 **2026-09-16 16:02 从 VPS 拷下来的快照**，
> 不保证与线上一致（快照里就没有 shop.acuventech.com 的 vhost）。标「待 VPS 核实」的必须实施前再看一眼。

- **http 级 resolver 已经存在**：`vps_infra/nginx/nginx.conf`（git 跟踪）里有
  `resolver 127.0.0.11 valid=10s ipv6=off;`，注释写明就是为了「容器重建后 IP 变了 nginx 缓存旧 IP 返回 502」。
  erp、aisearch、chatbot、whatsappgateway、rsroofpms，以及 **crm.acuventech.com 的前端**，都已经用
  `set $x "容器名:端口"; proxy_pass http://$x;` 的写法。
- **CRM 是唯一的例外**，两处写死：
  1. `upstream crm_backend { server crm_os-backend-1:8000 ...; keepalive 32; }` —— upstream 块里的主机名
     只在启动 / reload 时解析一次。两个 CRM 域名的 `/api/`、`/api/webhooks/`、`/health` 都走它。
     **这正是日志里 `172.19.0.7:8000` 的来源。**
  2. crm.kelvinpeng.com 的 `location /` 是 `proxy_pass http://crm_os-frontend-1:80;`（字面主机名，同样只解析一次）。
     crm.acuventech.com 的前端已经是变量写法，不受影响。
- **配置的「唯一来源」已经分叉**（待 VPS 核实）：
  - `crm_os/nginx/conf.d/crm.conf` 头部注释自称「THE production nginx config」，要求复制到
    `/srv/infra/nginx/conf.d/crm.conf`；它只含 crm.kelvinpeng.com。
  - 但 09-16 快照里 crm 的 upstream 和 crm.kelvinpeng.com 都写在 `kelvinpeng.com.conf`，crm.acuventech.com 写在
    `acuventech.com.conf`，**快照里没有 crm.conf**；而且快照里法律页文案是一句话，和 crm_os 仓库里的长文案不同。
  - 如果 VPS 上 crm.conf 和 kelvinpeng.com.conf **同时存在**，会出现重复的 `upstream crm_backend` / 重复 server_name，
    `nginx -t` 会失败或告警 —— 所以线上大概率只有其中一份。改之前必须先确认到底是哪个文件在生效。
- **nginx 版本**：`vps_infra/docker-compose.yml` 用的是未锁版本的 `nginx:alpine`。方案 (b′) 需要 ≥ 1.27.3（待 VPS 核实 `nginx -v`）。

## 2. 方案比较

| | (a) 部署后 reload | (b) 变量 + resolver | (b′) upstream 里加 `resolve`（**推荐**） |
|---|---|---|---|
| 改哪里 | `crm_os/.github/workflows/deploy.yml` 加一行 | VPS 上 CRM 的 vhost：4 处 `proxy_pass` 改变量 | VPS 上 CRM 的 upstream 块加 2 个词 + crm.kelvinpeng.com 前端一行改变量 |
| 根治？ | ❌ 只覆盖「经工作流部署」这一条路径。手动 `docker compose up`、容器崩溃重启、宿主机重启后的启动顺序、`docker restart` 都还会复现 | ✅ 每 10s 重新解析 | ✅ 同 (b) |
| 残留窗口 | `up -d` 到 reload 之间几秒；若 alembic 失败（`set -e`）reload 根本不会执行 | 最长 10s（resolver `valid=10s`） | 同 (b) |
| 副作用 | reload 是所有站点共享的；**任何别的项目把配置写坏，`nginx -t` 失败会让 CRM 部署失败**——把 CRM 部署和全站配置耦合了 | 变量 `proxy_pass` 绕过 upstream 块，**丢掉 `keepalive 32` 和 `max_fails`**；`/health` 的 `proxy_pass http://$v/api/health` 语义要重新确认 | upstream 块、keepalive、max_fails、`/health` 的写法全部不变 |
| 对其他项目影响 | reload 会让所有站点的 worker 平滑换代（不断连） | 无。resolver 已在 http 级，其他站点早已是这种写法 | 无。同 (b) |
| 前提 | 部署用户能 `docker exec infra_nginx` | 无 | nginx ≥ 1.27.3（开源版在这一版开始支持 upstream `resolve`） |

(b′) 的写法：

```nginx
upstream crm_backend {
    zone crm_backend 64k;                                        # resolve 需要共享内存区
    server crm_os-backend-1:8000 resolve max_fails=2 fail_timeout=10s;
    keepalive 32;
}
# crm.kelvinpeng.com 的 location /：
set $crm_frontend "crm_os-frontend-1:80";   # 与 crm.acuventech.com 现有写法一致
proxy_pass http://$crm_frontend;
```

**推荐 (b′)**，nginx 版本不够时退回 (b)。理由：
1. 问题根源是「CRM 是共享 nginx 里唯一没跟上既定写法的站点」，修它就是让 CRM 回归既有约定，不是引入新机制；
2. 同时覆盖崩溃重启、手动部署、宿主机重启等所有让 IP 变化的路径，(a) 只覆盖一条；
3. 不把 CRM 部署和其他项目的配置健康度绑在一起；
4. 比 (b) 改得更少，并保留 keepalive / 熔断。

**不建议** (a)+(b′) 同时做：(b′) 生效后 reload 多余，还会带回 (a) 的耦合问题。

## 3. 影响范围

- **只动 CRM 自己的 server / upstream 块**，所在文件是共享的（`kelvinpeng.com.conf`，或者如果线上是 crm.conf 就是它），
  不碰其他项目的 vhost，也不改 `nginx.conf`（resolver 已在）。
- 生效需要一次 `nginx -t && nginx -s reload`：平滑重载，影响所有站点但不断连；`-t` 失败则不 reload，旧配置继续服务。
- `vps_infra` 仓库：conf.d 不入 git，所以**不产生 vps_infra 提交**；本地快照可同步更新（可选）。
- `crm_os` 仓库：同步修改 `nginx/conf.d/crm.conf`，并修正它头部「THE production config」的说法（见步骤 1 的结论）。
  该路径在 deploy.yml 的触发列表里，**推送会触发一次部署**——和本修复一起推正好验证。

## 4. 实施步骤（批准后执行，每步都要 Kelvin 放行对生产机的 ssh）

- [x] **0. VPS 只读核实**：`ls -la /srv/infra/nginx/conf.d/`；`grep -rn "crm_backend\|crm_os-" /srv/infra/nginx/conf.d/`
      确认 CRM 配置在哪个文件、有没有重复；`docker exec infra_nginx nginx -v`；`docker exec infra_nginx nginx -T | grep -n resolver`
      **结果（2026-10-09，Kelvin 批准 b′ 后执行）**：
      - 线上 conf.d 只有 `acuventech.com.conf`、`kelvinpeng.com.conf`、`mcinter…`、`ta-cba…` 和一个 `bak/` 目录，**没有 crm.conf**。
        `crm_os/nginx/conf.d/crm.conf` 从来不是线上生效的文件，头部注释是错的。
      - `upstream crm_backend` 只定义一次，在 `kelvinpeng.com.conf:18`；`acuventech.com.conf` 的 crm.acuventech.com 跨文件引用它
        （http 级共享，合法）。所以**只改 kelvinpeng.com.conf 一个文件**就同时修好两个域名的后端；
        前端写死的只有 `kelvinpeng.com.conf:292`。
      - `kelvinpeng.com.conf` 线上 mtime 2026-06-22、8231 字节，和本地快照一致。
      - nginx **1.29.8**（≥ 1.27.3，b′ 可用）；`nginx -T` 里 http 级 `resolver 127.0.0.11 valid=10s ipv6=off` 生效；当前 `nginx -t` 通过。
      - 当前 IP：backend 172.18.0.5 / 172.19.0.10，frontend 172.19.0.7（与事故描述「前端拿到了后端旧 IP」吻合）。
      - demo：线上 `acuventech.com.conf:131` 起仍是 09-16 的下线注释，与 §6 结论一致。access 日志格式不记 Host，
        无法按域名统计 444，这点没法从日志再佐证。
- [x] **1–3 于 2026-10-09 20:25 执行**（Kelvin 放行后）：备份为 `conf.d/bak/kelvinpeng.com.conf.bak-20261009-resolve`；
      `nginx -t` 通过后 reload；两个域名 `/`、`/api/health` 全 200，reload 后 nginx 无 emerg/error/warn；
      `nginx -T` 确认 `zone crm_backend` / `resolve` / `$crm_frontend_kp` 已加载。
      **根治验证只做了一半**：`up -d --force-recreate frontend backend` 后 Docker 把**原 IP 原样还回来了**（backend 172.19.0.10、
      frontend 172.19.0.7 不变），所以这次重建没有证明「IP 变了也能自愈」。想用临时占位容器占住旧 IP、逼新容器换 IP，
      这一步被会话权限拦下，没有做。
- [x] **1. 备份**：`cp <文件> <文件>.bak-20261009`
- [x] **2. 改 VPS 上的 CRM 配置**：按 (b′)（或 (b)）修改；`docker exec infra_nginx nginx -t` 通过后 `nginx -s reload`
- [~] **3. 验证根治**（见上：IP 没变，未能证实）（不是只看 200）：两个域名 `/`、`/api/health` 200 →
      在 VPS 上 `docker compose -f /opt/crm_os/docker-compose.yml up -d --force-recreate backend frontend`（**不 reload nginx**）→
      等 10–15s 再测两个域名都 200，并看 `docker inspect` 确认 IP 确实变了
- [x] **4. crm_os 仓库**（`cbe8c1f`，等线上 nginx 改完后才推送）：`nginx/conf.d/crm.conf` 改成与线上一致的写法，修正头部注释说明真实来源；deploy.yml 里那段
      「手动同步 nginx」的注释按步骤 0 结论更新
- [x] **5. 推送 + 观察 Deploy CRM**（`35cc857`，run 37930139077 build/deploy 均 success）：部署后两个域名 `/`、`/api/health` 全 200。
      部署期间每 2s 探测一次：只在 **20:28:12** 两个域名的 `/api/health` 各 502 一次——nginx 日志显示连的是 `172.19.0.10:8000`，
      正是后端**当前**的 IP，容器创建时间也是 20:28:12，所以是 uvicorn 还没起来的一两秒启动空窗，不是旧 IP 问题；`/` 没有失败。
      本次部署 IP 仍然没变（backend 172.19.0.10、frontend 172.19.0.7），所以**「IP 变化后自愈」在生产上仍未被实际触发过**；
      依据是 `nginx -T` 已加载 `resolve` 配置 + 其他站点用同一 resolver 的长期表现。下次真的发生 IP 对调时看 nginx 日志即可确认。
      （启动空窗如要消除，需要给 backend 加 healthcheck / 滚动替换，不在本任务范围）
      原计划：部署结束后两个域名仍 200、nginx 日志无新 `connect() failed`

回滚：`cp <文件>.bak-20261009 <文件> && nginx -t && nginx -s reload`。

## 5. 边缘情况

- 容器重建的那几秒：旧 IP 可能已经被别的容器拿走（这次就是前端拿到了后端旧 IP），在 10s 有效期内请求可能打到错的容器。
  后端端口 8000 在前端容器上不监听 → 502 几秒后自愈。可接受；要更短可把 `valid` 调小，但那是全站 resolver，不建议为此改。
- `resolve` 下如果启动时容器名解析不到，nginx **照样能启动**（服务器标记为不可用）—— 比现在更好：
  现在 crm 容器不在时 infra_nginx 重启会因为 upstream 解析失败**整个起不来，拖垮所有站点**。
- nginx:alpine 未锁版本：以后有人 `docker compose pull` 拿到新版本不影响 (b′)（只会更新，不会降到 1.27.3 以下）。

## 6. 顺带：demo.acuventech.com 返回 520 的原因

**结论：是 2026-09-16 主动下线的预期结果，不是 10-09 19:18 那次 reload 引起的（证据见下，未看 VPS 日志，属高可信推断）。**

- 本地快照 `acuventech.com.conf`（09-16 16:02）里 demo 的 server 块已整段注释，并写明：
  「2026-09-16 已下线（Kelvin：项目不再使用），容器 demo_os_backend 与镜像已删除，数据保留在 /opt/demo_os/data 与 infra_mysql 的 demo_os 库」。
- 注释掉后，这个域名落到 `nginx.conf` 的默认 server（`server_name _`，`return 444`）——直接断开连接、不回任何响应。
- 2026-10-09 实测：经 Cloudflare 访问 → `520`（`server: cloudflare`）；绕过 Cloudflare 直连源站 IP → curl 退出码 56
  （连接被对端关闭），正是 444 的表现。Cloudflare 收到空响应就报 520。
- 当时的注释写着「curl 显示为 000，已实测确认」，说明 09-16 就已经是这个状态。19:18 的 reload 只会重新加载同一份配置，
  不会让一个早已注释掉的站点改变行为。
- 待 VPS 核实（可选）：`grep -n demo /srv/infra/nginx/conf.d/*.conf` 确认线上仍是注释状态；
  `docker logs infra_nginx --since 2026-10-09T00:00 | grep demo` 看有没有 444。
- 2026-10-09 后续（已处理）：上 VPS 核实 demo_os 容器与镜像都已删除，`/opt/demo_os/data`（87M）和 infra_mysql 的 `demo_os` 库
  （388K）仍保留；Kelvin 在 Cloudflare 删除了 demo 的 DNS 记录（acuventech.com 没有泛解析，删后权威 NS 已不返回地址，
  curl 报无法解析）；`E:\projects\CLAUDE.md` / `AGENTS.md` 已把 demo_os 标为下线。

---

# 任务五：部署改成按提交 SHA（✅ 2026-10-09 上线，PR kelvinpang90/crm_os#1）

依据：`acuven_hub/DECISIONS.md` I-07——`crm_os` 改成按提交 SHA 部署后，才登记进 OpenClaw。
目标：`deploy.yml` 满足模板的部署观察契约 D1–D5（`acuven-project-template/TEMPLATE-GUIDE.md`）。
做法：照搬 `acuven-shop` 已在生产跑通的 `deploy.yml` + `deploy/deploy.sh`，只改服务名、镜像名。

## 1. 现状（2026-10-09 仓库内核实）

- `deploy.yml`：push 到 master/main 触发；镜像打了 `latest` 和 `<sha>` 两个标签，但服务器上是
  `git pull --ff-only` + `docker compose pull`（拉 `latest`）→ **部署的是“最新”，不是触发它的那个提交（违反 D2）**。
- 迁移在 `up -d` **之后**才跑：新代码会先对着旧表结构跑一会儿。
- 没有 `concurrency`、`timeout-minutes`（D4、D5），没有部署后健康检查、没有回滚（D3）。
- `docker-compose.yml` 写死 `:latest`，没有 healthcheck；`/api/health` 不报版本，无法证明线上是哪个提交。
- 仓库 secrets 只有 `VPS_HOST/PORT/USER/SSH_KEY`；缺 `VPS_FINGERPRINT`、`VPS_APP_DIR`，也没有变量 `HEALTHCHECK_URL`。
- **未核实**：VPS 上 `/opt/crm_os` 的工作区是否干净（本地改动会挡住 `git checkout --detach`）、当前容器名。

## 2. 实施拆分（每步 ≤3 个文件）

- [x] **步骤 0 · VPS 只读核对**（2026-10-09）：`/opt/crm_os` 在 master `35cc857`，只有两个未跟踪的 `.env.bak.*`，
      不会挡 checkout；容器 `crm_os-backend-1` / `crm_os-frontend-1`，都跑 `:latest`；`.env` 里没有 `CRM_*_IMAGE`。
- [x] **步骤 1 · 健康检查报版本**（`6755c7e`；新测试 `test_health.py` 2 红 → 绿，全量 152 通过，本地 Docker 跑）（2 个文件）：`backend/Dockerfile` 加 `ARG GIT_SHA` → `ENV`；
      `/api/health` 多返回 `git_sha`。先写测试（红→绿）。这一步仍走旧流程上线，无风险。
- [x] **步骤 2 · 新部署流程**（`2a5d989`；compose 插值、前端 healthcheck 已在本地验证；去掉了原来的 `paths` 过滤，满足 D1）（3 个文件）：
  - `docker-compose.yml`：镜像改为 `${CRM_BACKEND_IMAGE:?}` / `${CRM_FRONTEND_IMAGE:?}`，服务名不变；
    加 healthcheck（backend 用 python 打 `/api/health`，frontend 用 wget）。
  - 新增 `deploy/deploy.sh`：记下正在跑的版本 → pull → `run --rm` 跑迁移 → `up -d` → 等健康 →
    不健康就回滚并非零退出 → 只清本项目（按 label）的旧镜像。
  - `deploy.yml`：只由 master 触发；`workflow_dispatch` 填 40 位 SHA 用于回滚；`concurrency` 不取消；
    两个 job 各 12 分钟；校验主机指纹；参数经 `envs` 传；部署后健康检查必须看到 `DEPLOY_SHA`；
    构建时传 `GIT_SHA`、打 `acuven.project=crm_os` 标签；**不再推 `latest`**。
- [x] **步骤 3 · GitHub 配置**（Kelvin 已设；2026-10-09 核对三项都在，secret 的值无法读出）：secrets `VPS_FINGERPRINT`、`VPS_APP_DIR=/opt/crm_os`；
      变量 `HEALTHCHECK_URL=https://crm.acuventech.com/api/health`。
- [x] **步骤 4 · 上线验证**（2026-10-09，Actions run 37947418108，build + deploy 均 success）：部署前在跑 `:latest`；
      拉镜像 → 迁移 → 换容器 → 6 秒内健康；健康检查第 1 次就报出合并提交 `99886a9`，
      crm.acuventech.com 与 crm.kelvinpeng.com 的 `/api/health` 都返回该 SHA，首页 200。回滚演练未做。
      原：合并后看第一次运行；线上 `/api/health` 返回合并的那个 SHA。
      可选：用 `workflow_dispatch` 填上一个 SHA 演练一次回滚。
- [x] **步骤 5 · 文档**（`f397c4c`）：`DEPLOY.md` 第 3 节（部署、回滚）改成新流程。

可选项（本次不做）：`paths-ignore`（`.platform/tasks.yaml` 等，等登记 OpenClaw 时一起做）；
CI 测试工作流；`.platform/project.yaml`。

## 3. 边缘情况

- **第一次切换**：部署前正在跑的是 `:latest`，回滚目标就是这个本地镜像。新流程不再拉 `latest`，它会一直留在本地，
  回滚能用；它没有 label，不会被自动清理，稳定后手动删。
- **容器名**：服务名、目录（compose 项目名）都不变 → 容器名不变，nginx 按名解析（任务四）不受影响。
- **服务器变成 detached HEAD**：以后不能再在 `/opt/crm_os` 手工 `git pull`；手工 `docker compose up` 会因为
  没给镜像变量直接报错（这是有意的，避免起一个不知道是哪个提交的版本）。要写进 DEPLOY.md。
- **迁移先于换容器**：迁移必须向后兼容（加表、加列）；回滚只换镜像，不降级数据库。
- **健康检查经 Cloudflare**：`/api/` 不应被缓存；第一次上线时确认返回的是实时 SHA。
- **本地开发**：compose 镜像变量必填后，本地 `.env` 里要加 `CRM_BACKEND_IMAGE` / `CRM_FRONTEND_IMAGE`（见 Q1）。
- 单实例换容器仍有几秒 502，和现在一样。

## 4. 需要 Kelvin 拍板

- **Q1** 本地开发：镜像变量必填（像 shop，本地 `.env` 里加两行）还是给本地默认值？→ **必填**（Kelvin，2026-10-09）
- **Q2** 停止推 `latest` 标签？→ **不推**（Kelvin，2026-10-09）
- **Q3** 步骤 3 的 secrets／变量：你在 GitHub 网页上设，还是授权我用 `gh` 设？主机指纹我可以先 `ssh-keyscan` 取出来给你比对。

## 评审记录

- 2026-10-09：照搬一个已在生产跑通的项目（acuven-shop）比自己设计省事得多：差异只有镜像名、服务名、
  去掉 `paths` 过滤，和 crm_os 没有 jobs 服务。
- 2026-10-09：镜像变量必填的代价是服务器上所有 `docker compose` 子命令都要先导出变量，已写进 DEPLOY.md 3.1。

## 后续（未做）

- VPS 上旧的 `crm_os-backend:latest` / `crm_os-frontend:latest` 镜像没有 label，不会被自动清理；
  等确认不再需要回滚到它们之后手动 `docker rmi`。
- 回滚演练（`workflow_dispatch` 填上一个 SHA）没做；需要时再做。
- 登记进 OpenClaw（I-07 的下半段）：`.platform/` 契约、`paths-ignore` 等，另开任务。

---

# 任务六：crm_os 登记进 OpenClaw（✅ 计划已批准 2026-10-09，进行中）

依据：`acuven_hub/DECISIONS.md` I-07 的下半段（任务五已经改成按 SHA 部署）。
权威格式：控制面仓库 `docs/ONBOARD-PROJECT.md`；模板 `acuven-project-template/TEMPLATE-GUIDE.md` 第 2–5 步。
分两边做：**业务仓库**在本会话做（走 PR）；**控制面**按根目录 CLAUDE.md 另开会话，在控制面自己的目录里做（受限评审、Kelvin 合并与部署）。

成功标准：对 crm_os 干净的 master 检出跑 `worker.contract_check` 没有 FAIL；控制面登记并分配给 windows-native；
Worker 上报规划与就绪全部 ok；Kelvin 在 Telegram `开启 crm_os <首个任务>` 后得到一个 CI 全绿的 Draft PR。

## 1. 现状差距（2026-10-09 只读核实）

- 没有 `.platform/`（project / commands / tasks 三个契约），计划文件里没有 planning-v1 块 → contract_check 必然 FAIL。
- 没有 CI 工作流，master 没有分支保护 → Worker 会把「零检查」当成全绿，测试形同虚设。
- 沙箱里能跑的只有 python 的 ruff（asyncio 测试、node 检查只能放 CI），而每个任务的 `allowed_commands` 必须非空
  → 必须先引入 ruff。现状（按 shop 的规则集）约 450 处 lint、61 个文件要重新格式化，大部分可自动修。
- `deploy.yml` 没有 `paths-ignore`（自动收尾会以 `closeout_would_deploy` 被拒）；文件头写着「不设 paths 过滤」，要一起改。
- 主仓库检出不干净（`.claude/settings.local.json` 被跟踪且有改动、`frontend/tsconfig.tsbuildinfo` 等构建产物被跟踪、
  `AGENTS.md` 未提交）→ Worker preflight 的 `primary_checkout_clean` 会 FAIL。根目录还有一个空的 `package-lock.json`。
- 现有 CLAUDE.md 要求「写代码前先描述方案-等批准再动手」，Worker 的实现会话没人在线确认，需要限定适用范围。
- 默认分支是 master：控制面和 Worker 都取本机配置的 `default_branch`，没有障碍；模板和 shop 里写的 main 要逐处改。

## 2. 业务仓库这一侧（本会话，5 个 PR，依次合并）

- [x] **PR-A · 工作区清理**（kelvinpang90/crm_os#2；干净导出的 frontend 构建成功；合并时误删了本机的 `.claude/settings.local.json`，已从历史恢复到最后提交版，见 lessons.md）：`.gitignore` 加 `.claude/settings.local.json`、`frontend/*.tsbuildinfo`、`frontend/vite.config.d.ts*`；
      对这 5 个文件 `git rm --cached`（本地文件保留）；删掉根目录空的 `package-lock.json`。全是删除与忽略，不动代码。
- [x] **PR-B · 引入 ruff 并一次性修好**（kelvinpang90/crm_os#3；首次部署因 GHCR 登录 503 失败，未动服务器，重跑成功；ruff 固定 0.16.7 = Worker 主机版本；76 个文件机械改动；手改两处：停用的邮件轮询 import 加 noqa、删一个未用变量；152 测试通过）：仓库根新建 `ruff.toml`（规则见 Q3；排除 `backend/alembic/versions`，迁移历史不改写）；
      `backend/requirements-dev.txt` 固定 ruff 版本；`ruff check --fix` + `ruff format`。
      **例外：这一步会机械地改动几十个文件**，超过「每步 ≤3 个文件」，但只有格式与自动修复，不改行为；
      需要手改的几处（未用变量等）单独列在提交说明里。验证：全量 pytest 仍 152 通过，两条 ruff 命令零退出。
- [x] **PR-C · CI 工作流**（kelvinpang90/crm_os#4；另加 shellcheck `deploy/deploy.sh` 与 compose 可解析检查；本地验证：shellcheck 通过、干净导出的 frontend `npm ci` + build 通过） `.github/workflows/ci.yml`：`pull_request` 与 `push` 到 master；
      job `backend`：装依赖 → `python -I -m ruff check .`、`python -I -m ruff format --check .`（与 commands.yaml 逐字相同）→ 在 `backend/` 下跑 pytest；
      job `frontend`：`npm ci` + `npm run build`（含 `tsc -b`）。**不设 paths 过滤**（收尾 PR 只改契约与计划文件，必需检查必须照样跑）。
- [x] **GitHub 设置（Kelvin，PR-C 合并、检查名出现之后；2026-10-10 用 GitHub API 核对：已设置，与下列要求一致）**：master 分支保护——要求 PR、审批 0 人、必需检查 `backend` `frontend`、
      要求分支与 master 同步（见 Q7）、线性历史、禁止 force push 与删除、管理员也受约束；合并方式只留 squash；不开 auto-merge。
      **从这以后不能再直推 master**，`tasks/todo.md` 的更新也要走 PR。
- [x] **PR-D · OpenClaw 契约**（kelvinpang90/crm_os#5；contract_check 提示 CRM-TASK-001 验收标准 9 条，合并为 8 条）：`.platform/project.yaml`（`project_id: crm_os`、`deploy_workflow: deploy.yml`、`execution_worker: windows-native`、
      `worker_enabled: true`，人读字段写 master / squash / backend、frontend）、`.platform/commands.yaml`（`lint.check`、`format.check`）、
      `.platform/tasks.yaml`（首个任务，见 Q4）；计划文件里加 planning-v1 块（见 Q2）。
- [x] **PR-E · 规则与部署收尾**（kelvinpang90/crm_os#6；用控制面 `worker.closeout.deploy_ignores` 验证收尾形状为 True）：`CLAUDE.md` 改成 Worker 能用的规则（保留 Kelvin 的 12 条；「先计划、等批准」限定为**没登记进 tasks.yaml 的工作**；
      加模板的角色与收尾、登记任务、密钥规则；写明合并分工：人工会话的 PR 由 Claude 验证后合并，Worker 的 PR 由 Kelvin 在 Telegram「批准」）；
      `AGENTS.md` 与它逐字相同并提交；`deploy.yml` 的 push 加 `paths-ignore: [.platform/tasks.yaml, <计划文件>]`，改文件头注释。
- [x] **自检**（2026-10-10，主检出 = 干净的 master `630b955`：`result: ready to register`；3 条 WARN 都是 Worker 主机的事——固定 python、为 lint.check / format.check 登记 check_win32k；
      用 Worker 主机固定的 pythoncore-3.14 原样跑两条命令均零退出；CRM-TASK-001 预审：不适用，只补已有的权限检查）：对 crm_os 干净的 master 检出跑 `python -m worker.contract_check --repo <检出> --project-id crm_os --task-id-pattern '^CRM-TASK-[0-9]{3}$' --planning <计划文件>`，
      没有 FAIL；首个任务按条件跑 `worker.design_precheck` 或写「预审：不适用」及理由。输出交给控制面会话。

## 3. 控制面这一侧（另开会话，在 `acuven-openclaw-control-plane` 做；照 acuven_shop 的 PR #79 → #81 先例）

1. 登记 PR：`projects/registry.json` 加 crm_os（`status: pilot`、`worker: none`、`task_id_pattern`、`authorized_principals: [operator-primary]`、
   `planning_source`、`merge_strategy: squash`），重新渲染 `PROJECTS.md`，PR 正文附 contract_check 输出与 deploy.yml 的 D1–D5 审阅
   → 受限评审 → Kelvin 合并。注意：此时 `开启` 会以 `plan_unavailable` 拒绝，备注要写对。
2. 分配 PR：`worker` 改为 `windows-native`，改 `tests/test_worker_disabled.py` 的固定集合和各处「两个项目」的文案 → 受限评审 → Kelvin 合并。
3. 部署（**Kelvin 的明确动作**）：控制 API 部署到合并提交（要挑三个项目都没有在途 run 的时候），再把 Worker 部署副本切到同一提交，同步 `PROJECTS.md`。
4. Worker 主机本地配置（Git 忽略，Kelvin / 运营者）：`worker.crm_os.local.json`（`worker_id: windows-crm`、`worktree_root: E:\projects\.acuven-openclaw-worktrees-crm`、
   `default_branch: master`、`check_win32k` 登记两条 ruff、`merge_enabled: true`；无 BOM 的 UTF-8）、`run-worker-crm_os.ps1`、
   计划任务 `AcuvenOpenClawWorker-crm_os`（从现有任务导出 XML 只改名字和脚本路径）；
   固定的 `pythoncore-3.14` 里装上与 CI 同版本的 ruff。
5. 先用 `enabled:false` 的副本跑 `worker.preflight`（每个 ready 任务）和 `worker.dry_run`，全部 PASS 才换上启用的配置。
6. 等 Worker 上报规划与就绪；Kelvin 授权后在 Telegram `开启 crm_os CRM-TASK-001`，合并还要对该 run `批准`。
7. 可选、之后单独做：登记表 `auto_closeout: true`（Q6）。
8. 根目录 `E:\projects\CLAUDE.md` / `AGENTS.md` 的 OpenClaw 链路一段加上 crm_os。

## 4. 边缘情况

- **主仓库检出必须保持干净**：Worker 在 `E:\projects\crm_os` 上 fetch、建 worktree。以后人工会话在这里留下未提交的改动，
  `开启` 就会被 preflight 拒。人工会话改用 git worktree，或做完即提交。
- **`paths-ignore` 之后**，只改契约与计划文件的提交不部署，线上 `/api/health` 的 SHA 会落后于 master HEAD，这是预期。
- **E712（`== True`）不能自动修**：SQLAlchemy 的过滤条件依赖它，改成 `is True` 查询就错了 → 规则里忽略。
- **ruff 版本**：CI、本地、Worker 主机三处必须同一版本，否则格式检查会互相打架。
- **重新格式化会让 git blame 失真**：把 PR-B 的提交写进 `.git-blame-ignore-revs`（可选）。
- **strict 分支保护**：开了以后，Worker 的 PR 开着时不要合并人工 PR，否则 Worker 的 PR 变成 BEHIND，批准后以 `merge_rejected` 结束。
- **公开仓库**：`.platform/`、任务文本、PR 正文都公开，不写主机名、IP、域名、绝对路径。
- **Codex 专属目录**里已有 acuven-shop 的信任记录，crm_os 不能共用（会 `codex_home_invalid`），见 Q5。
- 控制面部署窗口：任何项目有在途 run 时 `deploy.control_api` 会拒绝。

## 5. 需要 Kelvin 拍板

- **Q1** 任务编号规则：`^CRM-TASK-[0-9]{3}$`？（建议这样，和 shop 一致）
- **Q2** 计划文件：继续用 `tasks/todo.md`（建议：只有一个地方记任务）还是像模板那样新建 `docs/TODO.md`？
- **Q3** ruff 规则：建议 `E,F,W,I`，忽略 `E501`（长行）、`E712`（SQLAlchemy），再加 `ruff format`——基本全是自动修。
      另一选项是照 shop 加上 `B,UP`：多约 260 处自动修，并且要手改 74 处长行。
- **Q4** 首个登记任务：建议 `CRM-TASK-001` = 新建客户和 Excel 导入时的 `assigned_to_email` 也要过 `may_assign_to`（任务三遗留，小、低风险、只动后端）。
- **Q5** 实现者：只用 Claude（建议，像 ai_billing_hub，不配 Codex）还是给 crm_os 单独建 Codex 目录？
- **Q6** 自动收尾 `auto_closeout`：建议首次运行跑通后再单独开（shop 也是这样）。
- **Q7** 分支保护「要求与 master 同步」：建议开（和 shop 一致，防止拿旧基线的 CI 结果合并），代价见边缘情况。

**Kelvin 的答复（2026-10-09）**：Q1、Q2、Q4、Q6、Q7 按建议；合并分工按建议（人工会话的 PR 由 Claude 验证后合并，Worker 的 PR 由 Kelvin 在 Telegram 批准）。
Q5：**用 Claude Code 实现，同时用 Claude Code 独立审查**（Worker 本机配置 `reviewer.provider: claude_code`，不配 Codex）。
Q3：**选 1**（`E,F,W,I`，忽略 E501、E712）。

---

# CRM-TASK-001：新建客户和表格导入时校验负责人分派权限

来源：任务三「新发现、本次没处理的」第一条。编辑客户已经按 `access_service.may_assign_to` 校验负责人，
新建与导入两个入口补上同一个校验，口径与编辑一致。

## 做了什么

- [x] `backend/app/routers/contacts.py` 的 `create_contact`：请求带了 `assigned_to` 且 `may_assign_to` 判否时，
      在调 service 之前返回与 `update_contact` 越权时相同的 `403 FORBIDDEN`（`Permission denied`），不建客户。
      不带 `assigned_to` 时不进这个分支，sales 回落到自己、其他角色走路由，与原来相同。
- [x] `backend/app/services/contact_service.py` 的 `import_contacts`：`assigned_to_email` 找到了在职账号、
      但导入人无权分派给他时，按「找不到」处理，于是落进原有的 `Sales account not found or inactive` 行错误
      （同字段、同文案，不暴露账号是否存在），该行跳过、其他行照常。列为空时不查权限；`auto_assign` 与按策略分派只在
      没有显式负责人时才走，没有改。带 `customer_id` 的行（给已有客户加商机）用的是同一段负责人解析，所以也一并受这条校验约束。
- [x] 新增测试 `backend/tests/test_contact_create_assign.py`，每条的 docstring 写明守住哪条验收标准：
      销售给别人建被拒（且响应与编辑越权逐字相同）、销售给自己建成功、销售不带负责人仍归自己、经理给本团队建成功、
      经理给团队外建被拒、管理员给任何人建成功；导入时越权行被拒而其他行照常导入、越权行的错误与「账号不存在」除行号外相同、
      有权分派和空负责人的导入行为不变。
      新建接口的测试用一个每次请求都 commit 的 `get_db` 覆盖（与生产的 `get_db` 一致），这样「没建客户」是查库查出来的，
      而不是因为测试会话没提交。

## 偏离

- 没有改 `may_assign_to`、路由规则、前端和表结构。
- 管理员给一个不存在的 id 建客户，仍由原有的 `_validate_assigned_user` 返回 400；非管理员给不存在的 id 建客户现在先得到 403
  （不存在的 id 不在任何人的可分派范围内）。这与编辑客户的先后顺序一致。

## 验证到什么程度

- 验证依据是上面的新测试加已有后端测试，由 PR 的必需检查 `backend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。

---

# 任务七：回复模块核实与修复登记（2026-10-12）

来源：Kelvin 问「VPS 上 CRM 客户回复模块的功能完备了吗」。结论：不完备。

## 核实结果

代码以 master 为准（生产 `/api/health` 报 `ad473ae`，与 master 代码一致）；VPS 上只读核对配置是否填写（不看值）和数据库计数。

- 生产配置：WhatsApp 四项、网关地址、内部密钥、IMAP、SMTP 都已填。CRM 的 WhatsApp phone number id 与 `ai_chatbot_demo`、
  `acuven_aichat` 的都不同（只比哈希）。同日经 Kelvin 同意，用 CRM 的凭据向 Meta 只读查询：是 CRM 自己那个
  Meta App 的**沙盒测试号**（显示名 Test Number，未验证），只能发给登记过的号码，没有真实顾客；token 仍有效。
- 流量：WhatsApp 最后入站 2026-08-25、最后出站 08-17；邮件最后入站 05-10、出站 06-10。网关停用后没有流量。
  2 个演示联系人（`is_gateway`），回复它们会打到已停用的网关。当前未分派客户 0 个。
- 后端日志只有访问日志，应用 INFO 日志没有输出，看不出邮件轮询是否在跑；AutoCount 同步在报域名解析失败（另行处理）。

## 发现的问题

- [ ] 演示联系人回复走已停用的网关（等 WhatsApp 接入方案，见下）
- [ ] WhatsApp 凭据未配时「假发送」：存成已发出并返回成功 → CRM-TASK-004
- [ ] 邮件 SMTP 未配或出错时「假发送」 → CRM-TASK-004
- [ ] webhook 处理出错吞掉异常返回 200，Meta 不重投，消息丢失：不单独修，这个入口在 aichat 接入第 2 阶段撤掉（Kelvin 2026-10-12 撤回 CRM-TASK-005）
- [ ] 送达状态（failed / delivered / read）只写日志，超出 24 小时窗口被拒收也看不到
- [ ] 不支持模板消息、只能发文字、入站媒体只存占位符、收件箱不自动刷新
- [ ] 未分派客户只有管理员看得到且筛不出来 → CRM-TASK-002 / 003
- [ ] 会话认领、转派与并发保护（任务二缺口 3）
- [ ] 小问题：电话号码不规范化；验签密钥未配时放行；发邮件后立即显示的时间少了 `Z`；IMAP 同步调用阻塞事件循环
- [x] 公开仓库里的 VPS 公网 IP 换成占位符（kelvinpang90/crm_os#12，2026-10-12 合并部署）；地址仍在 git 历史里

## 登记（Kelvin 2026-10-12 批准）

顺序：CRM-TASK-004 → 002 → 003（先修会骗人的，再做未分派可见）。原拟的 005（CRM 自己的 Meta webhook 出错时让 Meta 重投）
已撤回：这个入口 8 月底后没有流量，会在 aichat 接入第 2 阶段删掉，入站可靠性改由 aichat 的待推队列保证。

- 设计闸门：三个都不适用。004 只让失败如实暴露，失败的消息不再入库，存的个人数据只会更少；002/003 只在管理员
  已经看得到的列表上加筛选，看得到哪些客户的范围不变。都不碰钱、状态机、表结构。
- 预审：不适用。没有设计闸门；文件最多 6 个、验收标准最多 8 条，改动预计各一两百行，远低于拆分线。
- 004 的代价：本地开发没有凭据时不能再「假装发出」来调试收件箱，接受。

## WhatsApp 接入方案（Kelvin 2026-10-12 批准）

- 起初定接 `acuven_aichat`（公司真实客服号），同日改为 **`ai_chatbot_demo` 的 demo 号**：Kelvin 定 crm_os 是 demo 项目，
  不承载 Acuven 真实客户，不应接公司真实客服号。
- 需求：一个号码、多个客服在 CRM 里分别回复各自的顾客，每个新顾客自动指派给某个客服。
- 分工：`ai_chatbot_demo` 是唯一对接 Meta 的一方，所有访客对话经内部签名接口推给 CRM；CRM 自动指派、多客服在收件箱回复并经
  chatbot 发出；客服回复后该对话 AI 静默 24 小时滑动，期满交还；完整对话存 CRM；CRM 自己的 Meta 沙盒号凭据在第 2 阶段作废。
- 设计全文与 D1–D11 在 `acuven_hub/CHATBOT_DEMO_CRM_MULTIAGENT_2026-10-12.md`，现行索引 `acuven_hub/DECISIONS.md` 的 J-04、A-18。
- 下一步：按两个仓库、按层拆第 1 阶段（入站进 CRM）的任务；每个任务过设计闸门与设计预审。

## 第 1 阶段拆分（2026-10-12，设计同日 Kelvin 批准）

设计与接口契约：[docs/chatbot-events.md](../docs/chatbot-events.md)。Kelvin 同日定：本地号码按 60 补国家码；号码冲突挂最早的联系人并显示冲突；
接管变化与送达状态第 1 阶段就存；chatbot 来的新访客不标 `is_gateway`；chatbot 那边人工会话做，不登记进 OpenClaw。

- [x] 设计 PR 获 Kelvin 批准（kelvinpang90/acuven_crm_demo#17，2026-10-12，原话「批准，开 PR 登记 006 到 010」）
- [x] 登记 CRM-TASK-006（表结构）、007（号码规范化）、011（事件处理规则）、008（接口）、009（存量号码迁移）、010（前端），附设计预审输出（见下「登记 CRM-TASK-006 至 011」）
- [ ] `ai_chatbot_demo`：C1 事件 outbox 与推送、C2 埋点与 statuses 转推

## 第 2 阶段设计（2026-10-13，同日 Kelvin 批准）

设计与接口契约：[docs/chatbot-replies.md](../docs/chatbot-replies.md)。CRM 回复经 chatbot 内部发送接口发出（第二把密钥）、客服回复后 AI 静默 24 小时滑动、
「交还 AI」、删 CRM 自己的 Meta webhook / 网关 / Graph 直连并作废沙盒号 token、`is_gateway` 先停用再删列；认领与锁、模板、媒体留第 3 阶段。
待决问题 Q1–Q7 Kelvin 2026-10-13 全部按建议定（设计第十二节）。

- [x] 设计 PR 获 Kelvin 批准（kelvinpang90/acuven_crm_demo#28，原话「Q1–Q7 都按建议，批准，开 PR 登记 012 到 019」）
- [x] 登记第 2 阶段任务 CRM-TASK-012 至 020（设计第十节；原 019 按预审拆成 019、020），附设计预审输出（见下「登记 CRM-TASK-012 至 020」）
- [ ] `ai_chatbot_demo`：C3 发送接口、C4 交还接口与接管时长 24 小时

## 登记 CRM-TASK-006 至 011（2026-10-12）

Kelvin 2026-10-12 批准设计（kelvinpang90/acuven_crm_demo#17），原话「批准，开 PR 登记 006 到 010」。本段是登记 PR 的记录。

### 做了什么

- `docs/chatbot-events.md`：文件头改为已批准；加第九节「登记预审后的补充」，Kelvin 2026-10-12 在登记 PR 上批准（原话「批准第九节，合并 PR」）。
- `.platform/tasks.yaml`：登记 006–011，全部 `ready`，依赖照设计第七节（011 依赖 006、007；008 依赖 011；010 依赖 008；009 依赖 007；
  007 只用到号码列，不依赖 006）。
- 拆分（Kelvin 2026-10-12 在登记 PR 上定）：原 007 验收标准 10 条超过拆分线，拆成 007（号码规范化、各保存入口、冲突查询，8 条、7 个文件）
  与 011（第五节事件处理规则，8 条、3 个文件）。设计第七节表格与上线顺序同步改了。编号用 011 而不是撤回过的 005，免得和撤回记录混淆。
- planning-v1：「当前计划」按 006、007、011、008、009、010 排；「后续计划」那条改写成第 2 阶段（CRM 回复经 chatbot 发出、交还 AI、
  作废沙盒号凭据），第 1 阶段已登记，不再写「待拆任务」。

### 设计闸门与预审

- 设计闸门：五个都适用，都引用 #17（006/009 改表结构与存量数据；007 有接管、送达两个状态规则并存 BSUID 与资料名；
  008 是接收个人数据的接口与验签；010 展示经 chatbot 进来的对话与号码冲突）。
- 预审：按控制面 `docs/ONBOARD-PROJECT.md`「登记任务之前」，六个都跑了（`python -m worker.design_precheck`，对本分支的干净克隆，
  设计文件 `docs/chatbot-events.md`）。第 1 轮 006–010 全跑；修改契约与设计后第 2 轮复查 007–010；原 007 第 3 轮仍 NOT_READY
  （含 TOO_LARGE）；拆分后第 4 轮复查 007、011、008（008 的依赖改了）。最终：006、007、008、009、010、011 全部 READY。
  第 4 轮报的三条 UNDECIDED 已写进验收标准（见下表最后三行），之后没有再跑预审。

逐条处理（成立的已修，不成立的写明理由）：

| 轮次 / 任务 | 发现 | 处理 |
|---|---|---|
| 1 / 007 | 同一秒的接管开始被补投会盖掉同一秒的结束 | 不成立：相同时间按到达顺序生效（开始、结束同秒时结束照常生效）；chatbot 顺序投递，重发只会是队首原样重发，同一事件重复写入结果不变；若改成「相同时间也忽略」，同秒的开始与结束反而会丢结束 |
| 1 / 007、008 | `occurred_at` 落在未来会让之后的接管事件全被忽略 | 不成立：接管事件的 `occurred_at` 是 chatbot 入队时刻，签名时间戳是同一个时钟的发送时刻，且已限定与 CRM 相差 300 秒内；除非 chatbot 时钟在入队与发送之间倒退，否则不会出现。未加额外限制（加了就是改契约） |
| 1 / 007 | BSUID 唯一索引含已软删除的联系人，补上或新建会撞索引、5xx、整条队列卡死 | 成立，设计缺陷 → 第九节第 1 条：只被已删除联系人占着时先清掉再用 |
| 1 / 007 | 创建时间相同的同号码联系人取哪个 | 写进 007 验收：按 id 取最小 |
| 1 / 007 | AutoCount 同步写号码不经规范化 | 成立 → 第九节第 2 条，007 加 `autocount_service.py` |
| 1 / 007、1 / 009 | 第 2 步按「原文」判断 00，`+0060…` 规范化不幂等 | 成立，设计缺陷 → 第九节第 3 条 |
| 1 / 007 | `delivery_error` 的拼法 | 第九节第 4 条 + 007 验收 |
| 1 / 007、2 / 007、3 / 007 | 验收标准 10 条，建议拆成两个任务 | 登记时先没拆（拆分写在已批准设计第七节）；Kelvin 2026-10-12 定拆，已拆成 007 与 011，见上「做了什么」 |
| 1 / 008 | 联系人详情路由不传当前用户，管理员才算的 `phone_conflict` 要改 `routers/contacts.py` | 成立 → 008 加这个路径（共 12 个） |
| 1 / 008 | `active` 为真而 `expires_at` 为空怎么办 | 第九节第 5 条：422；chatbot 现有实现正是这样推的 |
| 1 / 008 | `failed` 之后能不能被覆盖 | 第九节第 4 条：不能 |
| 1 / 010 | bot_id 的行业列表没写 | 写进 010 验收：food、hotel、realestate、retail、saas（读 `ai_chatbot_demo` 的 bot 数据目录得到，共 5 个，不是 6 个） |
| 1 / 010 | 接管到期时间的时区 | 写进 008/010 验收：按现有响应约定带 UTC 标记输出，前端按本地时间显示 |
| 2 / 007、3 / 007 | 现有 Meta webhook / 网关入站按号码找联系人用 `scalar_one_or_none`，规范化后同号码多联系人会抛异常 | 成立（设计 P2 本来就规定入站挂最早创建的那个）→ 007 加 `whatsapp_service.py`，只改这处查询。第 3 轮之后改的 |
| 2 / 007 | 同一送达状态重复到达算什么 | 写进 007 验收：不算升级，返回 ignored。第 3 轮前改的 |
| 2 / 007 | 事件里的号码要不要先规范化、无法规范化怎么办 | 写进 007 验收：一律先规范化；无法规范化的由 008 拒收（422），处理入口按无号码处理。第 3 轮前改的 |
| 3 / 007 | 空串、无法规范化的原文算不算号码冲突 | 写进 007 验收：只看能规范化的号码。第 3 轮之后改的 |
| 3 / 007 | 名字回退到 BSUID 时可能超过 100 字符 | 写进验收（现属 011）：三种来源都截断到 100。第 3 轮之后改的 |
| 4 / 007 | 冲突查询比较前规范不规范化、返回什么形式 | 写进 007 验收：输入先规范化，与库里号码按原值比较（存量由 009 规范化），以规范化形式返回 |
| 4 / 011 | 接管事件找联系人时补不补 BSUID / 号码、清不清已删除联系人的 BSUID | 写进 011 验收：只找，不补、不清 |
| 4 / 011 | 号码无法规范化又没有 BSUID 的消息事件返回什么 | 写进 011 验收：返回 ignored、不改数据（正常情况下 008 已拒收） |

### 与 ai_chatbot_demo 已实现推送的对齐（读其 `tasks/todo.md` 任务 40.1、40.2，未改那个仓库）

- 一致：签名 = HMAC-SHA256(密钥, `<ts>.` + 原始请求体字节)，`sha256=<hex>`，时间戳是发送时刻；`occurred_at` 形如 `...Z`；
  路径 `/internal/chatbot/events`（chatbot 默认推送地址的路径，不带 `/api`）；`console_reply` 不带 `bot_id`。
- 契约没写、按 chatbot 的实现写进 008 验收：密钥取 `CHATBOT_EVENTS_SECRET` **字符串的 UTF-8 字节**（chatbot 一侧变量名是
  `CRM_EVENTS_SECRET`，同值）；`ai_reply` 的 `bot_id` 可为空（还没选行业时的菜单回复）；`error_code` 可能是整数。
- 要特别注意的不一致：契约写返回 `{"result": ...}`，而 CRM 所有接口都经 `ok()` 套 `{"success", "data", "message"}` 外壳；
  chatbot 只读**顶层** `result`，套了外壳它会每条打 WARNING（仍出队，不丢事件）。008 验收明确要求不套外壳。
- 008 不能改 `backend/.env.example`（凭据形状的文件名，Worker 的路径规则拒收），两项新配置的示例要人另补。
- nginx：本仓库 `nginx/conf.d/crm.conf` 与 `vps_infra` 的配置里，CRM 域名只把 `/api/` 转给后端，其余转前端，`/internal/` 不会到后端。
  只读了仓库里的配置，没上 VPS 核对线上。

### 验证到什么程度

- 契约自检：控制面 `python -m worker.contract_check --repo <本分支干净克隆> --project-id crm_os --task-id-pattern '^CRM-TASK-[0-9]{3}$' --planning tasks/todo.md`，
  `result: ready to register`，退出码 0。WARN 只剩 Worker 主机要固定 python、ruff 要登记 `check_win32k`（已有项目级 WARN，与本次无关）；
  拆分前 007 的「验收标准 10 条建议拆分」已消失。planning-v1 评估 `ok, next: CRM-TASK-006`。
- 预审：上面所述；第 4 轮之后的三处补充没有再跑预审。
- 没验：Worker 主机上的真实就绪上报、Telegram 推荐行（合并后由 Worker 空闲刷新时上报）。

### 预审完整输出

```text
第 1 轮 CRM-TASK-006:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 8.1 万 token（其中缓存读取 6.1 万），折合 API 价约 $0.21；周额度读数 13%。
退出码 0

第 1 轮 CRM-TASK-007:
PRECHECK_FINDING: DESIGN_DEFECT §5 :: Handover ignores only events strictly earlier than the stored time; a re-sent same-second start event overwrites a same-second end, contradicting §8's promise that re-sends never corrupt state.
PRECHECK_FINDING: DESIGN_DEFECT §5 :: occurred_at is never bounded, so one future-dated handover event makes every later real event "earlier" and silently ignored until that time passes.
PRECHECK_FINDING: DESIGN_DEFECT §6 :: whatsapp_bsuid is unique across soft-deleted contacts, but lookups only cover non-deleted ones, so back-filling or creating with that BSUID fails with a 5xx and blocks the ordered event queue forever.
PRECHECK_FINDING: UNDECIDED §5 :: "Take the earliest created" has no tie-break when contacts sharing a number have equal created_at (same-second import), so the matched contact is random.
PRECHECK_FINDING: UNDECIDED §3 :: AutoCount sync writes contact.phone unnormalized (autocount_service.py:83) and §3 does not say whether that path must normalize.
PRECHECK_FINDING: UNDECIDED §3 :: Step 2 checks whether the original text starts with 00, so "+0060..." keeps 00 and stays valid, while the acceptance criterion strips 00 from the digits.
PRECHECK_FINDING: UNDECIDED §5 :: The delivery_error format for joining error code and title, and the value when both are null, are not specified.
PRECHECK_FINDING: TOO_LARGE tasks/todo.md :: Ten acceptance criteria exceed eight; split into 007a (normalization, contact save paths, conflict query) and 007b (the five event-handling rules and their tests).
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 16.2 万 token（其中缓存读取 12.6 万），折合 API 价约 $0.40；周额度读数 13%。
退出码 1

第 1 轮 CRM-TASK-008:
PRECHECK_FINDING: SCOPE_GAP backend/app/routers/contacts.py :: Contact detail calls contact_service.get_contact(db, contact_id) without the current user, so admin-only phone_conflict needs a router change outside allowed_change_paths.
PRECHECK_FINDING: DESIGN_DEFECT §5 :: Handover events are ignored only if older than the stored time and occurred_at is unbounded, so one future-dated event (e.g. 2099) blocks every later handover update with no undo.
PRECHECK_FINDING: UNDECIDED §4 :: Whether handover_changed with active=true and expires_at null is a 422 or is accepted and shown as false is not decided.
PRECHECK_FINDING: UNDECIDED §5 :: The upgrade-only delivery order never ranks failed, so whether a later delivered or read may overwrite failed is undefined.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 13.6 万 token（其中缓存读取 11 万），折合 API 价约 $0.28；周额度读数 13%。
退出码 1

第 1 轮 CRM-TASK-009:
PRECHECK_FINDING: DESIGN_DEFECT §三 :: Step 2 tests the original text for a leading 00, so "+0060123456789" normalises to "0060123456789", and a second pass turns it into "60123456789": the rule is not idempotent, which breaks the second acceptance criterion and inbound matching.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 11.3 万 token（其中缓存读取 9.1 万），折合 API 价约 $0.25；周额度读数 13%。
退出码 1

第 1 轮 CRM-TASK-010:
PRECHECK_FINDING: UNDECIDED docs/chatbot-events.md#4 :: The six demo-industry bot_id identifiers that must map to industry labels are not listed in the design, the task, or anywhere in the repo, so the required mapping cannot be written.
PRECHECK_FINDING: UNDECIDED docs/chatbot-events.md#6 :: Serialization of wa_handover_expires_at (naive UTC, existing isoformat emits no offset) is unspecified, so the frontend must guess whether to treat it as UTC before showing local time.
session verdict: NOT_READY
judged from the findings, not the session's verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 26.8 万 token（其中缓存读取 23.3 万），折合 API 价约 $0.40；周额度读数 13%。
退出码 0

第 2 轮 CRM-TASK-007:
PRECHECK_FINDING: DESIGN_DEFECT §3 :: Normalizing phones on create/edit/import/AutoCount makes shared numbers likely (P2 allows them), and the untouched whatsapp_service._handle_message uses scalar_one_or_none, so Meta and gateway inbound then raise MultipleResultsFound: lost messages or a 500.
PRECHECK_FINDING: UNDECIDED §5 :: A repeated delivery status (delivered then delivered again) is neither an upgrade nor clearly forbidden; the design does not say whether it returns stored or ignored.
PRECHECK_FINDING: UNDECIDED §5 :: Not stated whether the event phone is normalized before being stored on a new or BSUID-matched contact and in sender/recipient fields, or what happens when it cannot be normalized.
PRECHECK_FINDING: TOO_LARGE CRM-TASK-007 :: Ten acceptance criteria exceed the limit of eight; split into normalization plus write-path wiring plus conflict query, then section-five event handling as a dependent task.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 29.9 万 token（其中缓存读取 24.6 万），折合 API 价约 $0.58；周额度读数 13%。
退出码 1

第 2 轮 CRM-TASK-008:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 48.5 万 token（其中缓存读取 43.3 万），折合 API 价约 $0.58；周额度读数 13%。
退出码 0

第 2 轮 CRM-TASK-009:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 19.6 万 token（其中缓存读取 16.9 万），折合 API 价约 $0.31；周额度读数 13%。
退出码 0

第 2 轮 CRM-TASK-010:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 13 万 token（其中缓存读取 10.1 万），折合 API 价约 $0.29；周额度读数 13%。
退出码 0

第 3 轮 CRM-TASK-007:
PRECHECK_FINDING: DESIGN_DEFECT §2 :: P2 allows several live contacts with one number, but whatsapp_service._handle_message looks them up with scalar_one_or_none and returns a 500 once normalization makes such duplicates, and this task may not change that code.
PRECHECK_FINDING: UNDECIDED §3 :: The conflict query does not say whether contacts sharing the same un-normalizable raw value or an empty string count as a phone conflict.
PRECHECK_FINDING: UNDECIDED §5 :: The new-contact name falls back to the BSUID, which can be 128 characters, but Contact.name is 100 and only the profile name is truncated.
PRECHECK_FINDING: TOO_LARGE CRM-TASK-007 :: 10 acceptance criteria exceed 8; split into phone normalization with save paths and conflict query, then event handling rules depending on it.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 32.7 万 token（其中缓存读取 27.8 万），折合 API 价约 $0.57；周额度读数 13%。
退出码 1

第 4 轮（拆分后）CRM-TASK-007:
PRECHECK_FINDING: UNDECIDED 三 :: The conflict query doesn't say whether input and not-yet-migrated stored phones are normalised before comparing, nor whether returned numbers use the caller's form or the normalised form.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 11.7 万 token（其中缓存读取 9 万），折合 API 价约 $0.28；周额度读数 14%。
退出码 0

第 4 轮（拆分后）CRM-TASK-011:
PRECHECK_FINDING: UNDECIDED §5 :: Whether the handover_changed lookup also back-fills BSUID/phone or clears soft-deleted BSUIDs is unstated; if it does, an older ignored event still changes data, contradicting criterion 3.
PRECHECK_FINDING: UNDECIDED §5 :: The result for a message event whose phone cannot be normalized and that has no BSUID, leaving no identity, is not specified (ignored or error).
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 15.4 万 token（其中缓存读取 10.9 万），折合 API 价约 $0.47；周额度读数 14%。
退出码 0

第 4 轮（拆分后）CRM-TASK-008:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 38.3 万 token（其中缓存读取 31.5 万），折合 API 价约 $0.68；周额度读数 14%。
退出码 0
```

## 登记 CRM-TASK-012 至 020（2026-10-13）

Kelvin 2026-10-13 批准第 2 阶段设计（kelvinpang90/acuven_crm_demo#28），原话「Q1–Q7 都按建议，批准，开 PR 登记 012 到 019」。本段是登记 PR 的记录。

### 做了什么

- `.platform/tasks.yaml`：登记 012–020，全部 `ready`，依赖照设计第十节（012 依赖 009，因为迁移 014 接在 009 的迁移 013 之后；013 依赖 012、011、008；
  014 依赖 013；015 依赖 014；016 依赖 015、010；017 依赖 015；018 依赖 017；019 依赖 018；020 依赖 019）。
- **比 Kelvin 说的多一个 020**：第 2 轮预审指出原 019 在同一次部署里删模型字段与删列，而 `deploy.sh` 先迁移再换容器、失败回滚到上一版，
  删列那一刻在跑的 018 与回滚目标都还映射这一列。拆成 019（模型取消映射，列有数据库默认值 false）与 020（迁移删列），见设计第十四节第 12 条。
- planning-v1：「当前计划」在 010 之后接 012–016；017 放「已阻塞」（等第 2 阶段真机验收，chatbot 侧 C3、C4 上线并配好第二把密钥）；
  018、019、020 放「后续计划」。原「待登记：demo 号第 2 阶段」一行删掉。
- `docs/chatbot-replies.md`：文件头改为已批准（#28 已合并）；加第十四节「登记预审后的补充」（22 条），第八至十节随 019/020 的拆分同步改了。
  第十四节与 020 拆分 Kelvin 2026-10-13 在本 PR（kelvinpang90/acuven_crm_demo#31）上批准，原话「继续」（回复的是请他批准这两处的消息）。

### 设计闸门与预审

- 设计闸门：九个都适用，都引用 #28（012/019/020 改表结构与模型映射；013/014 是发送结果、重试、接管写入与送达回填的状态规则；
  015 是收发个人数据的接口；016 展示客服回复与接管；017 删掉个人数据的旧入站入口；018 改变哪些联系人出现在列表与统计里）。
- 预审：按控制面 `docs/ONBOARD-PROJECT.md`「登记任务之前」，九个都跑了（`python -m worker.design_precheck`，对本分支的干净克隆，
  设计文件 `docs/chatbot-replies.md`）。第 1 轮 012–019 全跑；之后每轮只复跑改过的任务。最终：012、013、014、016、017、018、019、020 READY，
  015 第 5 轮 READY。第 4 轮报的 014、016 的 UNDECIDED 已写进验收标准与第十四节第 22 条，之后没有再对 014、016 跑预审。

逐条处理（全部成立，已改；编号指设计第十四节）：

| 轮次 / 任务 | 发现 | 处理 |
|---|---|---|
| 1 / 013 | 连接、写、连接池超时与 30 秒总时限算哪类；其他 4xx、3xx、不带 rejected 的 502、字段不对的 200 怎么算 | 第 3 条：请求没送出去才算明确没发出，其余一切算结果不明 |
| 1 / 013 | 没有入站时 `WINDOW_CLOSED` 的关闭时间；恰好 24 小时算不算 | 第 2 条 |
| 1 / 014 | `CHATBOT_SEND_URL` 是基地址还是完整地址 | 第 1 条：基地址 |
| 1 / 014 | 重试发给存的收件人还是联系人现在的号码 | 第 6 条：存的收件人 |
| 1 / 015 | 正文 422 与其他校验的先后 | 第 4 条 |
| 1 / 016 | **设计缺陷**：前端 15 秒超时短于后端 30 秒，客服重发会换幂等键、访客收到两条 | 第 8 条：发送与重试请求 45 秒超时，收不到响应时提示先看有没有未确认的消息 |
| 1 / 016 | 窗口关闭时间、Meta 错误放在错误外壳哪里 | 第 5 条：`fields` |
| 1 / 016 | 已确认的消息重试返回「无权」会误导 | 第 7 条：返回 200 与这条消息 |
| 1 / 017 | **范围缺口**：`test_contact_phone.py` 用到要删的旧入口 | 第 11 条，017 加这个路径 |
| 1 / 017 | 删配置后本地环境文件多出的键让启动失败 | 第 9 条：配置忽略多出的键 |
| 1 / 017 | `DEPLOY.md` 补哪几项，设计与验收不一致 | 第 10 条：四项 |
| 1 / 019 | **范围缺口**：018 新增的 `test_gateway_contacts_visible.py` 用到 `is_gateway` | 第 11 条，归 019 删 |
| 1 / 019 | **契约自相矛盾**：「测试里不再出现 `is_gateway`」与迁移 015 的测试冲突 | 第 11 条：不含迁移文件与 `test_migration_015` |
| 1 / 019 | `test_chatbot_event_service` 里关于 `is_gateway` 的断言要删，与「原有断言不变」冲突 | 019 验收改为删掉只针对 `is_gateway` 的断言 |
| 2 / 014 | `WINDOW_CLOSED` 与 `RETRY_EXPIRED` 谁先；`visitor.bsuid` 从哪来；原发送人已删时 `agent.name` | 第 13 条 |
| 2 / 014 | 交还的时限；`ended` 但 `handover` 不合法 | 第 14 条 |
| 2 / 015 | 202 用哪个外壳；交还失败的错误码 | 第 15 条、第 14 条 |
| 2 / 016 | 超过 4096 字符；不带 Meta 字段的 `SEND_FAILED` 显示什么 | 第 16 条 |
| 2 / 019 | **设计缺陷**：删列时运行版本与回滚目标仍映射这一列 | 第 12 条：拆成 019 与 020 |
| 3 / 014 | 号码无法规范化时交还带什么 | 第 20 条 |
| 3 / 015 | 管理员对不存在的联系人交还返回什么 | 第 20 条：同一个 403 |
| 3 / 016 | **设计缺陷**：发送途中就能重试，可能与「明确没发出、删行」赛跑，重发一条已删的消息 | 第 17 条：创建不足 45 秒不能重试（`IN_PROGRESS`） |
| 3 / 016 | **设计缺陷**：重试成功会清掉回复框里不相干的草稿 | 第 18 条 |
| 3 / 016 | 交还后仍显示接管中怎么办 | 第 19 条 |
| 4 / 014 | 重试时联系人已软删除 | 第 22 条：`NOT_FOUND`，不调用 chatbot |
| 4 / 015 | **范围缺口**：`test_whatsapp_service_send.py` 里经发送接口的用例在改路由后会失败 | 第 21 条，015 加这个路径 |
| 4 / 015 | 发送与重试返回的消息带不带发送人 | 第 22 条：带 |
| 4 / 016 | 「发送中」到 45 秒怎么切；交还后的「稍后刷新」怎么刷 | 第 22 条 |
| 5 / 015 | 管理员给已删除的联系人发送返回什么 | 第 22 条：`NOT_FOUND`，接口同一个 403；第 5 轮之后改的，没有再跑预审 |

### 验证到什么程度

- 契约自检：控制面 `python -m worker.contract_check --repo <本分支干净克隆> --project-id crm_os --task-id-pattern '^CRM-TASK-[0-9]{3}$' --planning tasks/todo.md`，
  `result: ready to register`，退出码 0；planning-v1 `ok, next: CRM-TASK-011`。WARN 只有原有的三条（Worker 主机固定 python、ruff 的 `check_win32k`），与本次无关。
- 预审：上面所述。它调模型、不是确定性的，READY 不保证没有缺陷。
- 没验：Worker 主机上的就绪上报与 Telegram 推荐行（合并后 Worker 空闲刷新时上报）；chatbot 侧 C3、C4 尚未开始。

### 预审完整输出

```text
第 1 轮 CRM-TASK-012:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 11.8 万 token（其中缓存读取 9.1 万），折合 API 价约 $0.26；周额度读数 20%。
退出码 0

第 1 轮 CRM-TASK-013:
PRECHECK_FINDING: UNDECIDED 六.发送.8 :: Design does not classify connect timeout, write timeout, pool timeout, or the overall 30s deadline firing (httpx has no total timeout), so the implementer decides whether the message is deleted or kept.
PRECHECK_FINDING: UNDECIDED 五./internal/crm/send :: Other 4xx (400/403/404), 3xx, a 502 without result=rejected, and a 200 with unknown result or missing wa_message_id are unclassified; treating them as result-unknown and keeping the message seems the safe default.
PRECHECK_FINDING: UNDECIDED 六.发送.3 :: WINDOW_CLOSED must carry a close time, but none exists when the contact has no inbound message, and the design does not say whether exactly 24 hours counts as inside the window.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 13 万 token（其中缓存读取 9.8 万），折合 API 价约 $0.34；周额度读数 20%。
退出码 0

第 1 轮 CRM-TASK-014:
PRECHECK_FINDING: UNDECIDED 第五节 :: Only CHATBOT_SEND_URL is configured and config.py is outside this task's paths; nothing says whether it is a base URL or the full /internal/crm/send URL, so how to build the /internal/crm/handback URL is left to guess.
PRECHECK_FINDING: UNDECIDED 第六节 :: A retry re-checks the contact's current number, but the stored message keeps the original recipient; if the number changed since the first attempt, the design doesn't say whether to send to the current or the stored number.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 22.4 万 token（其中缓存读取 18.1 万），折合 API 价约 $0.45；周额度读数 20%。
退出码 0

第 1 轮 CRM-TASK-015:
PRECHECK_FINDING: UNDECIDED §6 :: Design §6 orders body check (4) between window (3) and config (5), but 013's service does 2/3/5 together, so the API must choose whether 422 for a bad body comes before or after 403, NO_PHONE and WINDOW_CLOSED.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 28.8 万 token（其中缓存读取 23.6 万），折合 API 价约 $0.53；周额度读数 20%。
退出码 0

第 1 轮 CRM-TASK-016:
PRECHECK_FINDING: DESIGN_DEFECT §6 :: The frontend axios timeout is 15 s but the backend chatbot call may take 30 s, so a slow send counts as an error, the text stays, and resending creates a new request_id that chatbot cannot dedupe, so the customer gets it twice.
PRECHECK_FINDING: UNDECIDED §6 :: Neither the design nor CRM-TASK-015 says where the SEND_FAILED error_code and error_title or the WINDOW_CLOSED close time go in the {code,message,fields} error envelope, which the frontend must display.
PRECHECK_FINDING: UNDECIDED §6 :: How retry should show RETRY_EXPIRED and 422 is unspecified, and a message confirmed meanwhile returns 403 NOT_ASSIGNED, which would show a misleading no-permission reason.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 29.2 万 token（其中缓存读取 23.9 万），折合 API 价约 $0.53；周额度读数 20%。
退出码 1

第 1 轮 CRM-TASK-017:
PRECHECK_FINDING: SCOPE_GAP backend/tests/test_contact_phone.py :: The file imports whatsapp_service, calls process_inbound, sets settings.internal_shared_secret and posts to /internal/whatsapp/inbound, so the deletions break it, yet it is outside allowed_change_paths.
PRECHECK_FINDING: UNDECIDED backend/app/config.py :: Settings reads .env with pydantic's default extra=forbid, so a leftover WHATSAPP_* or INTERNAL_SHARED_SECRET key fails startup; the design does not say whether to add extra=ignore or rely on cleanup.
PRECHECK_FINDING: UNDECIDED §7 :: The design says DEPLOY.md gets only CHATBOT_SEND_URL and CHATBOT_SEND_SECRET (phase-1 items added by hand), but the criteria also add CHATBOT_EVENTS_SECRET and not the phase-1 events switch; which list wins is open.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 20 万 token（其中缓存读取 16 万），折合 API 价约 $0.40；周额度读数 20%。
退出码 1

第 1 轮 CRM-TASK-018:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 20.1 万 token（其中缓存读取 15.8 万），折合 API 价约 $0.43；周额度读数 20%。
退出码 0

第 1 轮 CRM-TASK-019:
PRECHECK_FINDING: SCOPE_GAP backend/tests/test_gateway_contacts_visible.py :: CRM-TASK-018 creates this test with Contact(is_gateway=True); removing the model field breaks it and violates criterion 2, but the file is not in allowed_change_paths.
PRECHECK_FINDING: DESIGN_DEFECT .platform/tasks.yaml :: Criterion 2 bans is_gateway from tests except historical migrations, yet criterion 4 requires test_migration_015 (and migration 015 itself) to create and assert the is_gateway column.
PRECHECK_FINDING: UNDECIDED backend/tests/test_chatbot_event_service.py :: CRM-TASK-011 tests likely assert is_gateway is False for new contacts; after the field is removed that assertion must change, which "原有断言不变" forbids, and the replacement is not decided.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 12.6 万 token（其中缓存读取 7.7 万），折合 API 价约 $0.45；周额度读数 20%。
退出码 1

第 2 轮 CRM-TASK-013:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 28.8 万 token（其中缓存读取 24.5 万），折合 API 价约 $0.49；周额度读数 21%。
退出码 0

第 2 轮 CRM-TASK-014:
PRECHECK_FINDING: UNDECIDED 六 :: Retry does not fix which of WINDOW_CLOSED and RETRY_EXPIRED wins when both fail, nor whether exactly 24 hours after creation counts as expired.
PRECHECK_FINDING: UNDECIDED 六 :: Retry does not say where visitor.bsuid comes from or what agent.name to send when the sent_by user has been deleted.
PRECHECK_FINDING: UNDECIDED 五 :: Handback timeout is unspecified, and so is the outcome of a 200 ended response whose handover is null or malformed; §14.3 classifies only send results.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 18.6 万 token（其中缓存读取 14.5 万），折合 API 价约 $0.43；周额度读数 21%。
退出码 0

第 2 轮 CRM-TASK-015:
PRECHECK_FINDING: UNDECIDED 六 :: The result-unknown 202 response must carry both code UNCONFIRMED and the message, but fail() has no data and ok() has no code; the envelope CRM-TASK-016 reads is not specified.
PRECHECK_FINDING: UNDECIDED 六 :: The handback endpoint's 502 for chatbot failure or timeout has no error code name, unlike the send endpoint's SEND_FAILED.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 35.4 万 token（其中缓存读取 30.8 万），折合 API 价约 $0.50；周额度读数 21%。
退出码 0

第 2 轮 CRM-TASK-016:
PRECHECK_FINDING: UNDECIDED frontend/src/pages/Inbox/ReplyBox.tsx :: A body over 4096 characters gets FastAPI's default 422 without error.code; the design says neither whether to cap input nor what to show, nor what to show for SEND_FAILED without Meta fields.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 35.8 万 token（其中缓存读取 30.5 万），折合 API 价约 $0.55；周额度读数 21%。
退出码 0

第 2 轮 CRM-TASK-017:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 19.3 万 token（其中缓存读取 15.5 万），折合 API 价约 $0.38；周额度读数 21%。
退出码 0

第 2 轮 CRM-TASK-019:
PRECHECK_FINDING: DESIGN_DEFECT §8 :: CRM-TASK-018 keeps is_gateway mapped in the Contact model, so the 018 container still selects/inserts it; deploy.sh migrates before swapping and rolls back to that image, so dropping the column in 015 breaks the running and rollback version.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 24.6 万 token（其中缓存读取 18.8 万），折合 API 价约 $0.55；周额度读数 21%。
退出码 1

第 3 轮 CRM-TASK-014:
PRECHECK_FINDING: UNDECIDED 六 :: Hand back says to send the contact's normalized phone but not what to do when the stored phone can't be normalized: send phone null (and NO_IDENTITY if there's no BSUID either), or send it raw.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 18.1 万 token（其中缓存读取 14.4 万），折合 API 价约 $0.39；周额度读数 22%。
退出码 0

第 3 轮 CRM-TASK-015:
PRECHECK_FINDING: UNDECIDED backend/app/routers/contacts.py :: Handback by an admin for a nonexistent contact passes may_access_contact; neither this task nor CRM-TASK-014 says whether that is 400 NO_IDENTITY, 404, or 403.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 47.9 万 token（其中缓存读取 41.9 万），折合 API 价约 $0.64；周额度读数 22%。
退出码 0

第 3 轮 CRM-TASK-016:
PRECHECK_FINDING: DESIGN_DEFECT §六 :: A message saved before the chatbot call shows as unconfirmed with a retry button while still sending. A retry then can race a 4xx delete and resend a message that CRM has deleted.
PRECHECK_FINDING: DESIGN_DEFECT .platform/tasks.yaml :: Criterion 3 says retry results follow the send rule, so a retry that returns 200 or 202 would clear an unrelated draft in the reply box.
PRECHECK_FINDING: UNDECIDED §六 :: When hand-back returns not_active, or ended in the same second so nothing is written, the header still shows in handover; no text or behaviour is set for that case.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 45 万 token（其中缓存读取 39.7 万），折合 API 价约 $0.60；周额度读数 22%。
退出码 1

第 3 轮 CRM-TASK-019:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 19 万 token（其中缓存读取 13.6 万），折合 API 价约 $0.51；周额度读数 22%。
退出码 0

第 3 轮 CRM-TASK-020:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 20.2 万 token（其中缓存读取 16.1 万），折合 API 价约 $0.40；周额度读数 21%。
退出码 0

第 4 轮 CRM-TASK-014:
PRECHECK_FINDING: UNDECIDED docs/chatbot-replies.md#6 :: Retry does not say what happens when the message's contact is soft-deleted (admins pass may_access_contact); handback returns NOT_FOUND for this case, but retry could send a real message to a deleted contact.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 28.9 万 token（其中缓存读取 24.1 万），折合 API 价约 $0.51；周额度读数 22%。
退出码 0

第 4 轮 CRM-TASK-015:
PRECHECK_FINDING: SCOPE_GAP backend/tests/test_whatsapp_service_send.py :: Three tests post to /api/messages/whatsapp/send expecting old whatsapp_service behaviour (503 without inbound, Graph call, 502 API_ERROR); they fail once the route uses the new service, but the file is only removable in CRM-TASK-017.
PRECHECK_FINDING: UNDECIDED §6 :: The acceptance criteria require sent_by_name only on list, contact-messages and mark-read; whether the message returned by send and retry (200 and 202 UNCONFIRMED) also carries sent_by and sent_by_name is not stated.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 55.2 万 token（其中缓存读取 48.9 万），折合 API 价约 $0.68；周额度读数 22%。
退出码 1

第 4 轮 CRM-TASK-016:
PRECHECK_FINDING: UNDECIDED §14 :: Inbox has no auto-refresh and 202 messages are under 45 s old, so the design does not say whether a timer flips "sending" to "unconfirmed + retry" at 45 s or it waits for a manual refresh.
PRECHECK_FINDING: UNDECIDED §14 :: Item 19's "refresh later" after a hand-back that still shows handover gives no delay, attempt count, or whether to reload only the contact header or the whole conversation.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 32.9 万 token（其中缓存读取 27.9 万），折合 API 价约 $0.53；周额度读数 22%。
退出码 0

第 5 轮 CRM-TASK-015:
PRECHECK_FINDING: UNDECIDED docs/chatbot-replies.md#6 :: Send never says what an admin gets for a missing or soft-deleted contact (403 like retry/handback, or NO_PHONE, or allowed); may_access_contact lets admins through, so they could send to and store a message on a deleted contact.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 35.8 万 token（其中缓存读取 30 万），折合 API 价约 $0.63；周额度读数 23%。
退出码 0
```

---

# CRM-TASK-004：发送失败或未配置时如实报错，不再把消息记为已发出

来源：任务七「发现的问题」里的两条假发送。设计闸门：不适用（只让失败如实暴露）。

## 做了什么

- [x] `backend/app/services/whatsapp_service.py` 的 `send_message`：非演示联系人在 access token 或 phone number id
      缺任一项时，抛 `WhatsAppSendError("not_configured")`，不发请求、不入库（原来记一条 warning 后照样存成已发出）。
      凭据齐全时的 Graph API 调用、非 200 / 网络出错抛 `api_error`、演示联系人经网关的分支，逐行未改。
- [x] `backend/app/services/email_service.py`：新增 `EmailSendError`（`not_configured` / `api_error`）。`send_email`
      在 SMTP 主机或用户缺任一项时抛 `not_configured`；连接、STARTTLS、认证、发送任一步出错时记 `logger.exception`
      后抛 `api_error`，两种情况都不入库。联系人不存在或没有邮箱仍返回 `None`，发送成功仍入库并返回原来的字典。
- [x] `backend/app/routers/messages.py`：两个发送接口把 `not_configured` 映射为 `503 NOT_CONFIGURED`，邮件的
      `api_error` 映射为 `502 API_ERROR`（与 WhatsApp 原有的一致）；其余分支未改。
- [x] 测试：`backend/tests/test_whatsapp_service_send.py` 里原来断言未配置仍保存的用例改为经接口断言 503 且不入库、
      不发请求（缺 token、缺 id、都缺三种）；另补凭据齐全时成功入库、非 200 与网络出错仍 502 不入库的用例。
      新增 `backend/tests/test_email_send.py`：未配置（三种缺法）、发送出错（连接 / 认证 / 发送三步，含错误日志断言）、
      发送成功、没有邮箱。每条 docstring 写明守住哪条验收标准。
- [x] 评审第 1 轮：经接口的 WhatsApp 用例原来 patch 的是 `httpx.AsyncClient.post`，连测试客户端自己的请求也被替换，
      根本没到应用。改为只替换 `whatsapp_service` 模块里的 `httpx` 名字（`AsyncClient` 换成 mock，`HTTPError`
      保留真实类），测试客户端不受影响；未配置用例断言 `AsyncClient` 没有被实例化。业务代码未改。

## 偏离

- 没有改演示联系人经网关的分支、前端、表结构、收邮件。
- WhatsApp 测试里额外补了凭据齐全时的成功与失败用例（验收标准 2 说行为不变，原来没有测试守住）。
- 邮件的 STARTTLS 失败与连接、认证、发送同样走 `api_error`（原来的 `try` 本就包着它）。
- 任务七「发现的问题」里对应的两条复选框保持 `[ ]`，等本 PR 合并后再勾。

## 验证到什么程度

- 验证依据是上面的测试加已有后端测试，由 PR 的必需检查 `backend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。

---

# CRM-TASK-002：客户列表支持只看未分派的客户

来源：任务七「发现的问题」里的未分派客户一条（后端这一半，前端由 CRM-TASK-003 接）。设计闸门：不适用（只在管理员已可见的列表上加筛选）。

## 做了什么

- [x] `backend/app/routers/contacts.py` 的 `list_contacts`：新增查询参数 `unassigned`（`Query(0, ge=0, le=1)`），
      取值 0 或 1、默认 0，其他取值（如 `2`、`-1`、`abc`）由 FastAPI 的参数校验返回 422；原样传给 service。
- [x] `backend/app/services/contact_service.py` 的 `list_contacts`：新增 `unassigned: int = 0`，为 1 时在角色过滤、
      搜索、行业、负责人条件之后再加 `Contact.assigned_to IS NULL`，计数、排序、分页都在这条件之后，所以 `total` 是筛选后的数量。
      为 0 时不加任何条件，查询与原来逐字相同。角色过滤、demo 排除、`access_service` 都没动：销售的 `assigned_to == 自己`、
      经理的 `assigned_to IN 团队` 与 `IS NULL` 同时成立不了，于是自然得到空列表、`total` 为 0；`assigned_to` 与
      `unassigned=1` 同传时同理为空。
- [x] 新增测试 `backend/tests/test_contact_unassigned.py`，每条 docstring 写明守住哪条验收标准：管理员传 1 只看到未分派客户
      且 `total` 正确（demo 未分派客户仍被排除，分页时 `total` 仍是全量）；与归档、搜索、行业同时生效；销售与经理传 1 得到空列表；
      `assigned_to` 与 `unassigned=1` 同传为空；不传与传 0 的结果相同且与各角色原有可见范围一致；非法取值返回 422。

## 偏离

- 没有改 `access_service`、角色过滤、demo 排除、仪表盘、前端、表结构，没有加计数接口。
- 422 用的是 FastAPI 默认的参数校验响应体（与其他查询参数校验失败一致），没有改成 `fail()` 的格式。

## 验证到什么程度

- 验证依据是上面的新测试加已有后端测试，由 PR 的必需检查 `backend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。

---

# CRM-TASK-003：客户页给管理员显示未分派数量和筛选开关

来源：任务七「发现的问题」里的未分派客户一条（前端这一半，接 CRM-TASK-002 的 `unassigned` 参数）。设计闸门：不适用（纯前端筛选）。

## 做了什么

- [x] `frontend/src/services/contacts.ts`：`ContactListParams` 加 `unassigned?: number`。
- [x] `frontend/src/pages/Contacts/index.tsx`：只在 `user.role === 'admin'` 时，筛选区搜索框旁显示「只看未分派」开关，
      开关里带一个数量徽标，数量大于 0 时红底白字，为 0 时是灰色。数量取自
      `GET /contacts?unassigned=1&is_archived=<当前归档视图>&page=1&page_size=1` 的 `total`，在每次 `load()` 时一并请求
      （所以翻页、搜索、排序、切归档、删除、归档、编辑、同步之后都会刷新）；请求失败时保留上次的数量，不影响列表。
      打开开关把 `unassigned: 1` 合进列表参数并回到第 1 页，关闭时去掉该参数（也回到第 1 页），搜索、排序、归档切换的参数原样保留。
      非管理员不渲染开关，`loadUnassignedCount` 直接返回，不发数量请求，列表参数里也不会出现 `unassigned`。
- [x] `frontend/src/locales/{en,zh}/contacts.json`：新增 `unassignedOnly`（开关文字）、`unassignedCount`（徽标的悬停说明）。

## 偏离

- 数量跟随当前的归档视图（看活跃客户时数活跃的未分派客户，看已归档时数已归档的），不跟随搜索词：
  它表示「当前视图下有多少未分派客户」，搜索时不会变少，免得误以为未分派的已经处理掉了。
- 关闭开关时也回到第 1 页（任务只要求打开时回第 1 页；关闭后原来的页码可能超出新列表的范围）。
- 插值参数用 `number` 而不是 `count`，避开 i18next 按 `count` 找复数 key 的规则。
- 没有改后端、路由、其他页面，没有加计数接口。

## 验证到什么程度

- 前端类型检查与构建由 PR 的必需检查 `frontend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。
- 没有在浏览器里实际点过三种角色的页面。

---

# CRM-TASK-006：会话事件第1阶段的表结构迁移与模型

来源：`docs/chatbot-events.md` 第六节。设计闸门：kelvinpang90/acuven_crm_demo#17。

## 做了什么

- [x] 新增迁移 `backend/alembic/versions/012_add_chatbot_event_fields.py`（`down_revision = "011"`）：
      `contacts` 加 `whatsapp_bsuid`（`String(128)`，可空）、`wa_handover_active`（`Boolean`，非空，`server_default=false`，
      存量行取 false）、`wa_handover_expires_at` 与 `wa_handover_updated_at`（`DateTime`，可空，与其他时间列一样存无时区的 UTC）；
      加唯一索引 `idx_contacts_whatsapp_bsuid` 与普通索引 `idx_contacts_phone`。`messages` 加 `author_kind`
      （枚举 `message_author_kind`：customer / ai / console / agent）、`bot_id`（`String(50)`）、`delivery_status`
      （枚举 `message_delivery_status`：sent / delivered / read / failed）、`delivery_error`（`String(300)`），都可空。
      upgrade 只加列加索引，不改存量数据；downgrade 按相反顺序先删 `messages` 的列，再删两个索引和 `contacts` 的列。
- [x] `backend/app/models/contact.py`、`backend/app/models/message.py`：同名同类型同可空性的字段与同名索引，
      `wa_handover_active` 同时带 Python 默认值 `False` 与数据库默认值 `false()`。只加字段，不加行为。
- [x] 新增测试 `backend/tests/test_migration_012.py`，每条 docstring 写明守住哪条验收标准：
      revision 链；在只含 id、phone 的 `contacts` 与只含 id 的 `messages` 上用 `Operations` 跑 upgrade 后新列、
      可空性与两个索引（唯一与否）出现，downgrade 后回到原样；upgrade 不改存量行、存量行的 `wa_handover_active` 为 false；
      迁移建出的列与按模型建出的列逐列一致（类型、可空性、默认值、索引）；按模型建表后同一 `whatsapp_bsuid`
      的两个联系人插入失败、两个空值插入成功。

## 偏离

- 测试额外比较了迁移与模型反射出的列形状（验收标准 3「逐列一致」原来没有测试守住）；SQLite 把枚举存成 VARCHAR，
  枚举取值只在模型上断言。
- 没有改服务、路由、schema、前端，没有改存量数据（号码规范化是 CRM-TASK-009）。

## 验证到什么程度

- 验证依据是上面的新测试加已有后端测试，由 PR 的必需检查 `backend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。
- 迁移没有在 MySQL 上实际跑过；测试只在 SQLite 上跑 012 本身。

---

# CRM-TASK-009：存量联系人号码按统一规则规范化的数据迁移

来源：`docs/chatbot-events.md` 第六节末尾。设计闸门：kelvinpang90/acuven_crm_demo#17。

## 做了什么

- [x] 新增 013 数据迁移，逐行用 CRM-TASK-007 的 `normalize_phone` 改写可规范化的 `contacts.phone`，包含已软删除联系人；只写入实际变化的号码。
- [x] 迁移不合并或删除规范化后撞号的联系人，不建表、不改表结构；downgrade 保持数据不变，因为原始号码写法无法恢复。
- [x] 新增 SQLite 模型建表测试，覆盖带格式号码、本地和国际拨号前缀、已规范化与不可规范化号码、空号码及已软删除联系人，并断言 `updated_at`、幂等性和 downgrade 均不变。

## 偏离

- [x] 无。

## 验证范围

- [x] 测试定义覆盖迁移的号码改写范围、非号码列不变、重复执行不变、无结构改动及不可逆 downgrade；由必需的 `backend` CI 检查执行。

---

# CRM-TASK-007：号码规范化、各保存入口统一规范化与号码冲突查询

来源：`docs/chatbot-events.md` 第三节、第九节第 2、3 条与 P2。设计闸门：kelvinpang90/acuven_crm_demo#17。

## 做了什么

- [x] 新增 `backend/app/utils/phone.py`：`normalize_phone(raw)` 去掉非数字，对剩下的数字去掉开头的 `00`，恰好一个开头的 `0`
      换成 `60`，仍以 `0` 开头或不在 8–15 位的返回 `None`（空值、空串、没有数字的同样返回 `None`）；结果不以 `0` 开头，
      再规范化一次不变。`phone_for_storage(raw)` 给保存入口用：能规范化的返回规范化结果，否则原样返回输入。
- [x] `backend/app/services/contact_service.py`：`create_contact`、`update_contact`（只在请求带 `phone` 时）与
      `import_contacts` 新建联系人时，号码经 `phone_for_storage` 再存。不带号码时与原来一样（新建存 `None`，编辑不动原号码，
      导入的空格子仍存 `None`）。
- [x] 同文件新增 `find_phone_conflicts(db, phones)`：先规范化，丢掉无法规范化的；与库里存的号码按原值比较，
      返回被两个及以上未删除联系人共用的号码（规范化形式的集合）。不存字段，改号或删除后自然不再算冲突。供 CRM-TASK-008 用，
      本任务不接到接口上。
- [x] `backend/app/services/autocount_service.py` 的 `_sync_customers`：`Phone1` 经 `phone_for_storage` 再存。
      规范化幂等，所以拉回同一个号码时赋的值与已存的相等，不产生 UPDATE、`updated_at` 不动。
- [x] `backend/app/services/whatsapp_service.py` 的 `_handle_message`（Meta webhook 与网关入站共用）：按号码找联系人改为
      `ORDER BY created_at, id LIMIT 1` 取最早创建的一条，多个联系人同号码时不再因 `scalar_one_or_none` 报错。
      入站号码本身没有规范化，其余逻辑未改。
- [x] 新增测试 `backend/tests/test_phone.py`（规范化规则、设计里的每个例子、无法规范化的各种情况、8 与 15 位边界、幂等、
      保存时的原样保留）与 `backend/tests/test_contact_phone.py`（四个保存入口各自的规范化、原样保留与不带号码；AutoCount
      第二次拉回同号码时 `updated_at` 不动；冲突查询的规范化返回、按原值比较、空值与无法规范化的不算、改号与删除后消失；
      Meta webhook 与网关入站在同号码时挂到最早创建的联系人、创建时间相同取 id 最小、已删除的不算、不新建联系人）。
      每条 docstring 写明守住哪条验收标准。

## 偏离

- 导入的空号码格子原来经 `or None` 存成 `None`，现在仍然这样（规范化在 `or None` 之后）；新建与编辑传空串时原样存空串，与原来一致。
- 冲突查询收到空列表或全都无法规范化时直接返回空集合，不查库。
- 表格导入里 Excel 把号码存成数字时，路由层的 `str()` 可能带出 `.0` 等形状，这是原有行为，本任务没有改路由。
- 没有加接口、没有改表结构、没有迁移存量号码（CRM-TASK-009）、没有改前端，没有处理会话事件（CRM-TASK-011）。

## 验证到什么程度

- 验证依据是上面的新测试加已有后端测试，由 PR 的必需检查 `backend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。
- 冲突查询与同号码取最早的查询只在 SQLite 上由测试覆盖，没有在 MySQL 上实际跑过。

---

# CRM-TASK-011：会话事件的处理规则，找联系人、消息入库、接管与送达状态

来源：`docs/chatbot-events.md` 第五节、第九节第 1、4 条与第八节前三条。设计闸门：kelvinpang90/acuven_crm_demo#17。

## 做了什么

- [x] 新增 `backend/app/services/chatbot_event_service.py`，入口 `process_event(db, *, event_type, occurred_at, phone, bsuid,
      profile_name, wa_message_id, body, bot_id, handover_active, handover_expires_at, status, error_code, error_title)`，
      只收普通参数，返回 `stored` / `duplicate` / `ignored`；不认识的事件类型抛 `ValueError`。只 flush 不 commit，
      与 `get_db` 一样由调用方提交。不依赖 `app.schemas`、路由、FastAPI，不发外部请求。
- [x] 消息类事件（`store_message`）：先按 `wa_message_id` 查重，重复直接返回 `duplicate`，不碰联系人；号码经 `normalize_phone`，
      无法规范化的按没有号码处理，又没有 BSUID 时返回 `ignored`。找联系人按第五节：按号码找未删除联系人（`created_at`、`id`
      升序取第一个），没有 BSUID 且没有别的未删除联系人占用时补上；号码没找到再按 BSUID 找，没号码就补规范化号码；都没找到就新建
      （名字取资料名 / 号码 / BSUID，截断到 100；`is_gateway=False`；`assign_contact` 指派；建 lead 商机）。要用的 BSUID 只被
      已软删除的联系人占着时先把那些联系人的 `whatsapp_bsuid` 清空（第九节第 1 条）。消息存 `channel=whatsapp`、负责人取联系人
      当前负责人、访客一侧填号码（没有填 BSUID）、chatbot 一侧填 `ai_chatbot_demo`、`created_at` 取 `occurred_at` 转成的无时区 UTC；
      `customer_message` 入站未读 `customer`，`ai_reply` 出站已读 `ai` 记 `bot_id`，`console_reply` 出站已读 `console`。
- [x] `handover_changed`（`update_handover`）：只按号码、再按 BSUID 找，不新建、不补、不清已删除联系人的 BSUID；
      `occurred_at` 早于已存的 `wa_handover_updated_at` 返回 `ignored`，相等或更晚则写入是否接管、到期时间（结束时为空）与更新时间。
- [x] `delivery_status`（`update_delivery_status`）：只按 `external_id` 找那条消息；`sent < delivered < read` 只升，重复不算升级；
      `failed` 只覆盖空值、`sent`、`delivered`；已是 `failed` 的不再改变。`failed` 时 `delivery_error` 存「错误码 标题」
      （只有一个就只存它，都没有存空），截断到 300；其他状态不改 `delivery_error`。
- [x] 新增测试 `backend/tests/test_chatbot_event_service.py`，每条 docstring 写明守住哪条验收标准：号码规范化后匹配、同号码取最早 /
      同时间取 id 最小、已删除的不匹配、补 BSUID 与不覆盖已有 BSUID、按 BSUID 补号码与不覆盖已有号码、无法规范化的号码、
      无号码无 BSUID 时 ignored 且数据不变、新建联系人的字段 / 指派 / lead 商机、名字取值顺序与截断、不覆盖已有名字、
      软删除联系人占着 BSUID 时新建与补上两种情况；重复消息数据不变、三种消息的各字段、BSUID 作访客一侧、带时区的
      `occurred_at` 存成 UTC、回复先到也建联系人；接管的找不到、不补不清、开始与结束、旧事件 ignored 且数据不变、同一时间生效；
      送达状态的全部升降组合、错误文本的各种组合与截断、非 failed 不改错误文本；入口不导入接口层；第八节前三条边缘情况。

## 偏离

- 消息事件先查重再看号码与 BSUID：同一 `wa_message_id` 已存在时一律返回 `duplicate`，即使这次的访客身份无法使用。
- 「没有号码」包括联系人号码为空串的情况；联系人存着一个无法规范化的号码时算有号码，不被事件里的号码替换。
- `bot_id` 只在 `ai_reply` 时存，另两种消息即使传了也存空。`failed` 时错误码与标题都为空（`None` 或空串）则 `delivery_error` 存 `None`。
- 状态值不在 sent / delivered / read / failed 之内时抛 `ValueError`（接口层 CRM-TASK-008 会先按 422 拒收）。
- 没有加接口与验签（CRM-TASK-008）、没有改表结构、没有改号码规范化与各保存入口、没有改前端。

## 验证到什么程度

- 验证依据是上面的新测试加已有后端测试，由 PR 的必需检查 `backend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。
- 软删除联系人释放 BSUID 用的批量 UPDATE 与按号码取最早的查询只在 SQLite 上由测试覆盖，没有在 MySQL 上实际跑过。

---

# CRM-TASK-008：会话事件接收接口与验签，消息和联系人接口返回新字段

来源：`docs/chatbot-events.md` 第四节、第九节第 5 条；与 ai_chatbot_demo 任务 40.1、40.2 的对齐见上「登记 CRM-TASK-006 至 011」。
设计闸门：kelvinpang90/acuven_crm_demo#17。

## 做了什么

- [x] `backend/app/config.py`：新增 `chatbot_events_secret`、`chatbot_events_secret_previous`，默认空串；注释写明与 chatbot 一侧的
      `CRM_EVENTS_SECRET` 同值。`backend/.env.example` 没改（凭据形状的文件名，Worker 改不了），两项示例要人另补。
- [x] 新增 `backend/app/routers/internal_chatbot.py`，`backend/app/main.py` 以 `/internal/chatbot` 前缀挂上（不带 `/api`），
      接口 `POST /internal/chatbot/events`。顺序：两把密钥都没配 → 503；读原始请求体验签 → 不过 401；再解析请求体 → 不合法 422；
      合法则调 `chatbot_event_service.process_event`，提交后返回 200 `{"result": ...}`（`JSONResponse`，不经 `ok()` 外壳）。
      验签：`X-Acuven-Timestamp` 必须是纯数字的 Unix 秒、与当前相差不超过 300 秒；`X-Acuven-Signature` 必须是 `sha256=` 接 64 位十六进制；
      HMAC-SHA256，密钥是配置字符串的 UTF-8 字节，被签内容是时间戳、一个点、原始请求体字节；`hmac.compare_digest` 比较，
      当前与旧密钥都比一遍，任一把过即可。处理中抛出的异常不捕获，由 `get_db` 回滚后照常 5xx。`event_id` 只写进日志。
      现有 `/internal/whatsapp` 与 `X-Internal-Secret` 没动。
- [x] 新增 `backend/app/schemas/chatbot_event.py`：type 五选一；`occurred_at` 必须是带时区的 ISO 字符串；visitor 的 phone 与 bsuid
      至少一个非空，phone 非空时必须能被 `normalize_phone` 规范化；三种消息事件必须带 `message.wa_message_id`（非空）与 `message.body`；
      `ai_reply` 的 `bot_id` 可为空或缺省；`handover_changed` 带 `handover.active`（布尔）与 `handover.expires_at`（键必须在），
      active 为真时 expires_at 必须非空、为假时必须为空；`delivery_status` 带 `status.wa_message_id`、四种之一的 `status.status`、
      可空的 `error_code`（整数或字符串）与 `error_title`。`service_kwargs()` 把它转成 `process_event` 的普通参数（错误码转成字符串）。
- [x] `backend/app/routers/messages.py`、`backend/app/schemas/message.py`：消息列表、联系人消息与标记已读返回的每条消息多出
      `author_kind`、`bot_id`、`delivery_status`、`delivery_error`。
- [x] `backend/app/services/contact_service.py`、`backend/app/routers/contacts.py`、`backend/app/schemas/contact.py`：联系人字典多出
      `wa_handover_active`（标着接管中但到期时间已过或为空的按 false）、`wa_handover_expires_at`、`wa_handover_updated_at`
      （与 `created_at` 一样 `isoformat()`，经 `ok()` 统一补 `Z`）。列表与详情另加 `phone_conflict`：只对管理员调 CRM-TASK-007 的
      `find_phone_conflicts`，列表整页一次批量查询，按库里存的号码判断；其他角色不查、恒为 false。详情路由把当前用户传给
      `get_contact`（新参数可选，原有调用不受影响）。不返回 `whatsapp_bsuid`。
- [x] 新增测试 `backend/tests/test_chatbot_events_api.py` 与 `backend/tests/test_chatbot_fields_api.py`，每条 docstring 写明守住哪条验收标准：
      测试里按契约独立重算签名、时钟固定；五种事件各一条合法请求入库并返回 stored，另有 duplicate 与 ignored；响应体只有 `result`；
      改一个字节、换错密钥、时间戳前后差 301 秒、缺任一或两个头、签名或时间戳格式不对都是 401（差 300 秒仍通过）；旧密钥验得过；
      未配置 503；未签名或签错的坏请求是 401；签名正确的各类坏请求（含第九节第 5 条两种）与非 JSON 对象都是 422 且不写库；
      `bot_id` 为空或缺省、错误码为整数或字符串可以通过；处理出错是 5xx；`event_id` 不落库。消息三个接口的新字段；
      接管中、过期、到期为空、未接管的 `wa_handover_active` 与带 `Z` 的时间；不返回 BSUID；管理员看得到冲突、销售与经理恒为 false、
      删掉重复后冲突消失；列表只调一次冲突查询、非管理员不调。

## 偏离

- 401、503、422 的响应体用现有 `fail()` 的错误格式（chatbot 只看状态码）；只有 200 不套外壳。422 的 `fields` 只列字段路径与原因，不回显请求里的值。
- 比验收标准多收紧的几处：`bot_id` 超过 50 字符、BSUID 超过 128 字符、`wa_message_id` 超过 200 字符（各自列宽）按 422 拒收，
  免得写库时报错变成 5xx 卡住队列；时间字段只收字符串，不把数字当 Unix 时间；签名的十六进制大小写都收。
- `phone_conflict` 只加在列表与详情上；新建、编辑、归档接口返回的联系人字典里没有这个键（验收标准只要求列表与详情）。
  接管三项在所有返回联系人字典的接口里都有。
- 没有改处理规则、表结构、迁移、前端、nginx 与部署配置，没有改 `.platform/` 与 CI，没有写任何真实密钥（测试里的密钥是明显的假值）。

## 验证到什么程度

- 验证依据是上面两份新测试加已有后端测试，由 PR 的必需检查 `backend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。
- 没有和 ai_chatbot_demo 的真实推送联调；签名与请求体形状按设计第四节与登记时读到的 chatbot 任务 40.1、40.2 记录对齐。
- 冲突查询与接口只在 SQLite 上由测试覆盖，没有在 MySQL 上实际跑过；nginx 不转发 `/internal/` 沿用登记时读仓库配置的结论，本任务没有再核对线上。

---

# CRM-TASK-010：收件箱显示消息来源、送达与接管状态，管理员看得到号码冲突

来源：`docs/chatbot-events.md` 第七节。设计闸门：kelvinpang90/acuven_crm_demo#17。

## 做了什么

- [x] `frontend/src/types/index.ts`：补齐消息的来源、bot、送达状态与失败原因字段，以及联系人的接管和号码冲突字段。
- [x] `frontend/src/pages/Inbox/ConversationView.tsx`：显示 AI 行业或控制台来源、出站送达状态及失败原因；读取联系人详情，在有接管更新时间时按浏览器本地时间显示接管到期或 AI 回复状态。
- [x] `frontend/src/pages/Contacts/index.tsx`、`frontend/src/pages/Contacts/ContactDetailPanel.tsx`：在接口返回号码冲突时显示标记。
- [x] `frontend/src/locales/en` 与 `frontend/src/locales/zh` 的 inbox、contacts 命名空间：加入上述界面文案和五种行业名称。

## 偏离

- 无。

## 验证到什么程度

- 前端类型与构建的交付依据为 PR 的必需 `frontend` CI；结果由 Worker 和 CI 记录。
- 展示逻辑只消费 CRM-TASK-008 已返回的消息与联系人字段，未改变接口、路由或后端行为。

---

# 任务八：CRM 里可以加 AI 辅助的地方（盘点，2026-10-13）

> 来源：人工会话里盘点了 CRM 各模块，可以做的 AI 辅助列在下面。都按员工侧 AI API 的规则做：只生成文字，员工确认后由 CRM 自己保存，
> 不存 AI 正文，界面标「AI 生成，未核实」（acuven_hub `DECISIONS.md` E-16、E-17）。全部要等 AI API 并入 `ai_billing_hub` 后的 AI 模块上线。

| 优先 | 模块 | 功能 | 状态 |
|---|---|---|---|
| 1 | 收件箱 | 会话简报、起草回复、翻译 | 第一版已批准（PR kelvinpang90/acuven_crm_demo#29）；AI 分工改了，第二版 2026-10-13 已批准：`docs/inbox-ai-assist.md`（PR kelvinpang90/acuven_crm_demo#34） |
| 2 | 联系人 | 客户摘要（`crm.contact_summary`；原为 AI API 的契约 v1，按 ai_billing_hub 设计 #281 改为 CRM 自己的内部设计，待另写） | 已阻塞，待登记 |
| 3 | 跟进记录 / 任务 | 把员工的速记整理成结构化跟进记录，并生成任务草稿（E-09 原定的「任务草稿」） | 待设计 |
| 4 | 商机 | 下一步建议、商机停滞的原因说明（只给文字，不改状态） | 想法 |
| 4 | 仪表盘 | 经理看板的数据解读：数字由 CRM 算好，AI 只写解读 | 想法 |
| 5 | 联系人 | 从聊天里提取客户资料作为建议填入；表格导入时识别列名；疑似重复客户提示（不自动合并，B-05） | 想法 |
| 5 | 收件箱 | 意图和紧急度标签、按紧急度排序（每条入站消息都要调一次 AI，成本另议） | 想法 |
| 6 | 分配规则、屋顶项目、AutoCount | 建议分配给谁；根据勘察和完工照片起草报告；汇总客户的单据 | 想法 |

- [x] Kelvin 批准 `docs/inbox-ai-assist.md`（2026-10-13，第三节 Q1 至 Q6 均按建议）
- [x] ~~在 `ai_billing_hub` 提出三个能力的契约~~：不再需要。Kelvin 2026-10-13 定「每一个 AI 功能都在本系统修改，ai_billing_hub 只负责计费和调用 AI」
      （规格 §141 与设计 #281：AI 模块只有 `POST /v1/messages` 加 `feature` 标签，提示词与输出结构归调用方）。那边起草契约的会话没写任何东西就停了
- [x] 设计第二版（AI 分工改了，并补与第 2 阶段 CRM-TASK-012 至 020 的衔接，第八节）获 Kelvin 批准（2026-10-13，PR kelvinpang90/acuven_crm_demo#34，第十节 Q7 至 Q10 均按建议：共用一个 `AI_MODEL`、评测样例用 demo 数据、客户摘要另写设计、归档 ai_billing_hub 起草契约的会话）
- [x] 按设计第七节拆分并登记 CRM-TASK-021 至 026（Kelvin 2026-10-13 定不等 AI 模块上线、按 A 排在 016 之后，见下「登记 CRM-TASK-021 至 026」）
- [ ] AI 模块上线后：建客户端密钥、配四项环境变量、按设计第五节用 demo 会话做质量评测，Kelvin 打分认可

## 登记 CRM-TASK-021 至 026（2026-10-13）

Kelvin 2026-10-13 批准收件箱 AI 辅助设计第二版（kelvinpang90/acuven_crm_demo#34），并定不等 AI 模块上线、先登记实现，按 A 排在第 2 阶段 CRM-TASK-016 之后（原话「按 A，登记」）。本段是登记 PR 的记录。

### 做了什么

- `.platform/tasks.yaml`：登记 021–026，全部 `ready`。依赖：021 依赖 012（迁移 015 接 012 的 014）；022 依赖 021、013（快照要用第 2 阶段客服回复的送达语义）；023 依赖 022；024 依赖 023；
  025 依赖 024、016（与 016 改同一批前端文件）；026 依赖 025。
- **CRM-TASK-020 顺延**：它的删列迁移原为 015，现在 015 给了 `ai_requests` 建表，改为 016、接 015，并加依赖 021（设计第八节第 4 条）；`docs/chatbot-replies.md` 第九、十、十一（例 12）、十四节的迁移号同步改了。
- planning-v1：「当前计划」在 016 之后接 021–026；「已阻塞」里「待登记：收件箱 AI 会话简报、起草回复与翻译」一行删掉。
- `docs/inbox-ai-assist.md`：第七节写明先登记实现、AI 模块上线后只做建密钥、配环境变量与质量评测，并加只读的 `GET /api/ai/status`（前端未配置时隐藏按钮要用）；
  加第十一节「登记预审后的补充」（17 条），正文相应处同步改了。第十一节 Kelvin 2026-10-13 在本 PR（kelvinpang90/acuven_crm_demo#35）上批准，原话「批准」。

### 设计闸门与预审

- 设计闸门：六个都适用，都引用 #34（021 新增审计表与迁移；022、023 把会话内容组装后发给模型；024 是收发这些内容的接口；025、026 展示 AI 输出）。020 仍引用 #28。
- 预审：021–026 都跑了（设计文件 `docs/inbox-ai-assist.md`），020 因迁移号改了也复跑（设计文件 `docs/chatbot-replies.md`）。每轮只复跑改过或未通过的任务。
  最终 020、021、023、024、025、026 READY，022 第 4 轮只剩上一行两条文字层面的发现、已改；024、025、026 第 2 轮 READY 时报的 UNDECIDED 与 023 第 3 轮的 UNDECIDED 已写进验收标准与第十一节，之后没有再对它们跑预审。

逐条处理（全部成立，已改；编号指设计第十一节）：

| 轮次 / 任务 | 发现 | 处理 |
|---|---|---|
| 1 / 022 | **设计缺陷**：`author_kind` 为空的旧出站邮件按推断成了 `agent`，会全被标 `unconfirmed` | 第 4 条：只给 WhatsApp 出站带 `delivery`，且只看存的 `author_kind` |
| 1 / 022 | **设计缺陷**：发送途中（不足 45 秒）的客服回复没有对应取值 | 第 4 条：`sending` |
| 1 / 022 | **范围缺口**：`DEPLOY.md` 不在路径里 | 第 12 条，022 加这个路径 |
| 1 / 022 | 16 KiB 怎么算、`truncated` 何时为真、消息顺序；`request_id` 与模型文本在哪、哪个 `stop_reason` 算拒答 | 第 3、5 条 |
| 1 / 023 | **设计缺陷**：翻译没有快照，却也要求「只用快照里的事实」 | 第 6 条 |
| 1 / 023 | 校验不过由谁改判、起草 schema 按渠道、`as_of`、简报怎么说明截断、参数不合法的结果、超 64 KiB 写不写审计 | 第 7、8、9、3 条 |
| 1 / 024 | **设计缺陷**：管理员对不存在的联系人会调 AI 并写审计 | 第 10 条 |
| 1 / 024 | **设计缺陷**：后端 75 秒、前端 80 秒超过 nginx 的 60 秒 | 第 1 条：50 秒与 58 秒 |
| 1 / 024 | WhatsApp 起草的 `subject`；空文本与空要求 | 第 7 条；024 验收写最小长度 |
| 1 / 025 | 状态读几次、未列出的错误怎么提示 | 第 11 条 |
| 1 / 026 | **设计缺陷**：回复框 4096 字、翻译接口 4000 字，中间的必然 422 | 第 2 条：翻译 2000 字，超出置灰 |
| 1 / 026 | **设计缺陷**：消息气泡翻译超长正文 | 第 2 条 |
| 1 / 026 | **设计缺陷**：4000 字输入配 3000 `max_tokens` 必然被截断 | 第 2 条：`max_tokens` 4000 |
| 1 / 026 | 译文超 4096、邮件主题、默认语言、`note_for_agent` 何时清 | 第 2、11 条 |
| 2 / 022、023 | **设计缺陷**：只截邮件正文，长 WhatsApp 消息可能把最新一条也挤掉 | 第 13 条：所有正文截 2000 字、标签设上限、至少保留最新一条 |
| 2 / 022 | 邮件主题、同一时间的排序、哪些商机、接管块怎么输出 | 第 13 条 |
| 2 / 023 | 改判时审计留不留 `request_id`、`instruction` 缺省、检查顺序与审计、输出去空白与翻译上限 | 第 14 条 |
| 2 / 024 | 业务函数的返回形状、`INVALID_INPUT` 的映射 | 第 14 条 |
| 2 / 025、026 | 输出语言怎么由界面语言映射、未列出错误的提示、切换会话后迟到的结果、`note_for_agent` 在 202 与失败时、空正文与按什么计长度 | 第 15 条 |
| 3 / 022 | **设计缺陷**：中文每字 3 字节，上限合计约 19.6 KB 仍超 16 KiB | 第 16 条：收紧上限，最坏约 12.6 KB；字节按紧凑 JSON 算 |
| 3 / 023 | 哪些字段必填、返回去空白后的文本、`channel` 不合法、结果类别的名字 | 第 17 条 |
| 4 / 022 | 商机同一更新时间的次序；第十一节第 3、13 条还写着旧上限，与第 16 条不一致 | 加按 id 排；第 3、13 条改为指向第 16 条。第 4 轮之后的这两处修正没有再跑预审 |

### 验证到什么程度

- 契约自检：控制面 `python -m worker.contract_check --repo <本分支干净克隆> --project-id crm_os --task-id-pattern '^CRM-TASK-[0-9]{3}$' --planning tasks/todo.md`，
  `result: ready to register`，退出码 0；planning-v1 `ok, next: CRM-TASK-008`。WARN 只有原有的三条，与本次无关。
- 预审：上面所述。它调模型、不是确定性的，READY 不保证没有缺陷。
- 没验：AI 模块还没上线，接口格式以 ai_billing_hub 设计 #281 v5 为准，实现时若有变动只改 `ai_api_client.py`；合并后 Worker 主机的就绪上报与 Telegram 推荐行。

### 预审完整输出

```text
第 1 轮 CRM-TASK-020:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 24.9 万 token（其中缓存读取 20.7 万），折合 API 价约 $0.42；周额度读数 31%。
退出码 0

第 1 轮 CRM-TASK-021:
(no findings)
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 19.9 万 token（其中缓存读取 16.8 万），折合 API 价约 $0.32；周额度读数 31%。
退出码 0

第 1 轮 CRM-TASK-022:
PRECHECK_FINDING: DESIGN_DEFECT 4.4 :: Old outbound emails have empty author_kind, external_id and delivery_status; inferred as agent, the literal unconfirmed rule labels every one unconfirmed. The rule must check stored author_kind agent on whatsapp only.
PRECHECK_FINDING: DESIGN_DEFECT 4.4 :: An agent reply with empty external_id less than 45 s old is still sending under CRM-TASK-014/016, yet the snapshot can only call it unconfirmed; there is no value for in-flight replies.
PRECHECK_FINDING: SCOPE_GAP DEPLOY.md :: Design 8.6 says this design's tasks add the four AI settings to the config class and DEPLOY.md, but no task 021-026, including this one that adds the settings, lists DEPLOY.md.
PRECHECK_FINDING: UNDECIDED 4.4 :: Undecided whether the 16 KiB cap covers messages only or the whole snapshot (unbounded notes can exceed it alone), and whether it counts UTF-8 bytes with or without ensure_ascii.
PRECHECK_FINDING: UNDECIDED 4.4 :: Undecided whether truncated is set by the 40-message cap, the 16 KiB cut, a 2000-character email body cut, or all three, and whether messages are output newest-first or oldest-first.
PRECHECK_FINDING: UNDECIDED 4.1 :: The design does not say where request_id comes from (response body or header), where the model text sits, or which stop_reason means refusal, so stubbed tests would encode guesses.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 35 万 token（其中缓存读取 30.6 万），折合 API 价约 $0.49；周额度读数 31%。
退出码 1

第 1 轮 CRM-TASK-023:
PRECHECK_FINDING: DESIGN_DEFECT 5 :: The shared prompt rule (only use snapshot facts; snapshot and staff request are untrusted) is also required for crm.translate, which has no snapshot or request, so read literally the translate prompt forbids all facts.
PRECHECK_FINDING: UNDECIDED 4.1 :: Success in 4.1 needs the section-five checks, but the CRM-TASK-022 client already returns ok after JSON parsing; the design never says the service must turn that into error before writing the audit outcome and logging.
PRECHECK_FINDING: UNDECIDED 5 :: For reply_draft: one schema or one per channel, is subject required for email, and is a WhatsApp output with subject an AI error or dropped § all undecided, which changes the schema sent and the error tests.
PRECHECK_FINDING: UNDECIDED 5 :: as_of is listed as an input to the brief and draft but its value, format and place in the request are never defined, and the CRM-TASK-022 snapshot does not include it.
PRECHECK_FINDING: UNDECIDED 3 :: Q3 says the brief must mention that only recent history was seen when truncated, but no output field covers this and the prompt wording is not specified.
PRECHECK_FINDING: UNDECIDED 4.3 :: No result code is defined for bad business-function input (unsupported language, instruction over 200, text over 4000 or empty), and character counting (code points or otherwise) is not defined.
PRECHECK_FINDING: UNDECIDED 4.2 :: When the client refuses a request over 64 KiB without sending it, the design does not say whether that counts as a call that needs an audit row.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 10.2 万 token（其中缓存读取 7.6 万），折合 API 价约 $0.28；周额度读数 31%。
退出码 1

第 1 轮 CRM-TASK-024:
PRECHECK_FINDING: DESIGN_DEFECT 4.3 :: Read literally, may_access_contact lets an admin through for a missing or deleted contact, so translate calls the AI and audits a nonexistent contact_id and brief returns 400 not 403; the router needs its own existence check.
PRECHECK_FINDING: DESIGN_DEFECT 4.1 :: The 75s backend AI timeout and 80s frontend timeout exceed the nginx /api/ proxy_read_timeout of 60s, so slow calls get a proxy 504 outside the fail format while tokens are charged and audited as ok; nginx is out of scope here.
PRECHECK_FINDING: UNDECIDED 4.3 :: For WhatsApp reply drafts, the design does not say whether subject is omitted from the response or returned as null, and frontend tasks 025/026 will type against that choice.
PRECHECK_FINDING: UNDECIDED 4.3 :: No minimum length is set for translate text or reply-draft instruction, so whether empty or whitespace-only values get a 422 is left to the implementer.
session verdict: READY
judged from the findings, not the session's verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 20.6 万 token（其中缓存读取 16.7 万），折合 API 价约 $0.40；周额度读数 31%。
退出码 1

第 1 轮 CRM-TASK-025:
PRECHECK_FINDING: UNDECIDED frontend/src/pages/Inbox/ConversationView.tsx :: The criterion says read AI status once on entering the inbox, but the inbox page (index.tsx) is not an allowed path; choose a ConversationView-mount read or a module-level cache in services/ai.ts.
PRECHECK_FINDING: UNDECIDED 4.3 :: The task maps only AI_UNAVAILABLE, AI_ERROR and NOT_ASSIGNED; the message for 503 AI_NOT_CONFIGURED, 400 NO_MESSAGES, and the client-side 80 s timeout or network failure is not specified.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 10.1 万 token（其中缓存读取 7.8 万），折合 API 价约 $0.23；周额度读数 31%。
退出码 0

第 1 轮 CRM-TASK-026:
PRECHECK_FINDING: DESIGN_DEFECT 4.3 :: The reply box keeps 016's 4096-character cap but /api/ai/translate accepts at most 4000, so 4001§4096-character replies always fail with an uncoded 422; no rule says whether to disable the button or what to show.
PRECHECK_FINDING: DESIGN_DEFECT 4.5 :: Bubble translation applies to every message, but email bodies over 4000 characters exceed the translate limit; truncation, disabling or a specific message is not specified.
PRECHECK_FINDING: DESIGN_DEFECT 五 :: Translate allows 4000 input characters but max_tokens 3000; a 4000-character Chinese text translated to English or Malay needs more output tokens, gets truncated and is always AI_ERROR.
PRECHECK_FINDING: UNDECIDED 八 :: A WhatsApp reply translated into a longer language can exceed the 4096-character send cap after being filled back; truncate, refuse to fill, or warn is not decided.
PRECHECK_FINDING: UNDECIDED 4.5 :: For email conversations it is open whether translating the reply also translates the subject, and whether a draft overwriting an existing subject needs confirmation.
PRECHECK_FINDING: UNDECIDED 4.5 :: No default target language is given for translating the reply box or message bubbles, and when note_for_agent is cleared (send, new draft, conversation switch) is unspecified.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 13.8 万 token（其中缓存读取 10.6 万），折合 API 价约 $0.32；周额度读数 31%。
退出码 1

第 2 轮 CRM-TASK-022:
PRECHECK_FINDING: DESIGN_DEFECT §11.3 :: Only email bodies are truncated and tags are unbounded, so one long WhatsApp body plus full notes and deals forces dropping every message, including the newest, and the snapshot can stay over 16 KiB with nothing left to drop; no end state is defined.
PRECHECK_FINDING: UNDECIDED §4.4 :: The snapshot message shape omits the email subject; whether to include it, and whether it is truncated, is not decided.
PRECHECK_FINDING: UNDECIDED §11.3 :: Ordering is unspecified: no tie-break for messages with the same created_at second (ids are random UUIDs), and no rule on whether recent open deals sort by created_at or updated_at or exclude soft-deleted deals.
PRECHECK_FINDING: UNDECIDED §4.4 :: The handover block is unspecified when handover is inactive or expires_at is null or past: whether to emit it, and whether active means the stored flag or the effective state.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 14.8 万 token（其中缓存读取 11.8 万），折合 API 价约 $0.33；周额度读数 32%。
退出码 1

第 2 轮 CRM-TASK-023:
PRECHECK_FINDING: DESIGN_DEFECT 11.3 :: WhatsApp bodies are not truncated, so one long newest message plus notes and deals can push the snapshot over 16 KiB and dropping from the oldest empties messages; NO_MESSAGES vs calling the AI with no messages is undefined.
PRECHECK_FINDING: UNDECIDED 4.2 :: When a 200 response fails CRM's output checks and is re-judged as error, it's unspecified whether the audit row keeps the billed request_id; 4.2 says empty on failure, which breaks Billing Hub reconciliation.
PRECHECK_FINDING: UNDECIDED 4.3 :: instruction is optional but empty-after-strip is INVALID_INPUT; unclear whether None and "" differ, whether stripped text is sent, and whether absent instruction is null or omitted in the data block.
PRECHECK_FINDING: UNDECIDED 11.9 :: Order of INVALID_INPUT, AI_NOT_CONFIGURED and NO_MESSAGES checks is unspecified, as is whether INVALID_INPUT and NO_MESSAGES write an audit row.
PRECHECK_FINDING: UNDECIDED 5 :: Whether whitespace-only output counts as empty for translation and the minimum-1 fields (customer_need, draft, subject) is not stated, and translation has no upper length bound.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 10.8 万 token（其中缓存读取 8.2 万），折合 API 价约 $0.29；周额度读数 32%。
退出码 1

第 2 轮 CRM-TASK-024:
PRECHECK_FINDING: UNDECIDED 4.3 :: CRM-TASK-023 never fixes its functions' return shape, so the brief's truncated and request_id may not be exposed; meeting this task would then require editing ai_assist_service.py, outside allowed_change_paths.
PRECHECK_FINDING: UNDECIDED 4.3 :: No HTTP status or code is specified for an INVALID_INPUT result from the business functions (e.g. 400 INVALID_INPUT), even though request-model validation should normally make it unreachable.
session verdict: NOT_READY
judged from the findings, not the session's verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 15 万 token（其中缓存读取 11.6 万），折合 API 价约 $0.35；周额度读数 32%。
退出码 0

第 2 轮 CRM-TASK-025:
PRECHECK_FINDING: UNDECIDED 4.3 :: Brief output_language must be zh-CN/en/ms, but i18n.language can be zh, en-US, zh-TW or other navigator values; the mapping (and fallback) is unspecified and a raw value gets INVALID_INPUT.
PRECHECK_FINDING: UNDECIDED 11.11 :: The error-to-message mapping omits INVALID_INPUT and any unlisted status or code; the implementer must choose the fallback message.
session verdict: NOT_READY
judged from the findings, not the session's verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 10.6 万 token（其中缓存读取 8.4 万），折合 API 价约 $0.23；周额度读数 32%。
退出码 0

第 2 轮 CRM-TASK-026:
PRECHECK_FINDING: UNDECIDED 4.5 :: ReplyBox isn't remounted when switching conversations (no key at pages/Inbox/index.tsx:140); the design says to discard late results only for the brief, so a late draft or translation could fill the wrong customer's reply box.
PRECHECK_FINDING: UNDECIDED 11.11 :: It isn't decided whether note_for_agent is cleared on 016's 202 unconfirmed result (input cleared) or on a timeout or network error (input kept); only "send succeeded" is named.
PRECHECK_FINDING: UNDECIDED 4.3 :: "Interface language" could mean i18n.language (may be en-US, zh-TW or other; only zh maps to zh-CN) or the stored user language the backend uses; they can differ because the sidebar toggle doesn't save it.
PRECHECK_FINDING: UNDECIDED 11.2 :: Bubble translate only greys out above 2000 characters; whitespace-only bodies get a backend 422 with no message in the 025 list, and trimmed vs raw and code points vs UTF-16 counting are unspecified.
session verdict: NOT_READY
judged from the findings, not the session's verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 27.2 万 token（其中缓存读取 22.2 万），折合 API 价约 $0.51；周额度读数 33%。
退出码 0

第 3 轮 CRM-TASK-022:
PRECHECK_FINDING: DESIGN_DEFECT 11.13 :: With CJK text (3 bytes per char) the capped contact, 10 deal titles (200 chars, uncapped), tags, notes and the newest message total about 19.6 KB, so keeping the newest message can still exceed 16 KiB, contradicting the stated guarantee.
PRECHECK_FINDING: UNDECIDED 11.3 :: The design fixes UTF-8 without escaping but not the JSON separators (compact or default with spaces) used to measure the 16 KiB snapshot and 64 KiB request limits, which changes how many messages are dropped.
session verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 19.2 万 token（其中缓存读取 14.7 万），折合 API 价约 $0.45；周额度读数 33%。
退出码 1

第 3 轮 CRM-TASK-023:
PRECHECK_FINDING: UNDECIDED 5 :: The design never lists which schema fields are required; it is unclear whether fields that may be empty (suggested_next_step, note_for_agent, open_questions) are required, or whether an empty-after-trim open question is allowed.
PRECHECK_FINDING: UNDECIDED 11 :: It does not say whether returned outputs are the trimmed or original text, whether translate sends the trimmed text, or whether a draft channel other than whatsapp/email returns INVALID_INPUT.
PRECHECK_FINDING: UNDECIDED 11 :: How the unified result object labels INVALID_INPUT, AI_NOT_CONFIGURED and NO_MESSAGES next to ok/unavailable/error is not fixed, although CRM-TASK-024 depends on it.
session verdict: READY
PRECHECK_RESULT: READY
用量：Claude 会话 1 次，共 19.6 万 token（其中缓存读取 16.7 万），折合 API 价约 $0.32；周额度读数 33%。
退出码 0

第 4 轮 CRM-TASK-022:
PRECHECK_FINDING: UNDECIDED 11.13 :: Open deals are picked as the 5 most recently updated with no tie-break (messages get one by id), so deals updated in the same second make the 5-deal cut nondeterministic and hard to test.
PRECHECK_FINDING: DESIGN_DEFECT 11.3 :: §11.3 still says notes 1000 chars and 10 deals, and §11.13 says 20 tags/10 deals; §11.16 overrides both, and the task uses the final values, so it only misleads someone reading the design text.
session verdict: READY
judged from the findings, not the session's verdict: NOT_READY
PRECHECK_RESULT: NOT_READY
用量：Claude 会话 1 次，共 26.1 万 token（其中缓存读取 22.3 万），折合 API 价约 $0.42；周额度读数 33%。
退出码 1
```

## CRM-TASK-012（2026-10-13）

### 做了什么

- 新增迁移 014：为 `messages` 增加可空的 `sent_by` 字符串列，长度为 36。
- 在消息模型中增加对应的纯字段映射；未改服务、路由、schema 或前端。
- 新增迁移升降级与模型省略该字段时的测试覆盖。

### 偏离了什么

- 无。

### 验证到什么程度

- 新增用例覆盖最小 `messages` 表的 014 升级与降级、旧行的空值、无外键和索引，以及模型建表后省略 `sent_by` 的插入。

## CRM-TASK-013（2026-10-13）

### 做了什么

- 新增经 ai_chatbot_demo 内部发送客服回复的业务服务，包含号码、回复窗口和配置校验，以及先提交未确认消息再作带签名请求的流程。
- 按 chatbot 返回的已发出、明确没发出和结果不明三类结果更新、删除或保留消息；同步响应的接管状态使用严格晚于的写入函数。
- 新增服务层用例，用于覆盖签名和请求体、校验、三类发送结果、条件更新、接管写入及同一秒接管冲突。

### 偏离了什么

- 无。

### 验证到什么程度

- 服务层用例对应本任务验收标准中的签名、校验、结果分类、条件更新与接管写入行为。

## CRM-TASK-014（2026-10-14）

### 做了什么

- `chatbot_send_service`：新增 `retry_agent_reply`（同一个 request_id、同样正文、发给消息存的收件人、bsuid 取联系人现在的值、agent 为原发送人；
  45 秒内 `IN_PROGRESS`、满 24 小时 `RETRY_EXPIRED`，先于联系人与窗口检查；任何失败都不删消息）与 `handback_to_ai`（签名调用交还接口，
  只有 `ended` 且 handover 合法时经 `write_response_handover` 严格晚于写入；200 不合契约打 ERROR）。
  签名请求、已发出的条件更新与「明确没发出」的判定从 `send_agent_reply` 提成共用函数，CRM-TASK-013 的行为不变。
  重试与交还在调用 chatbot 前先提交（只结束只读事务，不写任何东西），调用后重新读取（`populate_existing`），
  所以调用期间事件写入的状态会如实返回（例：交还时 handover_changed 先到、响应同一秒不写，返回的仍是事件写入后的接管状态）。
- `chatbot_event_service`：新增 `store_agent_reply`，按设计第六节回填 `external_id`，只 flush、由事件接口提交（与其他事件处理函数一致）。
- 新增 `backend/tests/test_chatbot_reply_rules.py`，按验收标准列出的重试（1）–（8）、agent_reply（9）–（12）与交还（1）–（7）用例逐条覆盖，
  docstring 写明对应的验收用例与设计第十一节例 3（事件部分）、例 4、例 8、例 9。

### 偏离了什么

- 重试失败的结果类别沿用 CRM-TASK-013 的分类：明确没发出（502 rejected、401、422、503、连接失败）返回 `send_failed`，其余返回 `unconfirmed`；
  两种都保留消息。验收只要求「返回结果类别与错误信息」，不区分会让接口层把可能已发出的超时说成发送失败。
- 重试时消息不存在与不是 agent 消息一样返回 `not_retryable`（接口层两者都回 403 `NOT_ASSIGNED`）。
- 本任务原由 OpenClaw run 45c0d6ea 实现，独立评审修复两轮后仍要求修改（重试结果类别），按 Kelvin 指示改在人工会话里接着这个分支完成。

### 验证到什么程度

- `ruff check` 与 `ruff format --check` 本地通过。
- pytest 本地没有跑（本机 Python 版本装不上锁定的依赖），以 PR 的必需 CI 检查 `backend` 为准。

## CRM-TASK-015（2026-10-14）

### 做了什么

- WhatsApp 发送与重试接口改由 chatbot 发送服务处理，补齐结果映射、发送人字段和正文模型校验。
- 新增交还 AI 接口及 agent_reply 事件的接口校验与转交；联系人和消息响应补齐回复窗口与发送人显示字段。
- 新增接口层测试，并移除经旧发送路由断言 Graph 行为的用例。

### 偏离了什么

- 无。

### 验证到什么程度

- 新增测试覆盖发送映射、重试与交还的权限隐藏、agent_reply 校验和委托。

## CRM-TASK-016（2026-10-14）

### 做了什么

- 收件箱 WhatsApp 回复改为多行输入，限制 4096 字符，并按回复窗口状态禁用和提示。
- 增加经 chatbot 发送与重试的 45 秒请求超时、未确认送出状态与重试入口、客服发送人显示，以及交还 AI 操作。
- 补齐 WhatsApp 发送、重试和交还的中英文错误与状态文案，并同步前端接口类型。

### 偏离了什么

- WhatsApp 回复输入框由原来的单行输入改为多行输入；邮件输入与发送行为保持原状。

### 验证到什么程度

- 前端类型检查与构建由 PR 的必需检查 frontend 执行，检查结果由 Worker 和 CI 记录，不写在这里；没有在浏览器里实际操作过。
