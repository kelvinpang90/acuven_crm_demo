# 疑似重复客户提示

> 状态：**已批准**（Kelvin 2026-10-21，原话「按建议」：第八节 Q1 至 Q5 均按建议，PR kelvinpang90/acuven_crm_demo#135）。登记的任务，设计闸门引用 kelvinpang90/acuven_crm_demo#135。
> 第一至八节如上已批准；**第九节**是登记时按拆分与设计预审补的，待 Kelvin 在登记 PR 上批准；与正文冲突处以第九节为准。
> 设计闸门的理由：要把其他客户的姓名、公司、电话、邮箱展示给员工（个人数据的展示范围），并改变导入的行为。不新增表、不加迁移。
> 上位决定：`acuven_hub` 的 `DECISIONS.md` B-05（手机号或聊天身份相同**不能自动合并**）、A-18。
> 来由：任务八优先 5 的「联系人：疑似重复客户提示（不自动合并，B-05）」。

## 一、结论先说：第一版不用 AI

重复的强信号是**同一个电话**或**同一个邮箱**，其次是**同名同公司**。这三条 CRM 用规则就能判断，又快又准、不花钱、不用把客户资料发给模型。
AI 能多抓的是「Tan Hardware Sdn Bhd」与「TAN HARDWARE」、「陈先生」与「Mr Tan」这种模糊情况，但误报也多，而且要在每次建客户、每行导入时调用。
建议第一版只做规则（Q1）；用一段时间看漏了多少，再决定要不要加 AI 的模糊比对。

## 二、现状

- 只有**管理员**能看到「电话冲突」标记（`contact_service.find_phone_conflicts`，读取时计算，不存）；销售与经理看不到。
- 新建、编辑、导入都不查重；导入同一张表两次会建出两套客户。
- chatbot 进来的消息挂到「同号码里最早建的那个未删除客户」上。
- 没有合并功能，本设计也不做（B-05）。

## 三、判定规则

对一个客户（或一份待建的资料），以下任一条成立即为「疑似重复」，比较对象是其他**未删除**的客户（含已归档）：

| 规则 | 怎么比 |
|---|---|
| `phone` | 两边 `normalize_phone` 后相同（规范化不了的不比） |
| `email` | 去首尾空白、转小写后相同 |
| `name_company` | 名字与公司**都非空**，且两边都「规范化」后相同：转小写，去掉标点与空白，公司再去掉常见后缀（`sdn bhd`、`bhd`、`berhad`、`plt`、`enterprise`、`trading`、`ltd`、`limited`、`co`、`inc`、`有限公司`、`公司`） |

- 名字只是号码（chatbot 访客）不参与 `name_company`。
- 一对客户命中几条规则就列几条，界面显示命中的是哪条。

## 四、谁看得到什么（Q2）

查重结果分两种：
- **看得到的重复**：对方客户这个员工本来就能看（`may_access_contact`）。显示名字、公司、负责人、命中的规则，可以点开。
- **看不到的重复**：对方在别人名下。只显示「另有 N 个客户可能是同一人，归属其他同事，请联系经理确认」，**不显示**对方的名字、公司、负责人，也不能点开。
  这样销售知道撞单了，但不会借查重看到别人客户的资料。
- 管理员看得到全部。现有的「电话冲突」标记保留不动（它是本功能 `phone` 规则的一个子集）。

## 五、在哪里提示

1. **新建客户**（`NewContact.tsx` 的表单）：电话、邮箱、名字或公司失焦时查一次；有疑似重复时在表单上方显示提示（第四节两种），**不拦**，员工可以照样保存（Q3）。
2. **客户详情面板**：面板顶部（名字下面）显示「可能重复」一块，列出命中的客户；没有就不显示。面板打开时查一次。
3. **导入**（Q4）：每行先按 `phone` 与 `email` 两条规则查（和库里已有的客户比，也和同一张表里前面的行比）：
   - 命中的行**默认跳过**，记进结果的错误列表，原因「疑似重复：与已有客户的电话（或邮箱）相同」，不写对方是谁；
   - 导入页加一个勾选框「疑似重复的也导入」，勾上则照导、不跳过；
   - `name_company` 只用于提示、不跳过（误判的代价比电话邮箱大）。
   - 只填了 `id`（加商机到已有客户）的行不查重。

## 六、CRM 侧设计

