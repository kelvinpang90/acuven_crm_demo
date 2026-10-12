# WhatsApp Cloud API 联调上线手册

> 适用版本：仓库 master（含 statuses / 非文本消息 / 错误透传 修复）
> 域名：`crm.kelvinpeng.com` · VPS：`<VPS 地址>` · 后端镜像：`ghcr.io/kelvinpang90/crm_os-backend:latest`

按节顺序执行；每一节末尾都有"应看到 / 验证"，做不到不要往下走。

---

## 1. 前置条件检查

在本地任意一台机器跑：

```bash
# 域名 + HTTPS 通
curl -I https://crm.kelvinpeng.com/

# Webhook 验证端点：无参数应 403
curl -i 'https://crm.kelvinpeng.com/api/webhooks/whatsapp'

# Webhook 用错误 token 验证应 403
curl -i 'https://crm.kelvinpeng.com/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=ping'
```

**应看到：** 第 1 条返回 200/3xx；第 2、3 条返回 `403 Forbidden`。

---

## 2. Meta 后台申请

> Meta Business Verification（商业认证）耗时数天，可先用 Test Number 走通流程，认证完再切换正式号。

1. **创建 Meta Business Account**：[business.facebook.com](https://business.facebook.com) → 创建账号。
2. **创建 App**：[developers.facebook.com/apps](https://developers.facebook.com/apps) → Create App → **Type: Business**。
3. **添加 WhatsApp Product**：进入 App → Add Product → **WhatsApp** → Set Up。
4. **拿 Test Phone Number**：WhatsApp → API Setup。复制以下三项保存：
   - **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   - **Temporary access token**（24h 有效）→ 先用它联调
   - 在 API Setup 页面把你的个人 WhatsApp 号码加入 Recipient phone numbers（沙盒只能给已添加的号码发消息）。
5. **App Secret**：左侧 App settings → Basic → **App Secret** → Show → 复制 → `WHATSAPP_APP_SECRET`。
6. **Verify Token**：本地生成一段随机字符串：

   ```bash
   openssl rand -hex 32
   ```

   保存为 `WHATSAPP_WEBHOOK_VERIFY_TOKEN`。
7. **生成 Permanent Access Token**（联调通过后再做，避免 24h 之后凭证失效）：
   - Business Settings → Users → System Users → Add → Role: Admin。
   - 给该 System User Assign Asset：选中你的 App，给 Full Control。
   - Generate New Token → 选 App + 权限 `whatsapp_business_messaging` + `whatsapp_business_management` → Never expires → 复制。
   - 替换 `.env` 里的 `WHATSAPP_ACCESS_TOKEN`，重启后端。

---

## 3. 填 `.env` 并重启

SSH 登 VPS：

```bash
ssh ubuntu@<VPS 地址>
cd /opt/crm_os
sudo nano .env   # 或 vim
```

补 4 行（已有则覆盖）：

```ini
WHATSAPP_PHONE_NUMBER_ID=123456789012345
WHATSAPP_ACCESS_TOKEN=EAAG...（粘贴 Temporary 或 Permanent token）
WHATSAPP_WEBHOOK_VERIFY_TOKEN=<openssl rand -hex 32 的输出>
WHATSAPP_APP_SECRET=<App Settings → Basic 里的 App Secret>
```

重启后端并跟踪日志：

```bash
sudo docker compose up -d backend
sudo docker compose logs -f backend
```

**应看到：** 后端启动日志里**没有** `WHATSAPP_APP_SECRET not set` warning。如果看到这行，说明 `.env` 没被加载，检查 `docker compose config | grep WHATSAPP`。

---

## 4. 配置 Webhook（Meta 后台）

1. 回到 Meta App → WhatsApp → **Configuration**（左侧菜单）。
2. Webhook 部分点 **Edit**：
   - **Callback URL**: `https://crm.kelvinpeng.com/api/webhooks/whatsapp`
   - **Verify token**: 跟 `.env` 里的 `WHATSAPP_WEBHOOK_VERIFY_TOKEN` 一字不差。
3. 点 **Verify and save**。
4. Webhook 保存后，下面有个 **Webhook fields** 列表，点 **Manage** → 勾选：
   - `messages` ✅（必勾，否则收不到入站消息）
   - 状态字段同样在 `messages` 内（Meta v18+ 把 statuses 合并到 messages 字段下推送）。

**应看到：**
- Meta 后台 Verify 成功（绿色对号）。
- 后端日志一行 `GET /api/webhooks/whatsapp` 200。

**失败排查：**
- `Verify failed`：Verify Token 不一致；或 Callback URL 没走 HTTPS；或 nginx 没 reload。
- 后端没收到 GET：Cloudflare WAF 拦了，到 CF 后台 → Security → WAF 看 Events。

---

## 5. 入站联调（receive）

### 5.1 文本消息

1. 用个人 WhatsApp 给 Test Number 发：`hello from QA`。
2. 后端日志应有一行 `POST /api/webhooks/whatsapp 200`。
3. 进 MySQL 验证：

   ```bash
   sudo docker compose exec mysql mysql -u root -p crm_os -e \
     "SELECT id, phone, name FROM contacts WHERE phone LIKE '%<你后4位>%';"

   sudo docker compose exec mysql mysql -u root -p crm_os -e \
     "SELECT channel, direction, body, external_id, created_at \
      FROM messages ORDER BY created_at DESC LIMIT 5;"
   ```

4. 前端：`https://crm.kelvinpeng.com/inbox` 应能看到这条消息，contact 自动创建，关联 Deal 也已创建。

### 5.2 非文本消息

依次发：一张图（带 caption "测试图"）、一个 PDF。再查 messages 表：

- 图片对应行 `body = "[image] 测试图"`，`external_id` 非空。
- PDF 对应行 `body = "[document: xxx.pdf]"`。

**这些之前会被静默丢弃，本次修复后必须入库。**

### 5.3 去重

把 5.1 的消息体（原始 webhook payload 可在 Meta 后台 Recent Deliveries 看到）用 curl 二次重放：

```bash
# 取一份真实的 inbound payload 存为 /tmp/inbound.json
curl -X POST https://crm.kelvinpeng.com/api/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=$(openssl dgst -sha256 -hmac "$WHATSAPP_APP_SECRET" /tmp/inbound.json | awk '{print $2}')" \
  --data-binary @/tmp/inbound.json
```

**应看到：** messages 表行数不变（external_id 唯一约束去重）。

---

## 6. 出站联调（send）

1. 前端 `https://crm.kelvinpeng.com/inbox` → 点击 §5 创建的 contact → ReplyBox 输入 `test reply` → Send。
2. 手机上**应**收到这条消息。
3. MySQL 验证：

   ```sql
   SELECT direction, body, external_id FROM messages
   WHERE contact_id = '<刚才的 contact id>'
   ORDER BY created_at DESC LIMIT 3;
   ```

   - 最新一行 `direction=outbound`，`external_id` 非空（Graph API 返回的消息 id）。

### 6.1 故障路径验证

临时改坏 token 重放：

```bash
sudo sed -i 's/^WHATSAPP_ACCESS_TOKEN=.*/WHATSAPP_ACCESS_TOKEN=invalid/' /opt/crm_os/.env
sudo docker compose up -d backend
```

再从前端发一条 → **应看到：**
- 前端右上角弹 `WhatsApp API error: HTTP 401 ...` 红色 toast。
- `messages` 表**没有**新增 outbound 行（失败不入库）。
- 后端日志有 `ERROR WhatsApp send failed status=401`。

恢复正确 token、重启。

---

## 7. 回执验证（statuses）

1. 在手机上把刚收到的消息标记为已读（或自然送达）。
2. Meta 会回推 `statuses` 事件。后端日志应出现：

   ```
   INFO WhatsApp status=sent external_id=wamid.xxx recipient=86xxx
   INFO WhatsApp status=delivered external_id=wamid.xxx recipient=86xxx
   INFO WhatsApp status=read external_id=wamid.xxx recipient=86xxx
   ```

3. 故障消息会被 Meta 回推 `status=failed`，后端日志会 `ERROR` 级别打 errors。

> 当前版本只记日志，不写库（避免迁移）。后续若要在 Inbox 展示"已读/送达"，需要给 messages 表加 `delivered_at` / `read_at` 列。

---

## 8. 生产检查清单

部署后逐项打勾：

- [ ] `.env` 4 项凭证全部非空，`WHATSAPP_ACCESS_TOKEN` 是 Permanent（不是 24h Temporary）。
- [ ] 后端启动日志**没有** `WHATSAPP_APP_SECRET not set` warning。
- [ ] 伪造签名的 webhook POST 返回 403：

  ```bash
  curl -i -X POST https://crm.kelvinpeng.com/api/webhooks/whatsapp \
    -H "Content-Type: application/json" \
    -H "X-Hub-Signature-256: sha256=deadbeef" \
    -d '{}'
  ```

- [ ] Meta 后台 Recent Webhook Deliveries 全 200，无 retry。
- [ ] Inbox 能正常收发文本 / 图片 / 文档。
- [ ] 前端 send 失败有 toast 提示。
- [ ] Cloudflare WAF 没把 Meta 出口 IP 拦截（看 Events 面板）。
- [ ] （正式上线前）Meta Business Verification 通过 + Display Name approved。

---

## 附录 A：常见故障

| 现象 | 排查 |
|---|---|
| `Verify failed` | Verify token 大小写敏感；检查 `.env` 和 Meta 后台是否一字不差。 |
| Webhook GET 200 但 POST 收不到 | Webhook fields 没勾 `messages`；或 Cloudflare WAF 拦截。 |
| POST 200 但 messages 表无新行 | 看后端 `WhatsApp inbound missing from/id`；或 dedup（external_id 已存在）。 |
| 前端 send 502 | token 过期 / phone_number_id 错；后端日志会打 `WhatsApp send failed status=...` 含详细 body。 |
| 前端 send 400 NO_PHONE | contact.phone 为空——这是数据问题，不是凭证问题。 |
| `status=failed` 一直出现 | recipient 没加入 Test Number 的 Recipient list（沙盒限制）；或 24h 会话窗口外只能发模板。 |
| `status=failed` 且 errors 含 `131031 Business Account locked` | **Business Verification 未通过**（141010）。即使沙盒 Test Number 向 recipient list 内号码发送也会被 Meta 阻断。详见附录 C。 |

## 附录 B：Permanent Access Token 生成详细路径

```
business.facebook.com
  → Business Settings
    → Users → System Users  → Add（命名如 "crm-bot"，Role: Admin）
      → Assigned Assets → Add Assets → Apps → 勾你的 App → Full Control
      → Generate New Token
        → 选 App
        → Token expiration: Never
        → Permissions: whatsapp_business_messaging + whatsapp_business_management
        → 复制（只显示一次！）
```

## 附录 C：131031 / 141010 出站阻断（Business Verification）

**症状**：入站全部正常，statuses 回执也能收到，但前端 send 之后后端日志立刻打：

```
WhatsApp status=failed external_id=wamid.xxx
  errors=[{'code': 131031, 'title': 'Business Account locked',
           'message': 'Business Account locked',
           'error_data': {'details': 'Business account has been locked.'}}]
```

**根因**：用 `health_status` API 自查：

```bash
TOKEN=$(grep '^WHATSAPP_ACCESS_TOKEN=' /opt/crm_os/.env | cut -d= -f2-)
curl -s "https://graph.facebook.com/v18.0/<PHONE_NUMBER_ID>?fields=health_status" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -m json.tool
```

如果返回里 BUSINESS entity 的 errors 含 `141010 The Business has not passed business verification`，就是这个问题。**自 2024 年起 Meta 收紧：未通过 Business Verification 的 WABA 即使是沙盒 Test Number 向 recipient list 内号码发送也会被阻断。**

**解法（按推荐顺序）**：

1. **完成 Business Verification（推荐长期方案）**
   - 需要营业执照（马来西亚 SSM 个人独资也行，~RM30/年）+ 地址证明（水电账单）
   - business.facebook.com → Business Settings → Security Center → Start Verification
   - 上传文件 → 2-7 天审核
   - 通过后 141010 消失，永久解锁
2. **借用现有公司认证**：用合作公司营业执照走 Business Verification。风险：WABA 归属对方。
3. **仅入站模式**：CRM 只用作消息归档，回复由销售用个人 WhatsApp 手动完成。0 成本立刻可用。

**注意**：填补 Business Info（Legal Name / Country / Website）只能消除 `131000`；加支付方式只能消除 `141006`；都消不掉 `141010`。**141010 唯一解法是 Business Verification**。

## 不在本次范围

- 模板消息（template messages）：24h 会话窗口外主动联系客户必须用模板。
- 媒体上传（出站发图/文件）：需 Graph API `/media` + `/messages` 两步。
- 把 `statuses` 持久化到数据库：需 alembic 迁移，列为后续 ticket。
- Settings/Integrations 页面凭证编辑表单：当前仍是 env-only。
