"""
character_loader 单测 (v0.3.2 P1)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.backup_reader import BackupReaderError
from src.character_loader import (
    Character,
    Characters,
    fetch_characters,
    get_cached_characters,
    parse_characters,
)

# ============================================================
# 样本数据
# ============================================================


SAMPLE_CHARACTERS = """# 元界 — 人物卡

## 林深 (主角)
- 角色: 主角
- 性别: 男
- 年龄: 28
- 外貌: 短黑发, 银白实验服, 1.82m, 瘦削
- 性格: 内向, 好奇, 决断
- 背景: 月球基地 A3 实验室研究员, 妻子 3 年前失踪
- 重要物品: 右手腕旧疤, 父亲留下的怀表
- 重要关系: 月小满 (AI 助手), 陈砚 (导师)

## 月小满
- 角色: AI 助手
- 性别: 女 (虚拟投影)
- 外貌: 少女轮廓, 月白长袍, 微光粒子
- 性格: 温和, 好奇, 偶尔幽默
- 背景: 林深妻子意识的数字副本, 在月球基地云端运行

## 陈砚
- 角色: 导师
- 性别: 男
- 年龄: 60+
- 外貌: 银发, 深灰长衫, 圆框眼镜
- 性格: 温和, 博学
- 背景: 元界理论先驱, 林深的引路人
"""


# ============================================================
# parse_characters (Markdown 解析)
# ============================================================


class TestParseCharacters:
    def test_parses_three_characters(self):
        chars = parse_characters(SAMPLE_CHARACTERS)
        assert len(chars.all()) == 3

    def test_main_character(self):
        chars = parse_characters(SAMPLE_CHARACTERS)
        assert chars.main is not None
        assert chars.main.name == "林深"
        assert "主角" in chars.main.role

    def test_main_fields(self):
        chars = parse_characters(SAMPLE_CHARACTERS)
        m = chars.main
        assert m is not None
        assert m.gender == "男"
        assert m.age == "28"
        assert "短黑发" in m.appearance
        assert "银白实验服" in m.appearance
        assert "内向" in m.personality
        assert "月球基地" in m.background
        assert len(m.items) == 2
        assert "右手腕旧疤" in m.items
        assert "父亲留下的怀表" in m.items
        assert len(m.relationships) == 2
        assert any("月小满" in r for r in m.relationships)

    def test_supporting_characters(self):
        chars = parse_characters(SAMPLE_CHARACTERS)
        assert len(chars.supporting) == 2
        names = [c.name for c in chars.supporting]
        assert "月小满" in names
        assert "陈砚" in names

    def test_supporting_with_role_in_paren_fallback(self):
        """'## 月小满' 后跟 - 角色: AI 助手, 不在 ## 标题里"""
        chars = parse_characters(SAMPLE_CHARACTERS)
        yk = next((c for c in chars.all() if c.name == "月小满"), None)
        assert yk is not None
        assert yk.role == "AI 助手"
        assert "女" in yk.gender

    def test_no_main_marker_uses_first(self):
        """如果没标 '主角' role, 第一个作 main"""
        md = """# 测试
## A
- 性别: 男
## B
- 性别: 女
"""
        chars = parse_characters(md)
        assert chars.main is not None
        assert chars.main.name == "A"
        assert len(chars.supporting) == 1

    def test_empty_markdown(self):
        chars = parse_characters("")
        assert chars.main is None
        assert chars.supporting == []
        assert chars.raw == ""

    def test_one_line_description(self):
        chars = parse_characters(SAMPLE_CHARACTERS)
        m = chars.main
        assert m is not None
        desc = m.one_line_description()
        assert "28" in desc
        assert "男" in desc or "性" in desc


# ============================================================
# fetch_characters (拉取 + 缓存)
# ============================================================


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def _make_reader(content: str | None, sha: str = "abc") -> MagicMock:
    reader = MagicMock()
    if content is None:
        reader.fetch_file.return_value = None
    else:
        reader.fetch_file.return_value = {
            "content": content, "sha": sha, "size": len(content),
            "path": "novels/test/characters.md",
        }
    return reader


class TestFetchCharacters:
    def test_first_fetch(self, tmp_cache):
        reader = _make_reader(SAMPLE_CHARACTERS, sha="sha1")
        chars, meta = fetch_characters(
            reader, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache,
        )
        assert chars.main is not None
        assert chars.main.name == "林深"
        assert meta.sha == "sha1"
        assert meta.is_changed is True

    def test_unchanged_sha(self, tmp_cache):
        reader = _make_reader(SAMPLE_CHARACTERS, sha="sha1")
        _, m1 = fetch_characters(reader, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)
        _, m2 = fetch_characters(reader, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)
        assert m1.is_changed is True
        assert m2.is_changed is False

    def test_sha_change(self, tmp_cache):
        reader1 = _make_reader(SAMPLE_CHARACTERS, sha="sha1")
        fetch_characters(reader1, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)

        updated = SAMPLE_CHARACTERS.replace("28", "29")  # 老板改了林深年龄
        reader2 = _make_reader(updated, sha="sha2")
        chars, meta = fetch_characters(reader2, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)
        assert chars.main.age == "29"
        assert meta.is_changed is True

    def test_404_fallback(self, tmp_cache):
        reader1 = _make_reader(SAMPLE_CHARACTERS, sha="sha1")
        fetch_characters(reader1, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)

        reader2 = _make_reader(None)
        chars, meta = fetch_characters(reader2, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)
        assert chars.main is not None  # 旧缓存
        assert meta.error and "404" in meta.error

    def test_network_error_fallback(self, tmp_cache):
        reader1 = _make_reader(SAMPLE_CHARACTERS, sha="sha1")
        fetch_characters(reader1, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)

        reader2 = MagicMock()
        reader2.fetch_file.side_effect = BackupReaderError("timeout")
        chars, meta = fetch_characters(reader2, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)
        assert chars.main is not None
        assert meta.error and "拉取" in meta.error

    def test_no_cache_no_network(self, tmp_cache):
        reader = MagicMock()
        reader.fetch_file.side_effect = BackupReaderError("fail")
        chars, meta = fetch_characters(reader, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)
        assert chars.main is None
        assert meta.error is not None


class TestGetCachedCharacters:
    def test_no_cache(self, tmp_cache):
        assert get_cached_characters("none_obsidian", tmp_cache) is None

    def test_returns_parsed(self, tmp_cache):
        reader = _make_reader(SAMPLE_CHARACTERS, sha="sha1")
        fetch_characters(reader, "test_obsidian", "novels/test/characters.md", cache_dir=tmp_cache)
        chars = get_cached_characters("test_obsidian", tmp_cache)
        assert chars is not None
        assert chars.main.name == "林深"
