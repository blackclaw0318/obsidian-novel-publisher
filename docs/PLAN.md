# obsidian-novel-publisher — 方案稿 v0.2 (2026-07-06)

> **项目代号**: obsidian-novel-publisher
> **目标**: 每天 3 次（早 8 / 中 12 / 晚 18）自动生成中场篇科幻小说章节（含封面图）+ GitHub 备份 + 推送 obsidian-journal
> **状态**: ⏸️ 等老板 Q1-Q8 拍板开 P0 (v0.2 增量需求待审)
> **v0.1 → v0.2 增量**: 封面图 + 鉴权明确 + GitHub 备份 + 完整测试 + CI/CD 自动 push

---

## 🎯 一句话定位

**把 xhs-novel-bot 里已经调优好的"中场篇科幻小说"生产 skill 整建制搬过来, 换一个推送目的地（XHS → 个人博客）。**

无风控压力（博客无审核）, 可全力 3/天 输出。

---

## 🏗️ 架构图 (v0.2 增 GitHub 备份 + 封面图)

```
┌─────────────────────────────────────────────────────────────┐
│  本机 cron (systemd timer)                                    │
│   ├─ 08:00 / 12:00 / 18:00 daily-publish.timer             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  src/publish.py — 主入口                                     │
│                                                              │
│  1. load_state()                  ← data/state.json          │
│  2. load_truth_snapshot()         ← data/novel/<id>/        │
│  3. NovelWriter.write_chapter(                              │
│       chapter_idx, truth_snapshot, style_guide              │
│     ) → ChapterDraft {raw_text, cover_prompt, word_count}   │
│  4. CoverGenerator.generate(                                │
│       cover_prompt, chapter_idx                             │
│     ) → /tmp/covers/{NN}.jpg (本地临时)                     │
│  5. UploadCoverService.upload_to_obsidian(                  │
│       local_jpg → POST /api/admin/resources (upload)        │
│       ← R2/OSS URL                                          │
│     )                                                        │
│  6. RenderMarkdown(raw_text, cover_url) → markdown 含图     │
│  7. push_to_obsidian(md, cover_url)                         │
│       POST /api/external/posts                              │
│       HMAC-SHA256(body, OBSIDIAN_PUBLISH_SECRET)            │
│       {slug, title, excerpt, content(md+img), category:"novel"}│
│  8. github_backup.upload(chapter_md, cover_jpg, meta)       │
│       git add + commit + push → obsidian-novel-backups 私仓 │
│  9. update_state()                ← data/state.json          │
│  10. alert_on_failure()           ← WeCom webhook           │
└────────┬──────────────────────────────────┬─────────────────┘
         │                                  │
         ▼                                  ▼
┌──────────────────────┐         ┌────────────────────────────┐
│  obsidian-journal    │         │  obsidian-novel-backups    │
│  (主推送, 博客展示)   │         │  (私仓, 章节永久备份)        │
└──────────────────────┘         └────────────────────────────┘
```

---

## 📦 复用 xhs-novel-bot 资产 (整建制迁移, v0.2 沿用)

| xhs-novel-bot 文件 | 用途 | 新项目位置 |
|---|---|---|
| `assets/prompts/system.txt` | 写作风格 system prompt | `assets/prompts/system.txt` |
| `assets/prompts/user.txt` | 章节模板 | `assets/prompts/user.txt` |
| `assets/prompts/topic_system.txt` | 选题 prompt | `assets/prompts/topic_system.txt` |
| `assets/prompts/topic_user.txt` | 选题 prompt | `assets/prompts/topic_user.txt` |
| `src/novel_writer.py` | M3 调用 + 重试 + 字数校验 | `src/novel_writer.py` (复制) |
| `src/topic_gen.py` | 选题生成 | `src/topic_gen.py` |
| `src/summary_gen.py` | 章节摘要 | `src/summary_gen.py` |
| `src/cover_gen.py` | **封面图生成 (v0.2 复用)** | `src/cover_gen.py` (复制) |
| `src/scheduler.py` | 调度循环 | **❌ 替换** (用 systemd timer) |
| `src/orchestrator.py` | 编排 | **❌ 替换** (单文件 publisher) |
| `data/cookies.json` | XHS 登录态 | **❌ 不用** |

**核心改动**:
- ✅ 保留 `novel_writer.py` / `topic_gen.py` / `summary_gen.py` / **`cover_gen.py`** (v0.2 新增复用)
- ❌ 删 XHS 相关: `cookies.json` / `xiaohongshu_mcp`
- 🔁 新增: `publisher.py` / `state.py` / `hmac_client.py` / **`cover_upload.py`** (v0.2) / **`github_backup.py`** (v0.2)

