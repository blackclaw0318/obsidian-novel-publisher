"""
character_loader — characters.md 拉取 + 解析 (v0.3.2 P1)
==========================================================
- 拉 backups 仓 novels/{id}/characters.md
- 解析为 Characters { main: Character, supporting: list[Character] }
- Character 字段: name, role, gender, age, appearance, personality, background, items, relationships

依赖: src/backup_reader.py
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .backup_reader import BackupReader, BackupReaderError
from .novel_outline import DEFAULT_CACHE_DIR, OutlineResult
from .style_guide import (
    _read_cached_sha,
    _write_cache,
    parse_style_guide,  # noqa: F401  (re-export for convenience)
)
from .style_guide import StyleGuide as ParsedStyleGuide  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)


# ============================================================
# Models
# ============================================================


@dataclass
class Character:
    """单个人物卡"""

    name: str
    role: str = ""  # 主角 / 配角 / 反派
    gender: str = ""
    age: str = ""
    appearance: str = ""
    personality: str = ""
    background: str = ""
    items: list[str] = field(default_factory=list)  # 重要物品
    relationships: list[str] = field(default_factory=list)  # 重要关系

    def one_line_description(self) -> str:
        """prompt 用单行描述 (appearance + role 拼接)"""
        parts = []
        if self.age:
            parts.append(f"{self.age}岁")
        if self.gender:
            parts.append(self.gender)
        parts.append("性")
        if self.appearance:
            parts.append(f", {self.appearance}")
        return "".join(parts)


@dataclass
class Characters:
    """整本小说的人物集"""

    main: Character | None = None
    supporting: list[Character] = field(default_factory=list)
    raw: str = ""

    def all(self) -> list[Character]:
        """所有人物 (main + supporting)"""
        result = []
        if self.main:
            result.append(self.main)
        result.extend(self.supporting)
        return result


# ============================================================
# Markdown 解析
# ============================================================


_CHAR_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FIELD_RE = re.compile(r"^-\s+\*?\*?(?P<key>[^*:：]+?)\*?\*?\s*[:：]\s*(?P<value>.+?)$", re.MULTILINE)


def _parse_character_block(name: str, body: str) -> Character:
    """解析 '## 姓名' 下方的字段列表 → Character"""
    char = Character(name=name.strip())

    # 提取 role (可能 "## 林深 (主角)" 也可能 "## 林深" 后跟 "- 角色: 主角")
    role_in_name = re.search(r"\(([^)]+)\)", name)
    if role_in_name:
        char.role = role_in_name.group(1).strip()
        char.name = re.sub(r"\s*\([^)]+\)\s*", "", name).strip()

    for m in _FIELD_RE.finditer(body):
        key = m.group("key").strip().lower()
        value = m.group("value").strip()
        # 字段名归一化
        if key in ("角色", "role"):
            char.role = value
        elif key in ("性别", "gender"):
            char.gender = value
        elif key in ("年龄", "age"):
            char.age = value
        elif key in ("外貌", "appearance", "长相"):
            char.appearance = value
        elif key in ("性格", "personality"):
            char.personality = value
        elif key in ("背景", "background", "身世", "故事背景"):
            char.background = value
        elif key in ("重要物品", "物品", "items"):
            char.items = [x.strip() for x in re.split(r"[,，、;；]", value) if x.strip()]
        elif key in ("重要关系", "关系", "relationships"):
            char.relationships = [x.strip() for x in re.split(r"[,，、;；]", value) if x.strip()]

    return char


def parse_characters(md: str) -> Characters:
    """解析 characters.md → Characters"""
    # 找所有 "## 姓名" 段
    matches = list(_CHAR_SECTION_RE.finditer(md))
    if not matches:
        return Characters(raw=md)

    characters: list[Character] = []
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[body_start:body_end].strip()
        char = _parse_character_block(name, body)
        characters.append(char)

    # 第一个含"主角"role 的作 main, 其余 supporting
    main_char: Character | None = None
    supporting: list[Character] = []
    for c in characters:
        if "主角" in c.role and main_char is None:
            main_char = c
        else:
            supporting.append(c)

    # 如果都没标"主角", 第一个作 main
    if main_char is None and characters:
        main_char = characters[0]
        supporting = characters[1:]

    return Characters(main=main_char, supporting=supporting, raw=md)


# ============================================================
# 拉取
# ============================================================


def _characters_file_paths(novel_id: str, base_dir: Path = DEFAULT_CACHE_DIR) -> tuple[Path, Path]:
    import re as _re
    if not _re.match(r"^[a-z0-9_]+$", novel_id):
        raise ValueError(f"novel_id 非法: {novel_id!r}")
    d = base_dir / novel_id
    return d / "characters.md", d / "characters.sha"


def fetch_characters(
    reader: BackupReader,
    novel_id: str,
    characters_path: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> tuple[Characters, OutlineResult]:
    """拉取 characters.md + 解析

    Returns:
        (Characters, OutlineResult)
    """
    content_path, sha_path = _characters_file_paths(novel_id, cache_dir)
    cached_sha = None if force_refresh else _read_cached_sha(sha_path)

    try:
        result = reader.fetch_file(characters_path)
    except BackupReaderError as e:
        err = f"拉取 characters 失败: {e}"
        logger.error("[character_loader] %s — 尝试用旧缓存", err)
        if content_path.exists():
            old_md = content_path.read_text(encoding="utf-8")
            return parse_characters(old_md), OutlineResult(
                content=old_md, sha=cached_sha or "", is_changed=False,
                cache_path=content_path, error=err,
            )
        return Characters(raw=""), OutlineResult(
            content="", sha="", is_changed=False, cache_path=content_path, error=err,
        )

    if result is None:
        if content_path.exists():
            logger.warning("[character_loader] 404, 用旧缓存 (%s)", characters_path)
            old_md = content_path.read_text(encoding="utf-8")
            return parse_characters(old_md), OutlineResult(
                content=old_md, sha=cached_sha or "", is_changed=False,
                cache_path=content_path, error=f"404: {characters_path}",
            )
        return Characters(raw=""), OutlineResult(
            content="", sha="", is_changed=False, cache_path=content_path,
            error=f"404: {characters_path}",
        )

    new_content = result["content"]
    new_sha = result["sha"]
    is_changed = (cached_sha is None) or (new_sha != cached_sha)

    if is_changed:
        logger.info(
            "[character_loader] sha 变化: %s → %s (%s)",
            (cached_sha or "NONE")[:12], new_sha[:12], characters_path,
        )
    _write_cache(content_path, sha_path, new_content, new_sha)

    return parse_characters(new_content), OutlineResult(
        content=new_content, sha=new_sha, is_changed=is_changed, cache_path=content_path,
    )


def get_cached_characters(
    novel_id: str, cache_dir: Path = DEFAULT_CACHE_DIR
) -> Characters | None:
    """只读缓存"""
    content_path, _ = _characters_file_paths(novel_id, cache_dir)
    if not content_path.exists():
        return None
    return parse_characters(content_path.read_text(encoding="utf-8"))
