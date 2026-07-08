"""
cover_prompt_builder 单测 (v0.3.2 P3)
========================================
- build() 模板替换: style_guide.template + character + scene
- 退化: 没 template → DEFAULT
- 退化: 没主角 → generic placeholder
- 退化: 老板用了未支持占位符 → 退化 + warning
- 色板轮转: ch-1/2/3/4 → 循环
- build_cover_prompt() helper 行为
"""
from __future__ import annotations

import textwrap

import pytest

from src.character_loader import Character, Characters, parse_characters
from src.cover_prompt_builder import (
    DEFAULT_COVER_PROMPT_TEMPLATE,
    CoverPromptBuilder,
    CoverPromptContext,
    build_cover_prompt,
)
from src.style_guide import (
    CharacterRef,
    StyleGuide,
    parse_style_guide,
)

# ============================================================
# Sample data
# ============================================================


SAMPLE_SG_MD = textwrap.dedent(
    """
    # 元界 — 风格指南

    ## 风格主调
    日式水墨 + 极简科幻, 蓝灰冷色调, 留白多

    ## 核心人物 (固定, 跨章一致)
    - **林深 (主角)**: 28 岁东亚男性, 短黑发, 银白实验服, 1.82m, 瘦削
    - **月小满 (AI 助手)**: 虚拟投影, 少女轮廓, 月白长袍

    ## 场景色板
    - 月球基地: 蓝灰 #2C3E50 + 月光银 #C0C0C0
    - 意识空间: 紫黑 #1A0B2E + 萤光 #00F5D4
    - 飞船内部: 深木红 #4A2C2A + 黄铜 #B08D57

    ## 封面 prompt 模板 (image-01 英文)
    ```
    A minimalist sci-fi book cover.
    Main character: {char_main_description} (consistent across all chapters).
    Scene: {chapter_scene_specific}.
    Color palette: {scene_palette}.
    Style: hand-drawn, low saturation, 3:4, no text.
    ```
    """
)


@pytest.fixture
def sg() -> StyleGuide:
    return parse_style_guide(SAMPLE_SG_MD)


@pytest.fixture
def ch_with_main() -> Characters:
    md = textwrap.dedent(
        """
        # 元界 — 人物卡
        ## 林深 (主角)
        - 角色: 主角
        - 性别: 男
        - 年龄: 28
        - 外貌: 短黑发, 银白实验服, 1.82m, 瘦削
        - 性格: 内向, 好奇
        """
    )
    return parse_characters(md)


@pytest.fixture
def ch_no_main() -> Characters:
    """没 main 标记的 characters"""
    md = textwrap.dedent(
        """
        # 测试
        ## A
        - 性别: 男
        ## B
        - 性别: 女
        """
    )
    return parse_characters(md)


# ============================================================
# build() 主流程
# ============================================================


class TestBuildWithFullContext:
    def test_template_placeholders_replaced(self, sg, ch_with_main):
        ctx = CoverPromptContext(
            style_guide=sg,
            characters=ch_with_main,
            chapter_idx=1,
            chapter_scene="月球基地 A3 实验舱",
        )
        prompt = CoverPromptBuilder().build(ctx)
        assert "{char_main_description}" not in prompt
        assert "{chapter_scene_specific}" not in prompt
        assert "{scene_palette}" not in prompt
        assert "minimalist sci-fi" in prompt  # template 原文保留

    def test_main_character_description_injected(self, sg, ch_with_main):
        ctx = CoverPromptContext(
            style_guide=sg, characters=ch_with_main, chapter_idx=1,
        )
        prompt = CoverPromptBuilder().build(ctx)
        # 来自 characters.main (age 28 + 性别 男 + appearance)
        assert "28" in prompt
        assert "男" in prompt
        assert "短黑发" in prompt
        assert "银白实验服" in prompt

    def test_chapter_scene_injected(self, sg, ch_with_main):
        ctx = CoverPromptContext(
            style_guide=sg, characters=ch_with_main,
            chapter_idx=1, chapter_scene="月球基地 A3 实验舱, 漂浮的样本瓶",
        )
        prompt = CoverPromptBuilder().build(ctx)
        assert "月球基地" in prompt

    def test_scene_palette_ch1(self, sg, ch_with_main):
        """ch-1 → 第 1 个 scene (月球基地)"""
        ctx = CoverPromptContext(style_guide=sg, characters=ch_with_main, chapter_idx=1)
        prompt = CoverPromptBuilder().build(ctx)
        assert "蓝灰" in prompt
        assert "#2C3E50" in prompt

    def test_scene_palette_ch2(self, sg, ch_with_main):
        """ch-2 → 第 2 个 scene (意识空间)"""
        ctx = CoverPromptContext(style_guide=sg, characters=ch_with_main, chapter_idx=2)
        prompt = CoverPromptBuilder().build(ctx)
        assert "紫黑" in prompt
        assert "#1A0B2E" in prompt

    def test_scene_palette_ch3(self, sg, ch_with_main):
        """ch-3 → 第 3 个 scene (飞船内部)"""
        ctx = CoverPromptContext(style_guide=sg, characters=ch_with_main, chapter_idx=3)
        prompt = CoverPromptBuilder().build(ctx)
        assert "深木红" in prompt
        assert "#4A2C2A" in prompt

    def test_scene_palette_cycles(self, sg, ch_with_main):
        """ch-4 → 回到第 1 个 scene (循环)"""
        ctx = CoverPromptContext(style_guide=sg, characters=ch_with_main, chapter_idx=4)
        prompt = CoverPromptBuilder().build(ctx)
        assert "蓝灰" in prompt  # 回到月球基地


# ============================================================
# 退化路径
# ============================================================


