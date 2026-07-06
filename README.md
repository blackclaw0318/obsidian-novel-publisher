# obsidian-novel-publisher

> 每天 3 次自动生成中场篇科幻小说, 推送到 obsidian-journal 个人博客
> **状态**: 🚧 开发中 v0.2 (P0 仓库骨架已落地, P1-P7 待启动)

[![Status](https://img.shields.io/badge/status-proposal-yellow)]()
[![Python](https://img.shields.io/badge/python-3.12+-blue)]()
[![LLM](https://img.shields.io/badge/LLM-MiniMax--M3-orange)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

---

## 🎯 这是什么

把 [xhs-novel-bot](https://github.com/blackclaw0318/xhs-novel-bot) 里已经调优好的
"中场篇科幻小说"生成 skill 整建制搬过来, **把推送目的地从 XHS 改成老板的个人博客
[obsidian-journal](https://github.com/blackclaw0318/obsidian-journal)**。

每天 3 次（08:00 / 12:00 / 18:00）自动写一章 3000 字中篇科幻 + 配封面图, 通过 HMAC 鉴权
直接 POST 到博客 `posts` 表的 `category='novel'`, 老板打开网站就能看到新章节。每章节同时
备份到私有 GitHub 仓库 `obsidian-novel-backups`。

---

## 📦 依赖关系

```
┌─────────────────────────────┐
│  obsidian-novel-publisher   │  ← 本仓库 (推送器)
│  (Python 3.12, cron 调度)   │
└──────────────┬──────────────┘
               │ HTTPS POST
               │ HMAC-SHA256 鉴权
               ▼
┌─────────────────────────────┐
│  obsidian-journal           │  ← 博客 (接收方)
│  (Next.js 14, posts 表)     │
│  app/api/external/posts     │  ← 本仓库需新增
└─────────────────────────────┘

同时复用:
┌─────────────────────────────┐
│  xhs-novel-bot              │  ← 资产来源 (暂停中)
│  (Python, prompt 模板)      │
└─────────────────────────────┘
```

---

## 🚦 状态

- [x] **v0.1 方案稿** (2026-07-05) — 本 README
- [ ] 等老板拍 Q1-Q8 决策
- [ ] v0.2 P0-P6 实施 (~4d)
- [ ] v1.0 上线, 每天 3 章自动推送

---

## 📚 文档

- 📋 [docs/PLAN.md](docs/PLAN.md) — **完整方案稿** (老板决策清单 + 架构 + 工时)
- 🏗️ [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 详细技术架构
- 🔌 [docs/API_INTEGRATION.md](docs/API_INTEGRATION.md) — obsidian-journal 接收侧 API 契约
- 🛠 [docs/RUNBOOK.md](docs/RUNBOOK.md) — 运维 / 部署 / 故障排查

---

## 🚀 快速预览 (待实现)

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 .env
cp .env.example .env
# 填入 MINIMAXI_API_KEY 和 OBSIDIAN_PUBLISH_SECRET

# 3. 单次发布 (调试用)
python scripts/publish-once.py

# 4. systemd timer 启动 (本机)
sudo cp systemd/*.service /etc/systemd/system/
sudo cp systemd/*.timer /etc/systemd/system/
sudo systemctl enable --now novel-publish@{08,12,18}.timer

# 5. 查看日志
tail -f logs/publisher.log
```

---

## 🤝 与 xhs-novel-bot 的关系

| 项 | xhs-novel-bot (暂停) | obsidian-novel-publisher (新) |
|---|---|---|
| 推送目的地 | 小红书 (风控 ⛔) | 个人博客 (无风控 ✅) |
| 推送频率 | 2/天 (防封) | 3/天 (全力) |
| 鉴权方式 | XHS cookies | HMAC-SHA256 |
| 提示词 | 已调优 `assets/prompts/` | **直接复用** |
| 部署位置 | 生产机 (systemd) | 本机 (systemd timer) |
| 状态 | ⛔ 暂停 (账号被封 6-23) | 🆕 提案中 |

---

## ⚠️ 关键决策等老板

详见 [docs/PLAN.md § 老板决策清单](docs/PLAN.md#-老板决策清单-q1-q8-重启前必须拍):
- **Q1**: 推送鉴权方式
- **Q3**: novel id 复用 vs 新开
- **Q4**: posts.category 扩展
- **Q6**: 部署位置

---

*作者*: 黑 (Hei) · *创建*: 2026-07-05
*License*: MIT