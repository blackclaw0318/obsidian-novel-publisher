# obsidian-journal 接收侧 API 契约

> **本仓库 (obsidian-novel-publisher) 作为消费方, 必须知道 obsidian-journal 服务端怎么接**
> 本文档描述了需要在 obsidian-journal 仓库新增的 `app/api/external/posts/route.ts` 接口契约

---

## 🎯 接口目标

允许受信任的外部 publisher (本仓库 + obsidian-yk-script) 通过 HMAC 鉴权, 把内容 POST 到 obsidian-journal 的 `posts` 表。

---

## 🔌 接口定义

### POST /api/external/posts

**Headers (必填)**:

| Header | 说明 | 示例 |
|---|---|---|
| `Content-Type` | `application/json` | `application/json` |
| `X-Publisher-Id` | 标识 publisher 来源 | `novel-publisher` / `yk-script` |
| `X-Publisher-Signature` | HMAC-SHA256 签名 (hex) | `a3f2b8...` |
| `X-Publisher-Timestamp` | Unix ms, 服务端校验 ±5 分钟 | `1717699200000` |

**签名算法**:

```
canonical_body = JSON.stringify(sort_keys(body), no_whitespace)
message = `${timestamp}.${canonical_body}`
signature = HMAC-SHA256(OBSIDIAN_PUBLISH_SECRET, message).hex()

Header: X-Publisher-Signature: <signature>
Header: X-Publisher-Timestamp: <timestamp>
```

**Request Body**:

```typescript
interface PublishBody {
  // 必填
  slug: string;                // URL 友好, 唯一, max 200
  title: string;               // 标题, max 200
  content: string;             // 完整 markdown 正文
  category: "tech" | "life" | "novel";  // 扩展 novel
  external_id: string;         // publisher 内唯一 id (幂等用)
  
  // 可选
  excerpt?: string;            // 摘要, max 500
  tags?: string;               // 逗号分隔
  cover_image?: string | null; // 封面 URL
  external_meta?: object;      // 任意元数据 (chapter_idx, season_no 等)
  
  // idempotency (失败重试安全)
  idempotency_key?: string;    // 同 key 不重复创建
}
```

**Response**:

| 状态码 | 含义 | Body |
|---|---|---|
| 201 | 创建成功 | `{ ok: true, post: { id, slug, url } }` |
| 200 | idempotency 命中 (已存在, 不重复创建) | `{ ok: true, post: {...}, deduplicated: true }` |
| 400 | 参数错误 | `{ ok: false, error: "missing_slug" \| "invalid_category" \| ... }` |
| 401 | 签名错误 / 时间戳过期 | `{ ok: false, error: "bad_signature" \| "timestamp_expired" }` |
| 403 | publisher 未授权 | `{ ok: false, error: "unknown_publisher" }` |
| 409 | slug 已存在 (但 idempotency_key 不同) | `{ ok: false, error: "slug_exists" }` |
| 429 | 速率限制 (10 req/min/IP) | `{ ok: false, error: "rate_limited" }` |
| 500 | 服务端错 | `{ ok: false, error: "internal" }` |

---

## 🛡️ 安全实现要点

### 1. HMAC 验签

```typescript
import { createHmac, timingSafeEqual } from "node:crypto";

function verifySignature(body: string, signature: string, timestamp: string, secret: string): boolean {
  // 1. 时间戳校验 (5 分钟窗口)
  const now = Date.now();
  const ts = parseInt(timestamp, 10);
  if (Math.abs(now - ts) > 5 * 60 * 1000) return false;
  
  // 2. 签名校验
  const expected = createHmac("sha256", secret)
    .update(`${timestamp}.${body}`)
    .digest("hex");
  
  // 3. timing-safe 比较 (防侧信道)
  return timingSafeEqual(
    Buffer.from(signature, "hex"),
    Buffer.from(expected, "hex")
  );
}
```

### 2. Publisher 白名单

```typescript
const ALLOWED_PUBLISHERS: Record<string, { secret: string; author_id: string; category: string[] }> = {
  "novel-publisher": {
    secret: process.env.OBSIDIAN_NOVEL_PUBLISH_SECRET!,
    author_id: "novel-bot",  // 系统虚拟账号, 无密码
    category: ["novel", "tech"],
  },
  "yk-script": {
    secret: process.env.OBSIDIAN_YK_PUBLISH_SECRET!,
    author_id: "yk-bot",
    category: ["life"],
  },
};
```

