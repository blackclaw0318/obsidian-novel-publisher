# obsidian-novel-publisher — 方案稿 v0.1 (2026-07-05)

> **项目代号**: obsidian-novel-publisher
> **目标**: 每天 3 次（早 8 / 中 12 / 晚 18）自动生成中场篇科幻小说章节，POST 到 obsidian-journal 网站
> **状态**: ⏸️ 等老板 Q1-Q8 拍板开 P0

---

## 🎯 一句话定位

**把 xhs-novel-bot 里已经调优好的"中场篇科幻小说"生产 skill 整建制搬过来,换一个推送目的地（XHS → 个人博客）。**

无风控压力 (博客无审核),可全力 3/天 输出。

---

## 🏗️ 架构图

```
┌─────────────────────────────────────────────────────────────┐
│  本机 cron (systemd timer 或 crontab)                         │
│   ├─ 08:00 daily-publish.timer                             │
│   ├─ 12:00 daily-publish.timer                             │
│   └─ 18:00 daily-publish.timer                             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  src/publish.py — 主入口                                     │
│                                                              │
│  1. load_state()              ← data/state.json              │
│  2. load_truth_snapshot()     ← data/novel/<id>/            │
│  3. NovelWriter.write_chapter(                              │
│       chapter_idx=state.next_idx,                           │
│       truth_snapshot=...,                                   │
│       style_guide=STYLE_GUIDE_中场篇科幻                    │
│     ) → ChapterDraft                                        │
│  4. push_to_obsidian(draft)                                 │
│       POST https://shangkun.uk/api/external/posts           │
│       headers: x-publisher-id + x-publisher-signature       │
│                (HMAC-SHA256 of body + secret)                │
│       body: {slug, title, excerpt, content, category:"novel"}│
│  5. update_state()           ← data/state.json              │
│  6. alert_on_failure()       ← WeCom webhook                │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  obsidian-journal 接收侧                                     │
│   app/api/external/posts/route.ts (新增, HMAC 鉴权)         │
│     → 写 posts 表 (category='novel', author='novel-bot')    │
│     → 公开页 /novels 列表自动展示                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 📦 复用 xhs-novel-bot 资产 (整建制迁移)

| xhs-novel-bot 文件 | 用途 | 新项目位置 |
|---|---|---|
| `assets/prompts/system.txt` | 写作风格 system prompt | `assets/prompts/system.txt` |
| `assets/prompts/user.txt` | 章节模板 | `assets/prompts/user.txt` |
| `assets/prompts/topic_system.txt` | 选题 prompt | `assets/prompts/topic_system.txt` |
| `assets/prompts/topic_user.txt` | 选题 prompt | `assets/prompts/topic_user.txt` |
| `src/novel_writer.py` | M3 调用 + 重试 + 字数校验 | `src/novel_writer.py` (复制) |
| `src/topic_gen.py` | 选题生成 | `src/topic_gen.py` |
| `src/summary_gen.py` | 章节摘要 (供下章承接) | `src/summary_gen.py` |
| `src/cover_gen.py` | 封面图生成 | **❌ 不用** (博客用占位即可) |
| `src/scheduler.py` | 调度循环 | **❌ 替换** (用 systemd timer) |
| `src/orchestrator.py` | 编排 | **❌ 替换** (单文件 publisher) |
| `data/cookies.json` | XHS 登录态 | **❌ 不用** |

**核心改动**：
- ✅ 保留 `novel_writer.py` / `topic_gen.py` / `summary_gen.py` (已稳定, 2800-3200 字字数控制)
- ❌ 删 XHS 相关: `cookies.json` / `xiaohongshu_mcp` / `cover_gen`
- 🔁 新增: `publisher.py` (单文件发布逻辑) / `state.py` (JSON state) / `hmac_client.py` (签名)

---

## 🔧 技术栈

| 项 | 选 |
|---|---|
| 语言 | **Python 3.12** (沿用 xhs-novel-bot) |
| 调度 | **systemd timer** (本机 lavm-8h9bp4442f, 不在生产机) |
| LLM | **minimax M3** (`MiniMax-M3`, `assets/prompts/` 已调优) |
| HTTP | `requests` + `hmac` + `hashlib` |
| 状态 | JSON 文件 (`data/state.json`) |
| 日志 | `logs/publisher.log` (滚动 10MB × 5) |
| 告警 | **WeCom webhook** (沿用 xhs-novel-bot `phase2.alert`) |
| 凭据 | `.env` (gitignored) — `MINIMAXI_API_KEY` / `OBSIDIAN_PUBLISH_SECRET` |

---

## 📡 obsidian-journal 接收侧 API 契约

### 新增 `app/api/external/posts/route.ts`

```
POST /api/external/posts
Headers:
  Content-Type: application/json
  X-Publisher-Id: novel-publisher
  X-Publisher-Signature: <hex HMAC-SHA256(body, OBSIDIAN_PUBLISH_SECRET)>
  X-Publisher-Timestamp: <unix ms, 5 分钟内有效>
