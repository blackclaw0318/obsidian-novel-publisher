# RUNBOOK — obsidian-novel-publisher 老板操作手册

> **老板说"我要手动跑一次" → 看本手册**

## 30 秒版 (老板最快路径)

```bash
cd /root/.openclaw/workspace/projects/obsidian-novel-publisher
bash scripts/dry-run.sh --all    # 跑 novels.yaml 所有 enabled
```

看输出 ✅ + 去 https://dev.shangkun.uk/novels 验。结束。

---

## 详细版 — 4 种场景

### 场景 1: 改完 publisher.py / prompt, 要冒烟

```bash
bash scripts/dry-run.sh --all
```

跑前必看:
- ✅ `.env` 已填全 (MINIMAXI_API_KEY 真值, OBSIDIAN_PUBLISH_SECRET 真值)
- ✅ obsidian dev 服务在跑 (`systemctl status obsidian-dev`)
- ✅ 当前分支已 push (`git status` 干净)

跑完必看:
- ✅ exit code = 0
- ✅ `/tmp/publisher-dry-run-state.json` (单本) 或 `data/state/*.json` (多本) 显示 `last_status=success`
- ✅ obsidian dev 上 `/novels/<slug>` 有新章节

### 场景 2: 只验某一本书 (不动其他)

```bash
bash scripts/dry-run.sh --novel b_obsidian
```

(注: v0.3.2 CLI 暂未实装 `--novel` filter,目前等同于 `--all`。后续 v0.3.3 加)

### 场景 3: 只验 LLM + 封面, 不真推博客

```bash
bash scripts/dry-run.sh --all --no-obsidian
```

跑完会失败 (因为 OBSIDIAN_PUBLISH_URL 是 dummy), 但 LLM 和封面已落地,看:
- `ls /tmp/publisher-dry-run-covers/` 应有 jpg 文件
- `data/state/*.json` 的 `last_status=failed` 但 `last_pushed_idx=1` (失败的 idx 也记)

### 场景 4: 不走 GitHub 备份 (快 + 不污染 backup 仓)

```bash
bash scripts/dry-run.sh --all --no-github
```

适用:
- 调 prompt 时反复跑, 不希望 backup 仓被一堆 commit 淹没
- 周末试跑, 不希望主仓被无意义 commit 堆

---

## 进阶: 看 state 状态

```bash
# 单本 state
cat data/state/b_obsidian.json | python3 -m json.tool

# 多本批量看
for f in data/state/*.json; do
  echo "=== $f ==="
  cat "$f" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'  next_idx={d[\"next_idx\"]} last_status={d[\"last_status\"]} last_pushed_slot={d[\"last_pushed_slot\"]} cover_N={len(d.get(\"cover_urls\",{}))}')"
done
```

字段含义:
| 字段 | 含义 |
|---|---|
| `next_idx` | 下次要推的章节 idx (从 1 开始) |
| `last_pushed_idx` | 上次真推成功的 idx |
| `last_status` | `success` / `failed` / `skipped` |
| `last_error` | 失败原因 (failed 时有) |
| `last_pushed_slot` | 上次推的档位 (格式 `YYYY-MM-DD-HH`, 来自 `schedule.hours`) |
| `cover_urls` | idx → 公网 URL 的映射, 供下章 image-to-image 用 |
| `idempotency_keys` | idx → UUID, 防 obsidian 重复入库 |

---

## 故障排查 (老板视角)

### "publisher 报 LLM error"
1. 看 `.env` 的 `MINIMAXI_API_KEY` 是否还有效 (curl 验)
2. 看 minimax 余额 (老板的)
3. 改 prompt / 等 quota 重置

### "publisher 报 cover upload error"
1. 看 `.env` 的 `OBSIDIAN_ADMIN_TOKEN` 是否过期
2. 看 obsidian dev 是否在跑 (`systemctl status obsidian-dev`)
3. 看 `OBSIDIAN_ADMIN_BASE_URL` 是否对 (`https://localhost:3000` 不是 `https://dev.shangkun.uk`)

### "publisher 报 HMAC signature mismatch"
1. 老板和 publisher 用同一个 `OBSIDIAN_PUBLISH_SECRET` (不能跨 dev 环境复用)
2. 看 obsidian dev 端 `OBSIDIAN_PUBLISH_SECRET` env 是否一致
3. 时钟漂移 (本机 vs 生产机 5min+): 等下次自然漂回

### "publisher 跑成功但 obsidian 上看不到"
1. 看 obsidian dev 日志: `journalctl -u obsidian-dev -n 50`
2. 看 `OBSIDIAN_PUBLISH_URL` 是否对: 应是 `https://dev.shangkun.uk/api/external/chapters` (本机测试用 `http://localhost:3000/api/external/chapters`)
3. 看 idempotency_keys: 同 key 二次推会被 obsidian 拒 (幂等保护)

### "publisher 跑成功但 cover 图没显示"
1. 看 `cover_urls[<idx>]` 是否有公网 URL (应是 https://dev.shangkun.uk/...)
2. 看 obsidian 的 `media_items` 表: `sqlite3 data/dev.db "SELECT * FROM media_items ORDER BY created_at DESC LIMIT 5"`
3. 看 `OBSIDIAN_ADMIN_TOKEN` 是否有 media 写入权限

---

## 重置 state (老板说"全部重来")

```bash
# 备份当前
cp -r data/state data/state.backup-$(date +%Y%m%d)

# 清空
rm -rf data/state/*.json

# 下次跑会从 idx=1 重新开始
```

⚠️ **警告**: 清 state 后, 之前推过的章节会再次尝试推送。obsidian 端用 idempotency_key 保护, 不会重复入库, 但 GitHub backup 仓会多 commit。

---

## systemd 状态

```bash
systemctl status novel-publisher      # 主服务
systemctl list-timers | grep novel    # 3 次/天定时器
journalctl -u novel-publisher -n 100  # 最近 100 行日志
```

---

## 老板 0 决策快查表

| 老板说 | 黑做 |
|---|---|
| "今天别推了" | `bash scripts/dry-run.sh --skip-next` (v0.3.3 加) |
| "重推上次" | `bash scripts/dry-run.sh --force --chapter <N>` |
| "跑全部" | `bash scripts/dry-run.sh --all` |
| "看状态" | `cat data/state/*.json \| python3 -m json.tool` |
| "重置" | `rm -rf data/state/*.json` (慎!) |

---

## 相关文档

- `docs/PLAN.md` — 整体方案 (v0.3.2)
- `docs/OPERATIONS_V0_3_2.md` — 运维手册 (systemd / 监控 / 升级)
- `docs/API_INTEGRATION.md` — 与 obsidian-journal 的接口契约
- `docs/REFACTOR_PLAN_V3_MULTI_NOVEL.md` — v0.3.2 多本重构的设计依据