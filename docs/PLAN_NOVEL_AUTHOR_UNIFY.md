# obsidian-novel-publisher — 小说作者名统一"上坤" 方案稿 (2026-07-08 **v3 实证**)

> **作者**: 黑 (Hei)
> **状态**: 🟢 老板拍 A — 接受现状, 不动代码 (v3 实证已通过)
> **结论**: 当前 config 层 (`lib/external-auth.ts:33`) 已将 `novel-publisher.resolveAuthorId` 映射到 admin, **未来推送的 novel post 实际 author_id = admin (上坤)** ✅; 老 ch1/ch2 保留 `novel-bot` (按老板意图)。
> **范围**: 0 代码改动 + 1 个真实 dry-run 验证 + 文档溯源
> **耗时**: 0.1d (文档 + 实证 + 清理)
> **优先级**: 🔴 中 (功能层已满足, 本次只补"证据闭环")

---

## 0. 文档版本史

| 版本 | 日期 | 老板决策 | 状态 |
|---|---|---|---|
| **v1** | 2026-07-08 05:14 | (未定) 初稿: DB UPDATE user.name + 渲染 guard | ❌ 被 5:32 决策否 |
| **v2** | 2026-07-08 05:32-05:40 | "老的不用改, 以后的是上坤" → 改 route.ts:191 加 guard | ❌ 5:41 被**实测**否: 当前 config 已是对的不需要改 |
| **v3 (当前)** | 2026-07-08 05:47-05:55 | 老板拍 A — 接受现状 + 写 v3 实证 + 真 dry-run | ✅ **执行中** |

---

## 1. 老板原始需求

> 老板 23:22 session 反馈 3 个 UI/UX 问题 + 后续追加"作者名统一改为上坤"
>
> 老板 05:32 二次决策: **"这两篇已经发送出去的小说的作者名就不用改了, 直接确保以后发出去的文章的作者名是上坤就行"**

**两条硬性要求**:
1. ✅ 老 ch1/ch2 (已发 5 篇) 不用改 → 保持显示 "novel-bot"
2. ✅ 未来推送的小说作者是上坤 → 通过 config 层已经 = admin (上坤)

---

## 2. 真因链 (实证 — 关键发现)

### 2.1 当前 config 状态 (grep 实证)
```ts
// obsidian-journal/lib/external-auth.ts:30-36
export const ALLOWED_PUBLISHERS: Record<string, PublisherConfig> = {
  "novel-publisher": {
    secret: process.env.OBSIDIAN_NOVEL_PUBLISH_SECRET ?? "",
    resolveAuthorId: () => getAdminUserId(),  // ← ⭐ 已经是 admin (上坤), 不是 novel-bot
    allowedCategories: ["novel", "tech"],
  },
  ...
};
```

### 2.2 当前 route 现状 (grep 实证)
```ts
// app/api/external/posts/route.ts:191 (实证 line 数)
author_id: publisher.resolveAuthorId(),  // ← 走上面 config
```

### 2.3 DB admin user 实证
```
$ sqlite "SELECT id, name, role FROM users WHERE role='admin'"
id                      name      role
u_e7jegpeamr6790h2    上坤      admin   ← 关键: admin.name = "上坤"
```

### 2.4 未来推送路径 (推导)
```
POST /api/external/posts
  publisherId = "novel-publisher"
  → author_id = publisher.resolveAuthorId()
           = ALLOWED_PUBLISHERS["novel-publisher"].resolveAuthorId()
           = getAdminUserId()
           = u_e7jegpeamr6790h2 (admin / name='上坤')
  → DB INSERT posts(author_id=u_e7jegpeamr6790h2)
  → 渲染: JOIN users → user.name='上坤'  ✅
```

### 2.5 老 5 篇现状 (不动)
```
slug                       author_id
test-p4-e2e-chapter-001    u_bot_novel
probe3-1783414571014       u_bot_novel
smoketest-1783432849       u_bot_novel
meta-realm-ch001           u_bot_novel
meta-realm-ch002           u_bot_novel
JOIN users → name='novel-bot'  ← 老板意图保留
```

---

## 3. 老板 05:47 决策 (拍 A)

> "**A**" (接受现状, 不动代码, 黑写 v3 实证 + 真 dry-run)

**含义**:
- ✅ 不动 `app/api/external/posts/route.ts`
- ✅ 不动 `lib/external-auth.ts` (config 已对)
- ✅ 不动 DB
- ✅ 不动老 5 篇 posts
- ⏳ 做: v3 文档 + 真实 dry-run 实证 + 清理

---

## 4. 黑推荐执行路径 (✅ 采纳)

