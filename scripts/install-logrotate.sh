#!/usr/bin/env bash
# ============================================================
# install-logrotate.sh - 安装 logrotate 配置
# ============================================================
# 动作:
#   1. 复制 scripts/logrotate-novel-publisher → /etc/logrotate.d/obsidian-novel-publisher
#   2. logrotate -d 干跑验证语法 (不真轮转)
#   3. (可选) --force 真轮转一次, 让老板立刻看到效果
#
# 必须 sudo
# 用法:
#   sudo bash scripts/install-logrotate.sh         # 安装 + 干跑验证
#   sudo bash scripts/install-logrotate.sh --force # 安装 + 立刻真轮转
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC="${SCRIPT_DIR}/logrotate-novel-publisher"
DST="/etc/logrotate.d/obsidian-novel-publisher"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
err() { echo -e "${RED}[ERR]${NC} $*" >&2; }

if [[ $EUID -ne 0 ]]; then
  err "必须 sudo 运行: sudo bash $0"
  exit 1
fi

if [[ ! -f "${SRC}" ]]; then
  err "logrotate 配置缺失: ${SRC}"
  exit 1
fi

log "==== 复制 logrotate 配置 ===="
cp -v "${SRC}" "${DST}"
chmod 644 "${DST}"

log "==== logrotate -d 干跑 (语法验证) ===="
if logrotate -d "${DST}" 2>&1; then
  log "✅ 语法 OK"
else
  err "logrotate 配置语法错 (见上方)"
  exit 1
fi

if [[ "${1:-}" == "--force" ]]; then
  log "==== --force 触发真轮转 ===="
  if [[ -f "${PROJECT_DIR}/logs/publisher.log" ]]; then
    logrotate -f "${DST}"
    log "✅ 真轮转完成, 查看新归档: ls -la ${PROJECT_DIR}/logs/"
  else
    warn "logs/publisher.log 不存在, 跳过真轮转 (publisher 没跑过)"
  fi
fi

log "✅ 安装完成"
log "   配置路径: ${DST}"
log "   干跑:     sudo logrotate -d ${DST}"
log "   真轮转:   sudo logrotate -f ${DST}"