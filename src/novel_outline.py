"""
novel_outline — outline.md 拉取 + 缓存 + sha 检测 (v0.3.2 P1)
=============================================================
- 从 backups 仓拉 novel 的 outline.md
- 缓存到 data/cache/{novel_id}/outline.md + outline.sha
- 检测 sha 变化 (老板改了) → 返回 is_changed 标志 + log

依赖: src/backup_reader.py
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backup_reader import BackupReader, BackupReaderError

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/cache")


@dataclass
class OutlineResult:
    """outline 拉取结果"""

    content: str
    sha: str
    is_changed: bool  # 相对上次缓存, sha 变了 = True
    cache_path: Path  # 缓存文件路径
    error: str | None = None  # 拉取失败时记录 (但仍用旧 cache)


# ============================================================
# 缓存 I/O
# ============================================================


def _cache_paths(novel_id: str, base_dir: Path = DEFAULT_CACHE_DIR) -> tuple[Path, Path]:
    """返回 (content_path, sha_path)"""
    # novel_id 校验 (防止路径穿越)
    import re
    if not re.match(r"^[a-z0-9_]+$", novel_id):
        raise ValueError(f"novel_id 非法: {novel_id!r}")
    d = base_dir / novel_id
    return d / "outline.md", d / "outline.sha"


def _read_cached_sha(sha_path: Path) -> str | None:
    """读上次缓存的 sha (不存在返 None)"""
    if not sha_path.exists():
        return None
    return sha_path.read_text(encoding="utf-8").strip() or None


def _write_cache(content_path: Path, sha_path: Path, content: str, sha: str) -> None:
    """写缓存 (atomic: 先 tmp 再 rename)"""
    content_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_c = content_path.with_suffix(content_path.suffix + ".tmp")
    tmp_s = sha_path.with_suffix(sha_path.suffix + ".tmp")
    tmp_c.write_text(content, encoding="utf-8")
    tmp_s.write_text(sha, encoding="utf-8")
    tmp_c.replace(content_path)
    tmp_s.replace(sha_path)


# ============================================================
# 拉取
# ============================================================


def fetch_outline(
    reader: BackupReader,
    novel_id: str,
    outline_path: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> OutlineResult:
    """拉取 novel 的 outline.md, 缓存, 检测 sha 变化

    Args:
        reader: BackupReader 实例
        novel_id: 小说 id (用于缓存目录)
        outline_path: backups 仓内的相对路径 (e.g. "novels/meta_realm_obsidian/outline.md")
        cache_dir: 缓存根目录
        force_refresh: 强制从 GitHub 拉 (跳过 sha 检测)

    Returns:
        OutlineResult { content, sha, is_changed, cache_path, error? }

    行为:
        1. 强制 refresh 或首次 → 调 GitHub, 写缓存, 返回 is_changed=True (新内容)
        2. 拉取成功但 sha 未变 → 直接用缓存, is_changed=False
        3. 拉取失败 + 有缓存 → 用旧缓存, error 记录, is_changed=False
        4. 拉取失败 + 无缓存 → 返回空 content, error 记录
    """
    content_path, sha_path = _cache_paths(novel_id, cache_dir)
    cached_sha = None if force_refresh else _read_cached_sha(sha_path)

    # 1. 拉 GitHub
    try:
        result = reader.fetch_file(outline_path)
    except BackupReaderError as e:
        err = f"拉取 outline 失败: {e}"
        logger.error("[novel_outline] %s — 尝试用旧缓存", err)
        # 降级用旧缓存
        if content_path.exists():
            old_content = content_path.read_text(encoding="utf-8")
            return OutlineResult(
                content=old_content,
                sha=cached_sha or "",
                is_changed=False,
                cache_path=content_path,
                error=err,
            )
        return OutlineResult(
            content="", sha="", is_changed=False, cache_path=content_path, error=err
        )

    # 2. 文件不存在 (404) → 用旧缓存或空
    if result is None:
        if content_path.exists():
            logger.warning(
                "[novel_outline] outline 在 GitHub 不存在 (%s), 用旧缓存", outline_path
            )
            return OutlineResult(
                content=content_path.read_text(encoding="utf-8"),
                sha=cached_sha or "",
                is_changed=False,
                cache_path=content_path,
                error=f"outline 404: {outline_path}",
            )
        logger.warning("[novel_outline] outline 不存在 (%s), 无缓存", outline_path)
        return OutlineResult(
            content="", sha="", is_changed=False, cache_path=content_path,
            error=f"outline 404: {outline_path}",
        )

    # 3. 拉取成功, 检测 sha 变化
    new_content = result["content"]
    new_sha = result["sha"]
    is_changed = (cached_sha is None) or (new_sha != cached_sha)

    # 4. 写缓存 (不论是否变化, 都刷新本地文件 — sha 不变也省一次 GET)
    if is_changed:
        logger.info(
            "[novel_outline] outline sha 变化: %s → %s (%s)",
            (cached_sha or "NONE")[:12],
            new_sha[:12],
            outline_path,
        )
    _write_cache(content_path, sha_path, new_content, new_sha)

    return OutlineResult(
        content=new_content,
        sha=new_sha,
        is_changed=is_changed,
        cache_path=content_path,
    )


def get_cached_outline(
    novel_id: str, cache_dir: Path = DEFAULT_CACHE_DIR
) -> str | None:
    """只读缓存 (不调 GitHub), 用于 publisher 启动加速"""
    content_path, _ = _cache_paths(novel_id, cache_dir)
    if not content_path.exists():
        return None
    return content_path.read_text(encoding="utf-8")
