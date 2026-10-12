#!/usr/bin/env bash
#
# 把一个提交的镜像部署到本机的生产栈。由 .github/workflows/deploy.yml 经 SSH 调用。
# 写法照搬 acuven-shop 的 deploy/deploy.sh，只改了服务名与镜像名。
#
# 用法（在服务器上本仓库检出的根目录里）：
#     CRM_IMAGE_REPO=ghcr.io/所有者/acuven_crm_demo deploy/deploy.sh 完整的40位commitSHA
#
# 顺序：记下正在跑的版本 → 拉新镜像 → 迁移 → 换容器 → 等健康 → 不健康则回滚。
# 成功才以 0 退出；迁移失败、换容器失败、不健康、回滚（无论回滚成败）都以非零退出（部署观察契约 D3）。
# 迁移只能是向后兼容的（加表、加列、加索引）：迁移跑完而新容器没起来时，旧版本要能继续对着新表结构工作。
# 服务名 backend / frontend 不能改：infra_nginx 按容器名 crm_os-backend-1 / crm_os-frontend-1 解析。

set -euo pipefail

SHA="${1:-}"
HEALTH_TIMEOUT_SECONDS="${CRM_HEALTH_TIMEOUT_SECONDS:-180}"

log() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# compose 文件里的全部服务都在、且都报 healthy 才算健康。
wait_for_health() {
    local deadline expected healthy status
    deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
    expected="$(docker compose config --services | wc -l)"
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if status="$(docker compose ps --format '{{.Service}} {{.Health}}')"; then
            healthy="$(printf '%s\n' "$status" | awk '$2 == "healthy"' | wc -l)"
            if [ "$healthy" -eq "$expected" ]; then
                return 0
            fi
        fi
        sleep 5
    done
    return 1
}

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "usage: deploy/deploy.sh <full 40-character lowercase commit SHA>"
[ -n "${CRM_IMAGE_REPO:-}" ] || die "CRM_IMAGE_REPO is not set"

export CRM_BACKEND_IMAGE="${CRM_IMAGE_REPO}-backend:${SHA}"
export CRM_FRONTEND_IMAGE="${CRM_IMAGE_REPO}-frontend:${SHA}"

# 回滚目标必须在任何改动之前记下：出事时正在跑的那一版就是回滚目标。
PREVIOUS_BACKEND="$(docker compose ps --format '{{.Image}}' backend)"
PREVIOUS_FRONTEND="$(docker compose ps --format '{{.Image}}' frontend)"
log "currently running: ${PREVIOUS_BACKEND:-nothing} / ${PREVIOUS_FRONTEND:-nothing}"

log "pulling $CRM_BACKEND_IMAGE and $CRM_FRONTEND_IMAGE"
docker compose pull --quiet || die "pull failed; nothing has been changed"

log "running migrations"
docker compose run --rm --no-deps backend alembic upgrade head \
    || die "migration failed; the previous version is still running"

log "starting $SHA"
HEALTHY=0
if docker compose up -d --no-build --remove-orphans && wait_for_health; then
    HEALTHY=1
fi

if [ "$HEALTHY" = "1" ]; then
    log "deployed $SHA"
    # 每次部署都拉进一对新 SHA 标签的镜像，不清理会慢慢吃满共享服务器的磁盘。
    # 只清本项目的（构建时打了这个标签）、且没有任何容器在用的；正在跑的这一版不会被动到。
    # 清理失败不改变部署结论，只记一条警告。
    if ! docker image prune --all --force --filter "label=acuven.project=crm_os" >/dev/null; then
        log "WARNING: could not prune old images"
    fi
    exit 0
fi

log "deploy of $SHA is not healthy"
docker compose ps

if [ -z "$PREVIOUS_BACKEND" ] || [ -z "$PREVIOUS_FRONTEND" ]; then
    # 部署前没有在跑的版本，没有回滚目标：保留现场给人排查，不把栈停掉。
    die "nothing to roll back to; leaving the stack up for inspection"
fi

log "rolling back to $PREVIOUS_BACKEND / $PREVIOUS_FRONTEND"
export CRM_BACKEND_IMAGE="$PREVIOUS_BACKEND"
export CRM_FRONTEND_IMAGE="$PREVIOUS_FRONTEND"
docker compose up -d --no-build || die "rollback failed; manual intervention required"
if wait_for_health; then
    # 回滚成功也以非零退出：这个提交没能上线，部署结论必须是失败。
    die "rolled back to $PREVIOUS_BACKEND; the deploy of $SHA failed"
fi
die "rollback did not become healthy; manual intervention required"
