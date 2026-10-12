# acuven_crm_demo — 项目规则

> 本文件与同目录 `AGENTS.md` 是同一份内容的两个副本，改一个必须同步另一个。
> 「角色与流程」到「高风险操作」几节来自 Acuven 项目模板（OpenClaw 接入要求）；「Kelvin 的规则」是本项目原有的规则。

---

## 项目概况

- **是什么**：CRM 客户关系管理系统（客户、商机、任务、收件箱、WhatsApp / 邮件消息、AutoCount 同步）。
  **demo 项目**，不承载 Acuven 真实客户（Kelvin 2026-10-12）。原名 `crm_os`，2026-10-12 改名；OpenClaw 的 project_id `crm_os`、
  任务编号、VPS 目录、容器名、数据库名与域名都没改
- **技术栈**：后端 FastAPI + SQLAlchemy（async）+ Alembic + MySQL / Redis（共享基建）；前端 Vite + React + TypeScript；
  Docker Compose 部署，合并到 master 即按提交 SHA 自动部署生产（[DEPLOY.md](DEPLOY.md)）
- **任务编号**：`CRM-TASK-NNN`（控制面登记的 task_id_pattern：`^CRM-TASK-[0-9]{3}$`）
- **任务清单**：[tasks/todo.md](tasks/todo.md)（顶部的 planning-v1 块决定下一项任务；其余是各任务的计划与记录）；教训在 [tasks/lessons.md](tasks/lessons.md)
- **OpenClaw 契约**：[.platform/](.platform/)（project / commands / tasks 三个文件，默认 deny）

## 角色与流程

- **Claude Code 负责实现，另一个 Claude Code 会话负责独立审查。**
- **合并**：OpenClaw Worker 开的 PR，由 Kelvin 在 Telegram「批准」后合并；人工会话开的 PR，由 Claude 验证（CI 全绿、改动符合已批准的计划）后合并
  （Kelvin 2026-10-09 授权）。不直推 master。
- 一个编号任务一个分支一个 PR，不夹带；以 Draft PR 交付，CI 全绿后再请求审查。
- **碰钱、碰个人数据的任务先出设计、Kelvin 批准后才写代码**：定价 / 支付 / 退款 / 幂等 / 状态机，
  以及收集、存储、展示、导出个人数据（姓名、电话、邮箱、地址、证件等）的改动。设计一变，之前的批准作废。
  纯前端样式、文档、CI、脚本不走闸门。PR 正文写明「设计闸门：<设计的 Issue 或 PR>」或「设计闸门：不适用」并给出理由。
- `.platform/` 只由运营者的 PR 与下面的自动收尾 PR 改；OpenClaw 的 run 改不了它。任务合并部署之后由 OpenClaw 自动开收尾 PR，CI 通过后自动合并：
  只含两处机械改动——`tasks.yaml` 里该任务 `ready` 改成 `done`、从 planning-v1「当前计划」移除这一项（其后序号减一），
  不触发部署（`deploy.yml` 的 `paths-ignore`）。只有自动收尾失败（Telegram 收到「自动收尾失败」通知，附原因与建议）时才由人手工开收尾 PR。

## 主仓库检出保持干净

OpenClaw 的 Worker 直接在本仓库的主检出上 fetch、建 worktree；主检出有未提交的改动时，`开启` 会被预检拒绝。
人工会话改用 git worktree，或改完即提交；不在主检出里留下未跟踪的文件。

## 密钥与敏感信息不进仓库

- **本仓库是公开仓库。** 绝不提交凭据、密钥、token、`.env`、证书私钥、客户数据。
- 不写真实主机名、IP、域名、服务器上的绝对路径、本机路径。部署连接信息放 GitHub secrets，健康检查地址放 repository variables。
- 发现已经提交的密钥：立即告诉 Kelvin 去轮换，不要只删文件——历史里还在。

## 登记任务（.platform/tasks.yaml 与 planning-v1）

- 每个 `ready` 任务都要出现在 [tasks/todo.md](tasks/todo.md) 的 planning-v1 块里；「当前计划」里的每一项都要在 `tasks.yaml` 登记为 `ready`。
  格式见块上方的注释，格式不对整块作废、项目 fail closed。全文件只能有一对 planning-v1 标记。
- `allowed_change_paths` 逐个写精确文件路径（不是目录、不是通配符）；`allowed_commands` 只引用 `.platform/commands.yaml` 里已有的 id。
- **只在 CI 跑的检查不写进 `allowed_commands`**：后端 pytest（httpx AsyncClient，Worker 的 MXC 沙箱禁回环，会挂住）、
  前端 `npm ci` / `npm run build`。Worker 本来就要等 PR 的必需 CI 检查全绿才进入「等待批准」；
  验收标准依赖它们通过时，写明由哪条必需 CI 检查执行（`backend` 或 `frontend`）。
