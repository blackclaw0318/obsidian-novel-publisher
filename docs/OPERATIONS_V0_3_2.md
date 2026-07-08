# OPERATIONS v0.3.2 — obsidian-novel-publisher 运维手册

> **本项目 (publisher) 的运维:systemd / 监控 / 升级 / 故障处理**
> 区别于 `RUNBOOK.md` (老板手动操作) — 本文件是系统维护视角。

---

## 1. 部署拓扑

```
┌──────────────────────────────────────────────────────────────┐
│ 本机 (lavm-8h9bp4442f)                                       │
│                                                              │
│  ┌────────────────────────┐    ┌──────────────────────────┐ │
│  │ systemd                │    │ systemd                  │ │
│  │ novel-publisher.timer  │───▶│ novel-publisher.service   │ │
│  │ (8/12/18 daily)        │    │ python -m src.publisher  │ │
│  └────────────────────────┘    │   --all                  │ │
│                                └──────────┬───────────────┘ │
│                                           │                  │
│  ┌────────────────────────────────────────▼───────────────┐ │
│  │ .env (凭据, gitignored)                                 │ │
│  │   MINIMAXI_API_KEY                                      │ │
│  │   OBSIDIAN_PUBLISH_ID/SECRET/URL/ADMIN_TOKEN/BASE_URL   │ │
│  │   GITHUB_BACKUP_REPO/TOKEN                              │ │
│  │   PUBLISH_STATE_PATH (默认 data/state/)                 │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ data/state/         # per-novel state (P0 拆开)     │    │
│  │   a_obsidian.json                                    │    │
│  │   b_obsidian.json                                    │    │
│  │ data/cache/         # outline/style_guide/characters │    │
│  │   *.sha256                                          │    │
│  │ logs/                                                │    │
│  │   publisher.log                                      │    │
│  └─────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
         │                                      │
         ▼                                      ▼
┌────────────────────┐              ┌────────────────────────┐
│ minimax API        │              │ obsidian-journal       │
│ api.minimaxi.com   │              │ dev.shangkun.uk        │
│ M3 文本 + image-01 │              │ api/external/chapters  │
└────────────────────┘              │ + admin /media (上传)  │
                                    └────────────────────────┘
         │
         ▼
┌────────────────────────┐
│ GitHub backup 仓       │
│ obsidian-novel-backups │
│ main 分支              │
└────────────────────────┘
```

---

## 2. systemd 服务

### 2.1 service 文件 (`/etc/systemd/system/novel-publisher.service`)

```ini
[Unit]
Description=obsidian-novel-publisher (multi-novel daily push)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/root/.openclaw/workspace/projects/obsidian-novel-publisher
EnvironmentFile=/root/.openclaw/workspace/projects/obsidian-novel-publisher/.env
ExecStart=/root/.openclaw/workspace/projects/obsidian-novel-publisher/.venv/bin/python -m src.publisher --all
StandardOutput=journal
StandardError=journal
# 硬时限 (P2 加的, 防 publisher hang)
TimeoutStartSec=600
# 失败告警交 journal, 由外部 watcher 拉

[Install]
WantedBy=multi-user.target
```

### 2.2 timer 文件 (`/etc/systemd/system/novel-publisher.timer`)

```ini
[Unit]
Description=obsidian-novel-publisher daily 8/12/18

[Timer]
OnCalendar=*-*-* 08:00:00
OnCalendar=*-*-* 12:00:00
OnCalendar=*-*-* 18:00:00
AccuracySec=30s
Persistent=true
Unit=novel-publisher.service

[Install]
WantedBy=timers.target
```

### 2.3 安装

```bash
sudo cp scripts/install-systemd.sh /tmp/
sudo bash /tmp/install-systemd.sh
# 或手动:
sudo cp systemd/novel-publisher.service /etc/systemd/system/
sudo cp systemd/novel-publisher.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now novel-publisher.timer
sudo systemctl status novel-publisher.timer
```

### 2.4 常用命令

```bash
# 看定时器下次触发
systemctl list-timers novel-publisher.timer

# 手动跑一次 (不等定时)
sudo systemctl start novel-publisher.service

# 看日志
journalctl -u novel-publisher -n 100 --no-pager

# 实时跟
journalctl -u novel-publisher -f

# 停掉 (保留 state)
sudo systemctl stop novel-publisher.timer

# 彻底关
sudo systemctl disable --now novel-publisher.timer
```

---

## 3. 监控

### 3.1 关键指标 (老板视角每日 23:30 看)