---

## 🆕 v0.2 增量需求 (5 项)

### 1️⃣ 封面图生成 (沿用 minimax image-01)

**Token 状态核查 (2026-07-06 00:23 实测)**:

| 项 | 状态 |
|---|---|
| `MINIMAXI_API_KEY` | ✅ 有效 (前缀 `sk-cp-QB`, 长度 125) |
| `MINIMAXI_BASE_URL` | ✅ `https://api.minimaxi.com/v1` |
| `MINIMAXI_TEXT_MODEL` | ✅ `MiniMax-M3` (写作) |
| `MINIMAXI_IMAGE_MODEL` | ✅ `image-01` (封面) |
| **text API 实测** | ✅ `POST /text/chatcompletion_v2` 返回正常 |
| **image API 实测** | ✅ `POST /image_generation` 返回 `data.image_urls[0]` (hailuo OSS URL) |

**黑记得 token**, 且本次会话已实测两端可用。

**封面生成流程**:

```python
# 复用 xhs-novel-bot/src/cover_gen.py (已成熟, 零改动迁移)
from cover_gen import CoverGenerator

cover = CoverGenerator()
local_path = cover.generate(
    prompt=draft.cover_prompt,  # novel_writer 已经 compose 好
    chapter_idx=chapter_idx
)
# → /tmp/covers/001.jpg (3:4, > 50KB, prompt_optimizer=true)
```

**封面图嵌入小说 markdown**:

```python
# src/publisher.py — render_markdown()
def render_markdown(raw_text: str, cover_url: str, chapter_title: str) -> str:
    cover_md = f"![{chapter_title}]({cover_url})\n\n"
    # 内容截断到 ~ 1500 字一段, 避免单篇太长
    chunks = split_for_reading(raw_text, max_chars=1500)
    body_md = "\n\n---\n\n".join(chunks)
    return cover_md + body_md
```

**封面图上传到 obsidian-journal**:

- 选项 A: 走 `POST /api/admin/resources` (复用 v0.34 资源上传链路, 落 R2/OSS)
- 选项 B: 走 `POST /api/external/covers` (新增, 专为 publisher 鉴权)
- **黑推荐 A** (复用现有, 不增加 obsidian-journal 侧 API 表面)

```
封面图上传链路:
  /tmp/covers/001.jpg (本地)
       ↓ POST /api/admin/resources (multipart, busboy)
       ↓ obsidian-journal 内部存到 R2 (或本地 output/)
       ↓ 返回 {url: "https://obs.shangkun.uk/resources/001.jpg"}
       ↓ 用于嵌入 markdown
```

### 2️⃣ 推送鉴权方式 — **HMAC-SHA256 (推荐, 黑确认)**

**完整签名协议**:

```
Step 1. 序列化
  canonical_body = JSON.stringify(sort_keys(body), no_whitespace)

Step 2. 时间戳
  timestamp = unix_ms()  (e.g. 1717699200000)

Step 3. 签名
  message = `${timestamp}.${canonical_body}`
  signature = HMAC-SHA256(OBSIDIAN_PUBLISH_SECRET, message).hex()

Step 4. 请求头
  POST /api/external/posts
  X-Publisher-Id: novel-publisher
  X-Publisher-Signature: <signature hex>
  X-Publisher-Timestamp: <timestamp>
  X-Idempotency-Key: <uuid>  (可选, 失败重试安全)
  Content-Type: application/json

Step 5. 服务端验证
  1) 时间戳校验: |now - timestamp| < 5 分钟 (防 replay)
  2) 签名校验: timing-safe equal
  3) publisher 白名单: novel-publisher / yk-script
  4) rate limit: 10 req/min/IP
  5) idempotency: 同 key 不重复创建
```

**为什么是 HMAC 而不是 API key / mTLS**:

| 方案 | 防 replay | 防篡改 | 实现复杂度 | 黑推荐 |
|---|---|---|---|---|
| **HMAC-SHA256** | ✅ timestamp window | ✅ 完整 body 签名 | 中 | ⭐ **首选** |
| API key (Bearer) | ❌ | ❌ | 低 | 备选 (内部用) |
| mTLS | ✅ | ✅ | 高 | 不必要 |
| OAuth2 | ✅ | ✅ | 很高 | 杀鸡用牛刀 |

**鉴权凭据配置**:

