#!/usr/bin/env bash
# ============================================================
# uninstall-systemd.sh - 卸载 novel-publish systemd timer
# ============================================================
# 动作:
#   1. stop + disable timer
#   2. 删除 /etc/systemd/system/novel-publish.{service,timer}
#   3. daemon-reload
#   4. 不会删日志 / .env / 业务数据 (可手动清 logs/)
#
# 必须 sudo
# 用法: sudo bash scripts/uninstall-systemd.sh
# ============================================================

set -euo pipefail

SYSTEMD_DST="/etc/systemd/system"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
err() { echo -e "${RED}[ERR]${NC} $*" >&2; }

if [[ $EUID -ne 0 ]]; then
  err "必须 sudo 运行: sudo bash $0"
  exit 1
fi

log "==== stop + disable timer ===="
if systemctl is-active --quiet novel-publish.timer 2>/dev/null; then
  systemctl stop novel-publish.timer
fi
if systemctl is-enabled --quiet novel-publish.timer 2>/dev/null; then
  systemctl disable novel-publish.timer
fi

log "==== 删除 unit 文件 ===="
rm -fv "${SYSTEMD_DST}/novel-publish.service"
rm -fv "${SYSTEMD_DST}/novel-publish.timer"

log "==== daemon-reload ===="
systemctl daemon-reload
systemctl reset-failed novel-publish.service 2>/dev/null || true

log "✅ 卸载完成"
log "   残留日志 (不会自动删): logs/publisher.log (老板如要清, 看 RUNBOOK.md)"
log "   业务数据保留: truth/ state.json .env"