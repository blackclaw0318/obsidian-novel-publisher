"""
大任务 P2-4: 封面上传到 obsidian-journal
==============================================
走 obsidian-journal 现有的 /api/admin/resources (admin JWT + multipart)

设计:
- 走 multipart/form-data (busboy 解析), MIME=image/jpeg
- 鉴权: Admin JWT (Bearer) — publisher 启动时申请一次性 token 或 service account
- 返回 {url: 'https://obs.shangkun.uk/resources/001.jpg'}
- 重试: 3 次, 指数退避 (1s/2s/4s)
- 失败: 抛 CoverUploadError (publisher 接住)

依赖: requests
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60
DEFAULT_MAX_RETRIES = 3


class CoverUploadError(Exception):
    """封面上传失败 (重试耗尽或 4xx 参数错)"""


@dataclass(frozen=True)
class CoverUploadResult:
    """上传成功结果"""

    url: str
    resource_id: str
    file_size_bytes: int


class CoverUploader:
    """multipart 上传封面图到 obsidian-journal"""

    def __init__(
        self,
        base_url: str,
        admin_token: str,
        *,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        """
        Args:
            base_url:    obsidian-journal 站点 (e.g. 'https://shangkun.uk')
            admin_token: Admin JWT (Bearer)
            timeout_s:   HTTP timeout
            max_retries: 失败重试次数 (default 3)
        """
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url 必须以 http:// 或 https:// 开头: {base_url!r}")
        if not admin_token:
            raise ValueError("admin_token 不能为空")

        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def upload(
        self,
        jpg_path: Path,
        *,
        category: str = "image",
        title: str | None = None,
    ) -> CoverUploadResult:
        """上传 JPG 文件到 obsidian-journal 资源库

        Args:
            jpg_path:  本地 JPG 文件路径
            category:  资源分类 ('image' / 'document' / 'audio', 默认 'image')
            title:     资源标题 (默认用文件名)

        Returns:
            CoverUploadResult { url, resource_id, file_size_bytes }

        Raises:
            CoverUploadError: 上传失败 (重试耗尽 / 4xx 参数错)
            FileNotFoundError: jpg_path 不存在
        """
        if not jpg_path.exists():
            raise FileNotFoundError(f"封面文件不存在: {jpg_path}")

        title = title or jpg_path.stem
        file_size = jpg_path.stat().st_size

        url = f"{self.base_url}/api/admin/resources"
        # obsidian-journal 走 session cookie 鉴权 (lib/auth.ts: getSessionJwtFromCookie)
        # 不用 Authorization: Bearer (server 端读不到)
        # admin_token 实为 admin JWT, 通过 Cookie 头发送
        headers = {"Cookie": f"obsidian_session={self.admin_token}"}

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "[cover_upload] attempt %d/%d POST %s (%d bytes)",
                    attempt,
                    self.max_retries,
                    url,
                    file_size,
                )

                with jpg_path.open("rb") as f:
                    files = {"file": (jpg_path.name, f, "image/jpeg")}
                    data = {"category": category, "title": title}
                    resp = requests.post(
                        url,
                        headers=headers,
                        files=files,
                        data=data,
                        timeout=(10, self.timeout_s),
                    )

                # 4xx 参数错: 立即抛, 不重试
                if 400 <= resp.status_code < 500:
                    raise CoverUploadError(
                        f"4xx ({resp.status_code}) 上传失败 (不重试): {resp.text[:300]}"
                    )

                # 5xx / 网络错: 重试
                if resp.status_code >= 500:
                    raise CoverUploadError(
                        f"5xx ({resp.status_code}) 服务器错 (重试): {resp.text[:300]}"
                    )

                # 2xx: 解析返回
                resp.raise_for_status()
                body: dict[str, Any] = resp.json()

                if not body.get("ok"):
                    raise CoverUploadError(f"上传响应 ok=false: {body.get('error', 'unknown')}")

                # 7-7 fix: 兼容 obsidian-journal v0.34 P4 改的字段名 (resource → media)
                # v0.34 之前: body["resource"]["url"]
                # v0.34 P4 之后: body["media"]["url"]  ← 实际格式
                resource = body.get("media") or body.get("resource") or {}
                resource_url = resource.get("url")
                resource_id = str(resource.get("id", ""))

                if not resource_url:
                    raise CoverUploadError(f"响应缺 url (media/resource 字段都没有): {body}")

                logger.info("[cover_upload] 成功: %s (id=%s)", resource_url, resource_id)
                return CoverUploadResult(
                    url=resource_url,
                    resource_id=resource_id,
                    file_size_bytes=file_size,
                )

            except requests.exceptions.RequestException as e:
                last_err = e
                logger.warning("[cover_upload] 网络错 (attempt %d): %s", attempt, e)
                if attempt < self.max_retries:
                    wait = 2 ** (attempt - 1)  # 1s, 2s, 4s
                    time.sleep(wait)
                    continue
                break
            except CoverUploadError as e:
                last_err = e
                # 4xx 不重试, 5xx 重试
                if "4xx" in str(e) or "不重试" in str(e):
                    logger.error("[cover_upload] 不可重试错误: %s", e)
                    raise
                if attempt < self.max_retries:
                    wait = 2 ** (attempt - 1)
                    time.sleep(wait)
                    continue
                break

        raise CoverUploadError(f"封面上传失败 (重试 {self.max_retries} 次耗尽): {last_err}")
