# obsidian-novel-publisher — 小说作者名统一"上坤" 方案稿 (2026-07-08 v2)

> **作者**: 黑 (Hei)
> **状态**: ⏸️ 等老板 Q1-Q2 拍板开 P0
> **范围**: server-side author 强制注入 (不改老数据, 只管未来推送)
> **耗时预估**: < 0.1d (1-2 处 route 改动 + 1 个 spec)
> **优先级**: 🔴 中 (不影响推送链路, 仅影响未来推送的小说显示文字)
> **文档版本**: v2 (老板 2026-07-08 05:32 决策转弯后重写)

## 0. 老板决策更新 (2026-07-08 05:32)

> "**这两篇已经发送出去的小说的作者名就不用改了, 直接确保以后发出去的文章的作者名是上坤就行。**"

**核心转向**:
- ❌ 不动老 5 篇 posts (含 ch1/ch2)
- ❌ 不改 DB `users.name`
- ❌ 不加渲染层 guard (不需要 — 老数据不动意味着 ch1/ch2 永远显示 'novel-bot', 不需要补救)
- ✅ Server-side `/api/external/posts` 强制注入:future 推 novel-category post → author_id = admin user (上坤)

---

## 1. 背景

### 1.1 老板原话 (2026-07-07 23:22 那轮反馈 3 个问题)
> 老板看 www.shangkun.uk 反馈:
> 1. 小说放到了"文章"列表, 没放到"小说"列表
> 2. 封面图片显示 broken icon
> 3. 中文标点是 ASCII 半角
> 4. (新增) 小说作者名统一改为"上坤"

(老板在 2026-07-08 05:07 修正 typo "图片加载视频" → "图片加载失败", 并新增第 4 条)

### 1.2 2026-07-08 05:07 现场实测 (5 篇 novel posts)

| # | 问题 | 状态 | 现场证据 |
|---|---|---|---|
| 1 | 小说放错页面 | ✅ **已修** | DB 5 篇全 `category='novel'`;页面 tag `novel`;JSON-LD `articleSection: novel`;`/novels` 200 |
| 2 | 图片加载失败 | ✅ **已修** | ch1/ch2 封面 HTTP 200 (本地 + 公网双验) |
| 3 | 中文标点半角 | ✅ **已修** | 上次 session 完美交付 |
| 4 | 作者名改"上坤" | ⏸️ **本次待修 (只动未来推送)** | 老 5 篇维持显示 `novel-bot` (按老板意图);未来推送走 server-side 注入 |

### 1.3 老板 05:32 二次决策
> "**这两篇已经发送出去的小说的作者名就不用改了, 直接确保以后发出去的文章的作者名是上坤就行。**"

---

## 2. 真因诊断

### 2.1 DB 现状 (实测, 不动)
```sql
Users (3):
  u_e7jegpeamr... admin@obsidian.local → '上坤' (admin)   ← 未来注入用这个 id
  u_bot_novel     novel-bot@system.local → 'novel-bot' (bot)  ← 老 5 篇 author_id
  u_bot_yk        yk-bot@system.local → 'yk-bot' (bot)

Posts (5 篇 novel, 全 author_id=u_bot_novel, 不动):
  test-p4-e2e-chapter-001 (11 字 smoke 残留)
  probe3-1783414571014 (14 字 smoke 残留)
  smoketest-1783432849 (18 字 smoke 残留)
  meta-realm-ch001 (4054 字)
  meta-realm-ch002 (2743 字)
```

### 2.2 当前推送链路 (ch1/ch2 走的路径)
```
obsidian-novel-publisher (systemd timer)
   ↓ POST /api/external/posts (HMAC 鉴权)
   ↓ body: { slug, title, content, category, external_id, ... }
   ↓
obsidian-journal/app/api/external/posts/route.ts:191
   author_id: publisher.resolveAuthorId()  ← 当前是 getBotUserId("yk-bot") = u_bot_novel
   ↓
DB INSERT INTO posts (..., author_id, ...)
```

### 2.3 改造点 (改造仅一处, 老数据根本不动)
- 老 5 篇 posts 已在 DB, author_id = u_bot_novel, 任何改造都不会回溯 modify
- 改造:`route.ts:191` 在 store 前判断 category, novel 强制映射到 admin user id