- **共享文件清单**：登记时逐个对照，补齐 `allowed_change_paths`。
  - 前端页面任务：`frontend/src/App.tsx`（路由）、`frontend/src/locales/en/<模块>.json` 与 `frontend/src/locales/zh/<模块>.json`（两种语言的文案要同时改）
  - 后端接口任务：`backend/app/main.py`（新增 router 时）、对应的 `backend/app/schemas/<模块>.py`
  - 记录：`tasks/todo.md`（只加任务记录段，不改 planning-v1 块）
- **任务的标题、目的与验收标准里不写任何 `xxx://` 形式的地址、主机名、邮箱地址**（也不写 IP、绝对路径）：它们会原样进 PR 正文并过泄漏规则，被拒即 run 失败。
- **任务的 `title` 写成一句中文说明**：80 字符内，单行，不用反引号、尖括号、「」『』【】〖〗、emoji、链接、路径。
- **登记为 `ready` 之前按条件跑设计预审**（完整说明在控制面仓库 `docs/ONBOARD-PROJECT.md`「登记任务之前」）：
  - 要跑：任务有设计闸门（碰钱、碰个人数据、状态机、改数据库结构或迁移），或 `contract_check` 给出拆分 WARN（`consider splitting`），
    或预计改动接近约 1500 行。在控制面仓库对本仓库的干净检出运行 `python -m worker.design_precheck`，把完整输出贴进登记 PR；
    `NOT_READY` 的发现修掉，或逐条写明为什么不成立。
  - 其余任务在登记 PR 里写「预审：不适用」并给出理由。
- **拆分规则**：一个任务只做一层（数据库 / 业务规则 / 接口 / 前端），用 `depends_on` 串起来，每一层合并后都能单独通过检查与 CI；
  `allowed_change_paths` 超过 12 个、验收标准超过 8 条、或预计改动超过约 1500 行，先拆（拆不开在任务里写明原因）。
- 浏览器验收本项目未启用（`project.yaml` 没有 `acceptance_hosts`）：任务不带 `acceptance` 块。

## 高风险操作——失败一次即停

SSH / 远程登录、数据库连接与密码、任何认证连接测试、生产环境的删除 / 重启 / 清空、防火墙与安全组规则：
执行一次失败后停止并汇报现象，等 Kelvin 下一步指示，不换参数连续重试。诊断默认只读。

---

## Kelvin 的规则

> **适用范围**：「先计划、等批准」只适用于**没有登记进 `.platform/tasks.yaml` 的工作**（临时需求、探索、人工会话里的新任务）。
> 已登记为 `ready` 的任务，计划与验收标准就是批准过的方案：直接按验收标准实现，不再停下来等确认；
> 对计划本身有异议、或发现验收标准行不通时，停下并在 PR 里写明，不自行改范围。
> 已登记任务的拆分以上面「拆分规则」为准，不再按「超 3 个文件先拆分」。

# 沟通
* 全部使用中文回复
* 需求模糊时先澄清——不脑补需求

# 思考
* 第一性原理——从原始需求出发，不假设用户知道一切；目标不清先讨论，动机和目标不明确时停下来沟通
* 路径不优主动建议——发现更短路径时及时指出
* 除了注释，代码全部使用英文
* 多想少动——谋定而后动

# 执行
* 非平凡任务进计划模式——3+ 步骤或涉及架构决策时，先规划
* 写代码前先描述方案——等批准再动手
* 超 3 个文件先拆分——拆成小任务，明确每个文件改什么
* 使用子智能体——复杂任务、研究探索、并行分析委托子智能体，保持主上下文整洁
* 测试驱动修复——出 bug 时先写能重现的测试再修复
* 自主 Bug 修复——收到 bug 直接修，不手把手教

# 质量
* 不堆砌兼容性代码——除非主动要求
* 不临时修复——找到根本原因
* 追求优雅——非平凡修改问自己「有没有更优雅的方式」，简单修复不过度设计
* 完成前验证——问自己「资深工程师会批准这个吗？」
* 列出边缘情况——写完代码后主动思考哪里可能出错

# 任务管理
* 先计划——写到 tasks/todo.md，包含可选项
* 验证计划——获得确认后再实施
* 追踪进度——逐步标记完成项
* 记录结果——在 tasks/todo.md 添加评审部分
* 记录教训——纠正后更新 tasks/lessons.md

# 核心
多想少动 · 知错即改 · 往正确的方向走
