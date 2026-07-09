#!/usr/bin/env bash
# ============================================================
# run-5-chapters.sh - 跑完《碳与道》全 5 章
# v0.3.2 P0-P6 真链路 + image-to-image (ch-2+) + 「」 排版 + 版权字段
#
# 用法:
#   bash scripts/run-5-chapters.sh           # 跑 5 章 (从 next_idx 开始)
#   bash scripts/run-5-chapters.sh --start 1 # 从 idx=1 开始 (重置 state)
#
# 前置:
#   - novels.yaml 里 carbon_tao_obsidian.enabled=true
#   - backups 仓已有 novels/carbon_tao_obsidian/{outline,style_guide,characters}.md
#   - .env 完整
#
# 行为:
#   - 备份 data/state/ 到 /tmp (跑完还原)
#   - 5 次跑 publisher --all --force (跳过 slot 配额)
#   - 每次输出 state + cover
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
err() { echo -e "${RED}[ERR]${NC} $*" >&2; }
section() { echo -e "\n${BLUE}==== $* ====${NC}"; }

# 解析参数
START_IDX=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --start)
      START_IDX="$2"
      shift 2
      ;;
    -h|--help)
      echo "用法: bash scripts/run-5-chapters.sh [--start N]"
      exit 0
      ;;
    *)
      err "未知参数: $1"
      exit 1
      ;;
  esac
done

# 前置检查
section "前置检查"
if [[ ! -f ".env" ]]; then
  err ".env 缺失"
  exit 1
fi
if [[ ! -x ".venv/bin/python" ]]; then
  err ".venv 缺失"
  exit 1
fi
if grep -q "^MINIMAXI_API_KEY=sk-cp-…msWk" .env 2>/dev/null; then
  err "MINIMAXI_API_KEY 还是占位符"
  exit 1
fi
log "✅ 前置 OK (start_idx=$START_IDX)"

# 备份 data/state (不污染正式)
STATE_BAK="/tmp/publisher-carbon-tao-state-bak-$(date +%Y%m%d%H%M%S)"
if [[ -d "data/state" ]]; then
  cp -r data/state "$STATE_BAK"
  log "✅ data/state 备份到 $STATE_BAK"
fi

# 删除新小说 state (从 START_IDX 重跑)
if [[ -f "data/state/carbon_tao_obsidian.json" ]]; then
  rm -f "data/state/carbon_tao_obsidian.json"
  log "✅ 删 carbon_tao_obsidian state (从 $START_IDX 重跑)"
fi

# 预创建 state (next_idx=START_IDX, 标记 START_IDX-1 已推 + cover_url) 让 START_IDX 章真推
if [[ $START_IDX -gt 1 ]]; then
  PREV_IDX=$((START_IDX - 1))
  # 7-9 fix: 接受 ch1 状态(obsidian 端已存在)创建初始 state, 让 publisher 跳过 ch1
  .venv/bin/python -c "
import json, hashlib, sys
from src.state_per_novel import save_state_for_novel, DEFAULT_STATE_DIR
from src.state import PublishState

state = PublishState()
state.next_idx = $START_IDX  # 从 START_IDX 开始
state.last_status = 'success'
state.last_pushed_idx = $PREV_IDX
# 预填 cover_urls: 让 image-to-image 拿前章 raw URL
state.cover_urls = {}
# 给 prev_idx 填一个 placeholder URL (publisher 会自动转 raw URL)
state.cover_urls[str($PREV_IDX)] = 'https://www.shangkun.uk/uploads/PLACEHOLDER.jpg'
# 关键: last_pushed_slot 设为空, 避免 quota check skip 当前章
state.last_pushed_slot = ''
save_state_for_novel(state, 'carbon_tao_obsidian')
print(f'✅ 预创建 state: next_idx={state.next_idx} cover_urls={state.cover_urls}')
"
  log "✅ 预创建 state (next_idx=$START_IDX, 跳过 ch$PREV_IDX)"
fi

# 隔离封面目录
export COVER_TMP_DIR="/tmp/publisher-carbon-tao-covers-$(date +%Y%m%d%H%M%S)"
mkdir -p "$COVER_TMP_DIR"
log "✅ 封面隔离目录: $COVER_TMP_DIR"

# 跑 5 章
for i in $(seq $START_IDX 5); do
  section "📖 第 $i 章 / 5"
  log "启动 publisher --all --force (idx=$i 自动推进)"

  START_TIME=$(date +%s)
  if .venv/bin/python -m src.publisher --all --force -v 2>&1 | tee "/tmp/publisher-ch$i.log"; then
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    log "✅ 第 $i 章推送成功 (耗时 ${ELAPSED}s)"
  else
    EXIT_CODE=$?
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    err "❌ 第 $i 章推送失败 (exit $EXIT_CODE, 耗时 ${ELAPSED}s)"
    echo "  → 看日志: tail -50 /tmp/publisher-ch$i.log"
    # 还原 state 后退出
    if [[ -d "$STATE_BAK" ]]; then
      rm -rf data/state
      mv "$STATE_BAK" data/state
      log "✅ data/state 已还原"
    fi
    exit 1
  fi

  sleep 5  # 避免 LLM 限流
done

# 还原 state (保留新小说的 state)
section "还原 state"
if [[ -d "$STATE_BAK" ]]; then
  # 把旧 state 备份合并回 data/state
  for f in "$STATE_BAK"/*.json; do
    fname=$(basename "$f")
    if [[ ! -f "data/state/$fname" ]]; then
      cp "$f" "data/state/$fname"
    fi
  done
  log "✅ 老 state 已合并 (新小说 state 保留)"
  rm -rf "$STATE_BAK"
fi

# 最终汇报
section "🎉 5 章推送完成"

if [[ -f "data/state/carbon_tao_obsidian.json" ]]; then
  log "carbon_tao_obsidian state:"
  cat data/state/carbon_tao_obsidian.json | .venv/bin/python -m json.tool 2>/dev/null | head -30
fi

echo ""
echo "==== 验收清单 ===="
echo "1. 打开 https://www.shangkun.uk/novels/carbon-tao (5 章应在)"
echo "2. 检查 https://github.com/blackclaw0318/obsidian-novel-backups/commits/main (应有 5 个 chapter commits)"
echo "3. 检查每章版权字段 (license / aigc_disclosure)"
echo "4. 检查 ch2+ 封面是否与 ch1 主角脸一致 (image-to-image)"
echo "5. 检查每章末引号 「」 排版 (无 」 单独占行)"
echo ""

exit 0