---

## 3. 推荐方案: Server-side 强制注入 (黑推荐, ≤ 0.1d)

**核心思路**: 在 `/api/external/posts` 入口端, store 前判断 category, novel 强制映射到 admin user id (上坤)。老数据 (ch1/ch2) 完全不动, 未来推送自动归一。

### 3.1 改动清单 (1-2 个 route 文件 + 5 行代码 + 1 个 spec)

#### 改动 #1 (主): app/api/external/posts/route.ts (2 行 guard)
```tsx
// 现有 (line 191)
author_id: publisher.resolveAuthorId(),

// 改为
const authorId =
  data.category === 'novel'
    ? getAdminUserId()  // 强制注入 admin (上坤), 不动老数据
    : publisher.resolveAuthorId();
author_id: authorId,
```

**精准语义**:
- 老 ch1/ch2 (DB 已有 row) → **完全不动**, 它们的 author_id 仍 = `u_bot_novel`, 渲染仍 = `novel-bot` ✅ (符合老板"已发的不用改")
- 未来推送的 novel-category post → author_id 写入 = admin user (`u_e7jegpeamr...`), 渲染 = `上坤` ✅
- 未来推送的 tech/life post → 维持 `publisher.resolveAuthorId()` (保持原有 bot user) ✅

#### 改动 #2 (Q2 可选): app/api/external/chapters/route.ts
```ts
// chapters 路由也是 publisher 入口, 为保持一致也加 guard
// 注意: chapters schema 当前无 author_id 字段 (chapters 表没 author_id)
// 当前不需要真改 (改了无效果), 仅留作接口设计一致性建议
// 若未来加 chapter.author_id, 同样 guard. Q2 拍板后再说.
```

#### 改动 #3: tests/e2e/external-posts-novel-author.spec.ts (新)
```ts
test('novel-category 推送的 author_id 强制为 admin (上坤)', async ({ request }) => {
  // HMAC + payload 模拟 publisher 推一篇 novel post
  const r = await request.post('/api/external/posts', {
    headers: { /* x-publisher-id + x-timestamp + x-signature */ },
    data: { 
      slug: 'test-novel-author-fix-001', 
      title: '测试 author_id 强制', 
      content: '# 测试\\n\\n内容...',
      category: 'novel',
      external_id: 'test-author-fix-001'
    }
  });
  expect(r.ok()).toBeTruthy();
  
  // DB 查刚推的 post 的 author_id
  const post = db.prepare("SELECT author_id FROM posts WHERE slug = ?").get('test-novel-author-fix-001');
  expect(post.author_id).toBe(getAdminUserId());  // 强制为 admin (上坤)
});

test('tech-category 推送的 author_id 维持 publisher 默认', async ({ request }) => {
  // 推送 tech category, 应该仍走 publisher.resolveAuthorId() (yk-bot)
  ...
  expect(post.author_id).toBe(getBotUserId('yk-bot'));
});
```

### 3.2 为什么不改老数据 (符合老板 5:32 决策)
| 老 ch1/ch2 | 改 vs 不改 |
|---|---|
| 老 5 篇 posts 的 `author_id` | 保持 = `u_bot_novel` (不动) |
| 老 5 篇 `users.name` (u_bot_novel) | 保持 = `novel-bot` (不动) |
| 老 5 篇渲染显示 | 仍 = `novel-bot` (符合"已发的不用改") |

### 3.3 为什么不做方案 A 的渲染层 guard
- **不需要**: 老数据不动, 未来数据 server-side 强制, 渲染层永远拿到的就是正确 author
- **冗余**: server-side 注入 + 渲染 guard = 双保险但过度
- **黑推荐**: 单层 server-side 注入, 渲染层保持纯净 (`post.author.name ?? post.author.email` 不动)

---

## 4. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 老 ch1/ch2 与未来推送的渲染逻辑走两条路径 | 0% | 无 (DB 各走各 author_id, 渲染层不用关心) | — |
| Server-side 注入 admin user, future admin 改名后泄露 | 🟢 极低 | author 变化影响所有未来小说 | 留后续 task; 当前冻结 admin.name='上坤' |
| chapters 路由忘了加 (Q2 决定后再说) | 🟢 低 | 当前 publisher 走 posts 路径, 无影响 | Q2 决定; 当前 publisher 走 posts 路径 |
| 已有 novel-post 想 chang author_id | 中 | 改老数据破"已发的不用改"原则 | 不做, 必须改得人工手动 + 老板命令 |
| playwright spec 模拟 publisher HMAC 失败 | 低 | spec 自身的复杂度 | 用现有 helper fixture |

