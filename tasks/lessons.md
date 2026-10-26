# 教训记录

## L1 · 间接依赖不锁版本 → pool_pre_ping 全线 500

**现象**：代码未改动，重建镜像后约一半的 DB 请求返回 500：
`TypeError: AsyncAdapt_aiomysql_connection.ping() missing 1 required positional argument: 'reconnect'`

**根因**：不是 SQLAlchemy 或 aiomysql 的问题，而是**未固定的间接依赖 PyMySQL**。

SQLAlchemy `dialects/mysql/pymysql.py` 的 `do_ping()` 靠**反射 PyMySQL 的 `Connection.ping` 签名**来决定怎么调用：

```python
if self._send_false_to_ping:
    dbapi_connection.ping(False)
else:
    dbapi_connection.ping()      # 无参
```

- PyMySQL 1.1.1：`ping(self, reconnect=True)` → 走 `ping(False)` → 正常
- PyMySQL **1.2.0**：`ping(self, reconnect=False)` → 走无参 `ping()` → 而 SQLAlchemy 自己的
  `AsyncAdapt_aiomysql_connection.ping(self, reconnect)` 要求位置参数 → TypeError

`requirements.txt` 里 PyMySQL 由 aiomysql 间接带入、没有固定版本，PyMySQL 1.2.0 一发布，
任何一次重建镜像都会踩中。这是「代码没动却突然坏了」的典型来源。

**修复**：`backend/requirements.txt` 固定 `pymysql==1.1.1`（保留 `pool_pre_ping`）。

**教训**：
1. **先证伪再动手**。最初的两个候选方案（升 SQLAlchemy ≥2.0.31、升 aiomysql）看起来都很合理，
   实测 2.0.31/2.0.32/2.0.36/2.0.41/2.0.43 和 aiomysql 0.3.2 **全部无效**。
   如果直接按「首选方案」改完就提交，会得到一个假修复。
2. **验证必须用真实依赖栈**。SQLite 或 mock 完全测不出这个问题 —— 它只在
   MySQL + aiomysql + 连接池复用 三者同时成立时才触发。
3. **确定性复现优先于概率复现**。用 `pool_size=1` 强制连接复用，把「约 50% 概率」
   变成「稳定交替 OK/FAIL」，才能可靠地判断修没修好。
4. **要做对照实验**。修复后镜像通过不等于是这个改动修好的；必须拿修复前镜像跑同一套测试，
   看到它确实失败，因果才成立。
5. **锁间接依赖**。建议后续用 `pip-compile` 生成带哈希的完整锁文件，
   否则同类问题会在别的传递依赖上重演。

## L2 · 状态标记只在「新建」分支写入 → 迁移后的老数据永远走错路径

**现象**：2026-08-17 真机联调 `whatsapp_gateway` 的 CRM 分支。入站正常
（`POST /internal/whatsapp/inbound 200`），后台手动回复也「成功」了，用户收到了消息，
表面上完全正常。

但网关的 `/internal/whatsapp/outbound` 接口**自部署以来调用次数为 0** ——
回复根本没走网关，是 crm_os 自己直连 Graph API 发出去的。

**根因**：`app/services/whatsapp_service.py` 的 `_handle_message()` 里，
`is_gateway=True` 只写在**新建联系人**的分支内：

```python
if not contact:
    contact = Contact(..., is_gateway=is_gateway)   # 只有这里赋值
# 联系人已存在时，什么都不做
```

测试用的号码在 7 月直连测试时就已入库，`is_gateway` 是默认的 `False`。
走网关进来时这个标记不会被补上，于是 `send_message()` 里
`if contact.is_gateway:` 判断走了 else 分支，回落到直连 Graph API。

**为什么危险**：当时能成功，只是因为 crm_os 自己还持有一份有效 token。
一旦按架构规划把凭据收归网关独占，**所有迁移前就存在的联系人回复都会静默失败**，
而新建联系人一切正常 —— 故障只影响老数据，最难被测试发现。

**修复**：`_handle_message()` 增加 `elif is_gateway and not contact.is_gateway:` 分支，
在已存在的联系人从网关入站时补写标记。新增测试
`test_existing_contact_upgraded_to_gateway` 覆盖。

**教训**：
1. **「成功」不等于「走对了路」**。端到端手工测试只能证明用户收到了消息，
   证明不了它走的是设计的那条链路。要验证链路，得看**中间节点的日志有没有被打到**。
2. **查缺失比查存在更有效**。这个 bug 是靠「网关 outbound 调用次数为 0」发现的，
   不是靠任何一条报错。诊断时主动问一句：按设计**应该出现**但没出现的日志是什么？