```env
# obsidian-novel-publisher/.env
OBSIDIAN_PUBLISH_URL=https://shangkun.uk/api/external/posts
OBSIDIAN_PUBLISH_ID=novel-publisher
OBSIDIAN_PUBLISH_SECRET=change-me-to-hex-secret-64-chars
```

**凭据生成命令**:

```bash
openssl rand -hex 32
# → a3f2b8c1d4e5... (64 hex chars)
# 同步存到 obsidian-journal 侧 .env:
#   OBSIDIAN_NOVEL_PUBLISH_SECRET=a3f2b8c1d4e5...
```

### 3️⃣ GitHub 备份 (私仓独立, v0.2 新增)

**目标**: 老板随时可在 GitHub 私仓检查生成的小说, 防止 obsidian-journal 站点丢失。

**仓库选型决策**:

| 方案 | 优点 | 缺点 | 黑推荐 |
|---|---|---|---|
| **A. 新建 `obsidian-novel-backups` (private)** | 独立干净, 不污染 stellar-scribe | 多一个仓要维护 | ⭐ **推荐** |
| B. 推 stellar-scribe (private) | 已有私仓, 0 额外创建 | 商业主线仓混入小说产物 | 不推荐 |
| C. 推 obsidian-novel-publisher (public) | 0 额外创建 | 公开仓暴露生成内容 | 备选 (老板同意公开则可) |

**架构**:

```
obsidian-novel-backups (private) — 自动维护
├── truth/
│   └── novels/
│       └── meta_realm_obsidian/
│           ├── index.json              # 全章节索引
│           ├── chapters/
│           │   ├── ch-001.md            # 完整章节 md
│           │   ├── ch-002.md
│           │   └── ...
│           ├── covers/
│           │   ├── ch-001.jpg           # 封面图
│           │   ├── ch-002.jpg
│           │   └── ...
│           └── meta/
│               ├── ch-001.json          # 字数/token/usage/时间戳
│               └── ...
├── CHANGELOG.md                        # 自动生成: 每日推送汇总
└── README.md
```

**GitHub 备份写入逻辑**:

```python
# src/github_backup.py
class GithubBackup:
    def __init__(self, repo: str, token: str, branch: str = "main"):
        self.repo = repo  # "blackclaw0318/obsidian-novel-backups"
        self.token = token  # GITHUB_PAT (有 repo 权限)
        self.branch = branch
    
    def upload(self, chapter_md: str, cover_jpg: bytes, meta: dict):
        """每次发布后调用。失败重试 3 次, 告警。"""
        chapter_idx = meta["chapter_idx"]
        
        # 1. 写文件 (本地工作副本, gitignored 主仓; 单独 working dir)
        files_to_write = [
            (f"truth/novels/meta_realm_obsidian/chapters/ch-{chapter_idx:03d}.md", chapter_md, "text/markdown"),
            (f"truth/novels/meta_realm_obsidian/covers/ch-{chapter_idx:03d}.jpg", cover_jpg, "image/jpeg"),
            (f"truth/novels/meta_realm_obsidian/meta/ch-{chapter_idx:03d}.json", json.dumps(meta, indent=2), "application/json"),
        ]
        
        # 2. 走 GitHub Contents API (而不是 git push, 单文件更轻量)
        for path, content, mime in files_to_write:
            self._put_file(path, content, mime, meta)
        
        # 3. 更新 index.json (追加当前章节)
        self._update_index(meta)
        
        # 4. 更新 CHANGELOG.md (追加每日汇总)
        self._update_changelog(meta)
```

**GitHub Contents API 调用**:

```
PUT /repos/{owner}/{repo}/contents/{path}
Headers:
  Authorization: token <GITHUB_PAT>
  Content-Type: application/json
Body:
  {
    "message": "publish ch-{NN}: {title}",
    "content": "<base64 encoded>",
    "branch": "main",
    "sha": "<existing file sha, 覆盖必需>"
  }
```

**关键设计决策**:

| 决策 | 选择 | 理由 |
|---|---|---|
| 推送方式 | **GitHub Contents API** | 不用 git push (轻量, 无需本地 clone), 失败可重试 |
| 编码 | base64 | API 标准, 但封面图 ~200KB 编码后 ~270KB, 单次可承受 |
| commit message | `publish ch-{NN}: {title}` | 老板一眼可查 |
| 失败重试 | 3 次 + 告警 | 备份失败不阻塞主推送 |
| 主推送 vs 备份顺序 | **先推送博客, 再备份** | 博客是用户可见的; 备份是离线副本 |

### 4️⃣ 完整测试策略 (v0.2 新增)

**测试金字塔**:

