"""
style_guide 单测 (v0.3.2 P1)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.backup_reader import BackupReaderError
from src.style_guide import (
    StyleGuide,
    parse_style_guide,
    fetch_style_guide,
    get_cached_style_guide,
)

# ============================================================
# parse_style_guide (Markdown 解析)
# ============================================================


SAMPLE_STYLE_GUIDE = """# 元界 — 风格指南

## 风格主调
日式水墨 + 极简科幻, 主色调蓝灰 + 月光银, 留白多, 远景人物剪影为主。
- 不要: 赛博朋克霓虹, 暖色调, 高饱和度
- 镜头: 大量负空间, 单点光源, 仰视

## 核心人物 (固定, 跨章一致)
- **林深 (主角)**: 28 岁东亚男性, 短黑发, 银白实验服, 1.82m, 瘦削, 右手腕旧疤
- **月小满 (AI 助手)**: 虚拟投影, 少女轮廓, 月白长袍, 周围有微光粒子
- **陈砚 (导师)**: 60+ 银发, 深灰长衫, 戴圆框眼镜, 笑容温和

## 场景色板
- 月球基地: 蓝灰 #2C3E50 + 月光银 #C0C0C0 + 警示橙 #FF6B35
- 意识空间: 紫黑 #1A0B2E + 萤光 #00F5D4
- 飞船内部: 深木红 #4A2C2A + 黄铜 #B08D57

## 封面 prompt 模板 (image-01 英文)
```
A minimalist sci-fi book cover in Japanese ink-wash style.
Main character: {char_main_description} (consistent across all chapters).
Scene: {chapter_scene_specific}.
Color palette: {scene_palette}.
Style: hand-drawn, low saturation, negative space, single light source.
No text, no logos, no faces with eyes.
3:4 aspect ratio, painterly texture.
```

## 章节场景示例 (ch-001)
- 场景: 月球基地 A3 实验舱, 主角林深独自面对漂浮的样本瓶
- 构图: 仰视, 主角剪影, 蓝灰背景, 月光从左侧洒入
"""


class TestParseStyleGuide:
    def test_parses_style_description(self):
        sg = parse_style_guide(SAMPLE_STYLE_GUIDE)
        assert "日式水墨" in sg.style_description
        assert "留白" in sg.style_description

    def test_parses_character_refs(self):
        sg = parse_style_guide(SAMPLE_STYLE_GUIDE)
        assert len(sg.character_refs) == 3
        assert sg.character_refs[0].name == "林深"
        assert sg.character_refs[0].role == "主角"
        assert "28 岁东亚男性" in sg.character_refs[0].description
        assert sg.character_refs[1].name == "月小满"
        assert sg.character_refs[1].role == "AI 助手"

    def test_parses_scene_palette(self):
        sg = parse_style_guide(SAMPLE_STYLE_GUIDE)
        assert "月球基地" in sg.scene_palette
        assert "#2C3E50" in sg.scene_palette["月球基地"]
        assert "意识空间" in sg.scene_palette
        assert "#1A0B2E" in sg.scene_palette["意识空间"]
        assert "飞船内部" in sg.scene_palette
        assert len(sg.scene_palette) == 3

    def test_extracts_cover_prompt_template(self):
        sg = parse_style_guide(SAMPLE_STYLE_GUIDE)
        assert "minimalist sci-fi" in sg.cover_prompt_template
        assert "{char_main_description}" in sg.cover_prompt_template
        assert "{chapter_scene_specific}" in sg.cover_prompt_template
        assert "3:4 aspect ratio" in sg.cover_prompt_template

    def test_raw_preserved(self):
        sg = parse_style_guide(SAMPLE_STYLE_GUIDE)
        assert sg.raw == SAMPLE_STYLE_GUIDE

    def test_main_character_returns_first_protagonist(self):
        sg = parse_style_guide(SAMPLE_STYLE_GUIDE)
        main = sg.main_character()
        assert main is not None
        assert main.name == "林深"
        assert "主角" in main.role

    def test_minimal_markdown(self):
        """只有 1 个 section 的极简风格指南"""
        minimal = """# 测试
