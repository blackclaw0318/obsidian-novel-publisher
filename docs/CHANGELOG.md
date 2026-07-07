# Changelog

> obsidian-novel-publisher 版本历史。格式参照 [Keep a Changelog](https://keepachangelog.com/)。

---

## [v0.2.0] - 2026-07-07

### 🎉 收官
P0-P7 全部完成, 215 测试全绿, 单 owner 仓库即可独立运行。

### ✅ Added
- **P7 CI/CD**: GitHub Actions 6 jobs (lint / unit-test (matrix 3.11/3.12) / integration-test / build / auto-push / release)
- **P6 测试金字塔**: 199 测试 (含 24 novel_writer + 18 topic_gen + 15 cover_gen + 16 cover_upload + 11 publisher + 12 集成 + 10 mock 工厂 + 16 ci_workflow)
- **P5 systemd timer**: 每天 08/12/18 自动推送 + logrotate 30 天
- **P4** (obsidian-journal 侧): `/api/external/posts` HMAC-SHA256 接收端点
- **P3 GitHub 备份**: 每次推送自动备份到 `obsidian-novel-backups` 私仓
- **P2 主流程**: publisher.py run_once() 9 步编排 + state 状态机
- **P1 写作引擎**: novel_writer + topic_gen + cover_gen
- **P0 仓库骨架**: pyproject.toml + pre-commit + .env.example + .gitignore

### 🔧 Changed
- src/topic_gen.py: 修复 `from novel_writer` → `from .novel_writer` (相对导入)
- pyproject.toml: tests/* 加 SIM117 ignore (mock 测试嵌套 with 常见)

### 🐛 Fixed
- 修复 src/topic_gen.py 模块导入错误 (dot prefix 缺失, 部署到 systemd 会炸)

### 📚 Docs
- docs/PLAN.md - 完整方案稿
- docs/ARCHITECTURE.md - 技术架构
- docs/API_INTEGRATION.md - obsidian-journal 集成契约
- docs/RUNBOOK.md - 运维手册
- docs/CI_SETUP.md - CI/CD + branch protection 指南
- docs/CHANGELOG.md - 本文件

### 🛡 Security
- 凭据 (.env) gitignored
- HMAC-SHA256 ±5min timestamp window
- GitHub fine-grained PAT 限 backups-only (TODO 拆)
- idem_key 幂等防双发

---

## [v0.1.0] - 2026-07-05

### 🎯 提案稿
- README.md (项目说明 + 老板决策清单)
- docs/PLAN.md (完整方案稿 v0.1)
- docs/ARCHITECTURE.md (架构概览)
- .gitignore (凭据/logs/state 全部 gitignore)

---

[Unreleased]: https://github.com/blackclaw0318/obsidian-novel-publisher/compare/v0.2.0...HEAD
[v0.2.0]: https://github.com/blackclaw0318/obsidian-novel-publisher/releases/tag/v0.2.0
[v0.1.0]: https://github.com/blackclaw0318/obsidian-novel-publisher/releases/tag/v0.1.0