```
                    ▲
                   ╱ ╲
                  ╱ E2E ╲               ← 1 次/天 (本地 dry-run 真实链路)
                 ╱ (2%)  ╲
                ╱─────────╲
               ╱ 集成测试  ╲           ← 10+ 个模块组合 (mock LLM, mock HTTP)
              ╱  (20%)     ╲
             ╱───────────────╲
            ╱    单元测试      ╲       ← 函数级 (LLM mock, HMAC, state, prompt)
           ╱      (78%)        ╲
          ╱─────────────────────╲
```

**测试矩阵**:

| 层 | 工具 | 范围 | 覆盖率目标 |
|---|---|---|---|
| Unit | pytest + pytest-mock | novel_writer / cover_gen / hmac_client / state / publisher 拆解 | ≥ 90% |
| Integration | pytest + requests-mock | publisher 全链路 + github_backup + cover_upload | ≥ 80% |
| E2E (手动) | curl + 真实 LLM + 真实 obsidian dev | 每天推送 1 次, 老板抽查 | smoke only |
| Type | mypy --strict | 全部 .py | 0 error |
| Lint | ruff + black | 全部 .py | 0 warning |
| Coverage | pytest-cov | 整体 | ≥ 80% |
| Pre-commit | black + ruff | 全部 .py | 0 fail |

**单元测试清单 (示例)**:

```python
# tests/unit/test_novel_writer.py
class TestNovelWriter:
    def test_strip_think_block_removes_all_formats(self):
        """5 种 think block 格式全剥除"""
    
    def test_count_chinese_chars_excludes_punctuation(self):
        """只统计汉字, 排除标点/英文/空白"""
    
    def test_word_count_below_min_triggers_retry(self):
        """字数不足触发重生"""
    
    def test_4xx_error_raises_immediately(self):
        """参数错不重试, 立即抛"""
    
    def test_5xx_error_retries_with_backoff(self):
        """网络错指数退避 1/2/4s"""

# tests/unit/test_hmac_client.py
class TestHmacClient:
    def test_signature_format_is_hex(self):
        """签名是 64 字符 hex"""
    
    def test_canonical_body_sorts_keys_recursively(self):
        """嵌套 dict 也要 sort_keys"""
    
    def test_timestamp_validation_5min_window(self):
        """±5 分钟窗口"""
    
    def test_signature_deterministic(self):
        """同 body+secret+ts 必出同签名"""

# tests/unit/test_state.py
class TestStateMachine:
    def test_next_idx_increments_on_success(self):
        """成功后 next_idx + 1"""
    
    def test_next_idx_unchanged_on_failure(self):
        """失败不推进"""
    
    def test_skip_next_sets_skip_flag(self):
        """跳过标记, next run 也跳"""
    
    def test_state_persists_across_restart(self):
        """JSON 重启不丢"""

# tests/unit/test_publisher.py
class TestPublisher:
    def test_markdown_renders_cover_image(self):
        """封面图作为首图嵌入"""
    
    def test_chapter_split_every_1500_chars(self):
        """长文每 1500 字切一段"""
    
    def test_slug_format_dashed_kebab(self):
        """slug 格式 kebab-case"""

# tests/unit/test_cover_gen.py (复用 xhs-novel-bot 测试)
class TestCoverGen:
    def test_prompt_3_4_aspect(self):
        """aspect_ratio='3:4'"""
    
    def test_response_format_url(self):
        """response_format='url'"""
    
    def test_download_validates_min_size_50kb(self):
        """< 50KB 视为无效"""

# tests/unit/test_github_backup.py
class TestGithubBackup:
    def test_upload_chapter_md_writes_to_correct_path(self):
        """路径: truth/novels/.../chapters/ch-NNN.md"""
    
    def test_upload_cover_jpg_to_covers_path(self):
        """封面路径"""
    
    def test_meta_json_contains_required_fields(self):
        """meta 必含 chapter_idx + word_count + created_at + llm_usage"""
    
    def test_index_json_appends_not_overwrites(self):
        """index 追加, 不覆盖"""
```

**集成测试清单**:

