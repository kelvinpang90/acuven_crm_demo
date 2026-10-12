# CRM 部署手册（VPS + Docker Compose 共享栈 + GitHub Actions）

> 适用场景：VPS 已有 nginx / mysql / redis 以 Docker Compose 编排运行，本项目复用这些基础设施，通过 GitHub Actions 自动部署，使用独立二级域名 + Let's Encrypt HTTPS。WhatsApp / Email 集成暂不启用。

---

## 0. 占位符说明

下面所有命令出现的占位符，操作前替换成你环境的实际值：

| 占位符                       | 含义 | 示例                        |
|---------------------------|------|---------------------------|
| `crm.kelvinpeng.com`      | CRM 子域名 | `crm.kelvinpeng.com`      |
| `ubuntu`                  | VPS 登录用户 | `ubuntu`                  |
| `<VPS 地址>`              | VPS IP 或域名 | `203.0.113.10`（文档示例地址） |
| `/opt/crm_os`             | 项目在 VPS 的目录 | `/opt/crm_os`             |
| `proxy_net`               | nginx 所在网络（前端层） | `proxy_net`               |
| `data_net`                | mysql/redis 所在网络（数据层） | `data_net`                |
| `infra_mysql`             | 共享 mysql 容器名 | `infra_mysql`             |
| `infra_redis`             | 共享 redis 容器名 | `infra_redis`             |
| `infra_nginx`             | 共享 nginx 容器名 | `infra_nginx`             |
| `/srv/infra/nginx/conf.d` | 共享 nginx 配置目录 | `/srv/infra/nginx/conf.d` |
| `123456`                  | crm_user 的 mysql 密码（生产必须强密码） | `openssl rand -base64 24` |
| `123456`                  | JWT 密钥 | `openssl rand -hex 32`    |
| `kelvinpang90`            | GitHub 用户名/组织名（小写） | `kelvinpang90`            |
| `acuven_crm_demo`         | 仓库名（2026-10-12 前叫 crm_os） | `acuven_crm_demo`         |

---

## 1. 一次性准备（约 30 分钟）

### 1.1 盘点共享基础设施

SSH 进 VPS：

```bash
ssh ubuntu@<VPS 地址>

# 确认共享网络
docker network ls
# 确认 mysql / redis / nginx 容器名
docker ps --format "table {{.Names}}\t{{.Image}}"
# 确认共享 nginx 配置目录
docker inspect infra_nginx | grep -A2 Mounts
```

把上述拿到的实际名称填入第 0 节占位符。

### 1.2 在共享 MySQL 内建库建用户

```bash
docker exec -it infra_mysql mysql -uroot -p
```

```sql
CREATE DATABASE crm_os CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'crm_user'@'%' IDENTIFIED BY '<DB_PASSWORD>';
GRANT ALL PRIVILEGES ON crm_os.* TO 'crm_user'@'%';
FLUSH PRIVILEGES;
EXIT;
```

验证：

```bash
docker exec -it infra_mysql mysql -ucrm_user -p<DB_PASSWORD> -e "SHOW DATABASES;"
```

### 1.3 改造项目 `docker-compose.yml`

把仓库根目录 `docker-compose.yml` 改为只跑 backend 和 frontend，复用共享 mysql/redis/nginx：

```yaml
networks:
  proxy_net:
    external: true
    name: proxy_net          # nginx 所在网络
  data_net:
    external: true
    name: data_net           # mysql / redis 所在网络

services:
  backend:
    image: ghcr.io/kelvinpang90/acuven_crm_demo-backend:latest
    env_file: .env
    environment:
      - DB_HOST=infra_mysql
      - REDIS_URL=redis://infra_redis:6379/0
      - INSTANCE_ID=backend
    networks: [proxy_net, data_net]
    restart: unless-stopped
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000

  frontend:
    image: ghcr.io/kelvinpang90/acuven_crm_demo-frontend:latest
    networks: [proxy_net]
    restart: unless-stopped
```

> 网络拓扑：
> - `backend` 同时挂 `proxy_net`（让 `infra_nginx` 能反代）+ `data_net`（连 `infra_mysql` / `infra_redis`）
> - `frontend` 只挂 `proxy_net`
> - 原 `docker-compose.yml` 里的 `mysql` / `redis` / `nginx` 服务和 `volumes: mysql_data` 全部删除
>
> 单 backend 实例：发布时容器重启会有 ~5-10 秒短暂 502，不是零停机。后续如需高可用可加 `backend_2` 并改回 `least_conn` upstream。