### 6.1 业务（新文件 `backend/app/services/duplicate_service.py`）

- `normalize_company(value)`、`normalize_name(value)`：第三节的规范化。
- `find_duplicates(db, *, user, name, company, email, phone, exclude_id=None, limit=20) -> DuplicateResult`：
  按第三节三条规则查未删除的客户（排除 `exclude_id`）；结果分成 `visible`（按第四节可访问的，每个带 `id`、`name`、`company`、`assigned_to_name`、`rules`，最多 `limit` 个）与 `hidden_count`。
  `phone`、`email` 走索引或等值查询；`name_company` 先按规范化前的公司做宽筛（`ILIKE` 前缀），再在 Python 里比规范化结果，限 200 个候选。
- 不改 `contact_service.py` 的查询（CRM-TASK-018 也要改它）；导入的查重是例外，见 6.3。

### 6.2 接口

- `POST /api/contacts/duplicate-check`：请求体 `name`、`company`、`email`、`phone`（都可空），返回 `visible` 与 `hidden_count`。只要求登录。
- `GET /api/contacts/{id}/duplicates`：这个客户的疑似重复；客户看不到 → 403（同 `GET /api/contacts/{id}`）。
- 都加在 `routers/contacts.py`，路径写在 `/{id}` 路由之前，避免被当成 id。

### 6.3 导入

`import_contacts` 新增参数 `skip_duplicates`（缺省为真），导入接口加同名表单字段（缺省为真）。新建客户的行在插入前用 `phone`、`email` 两条规则和库里已有的客户、
以及本次已插入的行比较；命中且 `skip_duplicates` 为真时跳过并记错误。这需要改 `contact_service.import_contacts`（与 CRM-TASK-018 改同一个文件的不同函数，按依赖先后合并）。

### 6.4 前端

- 新组件 `DuplicateHint.tsx`：输入 `visible` 与 `hidden_count`，显示第四节的两种提示。新建表单与详情面板共用。
- 新建表单：电话、邮箱、名字、公司失焦时调 `duplicate-check`（300 毫秒内只发最后一次；请求未回来就保存也不拦）。
- 详情面板：打开时调 `GET /{id}/duplicates`，有结果时在名字下面显示；点看得到的那个客户切换面板。
- 导入页：加勾选框「疑似重复的也导入」（默认不勾）。
- 文案进 `locales/en/contacts.json` 与 `locales/zh/contacts.json`。

## 七、风险与边缘情况

- **误报**：同公司不同联系人（同一家公司的两个采购）会被 `name_company` 以外的规则放过，但若共用公司座机会被 `phone` 命中。只提示不拦，导入时可以勾选照导。
- **漏报**：名字写法不同、电话一个带分机一个不带、邮箱大小写之外的差异（如 Gmail 的点）都不算。第一版接受，看使用情况再议（Q1 的 AI 模糊比对）。
- **借查重探资料**：看不到的重复只给数量（第四节）；`duplicate-check` 只回答「有没有、有几个」，不回答是谁。仍能通过反复试号码得知「某号码在 CRM 里存在」，这与现在 chatbot 访客本来就进 CRM 一样，不另做限制（Q5）。
- **导入变慢**：每行两次等值查询，有索引（电话有，邮箱没有：邮箱按小写比较用不上索引，Q5 一并决定是否给 `email` 加索引）。导入本来就是同步一次请求，几千行内可以接受。
- **并发**：两人同时建同一个客户都会通过查重，事后在详情面板里会互相显示为重复。

## 八、登记时怎么拆、需要拍板（2026-10-21 已定：Q1 至 Q5 均按建议）

1. 业务：`duplicate_service`（6.1）与测试。路径：`backend/app/services/duplicate_service.py`（新）、`backend/tests/test_duplicate_service.py`（新）、`tasks/todo.md`
2. 接口：6.2。路径：`backend/app/routers/contacts.py`、`backend/app/schemas/contact.py`、`backend/tests/test_contact_duplicates_api.py`（新）、`tasks/todo.md`；依赖 1
3. 业务：导入查重（6.3）。路径：`backend/app/services/contact_service.py`、`backend/app/routers/contacts.py`、`backend/tests/test_contact_import_duplicates.py`（新）、`tasks/todo.md`；依赖 1
4. 前端：新建表单与详情面板（6.4 前三条）。路径：`frontend/src/pages/Contacts/DuplicateHint.tsx`（新）、`frontend/src/pages/Contacts/NewContact.tsx`、`frontend/src/pages/Contacts/ContactForm.tsx`、`frontend/src/pages/Contacts/ContactDetailPanel.tsx`、`frontend/src/services/contacts.ts`、`frontend/src/types/index.ts`、`frontend/src/locales/en/contacts.json`、`frontend/src/locales/zh/contacts.json`、`tasks/todo.md`；依赖 2
5. 前端：导入页勾选框。路径：`frontend/src/pages/Contacts/ExcelImport.tsx`、`frontend/src/services/contacts.ts`、`frontend/src/locales/en/contacts.json`、`frontend/src/locales/zh/contacts.json`、`tasks/todo.md`；依赖 3

