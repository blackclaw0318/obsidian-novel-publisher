# obsidian-novel-publisher — 小说作者名统一改"上坤" 方案稿 (2026-07-08)

> **作者**: 黑 (Hei)
> **状态**: ⏸️ 等老板 Q1-Q2 拍板开 P0
> **范围**: 小说类 (category='novel') post 渲染层 + DB bot user
> **耗时预估**: < 0.2d (1 个 SQL + 2 行渲染代码 + 1 个 playwright spec)
> **优先级**: 🔴 中 (不影响推送链路, 仅影响 ch1/ch2 显示文字)

---

## 1. 背景

### 1.1 老板原话 (2026-07-07 23:22 那轮反馈 3 个问题)
> 老板看 www.shangkun.uk 反馈:
> 1. 小说放到了"文章"列表, 没放到"小说"列表 (图 1)
> 2. 封面图片显示 broken icon (图 1)
> 3. 中文标点是 ASCII 半角 (图 2)
> 4. (新增) 小说作者名统一改为"上坤"

(老板在 2026-07-08 05:07 修正 typo, 把"图片加载视频"修正为"图片加载失败", 并新增第 4 条)

### 1.2 2026-07-08 05:07 现场实测 (5 篇 novel posts)

| # | 问题 | 状态 | 现场证据 |
|---|---|---|---|
| 1 | 小说放错页面 | ✅ **已修** | DB: 5 篇全 `category='novel'`;页面 `<span class="rounded bg-bg-muted px-2 py-0.5 uppercase">novel</span>`; JSON-LD `articleSection: novel`;`/novels` 200 |
| 2 | 图片加载失败 | ✅ **已修** | ch1/ch2 封面 HTTP 200 (本地 + 公网双验) |
| 3 | 中文标点半角 | ✅ **已修** | ch2 DB: 全角逗号 90 / 半角 0;全角句号 86 / 半角 12 (剩的是数字小数点, 正确) |
| 4 | 作者名改"上坤" | ❌ **未修** | UI `作者: novel-bot`;schema.org `author.name: novel-bot`;og:article:author: novel-bot |

---

## 2. 真因诊断

### 2.1 DB 现状 (实测)
```sql
-- users 表
id              email                       name          role
u_e7jegpeamr... admin@obsidian.local       上坤          admin
u_bot_novel     novel-bot@system.local     novel-bot     bot    ← 罪魁
u_bot_yk        yk-bot@system.local        yk-bot        bot

-- posts 表 (5 篇 novel posts)
slug                       author_id
test-p4-e2e-chapter-001    u_bot_novel
probe3-1783414571014       u_bot_novel
smoketest-1783432849       u_bot_novel
meta-realm-ch001           u_bot_novel
meta-realm-ch002           u_bot_novel
```

### 2.2 渲染链条 (3 个渲染点 + 1 个硬编码 meta)

```tsx
// app/posts/[slug]/page.tsx:64  ← schema.org JSON-LD author
authors: [post.author.name ?? post.author.email],

// app/posts/[slug]/page.tsx:126  ← 详情页 UI "作者:"
作者: {post.author.name ?? post.author.email}

// app/posts/page.tsx:137  ← 列表页 "— {author}"
— {post.author.name ?? post.author.email}

// lib/feed.ts:91  ← RSS/feed schema
<name>${escapeXml(p.author.name ?? p.author.email)}</name>

// app/posts/[slug]/page.tsx 边角  ← <meta name="author" content="上坤"/>
// 这是 SITE_DEFAULT 常量硬编码的, 跟 DB 完全没关系
```

### 2.3 错位真相
- meta `name="author"` = "上坤" ← SITE_DEFAULT 硬编码常量的副作用
- UI / JSON-LD / og ← 全读 DB 用户名,即 "novel-bot"
- **渲染层根本没读 meta,只读 user.name**

---

## 3. 推荐方案: 方案 A + guard (黑推荐)