## 风格主调
水墨风
"""
        sg = parse_style_guide(minimal)
        assert "水墨风" in sg.style_description
        assert sg.character_refs == []
        assert sg.scene_palette == {}
        assert sg.cover_prompt_template == ""

    def test_empty_markdown(self):
        sg = parse_style_guide("")
        assert sg.style_description == ""
        assert sg.character_refs == []
        assert sg.scene_palette == {}
        assert sg.cover_prompt_template == ""
        assert sg.raw == ""


# ============================================================
# fetch_style_guide (拉取 + 缓存)
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
            "path": "novels/test/style_guide.md",
        }
    return reader


class TestFetchStyleGuide:
    def test_first_fetch(self, tmp_cache):
        reader = _make_reader(SAMPLE_STYLE_GUIDE, sha="sha1")
        sg, meta = fetch_style_guide(
            reader, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache,
        )
        assert "日式水墨" in sg.style_description
        assert meta.sha == "sha1"
        assert meta.is_changed is True

    def test_unchanged_sha_no_change(self, tmp_cache):
        reader = _make_reader(SAMPLE_STYLE_GUIDE, sha="sha1")
        _, m1 = fetch_style_guide(
            reader, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache,
        )
        _, m2 = fetch_style_guide(
            reader, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache,
        )
        assert m1.is_changed is True
        assert m2.is_changed is False

    def test_sha_change_detected(self, tmp_cache):
        reader1 = _make_reader(SAMPLE_STYLE_GUIDE, sha="sha1")
        fetch_style_guide(reader1, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache)

        # 老板改了
        updated = SAMPLE_STYLE_GUIDE.replace("日式水墨", "末日废土")
        reader2 = _make_reader(updated, sha="sha2")
        sg, meta = fetch_style_guide(reader2, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache)
        assert "末日废土" in sg.style_description
        assert meta.is_changed is True

    def test_404_fallback_to_cache(self, tmp_cache):
        # 先有缓存
        reader1 = _make_reader(SAMPLE_STYLE_GUIDE, sha="sha1")
        fetch_style_guide(reader1, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache)

        # 之后 404
        reader2 = _make_reader(None)
        sg, meta = fetch_style_guide(reader2, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache)
        assert "日式水墨" in sg.style_description  # 旧缓存
        assert meta.error and "404" in meta.error

    def test_network_error_fallback(self, tmp_cache):
        reader1 = _make_reader(SAMPLE_STYLE_GUIDE, sha="sha1")
        fetch_style_guide(reader1, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache)

        reader2 = MagicMock()
        reader2.fetch_file.side_effect = BackupReaderError("connection reset")
        sg, meta = fetch_style_guide(reader2, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache)
        assert "日式水墨" in sg.style_description
        assert meta.error and "拉取" in meta.error

    def test_no_cache_no_network_returns_empty(self, tmp_cache):
        reader = MagicMock()
        reader.fetch_file.side_effect = BackupReaderError("fail")
        sg, meta = fetch_style_guide(reader, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache)
        assert sg.raw == ""
        assert meta.error is not None


# ============================================================
# get_cached_style_guide
# ============================================================


class TestGetCachedStyleGuide:
    def test_no_cache(self, tmp_cache):
        assert get_cached_style_guide("none_obsidian", tmp_cache) is None

    def test_returns_parsed(self, tmp_cache):
        reader = _make_reader(SAMPLE_STYLE_GUIDE, sha="sha1")
        fetch_style_guide(reader, "test_obsidian", "novels/test/style_guide.md", cache_dir=tmp_cache)
        sg = get_cached_style_guide("test_obsidian", tmp_cache)
        assert sg is not None
        assert "日式水墨" in sg.style_description