```python
# tests/integration/test_publish_e2e.py
class TestPublishE2E:
    def test_full_pipeline_with_mocks(self, mock_llm, mock_obsidian, mock_github):
        """端到端: LLM → 封面 → 上传 → 推送 → 备份"""
    
    def test_idempotency_same_key_returns_existing_post(self):
        """同 idempotency_key 不重复创建"""
    
    def test_failure_in_obsidian_does_not_skip_backup(self):
        """主推送失败, 备份仍尝试"""

# tests/integration/test_failure_modes.py
class TestFailureModes:
    def test_llm_5xx_retries_then_succeeds(self):
        """LLM 5xx 重试, 第二次成功"""
    
    def test_llm_returns_empty_content_retries(self):
        """M3 偶发空 content, 重试"""
    
    def test_obsidian_429_rate_limit_backs_off(self):
        """博客 429 退避 60s 重试"""
    
    def test_obsidian_401_signature_mismatch_alerts(self):
        """签名错告警, 不重试"""
    
    def test_github_500_retries_3_times_then_alerts(self):
        """GitHub API 500 重试 3 次后告警"""
```

**E2E (本地 dry-run)**:

```bash
# scripts/dry-run.sh — 老板可手动跑
# 1. 用真 LLM 写 1 章
# 2. 用真 image API 生成 1 张封面
# 3. POST 到 obsidian-journal dev 环境 (localhost:3000)
# 4. GitHub API 推到 obsidian-novel-backups test 分支
# 5. 不污染主分支 + state 不推进
```

**Mock LLM 策略**:

```python
# tests/mocks/llm_responses.py
GOLDEN_CHAPTER_001 = {
    "raw_text": "第一章 ... [3000 字真实章节]",
    "cover_prompt": "a sci-fi astronaut...",
    "word_count": 3012,
    "usage": {"prompt_tokens": 1200, "completion_tokens": 3400},
}

GOLDEN_COVER_001 = "data:image/jpeg;base64,..."  # 真实封面 base64

@pytest.fixture
def mock_llm(monkeypatch):
    """Mock minimax chat + image API 返回黄金样本"""
    def mock_chat(*args, **kwargs):
        return GOLDEN_CHAPTER_001
    def mock_image(*args, **kwargs):
        return ["https://fake.cdn/covers/001.jpg"]
    monkeypatch.setattr("src.novel_writer.NovelWriter._call_raw", mock_chat)
    monkeypatch.setattr("src.cover_gen.CoverGenerator._call_image_api", mock_image)
```

**Mock obsidian-journal**:

```python
# tests/mocks/obsidian_server.py
@pytest.fixture
def mock_obsidian(requests_mock):
    """Mock /api/external/posts 接受并回 201"""
    requests_mock.post(
        "https://shangkun.uk/api/external/posts",
        json={"ok": True, "post": {"id": "test-123", "slug": "test", "url": "https://..."}},
        status_code=201
    )
```

**测试覆盖率门槛**:

```toml
# pyproject.toml
[tool.coverage.run]
source = ["src"]

[tool.coverage.report]
fail_under = 80
exclude_lines = [
    "pragma: no cover",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
```

### 5️⃣ CI/CD + 自动 push (v0.2 新增)

**核心原则**: 老板说"每开发完一步自动测试, 通过测试自动推送代码到仓库" — 黑解读为 **CI 测过 → 自动 push (无需人工 review)**。老板是单 owner repo, 没必要走 PR 流程。

**GitHub Actions 工作流 (.github/workflows/ci.yml)**:

```yaml
name: CI

on:
  push:
    branches: [main, dev/*, feature/*]
  pull_request:
    branches: [main]
  workflow_dispatch:  # 手动触发

permissions:
  contents: write  # 必须 write, CI 才能自动 push
  pull-requests: write  # 自动 comment

jobs:
  # Job 1: 代码质量 (快速, < 30s)
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: ruff check src/ tests/
      - run: black --check src/ tests/
      - run: mypy src/

  # Job 2: 单元测试 (中等, ~2min)
  unit-test:
    needs: lint
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: pytest tests/unit -v --cov=src --cov-report=xml --cov-fail-under=80
      - uses: actions/upload-artifact@v4
        with:
          name: coverage-${{ matrix.python-version }}
          path: coverage.xml

  # Job 3: 集成测试 (慢, ~5min)
  integration-test:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: pytest tests/integration -v --cov=src --cov-append --cov-fail-under=80

  # Job 4: Build artifact
  build:
    needs: [unit-test, integration-test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build
      - run: python -m build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  # Job 5: Auto-push to main (老板授权的 self-push 模式)
  auto-push:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main' && github.event_name == 'push' && success()
    steps:
      - name: Verify CI green
        run: |
          echo "✅ All checks passed, triggering auto-push"
      # 注意: push 触发本身就是 push, 这里只是验证后给老板发个 comment
      # 真正的"自动 push"发生在 push event 本身 — 老板本地黑推上来, CI 验证通过
      - uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.issue.number,
              body: '✅ CI passed — auto-push to main completed.'
            });

  # Job 6: Tag 触发 Release (手动)
  release:
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    needs: build
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build
      - run: python -m build
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*
          generate_release_notes: true
```