**核心思路**: 一行 SQL + 两行渲染 guard。最小改动,最直接语义,避免过度设计。

### 3.1 改动清单 (3 个文件 / 1 个 SQL)

#### 改动 #1: DB 一行 SQL (30 秒)
```sql
-- 直接更新 bot user name 为"上坤"
-- 风险: u_bot_novel 当前只在小说推送链路用, 不影响其它业务
-- 兜底: 渲染层 guard 限定 category='novel', 即便未来其它 bot 共用, 也只在小说页生效
UPDATE users SET name = '上坤' WHERE id = 'u_bot_novel';
```

#### 改动 #2: app/posts/[slug]/page.tsx (2 行 guard)
```tsx
// 现有 (line 64, 126)
authors: [post.author.name ?? post.author.email],
作者: {post.author.name ?? post.author.email}

// 改为
const displayAuthor = post.category === 'novel' ? '上坤' : (post.author.name ?? post.author.email);

authors: [displayAuthor],
作者: {displayAuthor}
```

#### 改动 #3: app/posts/page.tsx (1 行 guard)
```tsx
// 现有 (line 137)
— {post.author.name ?? post.author.email}

// 改为
— {post.category === 'novel' ? '上坤' : (post.author.name ?? post.author.email)}
```

#### 改动 #4 (顺手): lib/feed.ts (1 行 og:article:author)
```tsx
// 现有 (line 91)
<name>${escapeXml(p.author.name ?? p.author.email)}</name>

// 改为 (RSS / Atom 也用同一表达式, 一改全改)
<name>${escapeXml(p.category === 'novel' ? '上坤' : (p.author.name ?? p.author.email))}</name>
```

### 3.2 为什么加 guard 而不是裸改 user.name

| 场景 | 裸改 user.name (无 guard) | 方案 A + guard |
|---|---|---|
| 未来多个 bot 都推送小说 | 共用 name='上坤', 追溯不到作者 source | guard 自动归一, 作者显示统一 |
| yk-bot 推送技术类文章 | 不影响 (yk-bot 不推 tech/life) | guard 限定 novel, tech/life 仍用 yk-bot 的真实名 |
| 真用户直接写小说 (admin 手动发) | 不影响 (admin name 已是"上坤") | 仍显示"上坤", 语义对 |
| 跨 bot 作者归属追溯 | **丢**(DB 全是"上坤") | 保 (DB 仍 record author_id) |

### 3.3 测试 (1 个 playwright spec)
```ts
// tests/e2e/novel-author.spec.ts (新)
test('小说类 post 显示作者为"上坤"', async ({ page }) => {
  await page.goto('/posts/meta-realm-ch001');
  await expect(page.locator('text=作者:')).toContainText('上坤');
});

test('tech 类 post 仍显示原始作者', async ({ page }) => {
  await page.goto('/posts/tech-sample');  // 用现有 fixture
  await expect(page.locator('text=作者:')).not.toContainText('novel-bot');
});

test('schema.org JSON-LD author name = "上坤"', async ({ page }) => {
  await page.goto('/posts/meta-realm-ch001');
  const ld = await page.locator('script[type="application/ld+json"]').textContent();
  const data = JSON.parse(ld!);
  expect(data.author.name).toBe('上坤');
});
```

### 3.4 部署顺序 (最小窗口)
1. SQL 改 user.name (30s, DB 立即生效, 当前所有小说 post 重新渲染)
2. 代码改 3 个文件 (5min)
3. playwright spec (10min)
4. pre-commit hook + lint + typecheck + build (1min)
5. 推送 main → CI 跑 → cloudflared 重连 (无感, 全站刷新后生效)
6. **总耗时 < 10 分钟**

---