第 3、5 项与导入列名识别（[contact-import-columns.md](contact-import-columns.md)）改同一批文件，登记时用 `depends_on` 排先后。全部有设计闸门，登记前跑设计预审。

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| Q1 | 用不用 AI | A 第一版只用规则；B 规则加 AI 模糊比对（建客户与导入时调用） | A：强信号规则就能抓，不花钱、不外发资料；用一段时间看漏报再议 |
| Q2 | 对方在别人名下时显示什么 | A 只显示数量，不显示是谁；B 显示名字与负责人；C 什么都不显示 | A：销售知道撞单、能找经理，但看不到别人客户的资料 |
| Q3 | 新建时发现重复要不要拦 | A 只提示，照样能保存；B 拦，要经理才能建 | A：误报时不卡人；B-05 只要求不自动合并 |
| Q4 | 导入时电话或邮箱重复 | A 默认跳过，可勾选照导；B 只提示，照导；C 一律跳过 | A：重复导入是现在最主要的重复来源；勾选照导留了出口 |
| Q5 | 邮箱要不要加索引、要不要防「试号码」 | A 都不做（规模小，号码本来就会进 CRM）；B 给 `email` 加小写索引（加迁移）并给查重接口限速 | A：demo 规模下不必要；真实客户上线前再看 |

## 九、登记时补的（2026-10-21）

登记 CRM-TASK-060 至 064 时把下面几处写死，对应任务的验收标准已按这里写；与正文冲突处以本节为准。

1. **看不到的客户返回 404**：`GET /api/contacts/{id}/duplicates` 对不存在、已删除或无权访问的客户返回与 `GET /api/contacts/{id}` 相同的 404 `NOT_FOUND`（6.2 原写 403；现有联系人接口一律 404，不透露 id 是否存在）。
2. **同名同公司不做宽筛**：取全部未删除、名字与公司都非空的客户（只取 id、名字、公司）在 Python 里比规范化结果。前缀宽筛会漏掉「T.A.N. Hardware」与「Tan Hardware」这类规则本身认为相同的情况；demo 规模下全量比可以接受，接真实客户前再议。6.1 原写的「`ILIKE` 前缀宽筛、限 200 个候选」作废。
3. **导入里本表内的重复**：先和本次导入已插入的行比，再和导入前就有的客户比；原因用英文（与现有导入错误一致）：`Possible duplicate: same phone as row N`、`same email as row N`、`same phone as an existing contact`、`same email as an existing contact`，N 是表格行号（表头是第 1 行，与错误列表的行号一致）；电话与邮箱都命中只记一条、记电话。
4. **公司名规范化的顺序**：先转小写、标点换空格、压缩空白，再从末尾**按整词**反复去掉后缀（中文后缀按末尾子串），最后去掉全部空白。原写法先去空白再去后缀，`sdn bhd` 永远去不掉，还会把「Zinc」去成「z」。
5. **邮箱为空不比**：两边去首尾空白转小写后比较，去空白后为空的不比。
6. **新建表单四项都空时**：清掉提示、不发请求。
7. **点重复客户切换面板**：Contacts 的 `index.tsx` 加一个回调读出该客户并设为当前选中，编辑、归档、删除随之指向它。
8. **号码名字两边都看**：任一边的名字只是号码就不比 `name_company`。
9. **导入查重的先后**：电话与本表已插入的行、电话与已有客户、邮箱与本表已插入的行、邮箱与已有客户，取第一个命中的；已因校验失败被跳过的行不再查重。
10. **新建页的重复客户不可点**：新建页没有客户详情的入口，只显示文字；详情面板里可点。
