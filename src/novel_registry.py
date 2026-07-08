"""
novel_registry — 多本小说注册表 (v0.3.2 P0)
==============================================
- 加载 novels.yaml (PyYAML + Pydantic 校验)
- 提供 load_novels() / current_volume() / get_novel()
- 校验:
  - id 唯一
  - slug 唯一
  - volumes 不能空, order 唯一递增
  - start_chapter <= end_chapter
  - chapter_slug_template 含 {novel_slug} 和 {idx:03d}
  - daily_chapter_target 与 hours.length 一致性 (warning, 不阻断)

依赖: pydantic >= 2.5, pyyaml
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


def _coerce_iso_datetime(v: Any) -> str:
    """PyYAML 会把 ISO 8601 字符串解析为 datetime, 这里统一转成 str"""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, str):
        return v
    raise ValueError(f"created_at 必须是 ISO 8601 str 或 datetime, got {type(v).__name__}")

# ============================================================
# Schema 版本 (novels.yaml 升级时方便迁移)
# ============================================================
NOVEL_REGISTRY_SCHEMA_VERSION = 1

# 老板可改默认 novel 配置文件位置
DEFAULT_NOVELS_YAML = Path("novels.yaml")


# ============================================================
# Pydantic models
# ============================================================


class NovelPaths(BaseModel):
    """backups 仓路径 (相对 backups 仓根)"""

    outline: str
    outline_meta: str
    style_guide: str
    characters: str
    state: str
    chapters_dir: str


class Volume(BaseModel):
    """单卷配置 (3-tier Novel > Volume > Chapter)"""

    order: int = Field(ge=1, description="卷序号, 1-based")
    title: str = Field(min_length=1)
    description: str | None = None
    start_chapter: int = Field(ge=1)
    end_chapter: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_range(self) -> Volume:
        if self.start_chapter > self.end_chapter:
            raise ValueError(
                f"Volume {self.order}: start_chapter ({self.start_chapter}) > "
                f"end_chapter ({self.end_chapter})"
            )
        return self


class Novel(BaseModel):
    """单本小说配置 (对应 novels.yaml 一条)"""

    id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    slug: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    description: str = ""
    status: str = "ongoing"  # ongoing | completed | hiatus
    enabled: bool = True
    daily_chapter: bool = True  # 7-8 老板拍: 默认 true, 7-8 老板拍 "所有 enabled 的每天都更新"
    target_word_count: int = Field(default=3000, ge=500, le=20000)
    category: str = ""
    keywords: list[str] = Field(default_factory=list)
    volumes: list[Volume] = Field(min_length=1)
    chapter_slug_template: str = "{novel_slug}-ch{idx:03d}"
    paths: NovelPaths
    created_at: str  # ISO 8601 (PyYAML 解析后可能是 datetime, 由 _coerce_iso_datetime 统一转 str)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, v: Any) -> str:
        return _coerce_iso_datetime(v)

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        if v not in ("ongoing", "completed", "hiatus"):
            raise ValueError(f"status must be ongoing|completed|hiatus, got {v!r}")
        return v

    @field_validator("chapter_slug_template")
    @classmethod
    def _check_slug_template(cls, v: str) -> str:
        """必须含 {novel_slug} 和 {idx:03d}"""
        if "{novel_slug}" not in v:
            raise ValueError(f"chapter_slug_template missing {{novel_slug}}: {v!r}")
        if "{idx" not in v:
            raise ValueError(f"chapter_slug_template missing {{idx}}: {v!r}")
        return v


class Schedule(BaseModel):
    """推送时间窗 (systemd timer 配合)"""

    hours: list[int] = Field(default_factory=lambda: [8, 12, 18])
    per_run_novel_limit: int | None = None  # null = 写所有 enabled
    daily_chapter_target: int = 3  # 每本每天目标章数

    @field_validator("hours")
    @classmethod
    def _check_hours(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("schedule.hours must not be empty")
        for h in v:
            if not 0 <= h <= 23:
                raise ValueError(f"schedule.hours has invalid hour {h}")
        return sorted(set(v))  # 去重 + 排序

    @field_validator("daily_chapter_target")
    @classmethod
    def _check_target(cls, v: int, info: Any) -> int:
        if v < 1:
            raise ValueError(f"daily_chapter_target must be >= 1, got {v}")
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> Schedule:
        # 软校验: daily_chapter_target 应当与 hours.length 一致 (或为其倍数)
        # 不一致只 warning, 不阻断
        if self.daily_chapter_target != len(self.hours):
            logger.warning(
                "schedule.daily_chapter_target (%d) != len(schedule.hours) (%d). "
                "建议设为相同值, 否则 LLM 配额控制不直观",
                self.daily_chapter_target,
                len(self.hours),
            )
        return self


class NovelRegistry(BaseModel):
    """novels.yaml 顶层结构"""

    novels: list[Novel] = Field(min_length=1)
    schedule: Schedule = Field(default_factory=Schedule)
    schema_version: int = NOVEL_REGISTRY_SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_uniqueness(self) -> NovelRegistry:
        # id 唯一
        ids = [n.id for n in self.novels]
        if len(set(ids)) != len(ids):
            dups = [x for x in ids if ids.count(x) > 1]
            raise ValueError(f"novel id 重复: {sorted(set(dups))}")
        # slug 唯一
        slugs = [n.slug for n in self.novels]
        if len(set(slugs)) != len(slugs):
            dups = [x for x in slugs if slugs.count(x) > 1]
            raise ValueError(f"novel slug 重复: {sorted(set(dups))}")
        # 每本 volumes order 唯一递增
        for novel in self.novels:
            orders = [v.order for v in novel.volumes]
            if sorted(orders) != list(range(1, len(orders) + 1)):
                raise ValueError(
                    f"novel {novel.id}: volumes order 必须是 1..N 连续, got {orders}"
                )
        return self


# ============================================================
# 加载与查询
# ============================================================


def load_novels(path: Path = DEFAULT_NOVELS_YAML) -> NovelRegistry:
    """加载 novels.yaml, 校验后返回 NovelRegistry

    Raises:
        FileNotFoundError: 文件不存在
        yaml.YAMLError: YAML 解析失败
        pydantic.ValidationError: schema 校验失败
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"novels.yaml 不存在: {path}")

    logger.info("加载 novels.yaml: %s", path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"novels.yaml 是空文件: {path}")

    registry = NovelRegistry.model_validate(raw)
    logger.info(
        "novels.yaml 加载成功: %d 本 (enabled=%d, status=%s)",
        len(registry.novels),
        sum(1 for n in registry.novels if n.enabled),
        {n.id: n.status for n in registry.novels},
    )
    return registry


