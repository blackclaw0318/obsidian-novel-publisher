#!/usr/bin/env bash
# ============================================================
# dry-run.sh - 老板手动 E2E (真 LLM + 真 obsidian dev + 真 GitHub test 分支)
# ============================================================
# 用法:
#   bash scripts/dry-run.sh                    # 默认全跑 (1 章)
#   bash scripts/dry-run.sh --chapter 3        # 指定 chapter idx
#   bash scripts/dry-run.sh --no-github        # 跳过 GitHub 备份
#   bash scripts/dry-run.sh --no-obsidian      # 只跑 LLM + 封面, 不真推博客
#
# 前置:
#   1. .env 已填全 (MINIMAXI_API_KEY / OBSIDIAN_PUBLISH_* / GITHUB_BACKUP_TOKEN)
#   2. obsidian-journal dev 服务在 http://localhost:3000 跑 (npm run dev 或 prod)
#   3. PUBLISH_STATE_PATH 指 tmp 路径, 不污染正式 state
#
# 老板使用场景:
#   - 部署前最后一次冒烟 (改了 prompt / 改了 publisher.py 后必跑)
#   - 新章节 idx 验证 (idx=1 时真跑一篇, 后续 systemd 自动)
#
# 输出:
#   - 真实 LLM 生成章节 (~30s)
#   - 真实 image-01 生成封面 (~80s)
#   - 真实 multipart 上传封面到 obsidian dev
#   - 真实 HMAC POST 到 localhost:3000/api/external/posts
#   - 真实 GitHub PUT 到 obsidian-novel-backups (test 分支)
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

# ============ 参数 ============
CHAPTER_IDX="${CHAPTER_IDX:-1}"
SKIP_GITHUB=false
SKIP_OBSIDIAN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --chapter)
      CHAPTER_IDX="$2"
      shift 2
      ;;
    --no-github)
      SKIP_GITHUB=true
      shift
      ;;
    --no-obsidian)
      SKIP_OBSIDIAN=true
      shift
      ;;
    -h|--help)
      echo "用法: bash scripts/dry-run.sh [--chapter N] [--no-github] [--no-obsidian]"
      exit 0
      ;;
    *)
      err "未知参数: $1"
      exit 1
      ;;
  esac
done

# ============ 前置检查 ============
section "前置检查"

if [[ ! -f ".env" ]]; then
  err ".env 缺失: 从 .env.example 复制并填值"
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  err ".venv/bin/python 缺失: uv sync 或 python -m venv .venv && uv pip install -e ."
  exit 1
fi

# .env 关键字段非占位符检查
if grep -q "^MINIMAXI_API_KEY=sk-cp-…msWk" .env 2>/dev/null; then
  err "MINIMAXI_API_KEY 还是占位符 (sk-cp-…msWk), 请填真 key"
  exit 1
fi

log "✅ 前置通过 (chapter=$CHAPTER_IDX, no-github=$SKIP_GITHUB, no-obsidian=$SKIP_OBSIDIAN)"

# ============ 隔离环境 ============
section "隔离环境 (不污染正式 state)"

export PUBLISH_STATE_PATH="/tmp/publisher-dry-run-state.json"
export COVER_TMP_DIR="/tmp/publisher-dry-run-covers"
mkdir -p "$COVER_TMP_DIR"

# 如果不要真推博客 → 用 dummy URL (会失败, 但能验证 LLM/封面)
if [[ "$SKIP_OBSIDIAN" == "true" ]]; then
  warn "SKIP_OBSIDIAN: 博客推送会失败, 仅验证 LLM + 封面"
  export OBSIDIAN_PUBLISH_URL="http://localhost:9999/skip"
fi

# 如果不要 GitHub 备份 → 清空 token
if [[ "$SKIP_GITHUB" == "true" ]]; then
  warn "SKIP_GITHUB: 备份被跳过"
  export GITHUB_BACKUP_TOKEN=""
fi

# 干掉旧 state
rm -f "$PUBLISH_STATE_PATH"
log "✅ 隔离 OK: state=$PUBLISH_STATE_PATH, covers=$COVER_TMP_DIR"

# ============ 跑 publisher ============
section "跑 publisher (真链路)"

log "开始跑 publisher (LLM ~30s + 封面 ~80s + 推送 ~5s)..."
START_TIME=$(date +%s)

if .venv/bin/python -m src.publisher --state "$PUBLISH_STATE_PATH"; then
  END_TIME=$(date +%s)
  ELAPSED=$((END_TIME - START_TIME))
  log "✅ publisher 跑成功 (耗时 ${ELAPSED}s)"
else
  EXIT_CODE=$?
  END_TIME=$(date +%s)
  ELAPSED=$((END_TIME - START_TIME))
  err "publisher 失败 (exit $EXIT_CODE, 耗时 ${ELAPSED}s)"
  echo ""
  echo "==== 调试提示 ===="
  echo "1. 看 state: cat $PUBLISH_STATE_PATH | jq ."
  echo "2. 重跑 verbose: .venv/bin/python -m src.publisher --state $PUBLISH_STATE_PATH -v"
  echo "3. 单步调试: 编辑 src/publisher.py 加 print / pdb.set_trace()"
  exit "$EXIT_CODE"
fi

# ============ 验证 ============
section "验证结果"

if [[ -f "$PUBLISH_STATE_PATH" ]]; then
  echo ""
  echo "==== state.json ===="
  cat "$PUBLISH_STATE_PATH" | .venv/bin/python -m json.tool 2>/dev/null || cat "$PUBLISH_STATE_PATH"
fi

if [[ -d "$COVER_TMP_DIR" ]]; then
  echo ""
  echo "==== 封面文件 ===="
  ls -la "$COVER_TMP_DIR"/*.jpg 2>/dev/null || echo "(无封面)"
fi

echo ""
log "🎉 dry-run 收口"
echo ""
echo "==== 老板下一步 ===="
echo "✅ 验证 obsidian dev: 打开 http://localhost:3000/posts 看新章节"
echo "✅ 验证 obsidian-journal log: tail -f logs/scheduler.log"
echo "✅ 验证 GitHub 备份:  打开 https://github.com/blackclaw0318/obsidian-novel-backups/commits/main"
echo ""
echo "⚠️  /tmp/publisher-dry-run-state.json 是隔离的, 不影响 systemd 的正式 state (./data/state.json)"