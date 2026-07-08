"""
style_guide — style_guide.md 拉取 + 解析 (v0.3.2 P1)
======================================================
- 拉 backups 仓 novels/{id}/style_guide.md
- 解析为 StyleGuide dataclass:
  - style_description: 风格主调 (## 风格主调 段)
  - character_refs: list[CharacterRef] (## 核心人物 段, 每条 "- **姓名 (角色)**: 描述")
  - scene_palette: dict[scene_name, color_string] (## 场景色板 段)
  - cover_prompt_template: str (## 封面 prompt 模板 段内 code block)
  - raw: 完整 markdown (兜底)
- 缓存到 data/cache/{novel_id}/style_guide.md + style_guide.sha

依赖: src/backup_reader.py
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backup_reader import BackupReader, BackupReaderError
from .novel_outline import DEFAULT_CACHE_DIR, OutlineResult, _cache_paths

logger = logging.getLogger(__name__)


# ============================================================
# Parsed models
# ============================================================


@dataclass
class CharacterRef:
    """主角/配角 描述 (style_guide.md 内的固定人物描述)"""

    name: str  # "林深"
    role: str  # "主角" / "AI 助手" / "导师"
    description: str  # "28 岁东亚男性, 短黑发, 银白实验服, ..."


@dataclass
class StyleGuide:
    """解析后的 style_guide.md"""

    style_description: str = ""  # 风格主调
    character_refs: list[CharacterRef] = field(default_factory=list)  # 核心人物
    scene_palette: dict[str, str] = field(default_factory=dict)  # 场景色板
    cover_prompt_template: str = ""  # 封面 prompt 模板
    raw: str = ""  # 完整 markdown 兜底

    def main_character(self) -> CharacterRef | None:
        """拿主角 (role 含 "主角" 第一个)"""
        for c in self.character_refs:
            if "主角" in c.role:
                return c
        return self.character_refs[0] if self.character_refs else None


# ============================================================
# Markdown 解析
# ============================================================


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_CHARACTER_RE = re.compile(
    r"^-\s+\*\*(?P<name>[^*]+?)\s*\((?P<role>[^)]+)\)\*\*:\s*(?P<desc>.+?)$",
    re.MULTILINE,
)
_PALETTE_RE = re.compile(
    r"^-\s+(?P<scene>[^:：]+?)\s*[:：]\s*(?P<colors>.+?)$", re.MULTILINE
)
_CODE_BLOCK_RE = re.compile(r"```(?:[a-z]*\n)?(?P<content>.+?)```", re.DOTALL)


def _split_sections(md: str) -> dict[str, str]:
    """按 ## section 切分

    e.g. md = "# title\\n## A\\nbody_a\\n## B\\nbody_b"
       → {"A": "body_a", "B": "body_b", "_preamble": "# title"}
       → 同时为 "## 核心人物 (固定, 跨章一致)" 添加 alias "核心人物"
    """
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(md))
    if not matches:
        sections["_preamble"] = md.strip()
        return sections

    # 第一个 ## 之前的内容
    first_start = matches[0].start()
    if first_start > 0:
        sections["_preamble"] = md[:first_start].strip()

    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[body_start:body_end].strip()
        sections[title] = body
        # alias: 去括号内容作为额外 key
        # "核心人物 (固定, 跨章一致)" → "核心人物"
        title_no_paren = re.sub(r"\s*\([^)]*\)\s*", "", title).strip()
        if title_no_paren and title_no_paren != title:
            sections[title_no_paren] = body

    return sections


def _parse_characters(text: str) -> list[CharacterRef]:
    """解析 '- **林深 (主角)**: 28 岁东亚男性, ...' 形式"""
    refs: list[CharacterRef] = []
    for m in _CHARACTER_RE.finditer(text):
        refs.append(
            CharacterRef(
                name=m.group("name").strip(),
                role=m.group("role").strip(),
                description=m.group("desc").strip(),
            )
        )
    return refs


def _parse_palette(text: str) -> dict[str, str]:
    """解析 '- 月球基地: 蓝灰 #2C3E50 + 月光银 #C0C0C0' 形式"""
    palette: dict[str, str] = {}
    for m in _PALETTE_RE.finditer(text):
        scene = m.group("scene").strip()
        colors = m.group("colors").strip()
        palette[scene] = colors
    return palette


def _extract_code_block(text: str) -> str:
    """提取第一个 code block 内容"""
    m = _CODE_BLOCK_RE.search(text)
    if m:
        return m.group("content").strip()
    return ""


def parse_style_guide(md: str) -> StyleGuide:
    """解析 style_guide.md → StyleGuide"""
    sections = _split_sections(md)

    # 风格主调
    style_description = sections.get("风格主调", "").strip()

    # 核心人物
    character_refs = _parse_characters(sections.get("核心人物", "") + sections.get("人物", ""))

    # 场景色板
    scene_palette = _parse_palette(sections.get("场景色板", "") + sections.get("色板", ""))

    # 封面 prompt 模板
    cover_section = sections.get("封面 prompt 模板", "") or sections.get("封面", "")
    cover_prompt_template = _extract_code_block(cover_section)

    return StyleGuide(
        style_description=style_description,
        character_refs=character_refs,
        scene_palette=scene_palette,
        cover_prompt_template=cover_prompt_template,
        raw=md,
    )


# ============================================================
# 拉取 (复用 novel_outline 缓存模式)
# ============================================================


def _style_guide_paths(novel_id: str, base_dir: Path) -> tuple[Path, Path]:
    return _cache_paths(novel_id, base_dir)  # 同 novel_id 的 cache 目录
# 实际缓存路径 = base_dir/novel_id/style_guide.{md,sha} (与 outline 同目录, 不同文件名)


def _style_guide_file_paths(novel_id: str, base_dir: Path = DEFAULT_CACHE_DIR) -> tuple[Path, Path]:
    """style_guide.md 实际缓存路径 (与 outline.md 同 novel_id 目录)"""
    import re as _re
    if not _re.match(r"^[a-z0-9_]+$", novel_id):
        raise ValueError(f"novel_id 非法: {novel_id!r}")
    d = base_dir / novel_id
    return d / "style_guide.md", d / "style_guide.sha"


def _read_cached_sha(sha_path: Path) -> str | None:
    if not sha_path.exists():
        return None
    return sha_path.read_text(encoding="utf-8").strip() or None


def _write_cache(content_path: Path, sha_path: Path, content: str, sha: str) -> None:
    content_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_c = content_path.with_suffix(content_path.suffix + ".tmp")
    tmp_s = sha_path.with_suffix(sha_path.suffix + ".tmp")
    tmp_c.write_text(content, encoding="utf-8")
    tmp_s.write_text(sha, encoding="utf-8")
    tmp_c.replace(content_path)
    tmp_s.replace(sha_path)


def fetch_style_guide(
    reader: BackupReader,
    novel_id: str,
    style_guide_path: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> tuple[StyleGuide, OutlineResult]:
    """拉取 style_guide.md + 解析

    Returns:
        (StyleGuide, OutlineResult) — OutlineResult 含 sha / is_changed
    """
    content_path, sha_path = _style_guide_file_paths(novel_id, cache_dir)
    cached_sha = None if force_refresh else _read_cached_sha(sha_path)

    # 拉 GitHub
    try:
        result = reader.fetch_file(style_guide_path)
    except BackupReaderError as e:
        err = f"拉取 style_guide 失败: {e}"
        logger.error("[style_guide] %s — 尝试用旧缓存", err)
        if content_path.exists():
            old_md = content_path.read_text(encoding="utf-8")
            return parse_style_guide(old_md), OutlineResult(
                content=old_md, sha=cached_sha or "", is_changed=False,
                cache_path=content_path, error=err,
            )
        return StyleGuide(raw=""), OutlineResult(
            content="", sha="", is_changed=False, cache_path=content_path, error=err,
        )

    if result is None:
        if content_path.exists():
            logger.warning("[style_guide] 在 GitHub 不存在 (%s), 用旧缓存", style_guide_path)
            old_md = content_path.read_text(encoding="utf-8")
            return parse_style_guide(old_md), OutlineResult(
                content=old_md, sha=cached_sha or "", is_changed=False,
                cache_path=content_path, error=f"404: {style_guide_path}",
            )
        return StyleGuide(raw=""), OutlineResult(
            content="", sha="", is_changed=False, cache_path=content_path,
            error=f"404: {style_guide_path}",
        )

    new_content = result["content"]
    new_sha = result["sha"]
    is_changed = (cached_sha is None) or (new_sha != cached_sha)

    if is_changed:
        logger.info(
            "[style_guide] sha 变化: %s → %s (%s)",
            (cached_sha or "NONE")[:12], new_sha[:12], style_guide_path,
        )
    _write_cache(content_path, sha_path, new_content, new_sha)

    return parse_style_guide(new_content), OutlineResult(
        content=new_content, sha=new_sha, is_changed=is_changed, cache_path=content_path,
    )


def get_cached_style_guide(
    novel_id: str, cache_dir: Path = DEFAULT_CACHE_DIR
) -> StyleGuide | None:
    """只读缓存 (不调 GitHub)"""
    content_path, _ = _style_guide_file_paths(novel_id, cache_dir)
    if not content_path.exists():
        return None
    return parse_style_guide(content_path.read_text(encoding="utf-8"))