| 指标 | 看哪 | 健康值 |
|---|---|---|
| 今日推送数 | `journalctl -u novel-publisher --since today \| grep "✓ 推送成功"` | 3 × N 本 (如 6 = 2 本 × 3 档) |
| 今日失败数 | `journalctl ... \| grep "✗"` 或 `state.last_status=failed` | 0 |
| 配额命中率 | `journalctl ... \| grep "本档.*已推过, 跳过"` | 0 (定时器正常就是 0) |
| image-to-image 启用 | `journalctl ... \| grep "image-to-image 启用"` | 仅 idx ≥ 2 |
| last_run 距今 | `stat data/state/*.json` | < 6h |

### 3.2 异常告警 (黑建议加, v0.3.3 实施)

- ❌ `state.last_status=failed` 连续 2 次 → 告警 (老板手机)
- ❌ 24h 内 0 次 "✓ 推送成功" → 告警 (定时器 / 服务挂了)
- ❌ `last_pushed_slot` 24h 未更新 → 告警
- ❌ `last_error` 含 "401" / "403" / "signature" → 凭据过期告警 (P0)

告警通道:
- 企业微信 webhook (复用 xhs-novel-bot 的群机器人) — P2 I.3
- obsidian-journal admin 页 (内部 dashboard) — v0.3.3

### 3.3 健康检查脚本

```bash
bash scripts/healthcheck.sh
# 检查 6 项:
# 1. .env 关键字段非占位符
# 2. systemd timer active
# 3. systemd service 最近 24h 跑过
# 4. data/state/*.json 全部 valid JSON
# 5. obsidian dev 服务可达
# 6. minimax API 可达 (curl https://api.minimaxi.com/v1/models)
```

---

## 4. 升级流程

### 4.1 v0.3.2 P0-P4 已落地,下次升级 (v0.3.3) 流程

```bash
# 1. 停 systemd (保 state)
sudo systemctl stop novel-publisher.timer

# 2. 拉新代码
cd /root/.openclaw/workspace/projects/obsidian-novel-publisher
git fetch origin
git pull origin main

# 3. 看 CHANGELOG, 跑 dry-run 验
cat CHANGELOG.md | head -50
bash scripts/dry-run.sh --all --no-github   # 不污染 backup

# 4. dry-run 成功,启 systemd
sudo systemctl start novel-publisher.timer
systemctl status novel-publisher.timer
```

### 4.2 schema 升级 (DB 不在 publisher 端, 但 state 文件格式可能变)

- v0.3.2 state schema_version=1
- 后续 v0.4.x 加 `schema_version=2`, 加载时自动迁移 (`state_per_novel._migrate_if_needed`)
- 老板侧: 升级前 `cp -r data/state data/state.v3.2.backup`

---

## 5. 故障处理 SOP

### 5.1 凭据过期

**症状**: journalctl 报 `401 Unauthorized` 或 `HMAC signature mismatch`

**修法**:
1. 老板提供新 key (minimax / obsidian / GitHub 任一)
2. 黑改 `.env`
3. 跑 `bash scripts/dry-run.sh --all --no-github --no-obsidian` 验凭据生效
4. 启 systemd

### 5.2 obsidian dev 挂

**症状**: publisher 报 `obsidian connection refused` 或 502

**修法**:
1. 看 obsidian dev: `systemctl status obsidian-dev`
2. 启: `sudo systemctl restart obsidian-dev`
3. 等 30s, 再手动跑: `sudo systemctl start novel-publisher.service`

### 5.3 minimax quota 用尽

**症状**: publisher 报 `429 rate limit` 或 `quota exceeded`

**修法**:
1. 老板充钱 / 升 quota
2. 临时降频: `cron` 改 1/天 (08:00) 或停 `novel-publisher.timer`
3. quota 恢复后改回

### 5.4 state 损坏

**症状**: publisher 报 `JSONDecodeError` 加载 state

**修法**:
```bash
# 1. 看哪个文件坏
for f in data/state/*.json; do
  python3 -c "import json; json.load(open('$f'))" 2>&1 | grep -q Error && echo "BAD: $f"
done

# 2. 备份坏的
cp data/state/b_obsidian.json /tmp/b_obsidian.json.broken

# 3. 删了, 下次跑会重建 (从 idx=1 开始)
rm data/state/b_obsidian.json
```

⚠️ **风险**: b_obsidian 重建后 next_idx=1, 但 obsidian 端 ch-1 已在 (idempotency_key 也丢了,可能重复入库)
- 黑推荐: 先用 `git log -- data/state/` 找最近好的 state 还原
- 兜底: 用幂等保护, 同 content + 同 novel_id 二次推会被 obsidian 拒