### 1.4 准备生产 `.env`

在 VPS 上：

```bash
mkdir -p /opt/crm_os
cd /opt/crm_os
nano .env
```

内容：

```env
# 数据库
DB_HOST=infra_mysql
DB_PORT=3306
DB_USER=crm_user
DB_PASSWORD=<DB_PASSWORD>
DB_NAME=crm_os

# Redis
REDIS_URL=redis://infra_redis:6379/0

# JWT
SECRET_KEY=
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# 应用
APP_ENV=production
CORS_ORIGINS=https://crm.kelvinpeng.com

# 集成（暂留空）
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_WEBHOOK_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
IMAP_HOST=
IMAP_PORT=993
IMAP_USER=
IMAP_PASSWORD=
IMAP_POLL_INTERVAL_SECONDS=60
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=

# 前端
VITE_API_BASE_URL=https://crm.kelvinpeng.com/api
VITE_APP_NAME=CRM Pro
VITE_DEFAULT_LANGUAGE=zh
```

```bash
chmod 600 .env
```

> 重要：`.env` 永远不进 Git。仓库已 `.gitignore`，但请二次确认。

### 1.5 配置共享 nginx 加 CRM vhost

在 VPS：

```bash
sudo nano /infra/nginx/conf.d/crm.conf
```

内容：

```nginx
upstream crm_backend {
    server backend:8000 max_fails=2 fail_timeout=10s;
    keepalive 32;
}

server {
    listen 80;
    server_name crm.kelvinpeng.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name crm.kelvinpeng.com;

    ssl_certificate     /etc/nginx/certs/crm.kelvinpeng.com/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/crm.kelvinpeng.com/privkey.pem;

    client_max_body_size 20M;

    location / {
        proxy_pass http://frontend:80;
        proxy_set_header Host $host;
    }

    location /api/ {
        proxy_pass http://crm_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 60s;
    }
}
```

域名启用了 Cloudflare 代理（橙云），用 **Cloudflare Origin Certificate** —— 15 年有效、不用续期、保留 CDN/DDoS 防护：

#### 1.5.1 在 Cloudflare 控制台签发 Origin 证书

1. Cloudflare → 选 `kelvinpeng.com` → **SSL/TLS → Origin Server → Create Certificate**
2. 默认 RSA 2048，hostnames 填 `crm.kelvinpeng.com` 和 `*.kelvinpeng.com`，有效期 15 年 → Create
3. 复制 **Origin Certificate** 文本 → 待会保存为 `fullchain.pem`
4. 复制 **Private Key** 文本 → 保存为 `privkey.pem`（页面关掉就再也看不到，先保存到本地或剪贴板）

#### 1.5.2 上传证书到 VPS

```bash
sudo mkdir -p /srv/infra/nginx/certs/crm.kelvinpeng.com
sudo nano /srv/infra/nginx/certs/crm.kelvinpeng.com/fullchain.pem
# 粘贴 Origin Certificate 全文（含 -----BEGIN/END CERTIFICATE-----）

sudo nano /srv/infra/nginx/certs/crm.kelvinpeng.com/privkey.pem
# 粘贴 Private Key 全文（含 -----BEGIN/END PRIVATE KEY-----）

sudo chmod 600 /srv/infra/nginx/certs/crm.kelvinpeng.com/privkey.pem
sudo chmod 644 /srv/infra/nginx/certs/crm.kelvinpeng.com/fullchain.pem
```

容器内路径：`/etc/nginx/certs/crm.kelvinpeng.com/{fullchain.pem,privkey.pem}`（vhost 配置已对应）。

#### 1.5.3 测试 + 重载共享 nginx

```bash
docker exec infra_nginx nginx -t
docker exec infra_nginx nginx -s reload
```

#### 1.5.4 Cloudflare SSL 模式

控制台 → **SSL/TLS → Overview → SSL/TLS encryption mode → Full (strict)**。

> Origin Cert 只被 Cloudflare 信任。浏览器看到的是 Cloudflare 边缘证书，无需让浏览器信任 Origin Cert。15 年到期前在控制台再签一张新证书替换文件即可，没有 cron / webroot / standalone 任何续期烦恼。