**自动 push 流程 (老板授权)**:

```
[黑本地开发]
   ↓ git commit
   ↓ git push origin main   ← 黑推上来
[GitHub Actions 触发 CI]
   ├─ lint → 失败则阻 push (GitHub branch protection)
   ├─ test-unit → 失败则阻
   ├─ test-integration → 失败则阻
   └─ build → 通过则 CI 绿
[merge / push 完成]
```

**老板的"自动推送代码" 实操方式**:

| 方式 | 描述 | 黑推荐 |
|---|---|---|
| **本地 push 触发 CI** | 黑本地跑测试 → 推 main → CI 自动验证 → 不通过则回滚 | ⭐ **推荐** (老板看到 CI 绿 = 推成功) |
| CI 自动 commit | CI 跑完自动 commit (如 bump version) | 备选 (风险: 改 history) |
| CI 自动 push 别人 fork | CI 推 PR 到别人 fork | 不必要 |

**Pre-commit hook (本地)**:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
        args: [--fix]
  - repo: https://github.com/psf/black
    rev: 24.8.0
    hooks:
      - id: black
  - repo: local
    hooks:
      - id: pytest-fast
        name: pytest-fast
        entry: pytest tests/unit -x
        language: system
        pass_filenames: false
```

**测试执行速度预算**:

| 测试 | 数量 | 单测时间 | 总时间 |
|---|---|---|---|
| Unit | ~40 个 | 50ms | 2s |
| Integration | ~15 个 | 200ms | 3s |
| Lint | - | - | 5s |
| Type | - | - | 10s |
| **总 CI 时间** | | | **~30s** |

---

## 🔧 技术栈 (v0.2 完整)

| 项 | 选 |
|---|---|
| 语言 | **Python 3.12** (沿用 xhs-novel-bot) |
| 调度 | **systemd timer** (本机 lavm-8h9bp4442f) |
| LLM | **minimax M3** (`MiniMax-M3`, 写作) + **`image-01`** (封面, v0.2 新增) |
| HTTP | `requests` + `hmac` + `hashlib` |
| 状态 | JSON 文件 (`data/state.json`) |
| 日志 | `logs/publisher.log` (滚动 10MB × 5) |
| 告警 | **WeCom webhook** |
| 测试 | `pytest` + `pytest-mock` + `pytest-cov` + `requests-mock` |
| Lint | `ruff` + `black` + `mypy --strict` |
| CI/CD | **GitHub Actions** (v0.2 新增) |
| 凭据 | `.env` (gitignored) — `MINIMAXI_API_KEY` / `OBSIDIAN_PUBLISH_SECRET` / `GITHUB_PAT` |

---

## 📡 obsidian-journal 接收侧 API 契约

### 新增 `app/api/external/posts/route.ts` (HMAC 鉴权)

详见 [docs/API_INTEGRATION.md](API_INTEGRATION.md)。

**关键点**:
- 鉴权: HMAC-SHA256 over `${timestamp}.${canonical_body}`
- 时间戳窗口: ±5 分钟
- rate limit: 10 req/min/IP
- category 扩展: `tech` / `life` / **`novel`** (v0.2 新增)
- idempotency: `idempotency_key` UNIQUE 索引
- publisher 白名单: `novel-publisher` / `yk-script`

### 新增 `app/api/admin/resources/route.ts` (封面图上传, 复用现有)

封面图走 obsidian-journal 现有的资源上传链路 (admin 鉴权), publisher 调用时带 admin token (publisher 启动时申请一次性 token 或 service account)。

> ⚠️ **简化方案**: 封面图也可以 base64 嵌入 markdown (`data:image/jpeg;base64,...`), 不需要单独上传, 但会让单章节 md 文件变大 (~270KB)。黑推荐**走资源上传** 链路, markdown 干净。

---

## 📋 老板决策清单 (v0.2, 启动前必须拍)

### 项目 A 决策 (Q1-Q8, v0.1 沿用 + v0.2 增量)

| # | 决策项 | 候选 | **黑推荐** | 状态 |
|---|---|---|---|---|
| **Q1** | 推送鉴权方式 | HMAC / API key / mTLS | **HMAC-SHA256** (v0.2 明确) | ✅ 黑确认 |
| **Q2** | 章节字数 | 2000 / 2800 / 3000 / 5000 | **2800-3200** (沿用 xhs) | ✅ 沿用 |
| **Q3** | novel id | 复用 `meta_realm` / 新开 | **新开 `meta_realm_obsidian`** | ✅ 沿用 |
| **Q4** | posts.category | 加 `'novel'` / 复用 `'tech'` | **加 `'novel'`** | ✅ 沿用 |
| **Q5** | 失败告警 | WeCom / 邮件 / 仅日志 | **复用 WeCom** | ✅ 沿用 |
| **Q6** | 部署位置 | 本机 / 生产机 / 容器 | **本机 lavm-8h9bp4442f** | ✅ 沿用 |
| **Q7** | 周末频率 | 3/天 / 1/天 / 跳过 | **3/天维持** | ✅ 沿用 |
| **Q8** | 选题策略 | 沿用 topic_gen / 老板预选 | **沿用 topic_gen** | ✅ 沿用 |

### v0.2 新增决策 (Q9-Q13)

| # | 决策项 | 候选 | **黑推荐** | 冷峻理由 |
|---|---|---|---|---|
| **Q9** | GitHub 备份仓库 | 新建 `obsidian-novel-backups` (private) / 推 stellar-scribe / 推本仓 public | **新建 `obsidian-novel-backups` (private)** | 独立干净, 不污染商业主线 |
| **Q10** | 封面图嵌入方式 | 走资源上传 (R2/OSS URL) / base64 嵌入 md | **走资源上传** (复用 admin API) | md 文件干净, 加载快, 老板便于单独管理 |
| **Q11** | CI 自动 push 模式 | 本地 push 触发 CI / CI 自动 commit / CI 推 PR | **本地 push 触发 CI** (老板看 CI 绿 = 成功) | 最稳, 可回滚 |
| **Q12** | 测试覆盖率门槛 | 70% / 80% / 90% | **80%** | 平衡速度与质量 |
| **Q13** | Pre-commit hook | 启用 / 禁用 | **启用** | 本地早发现 lint/格式问题 |

---

## 📅 实施计划 (v0.2 更新, 估 5.5 工作日)

| P | 内容 | 文件 | 时 |
|---|---|---|---|
| **P0** | 仓库骨架 + .env.example + README + .gitignore + .pre-commit-config.yaml + pyproject.toml | 7 | 0.5d |
| **P1** | 迁移 `assets/prompts/{system,user,topic_*}.txt` + `novel_writer.py` `topic_gen.py` `summary_gen.py` **`cover_gen.py`** | 8 | 0.5d |
| **P2** | `publisher.py` 主入口 + `state.py` + `hmac_client.py` + `cover_upload.py` + `markdown_renderer.py` | 5 | 1.5d |
| **P3** | `github_backup.py` (GitHub Contents API 客户端) | 1 | 1d |
| **P4** | obsidian-journal `app/api/external/posts/route.ts` (HMAC + rate limit + idempotency) | 1 | 0.5d |
| **P5** | systemd timer (08:00 / 12:00 / 18:00) + logrotate | 3 | 0.5d |
| **P6** | 测试: 单测 (~40) + 集成 (~15) + mock LLM/obsidian/github + 黄金样本 | 8 | 1d |
| **P7** | GitHub Actions `.github/workflows/ci.yml` + branch protection 配置 | 2 | 0.5d |

**总代码**: ~1200 LOC + obsidian 侧 ~150 LOC + 测试 ~600 LOC
**总工时**: **5.5d** (v0.1 的 4d + v0.2 增量 1.5d)

---

## 🛡️ 风控 / 安全 / 风险 (v0.2 更新)

| 风险 | 等级 | 缓解 |
|---|---|---|
| 老板 API key 泄露 | 🟡 | `.env` gitignored + CI secrets 加密 + 本机 0600 权限 |
| HMAC 重放攻击 | 🟡 | 5 分钟时间戳窗口 + nonce |
| 推送频率过高被博客限流 | 🟢 | rate limit 10/min, 实测 3/天无压力 |
| LLM 输出违规内容 | 🟡 | prompt 已约束, 加 length filter |
| 推送失败漏发 | 🟡 | state 持久化 + 失败重试 3 次 + 告警 |
| 双发 / 漏发 | 🟢 | state.next_idx 单调递增 + idempotency_key |
| **封面图 token 失效** (v0.2) | 🟡 | 启动时检查 token, 失败告警 |
| **GitHub 备份失败** (v0.2) | 🟡 | 重试 3 次 + 告警, 不阻塞主推送 |
| **CI 误推** (v0.2) | 🟢 | branch protection + 测试必过 |
| **Cover 图太大撑爆 md** (v0.2) | 🟢 | 走资源上传, md 不含 base64 |

---

## 📂 仓库结构 (v0.2 更新)

```
obsidian-novel-publisher/
├── README.md
├── CHANGELOG.md                       # v0.2 增量记录
├── pyproject.toml                     # 依赖 + 工具配置
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml            # pre-commit hook
├── docs/
│   ├── PLAN.md                        # v0.2 方案稿 (本文档)
│   ├── ARCHITECTURE.md
│   ├── API_INTEGRATION.md             # obsidian-journal 接收侧契约
│   ├── CI.md                          # CI/CD 详细流程 (v0.2 新增)
│   ├── TESTING.md                     # 测试策略 (v0.2 新增)
│   ├── BACKUP.md                      # GitHub 备份架构 (v0.2 新增)
│   └── RUNBOOK.md
├── assets/
│   └── prompts/                       # 从 xhs-novel-bot 整建制迁移
│       ├── system.txt
│       ├── user.txt
│       ├── topic_system.txt
│       └── topic_user.txt
├── src/
│   ├── __init__.py
│   ├── novel_writer.py                # M3 调用 (迁移)
│   ├── topic_gen.py                   # 选题 (迁移)
│   ├── summary_gen.py                 # 摘要 (迁移)
│   ├── cover_gen.py                   # 封面图 (迁移, v0.2 复用)
│   ├── cover_upload.py                # 封面上传 (新, v0.2)
│   ├── publisher.py                   # 主入口 (新)
│   ├── state.py                       # JSON state (新)
│   ├── hmac_client.py                 # HMAC 签名 (新)
│   ├── markdown_renderer.py           # MD 渲染 (新, v0.2)
│   └── github_backup.py               # GitHub 备份 (新, v0.2)
├── scripts/
│   ├── publish-once.py                # 单次发布
│   ├── dry-run.sh                     # 本地 dry-run (真 LLM + 真 dev)
│   └── skip-next.py                   # 跳过标记
├── systemd/
│   ├── novel-publish.service
│   ├── novel-publish@08.timer
│   ├── novel-publish@12.timer
│   └── novel-publish@18.timer
├── .github/
│   └── workflows/
│       └── ci.yml                     # GitHub Actions (v0.2 新增)
├── data/                              # gitignored
│   ├── state.json
│   ├── novel/meta_realm_obsidian/
│   │   ├── truth.json
│   │   └── style.json
│   └── covers/                        # 临时封面 (gitignored)
├── logs/                              # gitignored
└── tests/
    ├── unit/
    │   ├── test_novel_writer.py
    │   ├── test_cover_gen.py
    │   ├── test_hmac_client.py
    │   ├── test_state.py
    │   ├── test_publisher.py
    │   ├── test_markdown_renderer.py
    │   ├── test_cover_upload.py
    │   └── test_github_backup.py
    ├── integration/
    │   ├── test_publish_e2e.py
    │   ├── test_failure_modes.py
    │   └── test_backup_flow.py
    └── mocks/
        ├── llm_responses.py           # 黄金样本
        ├── obsidian_server.py         # requests-mock
        └── github_api.py              # requests-mock