class TestBuildFallbacks:
    def test_no_template_uses_default(self, ch_with_main):
        """style_guide.cover_prompt_template 为空 → 用 DEFAULT"""
        sg = StyleGuide(
            style_description="日式水墨",
            character_refs=[],
            scene_palette={},
            cover_prompt_template="",  # 空
        )
        ctx = CoverPromptContext(style_guide=sg, characters=ch_with_main, chapter_idx=1)
        prompt = CoverPromptBuilder().build(ctx)
        # DEFAULT 模板特征
        assert "hand-drawn, low saturation" in prompt

    def test_no_characters_uses_style_guide_refs(self, sg):
        """没 characters → 用 style_guide.character_refs[0]"""
        ctx = CoverPromptContext(
            style_guide=sg, characters=None, chapter_idx=1,
        )
        prompt = CoverPromptBuilder().build(ctx)
        # 来自 style_guide.character_refs[0] (林深)
        assert "林深" in prompt
        assert "28 岁东亚男性" in prompt

    def test_no_main_in_characters_uses_first_supporting(self, ch_no_main):
        """characters.main=None → 用 supporting[0] (姓 A)"""
        sg = StyleGuide(
            style_description="水墨",
            character_refs=[],
            scene_palette={},
            cover_prompt_template="{char_main_description}",
        )
        ctx = CoverPromptContext(style_guide=sg, characters=ch_no_main, chapter_idx=1)
        prompt = CoverPromptBuilder().build(ctx)
        # 来自 characters.main (first one)
        assert "A" in prompt or "男" in prompt

    def test_no_characters_no_refs_uses_generic(self):
        """全无 → generic placeholder"""
        sg = StyleGuide(
            style_description="水墨",
            character_refs=[],
            scene_palette={},
            cover_prompt_template="{char_main_description}",
        )
        ctx = CoverPromptContext(style_guide=sg, characters=None, chapter_idx=1)
        prompt = CoverPromptBuilder().build(ctx)
        assert "contemplative protagonist" in prompt

    def test_no_palette_uses_natural(self, ch_with_main):
        """scene_palette 为空 → natural muted tones"""
        sg = StyleGuide(
            style_description="水墨",
            character_refs=[],
            scene_palette={},
            cover_prompt_template="palette={scene_palette}",
        )
        ctx = CoverPromptContext(style_guide=sg, characters=ch_with_main, chapter_idx=1)
        prompt = CoverPromptBuilder().build(ctx)
        assert "natural muted tones" in prompt

    def test_unsupported_placeholder_falls_back(self, ch_with_main):
        """老板用了 {unknown_placeholder} → 退化用 DEFAULT, 仍能跑"""
        sg = StyleGuide(
            style_description="水墨",
            character_refs=[],
            scene_palette={},
            cover_prompt_template="{unknown_placeholder} {char_main_description}",
        )
        ctx = CoverPromptContext(style_guide=sg, characters=ch_with_main, chapter_idx=1)
        prompt = CoverPromptBuilder().build(ctx)
        # 退化为 DEFAULT (含 "hand-drawn")
        assert "hand-drawn" in prompt
        assert "{unknown_placeholder}" not in prompt

    def test_chapter_scene_fallback_when_empty(self, sg, ch_with_main):
        """chapter_scene="" → 用 generic 'a contemplative moment'"""
        sg2 = StyleGuide(
            style_description="水墨",
            character_refs=[],
            scene_palette={},
            cover_prompt_template="scene={chapter_scene_specific}",
        )
        ctx = CoverPromptContext(style_guide=sg2, characters=ch_with_main, chapter_idx=1, chapter_scene="")
        prompt = CoverPromptBuilder().build(ctx)
        assert "contemplative moment" in prompt

    def test_chapter_scene_truncates_long(self, sg, ch_with_main):
        """chapter_scene > 200 字符 → 截断"""
        long_scene = "x" * 500
        sg2 = StyleGuide(
            style_description="水墨",
            character_refs=[],
            scene_palette={},
            cover_prompt_template="scene={chapter_scene_specific}",
        )
        ctx = CoverPromptContext(style_guide=sg2, characters=ch_with_main, chapter_idx=1, chapter_scene=long_scene)
        prompt = CoverPromptBuilder().build(ctx)
        # scene 占位符替换为 ≤200 字符
        scene_part = prompt.split("scene=")[1]
        assert len(scene_part) <= 200


# ============================================================
# build_cover_prompt() helper
# ============================================================


class TestBuildCoverPromptHelper:
    def test_helper_returns_string(self, sg, ch_with_main):
        prompt = build_cover_prompt(sg, ch_with_main, chapter_idx=2, chapter_scene="实验室")
        assert isinstance(prompt, str)
        assert "实验室" in prompt

    def test_helper_with_minimal_args(self, sg):
        """只传 style_guide, chapter_idx 默认 1, characters/chapter_scene 默认"""
        prompt = build_cover_prompt(sg)
        assert isinstance(prompt, str)
        assert "{char_main_description}" not in prompt


# ============================================================
# Default template 自检
# ============================================================


class TestDefaultTemplate:
    def test_default_has_required_placeholders(self):
        """DEFAULT 模板必须含 4 个占位符 (保证 format 必填)"""
        for ph in ("{char_main_description}", "{chapter_scene_specific}", "{scene_palette}", "{style_description}"):
            assert ph in DEFAULT_COVER_PROMPT_TEMPLATE, f"DEFAULT 缺 {ph}"

    def test_default_format_works(self):
        """DEFAULT 模板能 format 不报错"""
        out = DEFAULT_COVER_PROMPT_TEMPLATE.format(
            char_main_description="X",
            chapter_scene_specific="Y",
            scene_palette="Z",
            style_description="W",
        )
        assert "X" in out
        assert "Y" in out
        assert "Z" in out
        assert "W" in out
