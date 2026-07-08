"""
tests/conftest.py - pytest 全局配置 (7-8 补)

背景: CI 环境没有 .env 文件, 但 src/novel_writer.py 在模块加载时
     load_dotenv() (找不到 .env 不报错但留空), TopicGenerator fixture
     内部 new NovelWriter() 会因 MINIMAXI_API_KEY 缺失而 ValueError。
     本地 .env 存在所以测试通过, CI 全炸。

解法: 7-8 加此 conftest, 在 test session 启动时为所有相关 env 填 dummy 值。
     单测/集成测跑时如果真用 API, 用 requests_mock 或 patch 拦截,
     dummy 值不会发真请求。
"""

from __future__ import annotations

import os


def pytest_configure(config: object) -> None:
    """在 pytest collect 前注入 dummy 凭据 (CI 友好)

    - MINIMAXI_API_KEY: minimax M3 + image-01 共用
    - OBSIDIAN_PUBLISH_SECRET: HMAC 签名用
    - OBSIDIAN_PUBLISH_ID: publisher id
    - GITHUB_BACKUP_TOKEN: 备份仓 PAT (单测 mock 拦截, dummy 即可)
    """
    defaults = {
        "MINIMAXI_API_KEY": "sk-test-dummy",
        "OBSIDIAN_PUBLISH_SECRET": "test-secret-dummy",
        "OBSIDIAN_PUBLISH_ID": "novel-publisher",
        "OBSIDIAN_PUBLISH_URL": "https://www.shangkun.uk/api/external/chapters",
        "GITHUB_BACKUP_TOKEN": "github_pat_dummy",
        "OBSIDIAN_ADMIN_TOKEN": "admin-jwt-dummy",
        "OBSIDIAN_ADMIN_BASE_URL": "http://localhost:3000",
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)