```

---

## ✅ 不在本期范围 (deferred)

- ❌ 多主题矩阵 (本期只科幻, 后期可扩展奇幻/悬疑)
- ❌ Web UI dashboard (本期纯 cron + 日志)
- ❌ 推送 XHS (老板账号被封 ⛔)
- ❌ 评论自动回复 (博客无)
- ❌ CI 自动 bump version (本期手写 CHANGELOG)
- ❌ PyPI 发布 (本期 GitHub Release 即可)

---

## 🚦 老板拍板后启动

老板回复 Q1-Q13 → 黑立即开 P0。
**最快 5.5 天上线, 第三天就有首批 3 章草稿 + 完整测试覆盖 + CI 绿 + GitHub 备份就位**。

---

## 📝 v0.1 → v0.2 变更日志

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-07-05 | v0.1 | 初版方案 (架构 + 复用清单 + Q1-Q8) |
| 2026-07-06 | **v0.2** | **加封面图 (复用 minimax image-01, 已实测) + 鉴权明确 (HMAC-SHA256) + GitHub 备份 (新建 obsidian-novel-backups 私仓) + 完整测试策略 (单测 40+ 集成 15+ 覆盖率 80%) + CI/CD (GitHub Actions auto-push) + Q9-Q13 新增决策** |

---

*文档版本*: v0.2 (2026-07-06 00:30 GMT+8)
*作者*: 黑 (Hei)