"""
cover_prompt_builder — style_guide 驱动的封面 prompt 构造器 (v0.3.2 P3)
=======================================================================
- 输入: StyleGuide (含 cover_prompt_template + character_refs + scene_palette + style_description)
       + Characters (含 main)
       + chapter_idx
       + chapter_scene (本章场景描述, 可选)
- 输出: 完整英文 cover prompt, 喂给 minimax image-01 (3:4, no text)

设计:
- 优先用 style_guide.cover_prompt_template (老板在 backups 仓手编, 7-8 拍可定制)
- 退化: 没 template / template 解析失败 → 用内置 DEFAULT_TEMPLATE
- 退化: 没主角 / 没色板 → 用通用 placeholder

依赖: src/style_guide.py + src/character_loader.py
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .character_loader import Characters
from .style_guide import CharacterRef, StyleGuide

logger = logging.getLogger(__name__)


# 内置 fallback template (老板没写 style_guide.md / template 为空时用)
DEFAULT_COVER_PROMPT_TEMPLATE = """A book cover in {style_description}.
Main character: {char_main_description}.
Scene: {chapter_scene_specific}.
Color palette: {scene_palette}.
Style: hand-drawn, low saturation, negative space, single light source.
No text, no logos, no faces with eyes.
3:4 aspect ratio, painterly texture."""


# 探测 template 里用了哪些占位符 (用于报错: 老板写了未支持的占位符)
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


@dataclass
class CoverPromptContext:
    """构造 cover prompt 的输入 (避免参数爆炸)"""

    style_guide: StyleGuide
    characters: Characters | None = None
    chapter_idx: int = 1
    chapter_scene: str = ""  # 本章场景描述 (e.g. "月球基地 A3 实验舱")


class CoverPromptBuilder:
    """封面 prompt 构造器

    用法:
        builder = CoverPromptBuilder()
        prompt = builder.build(CoverPromptContext(
            style_guide=sg, characters=ch,
            chapter_idx=4, chapter_scene="...",
        ))
    """

    def build(self, ctx: CoverPromptContext) -> str:
        """构造完整 cover prompt

        行为:
            1. 拿 template (priority: style_guide.cover_prompt_template > DEFAULT_COVER_PROMPT_TEMPLATE)
            2. 拿主角描述 (priority: characters.main > style_guide.character_refs[0] > generic)
            3. 拿色板 (按 chapter_idx 轮转, 优先 style_guide.scene_palette)
            4. format template
            5. 失败 (KeyError 老板用了未支持的占位符) → 退化
        """
        template = self._pick_template(ctx.style_guide)
        char_desc = self._pick_character_description(ctx)
        scene_palette = self._pick_scene_palette(ctx.style_guide, ctx.chapter_idx)
        style_desc = ctx.style_guide.style_description.strip() or "minimalist illustration"
        scene_specific = (ctx.chapter_scene or "a contemplative moment").strip()[:200]

        # 占位符替换
        try:
            return template.format(
                char_main_description=char_desc,
                chapter_scene_specific=scene_specific,
                scene_palette=scene_palette,
                style_description=style_desc,
            )
        except KeyError as e:
            # 老板在 template 里用了未支持的占位符
            unsupported = e.args[0] if e.args else "?"
            logger.warning(
                "[cover_prompt_builder] template 含未支持占位符 %s, 退化用 DEFAULT",
                unsupported,
            )
            return DEFAULT_COVER_PROMPT_TEMPLATE.format(
                char_main_description=char_desc,
                chapter_scene_specific=scene_specific,
                scene_palette=scene_palette,
                style_description=style_desc,
            )

    # ============================================================
    # 内部: 字段选取
    # ============================================================

    @staticmethod
    def _pick_template(sg: StyleGuide) -> str:
        """priority: style_guide.cover_prompt_template > DEFAULT"""
        t = sg.cover_prompt_template.strip()
        if t:
            return t
        return DEFAULT_COVER_PROMPT_TEMPLATE

    @staticmethod
    def _pick_character_description(ctx: CoverPromptContext) -> str:
        """priority: characters.main > style_guide.character_refs[0] > generic"""
        # 1. characters.md 来的 main (有 appearance 字段)
        if ctx.characters and ctx.characters.main:
            ch = ctx.characters.main
            parts = []
            if ch.age:
                parts.append(f"{ch.age}-year-old")
            if ch.gender:
                parts.append(ch.gender)
            parts.append("character")
            if ch.appearance:
                parts.append(f", {ch.appearance}")
            desc = " ".join(parts).strip()
            if desc and desc != "character":
                return desc
            if ch.name:
                return f"{ch.name}, {ch.appearance or ch.role or 'protagonist'}"

        # 2. style_guide.character_refs 第一个 (有 description 字段)
        if ctx.style_guide.character_refs:
            ref = ctx.style_guide.character_refs[0]
            if ref.description.strip():
                return f"{ref.name} ({ref.role}), {ref.description}"

        # 3. 通用
        return "a contemplative protagonist"

    @staticmethod
    def _pick_scene_palette(sg: StyleGuide, chapter_idx: int) -> str:
        """按 chapter_idx 轮转 scene_palette; 没色板 → generic

        逻辑:
        - 有 N 个 scene, ch-1 → 第 1 个, ch-2 → 第 2 个, ..., ch-N → 第 N 个
        - ch-(N+1) → 回到第 1 个 (循环)
        - 没 scene_palette → "natural muted tones"
        """
        if not sg.scene_palette:
            return "natural muted tones"
        scenes = list(sg.scene_palette.values())
        if not scenes:
            return "natural muted tones"
        idx = (chapter_idx - 1) % len(scenes)
        return scenes[idx]


# ============================================================
# 单次调用 helper (老板 CLI 调试用)
# ============================================================


def build_cover_prompt(
    style_guide: StyleGuide,
    characters: Characters | None = None,
    *,
    chapter_idx: int = 1,
    chapter_scene: str = "",
) -> str:
    """单次调用 helper"""
    return CoverPromptBuilder().build(
        CoverPromptContext(
            style_guide=style_guide,
            characters=characters,
            chapter_idx=chapter_idx,
            chapter_scene=chapter_scene,
        )
    )