def get_novel(registry: NovelRegistry, novel_id: str) -> Novel:
    """按 id 查 novel; 不存在抛 KeyError"""
    for n in registry.novels:
        if n.id == novel_id:
            return n
    raise KeyError(f"novel id 不存在: {novel_id!r} (可选: {[n.id for n in registry.novels]})")


def get_enabled_novels(registry: NovelRegistry) -> list[Novel]:
    """返回所有 enabled 的 novel (供 publisher 7-8 老板拍: "所有 enabled 都写")"""
    return [n for n in registry.novels if n.enabled]


# ============================================================
# 卷切换 (3-tier Novel > Volume > Chapter)
# ============================================================


class VolumeFullError(Exception):
    """小说所有卷都写满了, 需要在 novels.yaml 加新卷"""


def current_volume(novel: Novel, chapter_idx: int) -> Volume:
    """根据 chapter_idx 返回当前所属卷

    Raises:
        VolumeFullError: chapter_idx 超过最后一卷的 end_chapter
    """
    for vol in novel.volumes:
        if vol.start_chapter <= chapter_idx <= vol.end_chapter:
            return vol

    # chapter_idx 超过最后一卷
    last_vol = novel.volumes[-1]
    if chapter_idx > last_vol.end_chapter:
        raise VolumeFullError(
            f"小说 {novel.id} 第 {chapter_idx} 章超过最后一卷 "
            f"({last_vol.title} 上限 {last_vol.end_chapter} 章), "
            f"请在 novels.yaml 加新卷"
        )
    # chapter_idx < 第一卷的 start_chapter (边界: 老板可写 start_chapter=2?)
    return novel.volumes[0]


def render_chapter_slug(novel: Novel, chapter_idx: int) -> str:
    """渲染 chapter slug: 用 novels.yaml 的 chapter_slug_template

    e.g. "{novel_slug}-ch{idx:03d}" + novel_slug="meta-realm" + idx=4
       → "meta-realm-ch004"
    """
    template = novel.chapter_slug_template
    return template.format(novel_slug=novel.slug, idx=chapter_idx)


# ============================================================
# 校验 + 报告
# ============================================================


def print_registry_summary(registry: NovelRegistry) -> str:
    """人类可读的多本注册表摘要 (供 CLI / 日报使用)"""
    lines = ["=" * 60, f"📚 多本小说注册表 (schema v{registry.schema_version})", "=" * 60]
    for n in registry.novels:
        status = "🟢 enabled" if n.enabled else "⚪ disabled"
        daily = "📅 daily" if n.daily_chapter else "⏸ paused"
        lines.append(
            f"  {status} {daily}  {n.id} → '{n.title}' (slug={n.slug}, "
            f"status={n.status}, {len(n.volumes)} 卷, keywords={len(n.keywords)})"
        )
        for v in n.volumes:
            lines.append(
                f"      Vol.{v.order}: {v.title} (ch.{v.start_chapter}-{v.end_chapter})"
            )
    s = registry.schedule
    lines.append(
        f"\n⏰ schedule: hours={s.hours}, daily_target={s.daily_chapter_target}/本/天, "
        f"per_run_limit={s.per_run_novel_limit or 'ALL enabled'}"
    )
    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================
# CLI 入口 (调试用: python -m src.novel_registry)
# ============================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NOVELS_YAML
        reg = load_novels(path)
        print(print_registry_summary(reg))
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