3. **迁移引入的布尔标记，必须考虑存量数据**。新增 `is_gateway` 这类字段时，
   除了「新建时赋值」，一定要问「已存在的记录什么时候、由谁补上」。
   本例选择在下次入站时自动补写（幂等、无需数据迁移脚本），
   代价是「再也不发消息进来的老联系人」仍需人工处理。
4. **对照实验同 L1**：新测试先在未修复的代码上跑一遍，确认它**确实失败**
   （`assert contact.is_gateway is True` → 实际 `False`），修复后再跑通，因果才成立。

## L3 · 确认类自动回复应该是会话级，不是消息级

**现象**：用户截图反馈「CRM 一直自动发信息给顾客」—— 每发一句话，都收到一条
「您的留言已收到，我们的顾问会尽快通过此对话回复您。」

**根因**：`handle_demo_inbound()` 对**每一条**非重复入站消息都返回确认文案。
不是消息循环（出站不会变成入站），就是一对一地回，只是体验上像在刷屏。

**修复**：确认改成**每次会话发一次**，判据是「该联系人 24 小时内没有过入站消息」，
与 WhatsApp 自己的 24h 会话窗口对齐 —— 隔天再来的访客会重新收到一次收据，
但一次会话里连发几条不会重复。

**教训**：
1. **区分「收据」和「回复」**。收据（"我们收到了"）是会话级的，一次就够；
   回复是消息级的。把收据做成消息级，用户观感就是机器人坏了。
2. **自动发送的东西要按「对方视角」review 一遍**。代码逻辑上「每条都回」很自洽，
   但站在收信人那一侧看就是骚扰。写自动外发功能时主动问：连发 5 条会怎样？
3. 这类问题**测试很难自己发现**，因为每一次调用的行为都是"正确"的。
   只有真机连续发几条、或者写一个多消息的测试才暴露得出来。

## L4 · 取消跟踪的本地文件 → 切分支时被 git 覆盖、删除（2026-10-09）

**经过**：PR-A 用 `git rm --cached` + `.gitignore` 取消跟踪 `.claude/settings.local.json`（文件留在磁盘上），
随后 `gh pr merge --delete-branch` 把本地切回 master（那里这个文件仍被跟踪）再拉取合并提交。
切换时 git 把这个已被忽略的文件当成可丢弃的，用 master 的旧版本覆盖了；拉取的提交又把它删了。
本机后来新加、从没提交过的几条权限因此丢失，只能从历史恢复到最后一次提交的版本。

**教训**：
1. **被 `.gitignore` 忽略的文件，git 在切分支、拉取时会直接覆盖或删除，不会报错**。
   取消跟踪一个本地文件之前，先把它复制到仓库外；合并后再放回去。
2. **`gh pr merge --delete-branch` 不只是删远端分支**：它会切换本地分支并拉取。
   本地有只存在于工作区的东西时，不加这个参数，事后手动切换。

## L5 · 测试按自己的理解写死请求格式 → 上游契约对不上，CI 全绿、上线即 422（2026-10-16）

**经过**：AI 客户端把 `output_config` 写成 `{"format": "json_schema", "schema": …}`，单元测试断言的也是这个形状；
Billing Hub AI 模块的已批准契约是 `{"format": {"type": "json_schema", "schema": …}}`，且 `extra="forbid"`。
四个 AI 功能的代码与测试全部合并、CI 全绿，配好密钥后第一次真实调用每次都被 422 拒绝，CRM 报「AI 出错」（502）。
起因是本仓库设计文档的一句「`format` 为 `json_schema`」有歧义，实现照字面写了。

**教训**：
1. **对接外部接口时，请求体的断言要照对方的契约原文写**，不照自己的设计文档转述；转述处写成完整的 JSON 形状，不写成一句话。
2. 打桩测试只能证明「我发的是我以为的」，证明不了「对方收」。接通后的第一次真实调用要当成验收步骤做，不要默认能通。

## L6 · 人工 PR 占用了已登记任务预留的迁移编号 → Worker 照契约无法实现（2026-10-26）

**经过**：CRM-TASK-065 登记时预留迁移 `017`（`down_revision` 016），CRM-TASK-020 顺延为 `018`。
之后人工会话的商机来源 PR（kelvinpang90/acuven_crm_demo#146）新增 `017_add_deal_source.py`，也是 `016 → 017`，合并部署。
065 的 run 照字面再加一个 017 会让 Alembic 出现重复编号，Worker 以 `design_defect` 停下，只能开契约 PR 把 065 改为 018、020 改为 019。

**教训**：
1. **加迁移之前先查 `.platform/tasks.yaml` 里 ready 任务预留的编号**（搜 `新增迁移` 与 `alembic/versions`），不只看 `versions/` 目录里已有的文件。
2. 人工 PR 必须占用已预留的编号时，同一个 PR 里把受影响任务的契约与设计文档一起顺延。