---

## 6. 关键路径速查

| 数据 | 路径 |
|---|---|
| 凭据 (gitignored) | `projects/obsidian-novel-publisher/.env` |
| 多本 state | `projects/obsidian-novel-publisher/data/state/<novel_id>.json` |
| 单本 state (兜底) | `projects/obsidian-novel-publisher/data/state.json` |
| Cache (outline/sg/characters sha) | `projects/obsidian-novel-publisher/data/cache/` |
| 临时封面 | `projects/obsidian-novel-publisher/data/covers/` |
| 日志 | `journalctl -u novel-publisher` |
| systemd unit | `/etc/systemd/system/novel-publisher.{service,timer}` |
| 一键脚本 | `scripts/{install-systemd,uninstall-systemd,dry-run,healthcheck}.sh` |
| 文档 | `docs/{PLAN,API_INTEGRATION,RUNBOOK,OPERATIONS_V0_3_2,REFACTOR_PLAN_V3_MULTI_NOVEL,PLAN_NOVEL_AUTHOR_UNIFY}.md` |

---

## 7. 与 xhs-novel-bot 的运维对比

| 项 | xhs-novel-bot | obsidian-novel-publisher |
|---|---|---|
| 部署位置 | 远程生产机 | 本机 (lavm-8h9bp4442f) |
| systemd 服务名 | `xhs-novel-bot` | `novel-publisher` |
| 频率 | 2/天 → ⛔ 暂停 | 3/天 (8/12/18) |
| 风控 | 严 (账号被封过) | **无** (自家博客) |
| 凭据敏感度 | XHS cookies | minimax + GitHub + obsidian HMAC |
| 状态复杂度 | 1 个 state.json | N 个 per-novel state (P0 拆) |
| 配额机制 | 无 | 有 (slot_already_pushed) |
| 备份 | 无 (cookies 失效就废) | GitHub per-chapter |

**核心差异**: publisher 是无风控环境, 老板可放开跑, 重点在 LLM 成本控制 + per-novel 配额 + 凭据管理。

---

## 8. 沉淀教训 (v0.3.2 期间)

- ⚠️ **publisher 必须是无风控环境** — 不能套用 xhs 的"降速 / 去模板"策略
- ⚠️ **per-novel state 拆分必须用 `data/state/<id>.json` 而非 1 个聚合文件** — 避免 N 本串扰
- ⚠️ **mark_skipped 推进 next_idx 是设计选择** — skip_next 本质消耗一次, slot_already_pushed 也走这逻辑 (v0.3.2 已验证)
- ⚠️ **mock 测试不要嵌套 with patch** — 一层抛错, 外层 patch 还原顺序乱, 全部用 monkeypatch.setattr
- ⚠️ **集成测的 mock 函数签名要严格匹配真实调用顺序** — `_post_with_sig(url, body, sig_headers, *, raw_body=None)`, keyword-only 参数不能漏
- ⚠️ **dry-run.sh 必须隔离 state** — 否则污染正式 data/state/, 老板 systemd 自动跑会乱套
- ⚠️ **多本 dry-run 的 state 隔离用"备份 + 还原"** — state_per_novel 模块的 DEFAULT_STATE_DIR 是常量, env var 改不了

---

## 9. 老板决策清单 (升级 v0.4 前必拍)

- [ ] Q1: 是否要加 admin 后台 (publisher 状态 + 重推按钮)?
- [ ] Q2: 是否要接企业微信告警 (复用 xhs 的 webhook)?
- [ ] Q3: 是否要 GitHub backup 加密 (避免 .md 在公仓暴露)?
- [ ] Q4: 是否要加测试覆盖率门槛 (CI fail-under=80%)?
- [ ] Q5: 是否要把 dry-run 改成 GitHub Action (PR 触发自动验)?

(详见 `docs/PLAN.md` § 后续规划)

---

## 10. 相关文档

- `RUNBOOK.md` — 老板手动操作手册 (30 秒版)
- `PLAN.md` — 整体方案 + 决策清单
- `REFACTOR_PLAN_V3_MULTI_NOVEL.md` — v0.3.2 多本重构设计依据
- `PLAN_NOVEL_AUTHOR_UNIFY.md` — novel/author 拆分与统一
- `API_INTEGRATION.md` — 与 obsidian-journal 接口契约
- `CHANGELOG.md` — 版本变更记录
- `CI_SETUP.md` — GitHub Actions 配置