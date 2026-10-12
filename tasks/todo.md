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

1. `CRM-TASK-004` 发送失败或未配置时如实报错，不再把消息记为已发出
2. `CRM-TASK-002` 客户列表支持只看未分派的客户
3. `CRM-TASK-003` 客户页给管理员显示未分派数量和筛选开关

### 已阻塞

- 待登记：CRM AI 客户摘要接入 acuven_ai_api｜阻塞：等 acuven_ai_api 的客户摘要能力与 Billing Hub 第 3 阶段验收

### 后续计划

- 待登记：demo 号经 ai_chatbot_demo 收发、一个号码多客服分别回复（任务七，设计已批准，待拆任务）
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

## 偏离

- 没有改演示联系人经网关的分支、前端、表结构、收邮件。
- WhatsApp 测试里额外补了凭据齐全时的成功与失败用例（验收标准 2 说行为不变，原来没有测试守住）。
- 邮件的 STARTTLS 失败与连接、认证、发送同样走 `api_error`（原来的 `try` 本就包着它）。
- 任务七「发现的问题」里对应的两条复选框保持 `[ ]`，等本 PR 合并后再勾。

## 验证到什么程度

- 验证依据是上面的测试加已有后端测试，由 PR 的必需检查 `backend` 执行；检查结果由 Worker 和 CI 记录，不写在这里。