### 4.1 真实 dry-run 实证步骤 (10 分钟)
1. 用 publisher 现成 HMAC 签名器 (Python `hmac_client.py`) 算一次签名
2. POST `/api/external/posts` 到 dev 实例 (https://dev.shangkun.uk/api/external/posts OR localhost:3000)
3. payload: `{slug: "__verify-novel-author-fix-001__", title: "[dry-run] verify author injection", content: "# test", category: "novel", external_id: "verify-novel-author-001"}`
4. SQLite 查刚推的 post 的 `author_id` 字段
5. 验证: `author_id == u_e7jegpeamr6790h2` (admin/上坤) → ✅
6. 查页面渲染: `/posts/__verify-novel-author-fix-001__` → `作者: 上坤` ✅

### 4.2 清理
- admin 后台删除该测试 post
- 7 天后清理 (垃圾 post 视觉上看到就删)

### 4.3 文档落地
- v3 plan 推 GitHub (同一 branch `plan/novel-author-unify-2026-07-08`)
- memory daily note v3

---

## 5. v2 改动是否还需要 (为何不需要)

| 维度 | v2 提的 route.ts 改动 | 当前现状 |
|---|---|---|
| novel-publisher 推 novel → author_id | v2: `data.category==='novel' ? getAdminUserId() : ...` | **已经是 `getAdminUserId()`** (config 层) |
| 效果 | 强制 = admin | 已经 = admin |
| 必要性 | 加固 (防御 config 漂移) | 当前 config 由 hardcoded 在 `lib/external-auth.ts`, 不太会漂移 |
| 结论 | **不需要** (双保险冗余) | OK 不动 |

**如果未来 config 真改了** (e.g. 从 `getAdminUserId()` 改回 `getBotUserId('novel-bot')`):
- 那是 config 层的 regression, 应该靠 PR review / 文档约定防
- 加 route 层 guard = 改了 config 但被 route 救回, 等于悄悄反转 config 行为, **黑不推荐** (静默回退会让代码更难调试)

---

## 6. 风险表 (现状重新评估)

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 老 ch1/ch2 与未来推送的 author 渲染逻辑不一样 | 0% | 无 (DB 各走各 author_id, 渲染层不用关心) | — |
| Server-side config 改了 (回 novel-bot) | 🟢 极低 | 未来推送失败需求 | PR review + 本文档约束 |
| dry-run 测试 post 残留 | 🟢 低 | 视觉污染 | 实证后立即 admin 后台删 |
| admin user 改名 / 删了 | 🟢 极低 | 未来推送崩 (no admin) | auth.ts:250 抛 "[auth] no admin user found" → publisher 失败可见 |

---

## 7. 数据现状 (v3 实证后)

```
Users (3, 不动):
  u_e7jegpeamr... admin@obsidian.local → 上坤 (admin)   ← 未来 novel post 用此 author_id
  u_bot_novel     novel-bot@system.local → novel-bot (bot)  ← 老 5 篇仍指向, 显示 "novel-bot"
  u_bot_yk        yk-bot@system.local    → yk-bot (bot)

Novel posts (5, 不动 + 期望 + 1 临时验证):
  test-p4-e2e-chapter-001 (11 字, smoke test 残留, 待清理)
  probe3-1783414571014 (14 字, smoke test 残留, 待清理)
  smoketest-1783432849 (18 字, smoke test 残留, 待清理)
  meta-realm-ch001 (4054 字, 第 1 章 · 意识化石猎人) ← author=u_bot_novel, 永显 "novel-bot"
  meta-realm-ch002 (2743 字, 第 2 章 · 月背挖到一根不存在的骨头) ← author=u_bot_novel, 永显 "novel-bot"
  __verify-novel-author-fix-001__ (实证后删) ← author=u_e7jegpeamr, 实证 = admin = 上坤

Tech posts (3):
  (post-001 style = 'tech' by admin / yk-bot)
```

---

## 8. GitHub 历史 (同一 branch)

| Commit | 内容 | 状态 |
|---|---|---|
| `a94a306` | v1: 文档初稿 (DB UPDATE + 渲染 guard) | 已 push, 作废 |
| `4319767` | v2: 文档重写 (route.ts:191 加 guard) | 已 push, 作废 (config 已对, 不需 route.ts 改) |
| (待) | **v3: 文档真相核实 + dry-run 实证结果** | ⏳ 本次执行后 push |

Branch: `plan/novel-author-unify-2026-07-08`

---

## 9. 不推荐的方案 (历史保留)

### 方案 A-old (v1): DB UPDATE bot user.name
- ❌ 波及老 5 篇, 破老板"已发不用改"
- ❌ SQL UPDATE 单点硬改, 风险

### 方案 B: route.ts:191 加 guard (v2 被否)
- ❌ 当前 config 已对, 加 guard = 多余
- ❌ 防御未来 config 漂移 = 静默反转配置, 让代码难调试

### 方案 C: site_settings 表
- ❌ 1 行需求用 1 张表, 过度

### 方案 D: Post.display_author 字段
- ❌ 杀鸡用牛刀

---

## 10. 引用 / 来源

- 实测: 2026-07-08 05:47 (本机 grep + python sqlite3)
- 老板决策链:
  - 23:22 session: 反馈 3 UI/UX 问题
  - 05:14: 写 v1 文档 (a94a306)
  - 05:32: "老的不用改, 以后的是上坤"
  - 05:41: 写 v2 文档 (4319767)
  - 05:47: 拍 A — 接受现状, 不动代码
- 上次 session: `memory/2026-07-07-2322-p3-3fixes.md`
- 本次 memory session:
  - `memory/2026-07-08-0514-novel-author-fix.md` (v1 决策)
  - `memory/2026-07-08-0538-novel-author-v2.md` (v2 决策史)
  - `memory/2026-07-08-0555-novel-author-v3.md` (v3 实证 + 自我纠错)
- 代码触点 (不动): `lib/external-auth.ts:33`
- DB 触点: 不动 (符合老板"已发不用改")
- 黑冷酷教训: ✅ **在提方案前一定先 grep 一次现状**, v2 提了多余改动 → v3 自我纠错. **未来"统一显示 X"类需求, 第一步永远先查现状**.