### 1.6 GitHub 仓库准备

#### 1.6.1 启用 GHCR 镜像权限

GitHub → 仓库 → Settings → Actions → General → Workflow permissions：选 **Read and write permissions**。

#### 1.6.2 添加 Secrets

仓库 → Settings → Secrets and variables → Actions → New repository secret：

| Secret 名 | 值                                                                |
|-----------|------------------------------------------------------------------|
| `VPS_HOST` | `<VPS 地址>`                                                  |
| `VPS_USER` | `ubuntu`                                                         |
| `VPS_SSH_KEY` | 在 VPS 上 `ssh-keygen -t ed25519 -f ~/.ssh/gha_deploy` 生成的**私钥**全文 |
| `VPS_PORT` | SSH 端口（默认 22）                                                    |
| `VPS_FINGERPRINT` | VPS 主机公钥的 SHA256 指纹（形如 `SHA256:...`）；和 acuven-shop 同一台机器，**照抄 acuven-shop 仓库里已经在用的那个值**。没设时部署直接失败 |
| `VPS_APP_DIR` | `/opt/crm_os`                                                    |

另在 Variables 页加 repository variable `HEALTHCHECK_URL` = `https://crm.acuventech.com/api/health`（部署后据此确认线上版本）。

把 `~/.ssh/gha_deploy.pub` 内容追加到 VPS 的 `~/.ssh/authorized_keys`。

#### 1.6.3 GitHub Actions workflow

仓库已有 `.github/workflows/deploy.yml`，要点：

按提交 SHA 部署，满足 OpenClaw 部署观察契约 D1–D5（写法照搬 acuven-shop，细节见文件头注释）：

- **触发**：每次 push 到 `master` 都部署那个提交；例外是只改 `.platform/tasks.yaml` 与 `tasks/todo.md` 的提交（OpenClaw 收尾）不部署；Actions 页面手动 `workflow_dispatch` 时必须填完整 40 位 SHA，用于重部署或回滚
- **build job**：构建 `backend` / `frontend` 两个镜像，**只打 `<commit_sha>` 标签**（不再推 `latest`），推到 `ghcr.io/<owner>/acuven_crm_demo-backend|frontend`；构建时把 SHA 写进后端镜像
- **deploy job**：通过 `appleboy/ssh-action`（校验主机指纹）进 VPS：
  ```
  cd $VPS_APP_DIR
  git fetch && git checkout --detach <DEPLOY_SHA>
  docker login ghcr.io（使用 GITHUB_TOKEN）
  CRM_IMAGE_REPO=ghcr.io/<owner>/acuven_crm_demo bash deploy/deploy.sh <DEPLOY_SHA>
  ```
  `deploy/deploy.sh`：记下正在跑的版本 → 拉新镜像 → 先跑迁移 → 换容器 → 等 healthcheck → 不健康就回滚到原版本并以失败结束 → 清理本项目旧镜像
- **结论**：最后轮询 `HEALTHCHECK_URL`，响应里出现这次的 SHA 才算成功
- **并发控制**：同时只允许一个部署，且不取消正在跑的那个

> 如需改路径或加多环境（staging/prod），直接编辑 `.github/workflows/deploy.yml`。

---

## 2. 首次手动部署（验证链路）

```bash
ssh ubuntu@<VPS 地址>
cd /opt/crm_os

# 拉代码（首次）
git clone https://github.com/kelvinpang90/acuven_crm_demo.git .
# 此时 .env 已存在（步骤 1.4），不要被覆盖

# 登录 GHCR（首次拉镜像需要；后续 GHA 会自动登录）
echo <你的 GitHub PAT> | docker login ghcr.io -u kelvinpang90 --password-stdin

# 触发一次 GHA 构建：本地 push 一个空提交
# （在你本机仓库执行 git commit --allow-empty -m "trigger build" && git push）

# 等 GHA 构建完成后，VPS 上部署那个提交（<sha> 为完整 40 位）
git checkout --detach <sha>
CRM_IMAGE_REPO=ghcr.io/kelvinpang90/acuven_crm_demo bash deploy/deploy.sh <sha>

# 可选：写入种子数据
docker compose exec backend python seed.py
```

