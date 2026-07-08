"""
novel_registry 单测 (v0.3.2 P0)
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.novel_registry import (
    DEFAULT_NOVELS_YAML,
    Novel,
    NovelPaths,
    NovelRegistry,
    Schedule,
    Volume,
    VolumeFullError,
    current_volume,
    get_enabled_novels,
    get_novel,
    load_novels,
    print_registry_summary,
    render_chapter_slug,
)

# ============================================================
# fixtures
# ============================================================

VALID_NOVELS_YAML = textwrap.dedent(
    """
    novels:
      - id: meta_realm_obsidian
        title: 元界
        slug: meta-realm
        description: 意识与觉醒
        status: ongoing
        enabled: true
        daily_chapter: true
        target_word_count: 3000
        category: 科幻
        keywords: [意识上传, 月球基地]
        volumes:
          - order: 1
            title: 第一卷
            start_chapter: 1
            end_chapter: 50
        chapter_slug_template: "{novel_slug}-ch{idx:03d}"
        paths:
          outline: novels/meta/outline.md
          outline_meta: novels/meta/outline.meta.json
          style_guide: novels/meta/style_guide.md
          characters: novels/meta/characters.md
          state: novels/meta/state.json
          chapters_dir: novels/meta/chapters/
        created_at: 2026-07-08T00:00:00Z
      - id: glass_sea_obsidian
        title: 玻璃海
        slug: glass-sea
        description: 退潮
        status: ongoing
        enabled: false
        daily_chapter: true
        target_word_count: 3000
        category: 玄幻
        keywords: [退潮]
        volumes:
          - order: 1
            title: 卷一
            start_chapter: 1
            end_chapter: 30
        chapter_slug_template: "{novel_slug}-ch{idx:03d}"
        paths:
          outline: novels/glass/outline.md
          outline_meta: novels/glass/outline.meta.json
          style_guide: novels/glass/style_guide.md
          characters: novels/glass/characters.md
          state: novels/glass/state.json
          chapters_dir: novels/glass/chapters/
        created_at: 2026-07-08T00:00:00Z
    schedule:
      hours: [8, 12, 18]
      per_run_novel_limit: null
      daily_chapter_target: 3
    """
)


@pytest.fixture
def tmp_yaml(tmp_path: Path) -> Path:
    """写入临时 novels.yaml"""
    p = tmp_path / "novels.yaml"
    p.write_text(VALID_NOVELS_YAML, encoding="utf-8")
    return p


# ============================================================
# Novel schema 校验
# ============================================================


class TestNovelSchema:
    def test_minimal_valid_novel(self):
        """最小可用的 Novel (Pydantic 直构)"""
        n = Novel(
            id="test_obsidian",
            title="测试",
            slug="test",
            description="",
            status="ongoing",
            enabled=True,
            daily_chapter=True,
            target_word_count=3000,
            category="",
            keywords=[],
            volumes=[Volume(order=1, title="V1", start_chapter=1, end_chapter=10)],
            chapter_slug_template="{novel_slug}-ch{idx:03d}",
            paths=NovelPaths(
                outline="o",
                outline_meta="om",
                style_guide="sg",
                characters="c",
                state="s",
                chapters_dir="cd",
            ),
            created_at="2026-07-08T00:00:00Z",
        )
        assert n.id == "test_obsidian"
        assert n.daily_chapter is True

    def test_id_must_be_lowercase_alnum_underscore(self):
        with pytest.raises(ValidationError) as exc:
            Novel(
                id="Invalid-ID",
                title="X",
                slug="x",
                status="ongoing",
                volumes=[Volume(order=1, title="v", start_chapter=1, end_chapter=1)],
                paths=NovelPaths(
                    outline="o",
                    outline_meta="om",
                    style_guide="sg",
                    characters="c",
                    state="s",
                    chapters_dir="cd",
                ),
                created_at="2026-07-08T00:00:00Z",
            )
        assert "id" in str(exc.value).lower()

    def test_status_must_be_valid(self):
        with pytest.raises(ValidationError) as exc:
            Novel(
                id="ok",
                title="X",
                slug="ok",
                status="weird",
                volumes=[Volume(order=1, title="v", start_chapter=1, end_chapter=1)],
                paths=NovelPaths(
                    outline="o",
                    outline_meta="om",
                    style_guide="sg",
                    characters="c",
                    state="s",
                    chapters_dir="cd",
                ),
                created_at="2026-07-08T00:00:00Z",
            )
        assert "status" in str(exc.value)

    def test_target_word_count_bounds(self):
        # 太小 (< 500)
        with pytest.raises(ValidationError):
            Novel(
                id="ok",
                title="X",
                slug="ok",
                target_word_count=100,
                volumes=[Volume(order=1, title="v", start_chapter=1, end_chapter=1)],
                paths=NovelPaths(
                    outline="o",
                    outline_meta="om",
                    style_guide="sg",
                    characters="c",
                    state="s",
                    chapters_dir="cd",
                ),
                created_at="2026-07-08T00:00:00Z",
            )

    def test_chapter_slug_template_must_have_novel_slug_and_idx(self):
        with pytest.raises(ValidationError) as exc:
            Novel(
                id="ok",
                title="X",
                slug="ok",
                chapter_slug_template="ch-{idx:03d}",  # 缺 {novel_slug}
                volumes=[Volume(order=1, title="v", start_chapter=1, end_chapter=1)],
                paths=NovelPaths(
                    outline="o",
                    outline_meta="om",
                    style_guide="sg",
                    characters="c",
                    state="s",
                    chapters_dir="cd",
                ),
                created_at="2026-07-08T00:00:00Z",
            )
        assert "novel_slug" in str(exc.value)

    def test_volume_start_must_le_end(self):
        with pytest.raises(ValidationError) as exc:
            Volume(order=1, title="v", start_chapter=10, end_chapter=5)
        assert "start_chapter" in str(exc.value)


# ============================================================
# NovelRegistry 全局校验
# ============================================================


class TestNovelRegistry:
    def test_load_valid_yaml(self, tmp_yaml: Path):
        reg = load_novels(tmp_yaml)
        assert len(reg.novels) == 2
        assert reg.novels[0].id == "meta_realm_obsidian"
        assert reg.novels[1].id == "glass_sea_obsidian"
        assert reg.schedule.hours == [8, 12, 18]
        assert reg.schedule.daily_chapter_target == 3

    def test_duplicate_id_rejected(self, tmp_path: Path):
        bad = textwrap.dedent(
            """
            novels:
              - id: dup_obsidian
                title: A
                slug: a
                volumes: [{order: 1, title: v, start_chapter: 1, end_chapter: 1}]
                paths: {outline: o, outline_meta: om, style_guide: sg, characters: c, state: s, chapters_dir: cd}
                created_at: 2026-07-08T00:00:00Z
              - id: dup_obsidian
                title: B
                slug: b
                volumes: [{order: 1, title: v, start_chapter: 1, end_chapter: 1}]
                paths: {outline: o, outline_meta: om, style_guide: sg, characters: c, state: s, chapters_dir: cd}
                created_at: 2026-07-08T00:00:00Z
            """
        )
        p = tmp_path / "bad.yaml"
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ValidationError) as exc:
            load_novels(p)
        assert "id 重复" in str(exc.value)

    def test_duplicate_slug_rejected(self, tmp_path: Path):
        bad = textwrap.dedent(
            """
            novels:
              - id: a_obsidian
                title: A
                slug: same
                volumes: [{order: 1, title: v, start_chapter: 1, end_chapter: 1}]
                paths: {outline: o, outline_meta: om, style_guide: sg, characters: c, state: s, chapters_dir: cd}
                created_at: 2026-07-08T00:00:00Z
              - id: b_obsidian
                title: B
                slug: same
                volumes: [{order: 1, title: v, start_chapter: 1, end_chapter: 1}]
                paths: {outline: o, outline_meta: om, style_guide: sg, characters: c, state: s, chapters_dir: cd}
                created_at: 2026-07-08T00:00:00Z
            """
        )
        p = tmp_path / "bad.yaml"
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ValidationError) as exc:
            load_novels(p)
        assert "slug 重复" in str(exc.value)

    def test_volumes_order_must_be_sequential(self, tmp_path: Path):
        bad = textwrap.dedent(
            """
            novels:
              - id: a_obsidian
                title: A
                slug: a
                volumes:
                  - {order: 1, title: v1, start_chapter: 1, end_chapter: 30}
                  - {order: 3, title: v3, start_chapter: 31, end_chapter: 60}  # 缺 order=2
                paths: {outline: o, outline_meta: om, style_guide: sg, characters: c, state: s, chapters_dir: cd}
                created_at: 2026-07-08T00:00:00Z
            """
        )
        p = tmp_path / "bad.yaml"
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ValidationError) as exc:
            load_novels(p)
        assert "order" in str(exc.value).lower()

    def test_schedule_hours_sorted_and_deduped(self):
        s = Schedule(hours=[18, 8, 12, 12, 8])
        assert s.hours == [8, 12, 18]

    def test_schedule_target_mismatch_warning(self, caplog):
        """daily_chapter_target != len(hours) 应 warning 但不阻断"""
        with caplog.at_level("WARNING"):
            s = Schedule(hours=[8], daily_chapter_target=3)
        assert "daily_chapter_target" in caplog.text

    def test_default_novels_yaml_exists(self):
        """仓库根的 novels.yaml 必须存在 (P0 交付)"""
        assert DEFAULT_NOVELS_YAML.exists(), (
            f"novels.yaml 缺失: {DEFAULT_NOVELS_YAML} (P0 必须落地)"
        )


# ============================================================
# 查询
# ============================================================


class TestQueries:
    def test_get_novel_by_id(self, tmp_yaml: Path):
        reg = load_novels(tmp_yaml)
        n = get_novel(reg, "meta_realm_obsidian")
        assert n.title == "元界"

    def test_get_novel_not_found(self, tmp_yaml: Path):
        reg = load_novels(tmp_yaml)
        with pytest.raises(KeyError):
            get_novel(reg, "nope")

    def test_get_enabled_novels(self, tmp_yaml: Path):
        reg = load_novels(tmp_yaml)
        enabled = get_enabled_novels(reg)
        # VALID_NOVELS_YAML: 元界 enabled=true, 玻璃海 enabled=false
        assert len(enabled) == 1
        assert enabled[0].id == "meta_realm_obsidian"


# ============================================================
# current_volume (3-tier 切换)
# ============================================================


def _make_novel_with_volumes() -> Novel:
    """构造 3 卷小说: v1=1-10, v2=11-20, v3=21-30"""
    return Novel(
        id="multi_obsidian",
        title="X",
        slug="multi",
        status="ongoing",
        volumes=[
            Volume(order=1, title="V1", start_chapter=1, end_chapter=10),
            Volume(order=2, title="V2", start_chapter=11, end_chapter=20),
            Volume(order=3, title="V3", start_chapter=21, end_chapter=30),
        ],
        paths=NovelPaths(
            outline="o",
            outline_meta="om",
            style_guide="sg",
            characters="c",
            state="s",
            chapters_dir="cd",
        ),
        created_at="2026-07-08T00:00:00Z",
    )


class TestCurrentVolume:
    def test_in_first_volume(self):
        n = _make_novel_with_volumes()
        v = current_volume(n, 5)
        assert v.order == 1
        assert v.title == "V1"

    def test_in_second_volume(self):
        n = _make_novel_with_volumes()
        v = current_volume(n, 15)
        assert v.order == 2

    def test_at_volume_boundary_start(self):
        """第 11 章应是 V2 起点"""
        n = _make_novel_with_volumes()
        v = current_volume(n, 11)
        assert v.order == 2

    def test_at_volume_boundary_end(self):
        """第 10 章应是 V1 终点"""
        n = _make_novel_with_volumes()
        v = current_volume(n, 10)
        assert v.order == 1

    def test_exceeds_last_volume_raises(self):
        n = _make_novel_with_volumes()
        with pytest.raises(VolumeFullError) as exc:
            current_volume(n, 31)
        assert "multi_obsidian" in str(exc.value)
        assert "V3" in str(exc.value)


# ============================================================
# render_chapter_slug
# ============================================================


class TestRenderChapterSlug:
    def test_default_template(self, tmp_yaml: Path):
        reg = load_novels(tmp_yaml)
        n = get_novel(reg, "meta_realm_obsidian")
        assert render_chapter_slug(n, 4) == "meta-realm-ch004"
        assert render_chapter_slug(n, 100) == "meta-realm-ch100"

    def test_zero_padded(self, tmp_yaml: Path):
        reg = load_novels(tmp_yaml)
        n = get_novel(reg, "meta_realm_obsidian")
        assert render_chapter_slug(n, 1) == "meta-realm-ch001"  # 3 位补 0


# ============================================================
# 摘要 (人类可读)
# ============================================================


class TestSummary:
    def test_summary_contains_novel_ids(self, tmp_yaml: Path):
        reg = load_novels(tmp_yaml)
        s = print_registry_summary(reg)
        assert "meta_realm_obsidian" in s
        assert "glass_sea_obsidian" in s
        assert "元界" in s
        assert "玻璃海" in s
        assert "hours=[8, 12, 18]" in s
