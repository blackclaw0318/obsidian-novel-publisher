"""
novel_outline 单测 (v0.3.2 P1)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.backup_reader import BackupReaderError
from src.novel_outline import (
    DEFAULT_CACHE_DIR,
    fetch_outline,
    get_cached_outline,
    _cache_paths,
    _read_cached_sha,
    _write_cache,
)


@pytest.fixture
def tmp_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 data/cache 目录"""
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache


# ============================================================
# Helpers
# ============================================================


def _make_reader(content: str | None, sha: str = "abc123") -> MagicMock:
    """Mock BackupReader"""
    reader = MagicMock()
    if content is None:
        reader.fetch_file.return_value = None  # 404
    else:
        reader.fetch_file.return_value = {
            "content": content,
            "sha": sha,
            "size": len(content),
            "path": "novels/test/outline.md",
        }
    return reader


# ============================================================
# _cache_paths (路径解析)
# ============================================================


class TestCachePaths:
    def test_paths_under_cache_dir(self, tmp_cache):
        c, s = _cache_paths("test_obsidian", tmp_cache)
        assert c == tmp_cache / "test_obsidian" / "outline.md"
        assert s == tmp_cache / "test_obsidian" / "outline.sha"

    @pytest.mark.parametrize("bad", ["../escape", "UPPER", "with-dash", "a/b", ""])
    def test_invalid_novel_id_rejected(self, tmp_cache, bad):
        with pytest.raises(ValueError):
            _cache_paths(bad, tmp_cache)


# ============================================================
# fetch_outline — 拉取
# ============================================================


class TestFetchOutline:
    def test_first_fetch_writes_cache(self, tmp_cache):
        reader = _make_reader("# 元界\\n\\n大雾。", sha="sha1")
        result = fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)

        assert result.content == "# 元界\\n\\n大雾。"
        assert result.sha == "sha1"
        assert result.is_changed is True  # 首次
        assert result.error is None
        assert result.cache_path.exists()

    def test_second_fetch_unchanged_sha(self, tmp_cache):
        reader = _make_reader("# 元界 v1", sha="sha1")
        r1 = fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)
        assert r1.is_changed is True

        # 第二次拉取, sha 相同
        r2 = fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)
        assert r2.is_changed is False
        assert r2.content == "# 元界 v1"

    def test_sha_change_detected(self, tmp_cache):
        reader = _make_reader("v1", sha="sha1")
        fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)

        reader2 = _make_reader("v2 updated", sha="sha2")
        r2 = fetch_outline(reader2, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)
        assert r2.is_changed is True
        assert r2.content == "v2 updated"
        assert r2.sha == "sha2"

    def test_force_refresh_bypasses_cache(self, tmp_cache):
        reader = _make_reader("v1", sha="sha1")
        fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)

        reader2 = _make_reader("forced", sha="sha2")
        r = fetch_outline(
            reader2, "test_obsidian", "novels/test/outline.md",
            cache_dir=tmp_cache, force_refresh=True,
        )
        assert r.is_changed is True
        assert r.content == "forced"

    def test_404_with_no_cache(self, tmp_cache):
        reader = _make_reader(None)
        r = fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)
        assert r.content == ""
        assert r.sha == ""
        assert r.is_changed is False
        assert r.error and "404" in r.error

    def test_404_falls_back_to_old_cache(self, tmp_cache):
        # 先写入旧缓存
        c, s = _cache_paths("test_obsidian", tmp_cache)
        _write_cache(c, s, "old outline", "old-sha")

        # 拉不到 (404)
        reader = _make_reader(None)
        r = fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)
        assert r.content == "old outline"
        assert r.sha == "old-sha"
        assert r.is_changed is False
        assert r.error and "404" in r.error

    def test_network_error_with_no_cache(self, tmp_cache):
        reader = MagicMock()
        reader.fetch_file.side_effect = BackupReaderError("network timeout")
        r = fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)
        assert r.content == ""
        assert r.error and "网络" in r.error or "timeout" in r.error

    def test_network_error_falls_back_to_old_cache(self, tmp_cache):
        c, s = _cache_paths("test_obsidian", tmp_cache)
        _write_cache(c, s, "old", "old-sha")

        reader = MagicMock()
        reader.fetch_file.side_effect = BackupReaderError("network timeout")
        r = fetch_outline(reader, "test_obsidian", "novels/test/outline.md", cache_dir=tmp_cache)
        assert r.content == "old"
        assert r.error and "拉取" in r.error


# ============================================================
# get_cached_outline (不调 GitHub)
# ============================================================


class TestGetCachedOutline:
    def test_no_cache_returns_none(self, tmp_cache):
        assert get_cached_outline("none_obsidian", tmp_cache) is None

    def test_returns_cached_content(self, tmp_cache):
        c, s = _cache_paths("test_obsidian", tmp_cache)
        _write_cache(c, s, "cached content", "sha")
        assert get_cached_outline("test_obsidian", tmp_cache) == "cached content"