## 4. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| bot user 改名后, admin UI 显示"上坤" 太多混淆 | 🟢 低 | UI 微调, 不影响功能 | guard 限定只在 novel 显示 |
| DB 直接 UPDATE 触发外键 / 索引重算 | 🟢 极低 | 1 row update, 无 schema 变更 | 跑前 backup `data/dev.db` |
| 老 ch1/ch2 还残留旧 cache (CDN/Cloudflare) | 🟡 中 | 显示不一致几分钟 | deploy 时一次性 purge cache |
| playwright spec 依赖外部 fixture (tech-sample) | 🟢 低 | 测试自身的可移植性 | 用 stable slug |

---

## 5. 不推荐的备选方案

### 方案 B: site_settings 配置 (优雅但过度设计)
```sql
CREATE TABLE site_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);
INSERT INTO site_settings (key, value) VALUES ('novel_default_author', '上坤');
```
- 优点: 配置驱动, 未来改作者不用动代码
- 缺点: 1 行需求用 1 张表 + 3 行代码 + 1 行 UI, **性价比低**
- **黑不推荐**

### 方案 C: Post.display_author 字段 (设计最满)
```sql
ALTER TABLE posts ADD COLUMN display_author TEXT;
```
- 优点: 每个 post 自定义, 完全灵活
- 缺点: 5 篇 post 都要 backfill, publisher 推送每次带这个字段, admin 手写 post 也要选 author, **杀鸡用牛刀**
- **黑强烈不推荐**

---

## 6. 数据现状 (部署前快照)

```
Users (3):
  u_e7jegpeamr... admin@obsidian.local → 上坤 (admin)
  u_bot_novel     novel-bot@system.local → novel-bot (bot)  ← 改后变为"上坤"
  u_bot_yk        yk-bot@system.local    → yk-bot (bot)

Novel posts (5, 全 author_id=u_bot_novel):
  test-p4-e2e-chapter-001 (11 字, smoke test 残留, 待清理)
  probe3-1783414571014 (14 字, smoke test 残留, 待清理)
  smoketest-1783432849 (18 字, smoke test 残留, 待清理)
  meta-realm-ch001 (4054 字, 第 1 章 · 意识化石猎人) ← 唯一真章
  meta-realm-ch002 (2743 字, 第 2 章 · 月背挖到一根不存在的骨头) ← 唯一真章

Tech posts (3):
  (post-001 style = 'tech' by admin)
```

---

## 7. 等老板拍板

| Q# | 决策项 | 候选 | 黑推荐 |
|---|---|---|---|
| **Q1** | 方案确认 | 方案 A (一行 SQL + 3 行代码) / 方案 B (site_settings) / 方案 C (display_author 字段) | **方案 A** (最小, 直接, 防御性 guard) |
| **Q2** | smoke test 残留 3 篇 (test-p4-*, probe3-*, smoketest-*) 是否一并清 | 是 / 否 / 单独清理 | **是** (一并清, 老板从 admin 后台删即可, 我不动 DB) |
| **Q3** | meta `name="author" content="上坤"` 那个硬编码 SITE_DEFAULT 是否删 | 保留 (双保险) / 删 (单一数据源) | **保留** (SITE_DEFAULT 兜底, 万一渲染 guard 漏了, meta 仍是"上坤") |
| **Q4** | 部署窗口 | 立即 / 23:30 日报后 / 等指令 | **等我命令** (老板拍板才动) |

**Q1-Q3 拍板后, < 10 分钟我交付: SQL + 3 文件代码 + playwright spec + lint/typecheck/build 全绿 + 自动推 main + CI 跑过 + playwright 实测双 post 截图给老板看。**

---

## 8. 引用 / 来源

- 实测时间: 2026-07-08 05:07-05:14 (本机 curl + sqlite3 + grep)
- 上次 session: `memory/2026-07-07-2322-p3-3fixes.md` (老板原 3 + #0 defer)
- 本次 session: `memory/2026-07-08-0514-novel-author-fix.md` (待写)
- 代码触点: `obsidian-journal/app/posts/[slug]/page.tsx:64,126`, `app/posts/page.tsx:137`, `lib/feed.ts:91`
- DB 触点: `obsidian-journal/data/dev.db` (SQLite)
