"""
test_markdown_renderer.py — markdown_renderer.py 单元测试

覆盖:
- 基本渲染 (title + 封面 + 段切分)
- 单段 (内容 < max_chars 不切分)
- 多段 (内容 > max_chars 切多段, 段间 --- 分隔)
- 超长单句 (超过 max_chars 硬切)
- excerpt 前 150 字
- 封面 URL 空 (不渲染图)
- empty raw_text 抛错
- normalize_text (合并连续空行 + 去首尾空白)
"""

from __future__ import annotations

import pytest

from src.markdown_renderer import (
    MAX_SECTION_CHARS,
    render,
)

CHAPTER_TITLE = "星际穿越者"
COVER_URL = "https://obs.shangkun.uk/resources/ch-001.jpg"


class TestRenderBasic:
    """基本渲染测试"""

    def test_renders_title_cover_and_content(self):
        text = "这是第一段。第二段更精彩。第三段来了。"
        result = render(text, COVER_URL, chapter_idx=1, chapter_title=CHAPTER_TITLE)
        assert result.title == f"第 1 章 · {CHAPTER_TITLE}"
        assert f"![{CHAPTER_TITLE} 封面]({COVER_URL})" in result.content_markdown
        assert "第一段" in result.content_markdown

    def test_no_cover_url_skips_image(self):
        text = "内容。"
        result = render(text, "", chapter_idx=1, chapter_title=CHAPTER_TITLE)
        assert "![" not in result.content_markdown  # 无图
        assert "内容" in result.content_markdown

    def test_excerpt_first_150_chars(self):
        text = "甲" * 200
        result = render(text, "", chapter_idx=1, chapter_title=CHAPTER_TITLE)
        assert result.excerpt.startswith("甲" * 100)
        assert result.excerpt < "甲" * 200  # 被截断
        assert result.excerpt.endswith("…")  # 截断标识


class TestSectionSplitting:
    """段切分测试"""

    def test_short_text_single_section(self):
        text = "一句话内容。" * 10  # 约 70 chars
        result = render(
            text, COVER_URL, chapter_idx=1, chapter_title=CHAPTER_TITLE, max_section_chars=1500
        )
        # 不应该有 --- 分隔
        assert "---" not in result.content_markdown

    def test_long_text_multi_section(self):
        # 造 ~3000 字内容 → 应切 ≥ 2 段
        text = ("这是测试句子。" * 100) + ("第二段句子。" * 100)
        result = render(
            text,
            COVER_URL,
            chapter_idx=1,
            chapter_title=CHAPTER_TITLE,
            max_section_chars=1500,
        )
        assert result.content_markdown.count("---") >= 1
        # 第一段应在 max_section_chars 内
        first_section_end = result.content_markdown.find("\n\n---\n\n")
        first_section = result.content_markdown[:first_section_end]
        assert len(first_section) < 1700  # 略宽松

    def test_very_long_single_sentence_hard_cut(self):
        # 一句话超 max_chars → 应硬切
        text = "甲" * 3000
        result = render(
            text,
            "",
            chapter_idx=1,
            chapter_title=CHAPTER_TITLE,
            max_section_chars=1500,
        )
        # 应有 --- 分隔 (因为硬切产生 2+ 段)
        assert "---" in result.content_markdown


class TestNormalization:
    """normalize_text 测试"""

    def test_strips_leading_trailing_whitespace(self):
        text = "   \n\n内容。\n\n   "
        result = render(text, "", chapter_idx=1, chapter_title=CHAPTER_TITLE)
        assert result.content_markdown.startswith("# 第 1 章")

    def test_collapses_excessive_newlines(self):
        text = "段落一。\n\n\n\n\n段落二。"
        # 应被合并为 2 个空行间隔
        result = render(text, "", chapter_idx=1, chapter_title=CHAPTER_TITLE)
        # 不应有连续 3+ 空行
        assert "\n\n\n\n" not in result.content_markdown


class TestEdgeCases:
    """边界测试"""

    def test_empty_text_raises(self):
        with pytest.raises(ValueError, match="切分后为空"):
            render("", COVER_URL, chapter_idx=1, chapter_title=CHAPTER_TITLE)

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="切分后为空"):
            render("   \n\n  ", COVER_URL, chapter_idx=1, chapter_title=CHAPTER_TITLE)

    def test_special_chapter_idx(self):
        text = "内容。"
        result = render(text, "", chapter_idx=99, chapter_title=CHAPTER_TITLE)
        assert "第 99 章" in result.title


def test_max_section_chars_is_1500():
    """默认 max_section_chars 应为 1500 (博客单章节长度经验值)"""
    assert MAX_SECTION_CHARS == 1500