### 3. Rate Limit

```typescript
// 简单内存限流 (生产建议 Redis)
const rateLimitMap = new Map<string, number[]>();
function checkRateLimit(ip: string, limit = 10, windowMs = 60_000): boolean {
  const now = Date.now();
  const arr = (rateLimitMap.get(ip) ?? []).filter(t => now - t < windowMs);
  if (arr.length >= limit) return false;
  arr.push(now);
  rateLimitMap.set(ip, arr);
  return true;
}
```

### 4. Category 校验扩展

```typescript
// 原 admin/posts 只接受 tech / life, 扩展接受 novel
const VALID_CATEGORIES = ["tech", "life", "novel"];
if (!VALID_CATEGORIES.includes(body.category)) {
  return 400 "invalid_category";
}
```

### 5. Idempotency

```typescript
// 在 posts 表加 external_id 字段 (UNIQUE 索引)
ALTER TABLE posts ADD COLUMN external_id TEXT;
CREATE UNIQUE INDEX idx_posts_external_id ON posts(external_id);

CREATE UNIQUE INDEX idx_posts_idempotency ON posts(idempotency_key) 
  WHERE idempotency_key IS NOT NULL;
```

---

## 📁 obsidian-journal 侧需新增/改动

| 文件 | 改动 |
|---|---|
| `app/api/external/posts/route.ts` | **新增** ~150 LOC (HMAC + rate limit + idempotency) |
| `lib/db.ts` | `posts` 表加 `external_id` `idempotency_key` `external_meta` (JSON) 列 |
| `lib/repo.ts` | `postRepo.findByExternalId()` / `create()` 接受外部 author |
| `lib/auth.ts` | 新增 `botUserId: { novel-bot, yk-bot }` 虚拟账号创建 (无密码) |
| `app/api/external/posts/__tests__/route.test.mts` | 新增测试 |

---

## 🧪 测试用例

```typescript
// 1. 正常 POST (签名正确)
POST /api/external/posts
  X-Publisher-Id: novel-publisher
  X-Publisher-Signature: <valid hmac>
  X-Publisher-Timestamp: <now>
  Body: { slug: "...", title: "...", content: "...", category: "novel" }
→ 201 { ok: true, post: { id, slug, url } }

// 2. 签名错误
POST /api/external/posts
  X-Publisher-Signature: wrong
→ 401 { ok: false, error: "bad_signature" }

// 3. 时间戳过期
X-Publisher-Timestamp: <now - 10min>
→ 401 { ok: false, error: "timestamp_expired" }

// 4. 幂等
POST (same idempotency_key)
→ 200 { ok: true, post: {...}, deduplicated: true }

// 5. rate limit (11 次/分钟)
POST × 11
→ 第 11 次: 429 { ok: false, error: "rate_limited" }

// 6. category 非法
{ category: "spam" }
→ 400 { ok: false, error: "invalid_category" }
```

---

## 🔧 调试技巧

### publisher 侧生成签名 (curl 示例)

```bash
TIMESTAMP=$(date +%s%3N)
BODY='{"slug":"test","title":"Test","content":"Hello","category":"novel"}'
SECRET="change-me-32-bytes-random-hex"
SIGNATURE=$(echo -n "${TIMESTAMP}.${BODY}" | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $2}')

curl -X POST https://shangkun.uk/api/external/posts \
  -H "Content-Type: application/json" \
  -H "X-Publisher-Id: novel-publisher" \
  -H "X-Publisher-Timestamp: $TIMESTAMP" \
  -H "X-Publisher-Signature: $SIGNATURE" \
  -d "$BODY"
```

### 服务端日志

每次请求记录:
```
[external-posts] pub=novel-publisher sig=✓ ts=1717699200000 slug=test-123 idem=abc-456 → 201 (124ms)
```

---

*文档版本*: v0.1 (2026-07-05)
*作者*: 黑 (Hei)
*对应 obsidian-journal PR*: 待开发