---

## 5. 不推荐的备选方案

### 方案 A-old: DB UPDATE bot user.name (v1 被否)
- 改 `users SET name='上坤' WHERE id='u_bot_novel'` + 渲染 guard
- ❌ **被老板 5:32 否**: 老 5 篇 posts 也会被波及显示'上坤'
- 教训: 决策方向变了, 旧方案保留作历史参考

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

## 6. 数据现状 (不动, 仅记录)

```
Users (3, 不动):
  u_e7jegpeamr... admin@obsidian.local → 上坤 (admin)   ← 未来 novel post 用此 author_id
  u_bot_novel     novel-bot@system.local → novel-bot (bot)  ← 老 5 篇仍指向它, 显示 "novel-bot"
  u_bot_yk        yk-bot@system.local    → yk-bot (bot)

Novel posts (5, 不动):
  test-p4-e2e-chapter-001 (11 字, smoke 残留, 待清理)
  probe3-1783414571014 (14 字, smoke 残留, 待清理)
  smoketest-1783432849 (18 字, smoke 残留, 待清理)
  meta-realm-ch001 (4054 字, 第 1 章 · 意识化石猎人) ← author=u_bot_novel, 永显 "novel-bot"
  meta-realm-ch002 (2743 字, 第 2 章 · 月背挖到一根不存在的骨头) ← author=u_bot_novel, 永显 "novel-bot"

Tech posts (3):
  (post-001 style = 'tech' by admin / yk-bot)

**全部 5 篇老 novel posts 永远显示 "novel-bot", 老板 5:32 决策已发不用改.**
```

---

## 7. 等老板拍板 (v2)

| Q# | 决策项 | 候选 | 黑推荐 |
|---|---|---|---|
| **Q1** | Server-side 强制注入确认 (方案 = 当前 §3) | 是 / 否 | **是** |
| **Q2** | chapters route (app/api/external/chapters/route.ts) 是否一并加 guard | 是 (一致性) / 否 (chapters schema 当前无 author 字段, 加了也是预留) | **否** (chapters 当前无 author 字段, 不需要加; 等 schema 改了再说, 否则是浪费测试) |
| **Q3** | 部署窗口 | 立即 / 等指令 | **等我命令** (老板拍板才动) |

**Q1-Q2 拍板后, < 15 分钟我交付**:
- 改 1 个 route 文件 (route.ts:191 主, 2-5 行)
- 1 个 playwright spec (10min)
- lint + typecheck + build 全绿
- 推 main → CI 跑过 → 部署 obsidian-dev
- 触发一次 dry-run 让 publisher 真推一篇 novel 章节, 截图给你看:
  - 未来 novel post → `作者: 上坤` ✅
  - 老 ch1/ch2 → 仍是 `作者: novel-bot` (保留不动)
  - tech post → 仍是 `作者: 上坤` / `yk-bot` (admin 推 = 上坤, yk-bot 推 = yk-bot)

---

## 8. 引用 / 来源

- 实测时间: 2026-07-08 05:07-05:14 (本机 curl + sqlite3 + grep)
- 老板决策: 2026-07-08 05:32 ("已发不用改, 以后发的作者是上坤")
- 上次 session: `memory/2026-07-07-2322-p3-3fixes.md` (老板原 3 + #0 defer)
- 本次 session: `memory/2026-07-08-0514-novel-author-fix.md` (5:14 第一版) + `memory/2026-07-08-0538-novel-author-v2.md` (v2 更新)
- 代码触点 (改动): `obsidian-journal/app/api/external/posts/route.ts:191`
- DB 触点: 不动 (符合老板"已发不用改")
- 黑冷酷教训: ✅ **老板偏好转弯时, 不要坚持上一版方案**. 先吃决策, 重写方案, 再 commit. 文档版本 v1 → v2, 不要 silently amend, 留历史.
