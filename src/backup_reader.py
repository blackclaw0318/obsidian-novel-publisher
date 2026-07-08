"""
backup_reader — backups 仓只读客户端 (v0.3.2 P1)
====================================================
- 从 obsidian-novel-backups 仓读文件 (outline / style_guide / characters)
- 老板在 GitHub UI 上手编, publisher 拉最新
- 不写 (写操作用 github_backup.py)

依赖: requests, .env GITHUB_BACKUP_REPO + GITHUB_BACKUP_TOKEN
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_RETRIES = 3


class BackupReaderError(Exception):
    """读 backups 仓失败 (网络/4xx/重试耗尽)"""


class BackupReader:
    """GitHub Contents API 只读客户端

    用法:
        reader = BackupReader(repo="blackclaw0318/obsidian-novel-backups", token=...)
        result = reader.fetch_file("novels/meta_realm_obsidian/outline.md")
        # result = {"content": "...", "sha": "abc123...", "size": 1234, "path": "..."}
    """

    def __init__(
        self,
        repo: str,
        token: str,
        branch: str = "main",
        *,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        api_base: str = GITHUB_API_BASE,
    ):
        if "/" not in repo:
            raise ValueError(f"repo 必须是 'owner/name' 格式: {repo!r}")
        if not token or not token.strip():
            raise ValueError("token 不能为空 (或纯空白)")

        self.owner, self.name = repo.split("/", 1)
        self.token = token
        self.branch = branch
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.api_base = api_base.rstrip("/")

    # ---------- public API ----------

    def fetch_file(self, path: str) -> dict[str, Any] | None:
        """从 backups 仓读文件

        Returns:
            {"content": str, "sha": str, "size": int, "path": str} 或
            None (文件不存在, 404)

        Raises:
            BackupReaderError: 5xx 重试耗尽 / 4xx 参数错 / 网络错
        """
        url = self._url(path)
        last_err: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.get(
                    url,
                    headers=self._headers(),
                    params={"ref": self.branch},
                    timeout=(10, self.timeout_s),
                )
                # 404 → 文件不存在, 返回 None 不算错
                if resp.status_code == 404:
                    logger.info("[backup_reader] GET %s → 404 (不存在)", path)
                    return None
                # 4xx → 参数错, 不重试
                if 400 <= resp.status_code < 500:
                    raise BackupReaderError(
                        f"4xx ({resp.status_code}) GET {path} 失败: {resp.text[:300]}"
                    )
                # 5xx → 重试
                if resp.status_code >= 500:
                    raise BackupReaderError(
                        f"5xx ({resp.status_code}) GET {path}: {resp.text[:200]}"
                    )

                resp.raise_for_status()
                body = resp.json()

                # GitHub Contents API: content 是 base64 (with \n)
                content_b64 = body.get("content", "").replace("\n", "")
                try:
                    content_bytes = base64.b64decode(content_b64)
                    content_str = content_bytes.decode("utf-8")
                except (ValueError, UnicodeDecodeError) as e:
                    raise BackupReaderError(
                        f"GET {path} 内容解码失败 (非 utf-8?): {e}"
                    ) from e

                logger.info(
                    "[backup_reader] GET %s → %d bytes, sha=%s",
                    path,
                    len(content_str),
                    body.get("sha", "")[:12],
                )
                return {
                    "content": content_str,
                    "sha": body.get("sha", ""),
                    "size": body.get("size", len(content_str)),
                    "path": body.get("path", path),
                }
            except requests.exceptions.RequestException as e:
                last_err = e
                logger.warning(
                    "[backup_reader] GET %s attempt %d/%d 网络错: %s",
                    path,
                    attempt,
                    self.max_retries,
                    e,
                )
            except BackupReaderError as e:
                # 4xx/5xx
                if "4xx" in str(e):
                    raise  # 不重试
                last_err = e
                logger.warning(
                    "[backup_reader] GET %s attempt %d/%d 失败: %s",
                    path,
                    attempt,
                    self.max_retries,
                    e,
                )

        raise BackupReaderError(
            f"GET {path} 重试 {self.max_retries} 次仍失败: {last_err}"
        )

    def file_exists(self, path: str) -> bool:
        """检查文件是否存在 (不下载内容)"""
        return self.fetch_file(path) is not None

    # ---------- 内部 ----------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, path: str) -> str:
        return f"{self.api_base}/repos/{self.owner}/{self.name}/contents/{path}"


# ============================================================
# Factory
# ============================================================


def reader_from_env() -> BackupReader:
    """从 .env 读 GITHUB_BACKUP_REPO + GITHUB_BACKUP_TOKEN 构造 reader

    Raises:
        ValueError: 缺关键环境变量
    """
    import os

    repo = os.environ.get("GITHUB_BACKUP_REPO")
    token = os.environ.get("GITHUB_BACKUP_TOKEN")
    if not repo:
        raise ValueError("GITHUB_BACKUP_REPO 环境变量缺失")
    if not token:
        raise ValueError("GITHUB_BACKUP_TOKEN 环境变量缺失")
    return BackupReader(repo=repo, token=token)
