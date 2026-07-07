# CI/CD 设置指南 (v0.2 P7)

> 单 owner repo, 黑本地开发 → CI 测过即 push。本文档说明 GitHub Actions 已自动化 + branch protection 手动配置 1 步。

---

## ✅ 已自动化的部分 (commit 后即生效)

`.github/workflows/ci.yml` 已提交, push 后自动跑 6 jobs:

| Job | 触发 | 耗时 | 作用 |
|---|---|---|---|
| `lint` | push/PR | ~30s | ruff + black --check + mypy strict |
| `unit-test` | push/PR | ~2min | pytest tests/unit + coverage ≥80% (matrix 3.11/3.12) |
| `integration-test` | push/PR | ~5min | pytest tests/integration + coverage ≥80% |
| `build` | push | ~1min | python -m build → dist/* |
| `auto-push` | main push 全绿 | ~5s | workflow run 页面发 ✅ comment |
| `release` | tag v* | ~2min | GitHub Release + dist artifact |

**老板体验**: 推完代码 → GitHub Actions 自动跑 → 看 https://github.com/blackclaw0318/obsidian-novel-publisher/actions

---

## 🛡️ Branch Protection (老板手动 1 步, P7 收官)

### 配置目的
- 防止老板 / 黑手滑 `git push --force` 把 main 干炸
- 强制 PR 必须过 CI 才能 merge (虽然本项目不走 PR, 但加双保险)
- 限制只有 owner 能直接 push

### 配置步骤 (浏览器, 1 分钟)

1. 打开 https://github.com/blackclaw0318/obsidian-novel-publisher/settings/branches
2. 点 **Add rule** → Branch name pattern: `main`
3. 勾选以下选项:
   - ☑ **Require a pull request before merging** (允许 owner bypass)
     - ☑ Require approvals: 0 (单 owner 不需要 approval)
   - ☑ **Require status checks to pass before merging**
     - 搜索并勾选 3 个 jobs: `Lint`, `Unit tests (Python 3.12)`, `Integration tests`
     - ☑ Require branches to be up to date
   - ☑ **Do not allow force pushes** (防 main 干炸)
   - ☑ **Do not allow deletions**
4. **Allow specified actors to bypass required pull requests**: 勾选 + 添加 `blackclaw0318`
5. 点 **Create** / **Save changes**

### 配置 API (可选, 自动化)

```bash
# 老板可手动跑这段 (需 admin token, GitHub Settings → PAT)
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  /repos/blackclaw0318/obsidian-novel-publisher/branches/main/protection \
  -f required_status_checks='{"strict":true,"contexts":["Lint","Unit tests (Python 3.12)","Integration tests"]}' \
  -f enforce_admins=false \
  -f required_pull_request_reviews='{"dismissal_restrictions":{},"dismiss_stale_reviews":true,"require_code_owner_reviews":false,"required_approving_review_count":0}' \
  -f restrictions=null \
  -f required_linear_history=true \
  -f allow_force_pushes=false \
  -f allow_deletions=false \
  -f block_creations=false \
  -f required_conversation_resolution=true \
  -f lock_branch=false \
  -f allow_fork_syncing=false
```

⚠️ 黑**没有**自动配: GitHub PAT 创建 branch protection 是 admin-only, 老板手动 1 分钟搞定更安全。

---

## 🏷️ Release 流程 (手动, 老板决定时机)

### 老板想发版本时

```bash
# 1. 老板拍板版本号 (v0.2.0 / v0.3.0 / v1.0.0)
# 2. 黑本地打 tag + push
git tag v0.2.0
git push origin v0.2.0

# 3. GitHub Actions 自动:
#    - 跑完 lint + unit + integration + build
#    - 创建 Release: https://github.com/blackclaw0318/obsidian-novel-publisher/releases/tag/v0.2.0
#    - 上传 dist/*.whl + *.tar.gz 到 Release assets
#    - 自动生成 release notes (从 PR / commit 历史)
```

### 当前版本

**v0.2.0** (2026-07-07) — 本次收官
- P0-P6 全部完成 (199 测试)
- P7 CI/CD 自动化
- 待老板手动配 branch protection (上面 1 步)

---

## 🐛 CI 故障排查

### Lint 失败

```bash
# 本地复现
ruff check src tests
black --check src tests
mypy src
```

修完再 push。

### 测试失败

```bash
# 本地跑全套
.venv/bin/python -m pytest tests/ -v

# 看具体失败
.venv/bin/python -m pytest tests/integration/test_failure_modes.py::test_llm_5xx_retry_then_succeed -v
```

### Coverage 不足 80%

```bash
# 看覆盖率详情
.venv/bin/python -m pytest tests/unit --cov=src --cov-report=term-missing

# 输出会显示哪些行没覆盖, 补测试或加 # pragma: no cover
```

### Build 失败

```bash
# 本地 build
.venv/bin/python -m build

# 看 dist/
ls -la dist/
```

---

## 📊 CI 配额预估

按本项目规模:
- 每次 push: 6 jobs × ubuntu-latest = ~6 分钟
- 老板每天 ~3 次 push: ~18 min/day
- GitHub free tier: 2000 min/month → 足够 (单 owner)
- private repo: 同样 2000 min 免费

**够用**, 不需要升级。

---

## 🔗 相关链接

- Actions: https://github.com/blackclaw0318/obsidian-novel-publisher/actions
- Branch settings: https://github.com/blackclaw0318/obsidian-novel-publisher/settings/branches
- Releases: https://github.com/blackclaw0318/obsidian-novel-publisher/releases