验证：

- 浏览器打开 `https://crm.kelvinpeng.com` → 看到登录页
- 用种子账号登录 → 进入仪表盘
- 检查 API：`curl https://crm.kelvinpeng.com/api/health`，返回里的 `git_sha` 应是刚部署的提交

---

## 3. 持续部署（日常使用）

### 3.1 标准发布流程

1. 本机改代码 → 提 PR → 合并到 `master`
2. GHA 自动按 1.6.3 部署这个提交；Actions 里那次运行绿了，就表示线上 `/api/health` 已报出这个 SHA
3. 运行失败时看日志：迁移失败则旧版本仍在跑；新版本不健康会自动回滚到部署前的版本（运行仍记为失败）
4. backend 容器重启时会有 ~5-10 秒 502 短暂中断（单实例代价）

> 服务器上的 `/opt/crm_os` 现在停在某个提交的 detached HEAD，**不要再在里面 `git pull`**。
> `docker-compose.yml` 的镜像变量没有默认值，在服务器上直接跑 `docker compose ...`（ps、logs、restart）会报
> `required variable CRM_BACKEND_IMAGE is missing`。先从正在跑的容器取出镜像名：
> ```bash
> export CRM_BACKEND_IMAGE=$(docker inspect -f '{{.Config.Image}}' crm_os-backend-1)
> export CRM_FRONTEND_IMAGE=$(docker inspect -f '{{.Config.Image}}' crm_os-frontend-1)
> ```

### 3.2 回滚

```bash
# 首选：GitHub → Actions → Deploy CRM → Run workflow，ref 填上一个正常提交的完整 40 位 SHA。
# 它和普通部署走同一条路（检出、迁移、换容器、健康检查）。
# 本机找 SHA：git log --format='%H %s' -10
```

GitHub Actions 不可用时，在 VPS 上手工做同样的事：

```bash
ssh ubuntu@<VPS 地址>
cd /opt/crm_os
git fetch && git checkout --detach <good-sha>
CRM_IMAGE_REPO=ghcr.io/kelvinpang90/acuven_crm_demo bash deploy/deploy.sh <good-sha>
```

> 数据库迁移回滚需手动 `alembic downgrade -1`，且仅适用于向后兼容的迁移。

### 3.3 数据库迁移规范（避免破坏性变更）

新建迁移要满足"两阶段兼容"：

- 加列、加索引：直接发布
- 改列名 / 删列：先发版本 A（同时保留新旧列、双写），再发版本 B（清理旧列）
- 永不在生产做 `DROP TABLE` 而不备份

### 3.4 日志查看

```bash
docker logs -f --tail=200 crm_os-backend-1
docker logs -f crm_os-frontend-1
```

---

## 4. 排错速查

| 现象 | 原因 | 处理 |
|------|------|------|
| `502 Bad Gateway` | 后端容器没起来 | `docker compose ps` 查状态，`docker compose logs backend` 看异常 |
| 前端能开但 API 401 | CORS 或 token 问题 | 确认 `.env` 中 `CORS_ORIGINS` 包含 `https://crm.kelvinpeng.com` |
| 数据库连不上 | mysql 容器名 / 网络不对 | `docker exec backend ping infra_mysql`；确认 backend 已接入 `data_net` |
| GHA `docker login` 失败 | Workflow permissions 没开 | Settings → Actions → Workflow permissions → Read & write |
| GHA SSH 失败 | key 格式错 / 公钥未授权 | secret 里粘**完整私钥**含 BEGIN/END 行；VPS 的 `authorized_keys` 加公钥 |
| Let's Encrypt 续期失败 | 80 端口被占 / 容器没 reload | `certbot renew --dry-run` 看错误；定时 cron 续期后 `nginx -s reload` |

---

## 5. 后续接入 WhatsApp / Email 时

只需在 VPS 的 `/opt/crm_os/.env` 填入对应字段，按 3.1 末尾先导出镜像变量，再 `docker compose up -d backend`（重建容器才会读到新的 `.env`），无需重新部署。WhatsApp Webhook 公网入口已通过 nginx `/api/webhooks/whatsapp` 暴露（HTTPS 已就绪）。

---

*文档结束。首次执行时按 1 → 2 顺序，之后日常发布只走 3.1。*