Body:
  {
    "slug": "meta-realm-ch-06",
    "title": "第六章 · 量子回声",
    "excerpt": "<= 200 字摘要",
    "content": "完整 markdown 正文",
    "category": "novel",
    "tags": "科幻,中篇,meta_realm",
    "cover_image": null | "url",
    "external_id": "<publisher 内唯一 id, 幂等用>",
    "external_meta": {
      "chapter_idx": 6,
      "word_count": 3012,
      "llm_usage": { ... }
    }
  }

Response:
  201 { ok: true, post: { id, slug, url } }
  401 { ok: false, error: "bad_signature" }
  409 { ok: false, error: "slug_exists" }
  429 { ok: false, error: "rate_limited" }
```

**安全要点**：
- HMAC-SHA256 over canonical body (sort keys + no whitespace)
- 时间戳校验 ±5 分钟 (防 replay)
- rate limit 10 req/min/IP
- category 白名单: `tech` / `life` / `novel`
- author_id 用系统虚拟账号 `novel-bot` (无密码, 仅 API 注入)

---

## 📋 老板决策清单 (Q1-Q8, 重启前必须拍)

| # | 决策项 | 候选 | **黑推荐** | 理由 |
|---|---|---|---|---|
| **Q1** | 推送鉴权方式 | HMAC / API key / mTLS | **HMAC-SHA256** | 行业标准, 防 replay, 实现简单 |
| **Q2** | 章节字数 | 2000 / 2800 / 3000 / 5000 | **2800-3200** (沿用 xhs) | 已调优, M3 一次写满不超 8000 token |
| **Q3** | novel id | 复用 `meta_realm` / 新开 | **新开 `meta_realm_obsidian`** | 与 xhs 隔离, 互不影响 |
| **Q4** | posts.category | 加 `'novel'` / 复用 `'tech'` | **加 `'novel'`** | 语义清晰, 可独立 /novels 列表 |
| **Q5** | 失败告警 | WeCom / 邮件 / 仅日志 | **复用 WeCom** | xhs 已配, 直接复用 webhook |
| **Q6** | 部署位置 | 本机 / 生产机 / 容器 | **本机 lavm-8h9bp4442f** | 无生产机 systemd, 单机足够 |
| **Q7** | 周末频率 | 3/天 / 1/天 / 跳过 | **3/天维持** | 博客无风控, 不必降速 |
| **Q8** | 选题策略 | 沿用 topic_gen / 老板预选 | **沿用 topic_gen** | 已稳定, 随机+承接 |

---

## 📅 实施计划 (估 4 工作日)

| P | 内容 | 文件 | 时 |
|---|---|---|---|
| **P0** | 仓库骨架 + .env.example + .gitignore + README | 4 | 0.5d |
| **P1** | 迁移 `assets/prompts/{system,user,topic_*}*.txt` + `novel_writer.py` `topic_gen.py` `summary_gen.py` | 7 | 0.5d |
| **P2** | `publisher.py` 主入口 + `state.py` + `hmac_client.py` | 3 | 1d |
| **P3** | obsidian-journal `app/api/external/posts/route.ts` (HMAC 鉴权 + rate limit + category 扩展) | 1 | 0.5d |
| **P4** | systemd timer (`08:00` / `12:00` / `18:00`) + logrotate | 3 | 0.5d |
| **P5** | 测试: 单测 + 集成 (mock LLM + mock obsidian) + e2e (curl dry-run) | 5 | 0.5d |
| **P6** | docs: RUNBOOK.md + 部署 + 监控 + 故障排查 | 1 | 0.5d |

**总代码**: ~800 LOC + obsidian 侧 ~150 LOC
**总工时**: 4d (含 obsidian 侧改动)

---

## 🛡️ 风控 / 安全 / 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| 老板 API key 泄露 | 🟡 | `.env` gitignored + 仅服务器本机 |
| HMAC 重放攻击 | 🟡 | 5 分钟时间戳窗口 + nonce |
| 推送频率过高被博客限流 | 🟢 | rate limit 10/min, 实测 3/天无压力 |
| LLM 输出违规内容 | 🟡 | prompt 已约束 (沿用 xhs), 加 length filter |
| 推送失败漏发 | 🟡 | state 持久化 + 失败重试 3 次 + 告警 |
| 双发 / 漏发 | 🟢 | state.next_idx 单调递增 + idempotency_key |

---

## 📂 仓库结构

```
obsidian-novel-publisher/
├── README.md
├── .env.example
├── .gitignore
├── docs/
│   ├── PLAN.md              # 本文档
│   ├── ARCHITECTURE.md      # 详细架构
│   ├── RUNBOOK.md           # 运维 (部署/监控/故障)
│   └── CHANGELOG.md
├── assets/
│   └── prompts/             # 从 xhs-novel-bot 整建制迁移
│       ├── system.txt
│       ├── user.txt
│       ├── topic_system.txt
│       └── topic_user.txt
├── src/
│   ├── novel_writer.py      # M3 调用 (迁移 + 简化)
│   ├── topic_gen.py         # 选题 (迁移)
│   ├── summary_gen.py       # 摘要 (迁移)
│   ├── publisher.py         # 主入口 (新)
│   ├── state.py             # JSON state (新)
│   └── hmac_client.py       # HMAC 签名 (新)
├── scripts/
│   ├── publish-once.py      # 单次发布 (cron 调用)
│   └── dry-run.py           # 干跑 (不真推, 仅 LLM 调用)
├── systemd/
│   ├── novel-publish.service
│   ├── novel-publish@08.timer
│   ├── novel-publish@12.timer
│   └── novel-publish@18.timer
├── data/
│   ├── state.json           # 状态 (gitignored)
│   └── novel/
│       └── meta_realm_obsidian/
│           ├── truth.json   # 世界观 + 人物
│           └── style.json   # 风格指南
├── logs/                    # gitignored
└── tests/
    ├── unit/
    │   ├── test_novel_writer.py
    │   ├── test_state.py
    │   └── test_hmac.py
    └── integration/
        └── test_publish_e2e.py
```

---

## ✅ 不在本期范围 (deferred)

- ❌ 自动生成封面图 (博客用占位即可, 后期再上 minimax image-01)
- ❌ 推送 XHS (老板账号被封 ⛔, 暂停)
- ❌ 评论自动回复 (xhs 才有, 博客无)
- ❌ 多主题矩阵 (本期只科幻, 后期可扩展奇幻/悬疑)
- ❌ Web UI (本期纯 cron + 日志, 后期加 dashboard)

---

## 🚦 老板拍板后启动

老板回复 Q1-Q8 → 黑立即开 P0。
**最快 4 天上线, 第二天就有第一批 3 章草稿**。

---

*文档版本*: v0.1 (2026-07-05 09:30 GMT+8)
*作者*: 黑 (Hei)