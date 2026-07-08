"""
publisher.run_all_novels 单测 (v0.3.2 P2)
============================================
- _current_slot 配额档位计算
- _should_skip_slot 配额检查
- AggregateResult 汇总
- run_all_novels 错误隔离 + enabled 过滤 + 配额跳过
"""
from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.publisher import (
    AggregateResult,
    NovelRunResult,
    PublisherConfig,
    PublisherError,
    _current_slot,
    _should_skip_slot,
    run_all_novels,
)
from src.state import PublishState

# ============================================================
# Fixtures
# ============================================================


VALID_NOVELS_YAML = textwrap.dedent(
    """
    novels:
      - id: a_obsidian
        title: A
        slug: a
        status: ongoing
        enabled: true
        daily_chapter: true
        target_word_count: 3000
        category: 科幻
        keywords: [测试]
        volumes:
          - {order: 1, title: V1, start_chapter: 1, end_chapter: 50}
        chapter_slug_template: "{novel_slug}-ch{idx:03d}"
        paths:
          outline: novels/a/outline.md
          outline_meta: novels/a/outline.meta.json
          style_guide: novels/a/style_guide.md
          characters: novels/a/characters.md
          state: novels/a/state.json
          chapters_dir: novels/a/chapters/
        created_at: 2026-07-08T00:00:00Z
      - id: b_obsidian
        title: B
        slug: b
        status: ongoing
        enabled: false
        daily_chapter: true
        target_word_count: 3000
        category: 玄幻
        keywords: [测试]
        volumes:
          - {order: 1, title: V1, start_chapter: 1, end_chapter: 50}
        chapter_slug_template: "{novel_slug}-ch{idx:03d}"
        paths:
          outline: novels/b/outline.md
          outline_meta: novels/b/outline.meta.json
          style_guide: novels/b/style_guide.md
          characters: novels/b/characters.md
          state: novels/b/state.json
          chapters_dir: novels/b/chapters/
        created_at: 2026-07-08T00:00:00Z
    schedule:
      hours: [8, 12, 18]
      per_run_novel_limit: null
      daily_chapter_target: 3
    """
)


@pytest.fixture
def tmp_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "novels.yaml"
    p.write_text(VALID_NOVELS_YAML, encoding="utf-8")
    return p


@pytest.fixture
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 data 目录, 避免老测试 state 污染 + 不动真实 /opt 数据"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("src.state_per_novel.DEFAULT_STATE_DIR", data_dir / "state")
    monkeypatch.setattr("src.state_per_novel.FALLBACK_OLD_STATE", data_dir / "state.json")
    monkeypatch.setattr("src.state.DEFAULT_STATE_PATH", data_dir / "state.json")
    return data_dir


@pytest.fixture
def mock_config(isolated_data) -> PublisherConfig:
    """构造一个最小可用的 PublisherConfig, 不用 .env"""
    return PublisherConfig(
        minimaxi_api_key="sk-test",
        minimaxi_base_url="https://api.test/v1",
        minimaxi_text_model="M3",
        minimaxi_image_model="image-01",
        obsidian_publish_url="https://test.example/api/external/chapters",
        obsidian_publish_id="test",
        obsidian_publish_secret="secret",
        obsidian_admin_token="",
        obsidian_admin_base_url="https://test.example",
        state_path=Path("/tmp/state.json"),
        cover_tmp_dir=Path("/tmp/covers"),
        github_backup_repo="test/test",
        github_backup_token="",  # 空 → 不拉 outline
    )


# ============================================================
# _current_slot
# ============================================================


class TestCurrentSlot:
    def test_slot_at_8am(self):
        from src.novel_registry import Schedule
        s = Schedule(hours=[8, 12, 18])
        # 2026-07-08 08:00 UTC == 16:00 Shanghai (UTC+8)
        # 但 08:00 UTC < 16:00 Shanghai, 8:00 Shanghai → 0:00 UTC
        # 我们用 0:00 UTC = 08:00 Shanghai
        now = datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc)
        assert _current_slot(now, s, tz_name="Asia/Shanghai") == "2026-07-08-08"

    def test_slot_at_12pm_shanghai(self):
        from src.novel_registry import Schedule
        s = Schedule(hours=[8, 12, 18])
        # 04:00 UTC = 12:00 Shanghai
        now = datetime(2026, 7, 8, 4, 0, tzinfo=timezone.utc)
        assert _current_slot(now, s, tz_name="Asia/Shanghai") == "2026-07-08-12"

    def test_slot_at_18pm_shanghai(self):
        from src.novel_registry import Schedule
        s = Schedule(hours=[8, 12, 18])
        # 10:00 UTC = 18:00 Shanghai
        now = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
        assert _current_slot(now, s, tz_name="Asia/Shanghai") == "2026-07-08-18"

    def test_slot_in_between_floors_down(self):
        """14:00 Shanghai (在 12-18 之间) 应归到 12 档"""
        from src.novel_registry import Schedule
        s = Schedule(hours=[8, 12, 18])
        # 06:00 UTC = 14:00 Shanghai
        now = datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)
        assert _current_slot(now, s, tz_name="Asia/Shanghai") == "2026-07-08-12"

    def test_slot_before_first_hour_uses_first(self):
        """07:00 Shanghai (在 8 点之前) 应归到 8 档 (向下取首)"""
        from src.novel_registry import Schedule
        s = Schedule(hours=[8, 12, 18])
        # 23:00 UTC 前一天 = 07:00 Shanghai 当天
        # (2026-07-07 23:00 UTC) = (2026-07-08 07:00 Shanghai)
        now = datetime(2026, 7, 7, 23, 0, tzinfo=timezone.utc)
        assert _current_slot(now, s, tz_name="Asia/Shanghai") == "2026-07-08-08"

    def test_slot_unknown_tz_falls_back_utc(self):
        from src.novel_registry import Schedule
        s = Schedule(hours=[8, 12, 18])
        now = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
        slot = _current_slot(now, s, tz_name="Mars/Olympus_Mons")
        assert slot.startswith("2026-07-08-")


# ============================================================
# _should_skip_slot
# ============================================================


class TestShouldSkipSlot:
    def test_empty_slot_no_skip(self):
        s = PublishState()
        assert s.last_pushed_slot == ""
        assert _should_skip_slot(s, "2026-07-08-08") is False

    def test_same_slot_skip(self):
        s = PublishState(last_pushed_slot="2026-07-08-08")
        assert _should_skip_slot(s, "2026-07-08-08") is True

    def test_different_slot_no_skip(self):
        s = PublishState(last_pushed_slot="2026-07-08-08")
        assert _should_skip_slot(s, "2026-07-08-12") is False

    def test_different_day_no_skip(self):
        s = PublishState(last_pushed_slot="2026-07-08-18")
        assert _should_skip_slot(s, "2026-07-09-08") is False


# ============================================================
# AggregateResult
# ============================================================


class TestAggregateResult:
    def test_from_results_counts(self):
        results = [
            NovelRunResult(novel_id="a", status="success"),
            NovelRunResult(novel_id="b", status="failed", error="LLM error"),
            NovelRunResult(novel_id="c", status="skipped"),
        ]
        agg = AggregateResult.from_results(results)
        assert agg.total == 3
        assert agg.success == 1
        assert agg.failed == 1
        assert agg.skipped == 1

    def test_is_all_success(self):
        ok = AggregateResult(
            total=2, success=2, failed=0, skipped=0,
            details=[NovelRunResult(novel_id="a", status="success")],
        )
        assert ok.is_all_success() is True

        bad = AggregateResult(
            total=2, success=1, failed=1, skipped=0,
            details=[],
        )
        assert bad.is_all_success() is False

    def test_str_contains_all_novels(self):
        results = [
            NovelRunResult(novel_id="a", status="success", chapter_idx=1),
            NovelRunResult(novel_id="b", status="failed", error="network"),
        ]
        agg = AggregateResult.from_results(results)
        s = str(agg)
        assert "a" in s
        assert "b" in s
        assert "✅" in s
        assert "❌" in s
        assert "network" in s


# ============================================================
# run_all_novels (主流程)
# ============================================================


class TestRunAllNovels:
    def test_no_enabled_returns_empty_aggregate(self, mock_config, tmp_yaml):
        """所有 enabled=false → 返空 AggregateResult"""
        # 改 yaml 全 disabled
        bad = VALID_NOVELS_YAML.replace("enabled: true", "enabled: false")
        tmp_yaml.write_text(bad, encoding="utf-8")

        from src.novel_registry import load_novels
        registry = load_novels(tmp_yaml)
        agg = run_all_novels(mock_config, registry=registry)
        assert agg.total == 0
        assert agg.success == 0
        assert agg.failed == 0

    def test_only_enabled_novels_processed(self, mock_config, tmp_yaml):
        """enabled=true 的才会被调"""
        from src.novel_registry import load_novels
        registry = load_novels(tmp_yaml)
        assert len(get_enabled_via_registry := registry.novels) == 2
        assert sum(1 for n in registry.novels if n.enabled) == 1

    def test_error_isolation_one_fails_others_continue(self, mock_config, tmp_yaml):
        """1 本失败, 不阻塞其他本 (但本测试只 1 本 enabled, 改 yaml 让 2 本 enabled)"""
        yaml2 = VALID_NOVELS_YAML.replace(
            "      - id: b_obsidian",
            "      - id: b_obsidian"
        ).replace(
            "enabled: false",
            "enabled: true",
            1,  # 只替 1 次 (b 那一行)
        )
        # 上面 replace(1) 可能没生效, 简化: 直接写 2 本 enabled 的 yaml
        two_enabled = textwrap.dedent(
            """
            novels:
              - id: a_obsidian
                title: A
                slug: a
                status: ongoing
                enabled: true
                daily_chapter: true
                target_word_count: 3000
                category: 科幻
                keywords: [a]
                volumes:
                  - {order: 1, title: V1, start_chapter: 1, end_chapter: 50}
                chapter_slug_template: "{novel_slug}-ch{idx:03d}"
                paths:
                  outline: novels/a/outline.md
                  outline_meta: novels/a/outline.meta.json
                  style_guide: novels/a/style_guide.md
                  characters: novels/a/characters.md
                  state: novels/a/state.json
                  chapters_dir: novels/a/chapters/
                created_at: 2026-07-08T00:00:00Z
              - id: b_obsidian
                title: B
                slug: b
                status: ongoing
                enabled: true
                daily_chapter: true
                target_word_count: 3000
                category: 玄幻
                keywords: [b]
                volumes:
                  - {order: 1, title: V1, start_chapter: 1, end_chapter: 50}
                chapter_slug_template: "{novel_slug}-ch{idx:03d}"
                paths:
                  outline: novels/b/outline.md
                  outline_meta: novels/b/outline.meta.json
                  style_guide: novels/b/style_guide.md
                  characters: novels/b/characters.md
                  state: novels/b/state.json
                  chapters_dir: novels/b/chapters/
                created_at: 2026-07-08T00:00:00Z
            schedule:
              hours: [8, 12, 18]
              per_run_novel_limit: null
              daily_chapter_target: 3
            """
        )
        tmp_yaml.write_text(two_enabled, encoding="utf-8")
        from src.novel_registry import load_novels
        registry = load_novels(tmp_yaml)

        # mock _run_one_novel: a 失败, b 成功
        with patch("src.publisher._run_one_novel") as mock_run:
            from src.state import PublishState
            mock_run.side_effect = [
                PublisherError("LLM 失败"),  # a
                PublishState(novel_id="b_obsidian", next_idx=2, last_pushed_idx=1,
                            last_status="success", last_pushed_slot="2026-07-08-08"),
            ]
            agg = run_all_novels(mock_config, registry=registry)

        assert agg.total == 2
        assert agg.success == 1
        assert agg.failed == 1
        # a 失败详情
        a_result = next(r for r in agg.details if r.novel_id == "a_obsidian")
        assert a_result.status == "failed"
        assert "LLM" in a_result.error
        # b 成功详情
        b_result = next(r for r in agg.details if r.novel_id == "b_obsidian")
        assert b_result.status == "success"
        assert b_result.chapter_idx == 1

    def test_quota_skip_when_slot_already_pushed(self, mock_config, tmp_yaml):
        """state.last_pushed_slot == 当前 slot → skip, 不调底层"""
        # 预写 state, last_pushed_slot = 今天 8 档
        from src.state_per_novel import save_state_for_novel
        from src.state import PublishState
        from src.publisher import _current_slot
        from src.novel_registry import Schedule
        schedule = Schedule(hours=[8, 12, 18])
        # 算当前 slot (用真实 now)
        from datetime import datetime, timezone
        # 拿上海时间
        import zoneinfo
        sh = zoneinfo.ZoneInfo("Asia/Shanghai")
        local_now = datetime.now(sh)
        slot = _current_slot(local_now.astimezone(timezone.utc), schedule)

        state = PublishState(
            novel_id="a_obsidian", next_idx=2, last_pushed_idx=1,
            last_status="success", last_pushed_slot=slot,
        )
        save_state_for_novel(state, novel_id="a_obsidian")

        with patch("src.publisher._run_one_novel") as mock_run:
            from src.novel_registry import load_novels
            registry = load_novels(tmp_yaml)
            agg = run_all_novels(mock_config, registry=registry)
            mock_run.assert_not_called()  # 配额跳过, 没调底层

        assert agg.total == 1
        assert agg.skipped == 1
        assert agg.success == 0
        assert agg.details[0].status == "skipped"

    def test_force_bypasses_quota_check(self, mock_config, tmp_yaml):
        """--force 模式忽略配额检查"""
        from src.state_per_novel import save_state_for_novel
        from src.state import PublishState
        from src.publisher import _current_slot
        from src.novel_registry import Schedule
        schedule = Schedule(hours=[8, 12, 18])
        from datetime import datetime, timezone
        import zoneinfo
        sh = zoneinfo.ZoneInfo("Asia/Shanghai")
        local_now = datetime.now(sh)
        slot = _current_slot(local_now.astimezone(timezone.utc), schedule)

        state = PublishState(
            novel_id="a_obsidian", next_idx=2, last_pushed_idx=1,
            last_status="success", last_pushed_slot=slot,
        )
        save_state_for_novel(state, novel_id="a_obsidian")

        with patch("src.publisher._run_one_novel") as mock_run:
            mock_run.return_value = PublishState(
                novel_id="a_obsidian", next_idx=2, last_pushed_idx=1,
                last_status="success", last_pushed_slot=slot,
            )
            from src.novel_registry import load_novels
            registry = load_novels(tmp_yaml)
            agg = run_all_novels(mock_config, registry=registry, force=True)
            mock_run.assert_called_once()  # force 跳过配额, 调了

        assert agg.success == 1

    def test_aggregate_printed_with_details(self, mock_config, tmp_yaml, capsys):
        """AggregateResult.__str__ 输出详情"""
        agg = AggregateResult(
            total=2, success=1, failed=1, skipped=0,
            details=[
                NovelRunResult(novel_id="a", status="success", chapter_idx=1),
                NovelRunResult(novel_id="b", status="failed", error="x"),
            ],
        )
        s = str(agg)
        assert "total=2" in s
        assert "success=1" in s
        assert "failed=1" in s
        assert "a" in s
        assert "b" in s
        assert "x" in s


# ============================================================
# mark_pushed 兼容 (slot 字段)
# ============================================================


class TestMarkPushedSlot:
    def test_mark_pushed_records_slot(self):
        s = PublishState()
        s.mark_pushed(1, "key-1", slot="2026-07-08-08")
        assert s.last_pushed_slot == "2026-07-08-08"
        assert s.next_idx == 2

    def test_mark_pushed_empty_slot_does_not_overwrite(self):
        """slot="" 时不覆盖 (老调用点)"""
        s = PublishState(last_pushed_slot="old-slot")
        s.mark_pushed(1, "key")
        assert s.last_pushed_slot == "old-slot"  # 保留

    def test_mark_pushed_with_new_slot(self):
        s = PublishState(last_pushed_slot="old-slot")
        s.mark_pushed(1, "key", slot="new-slot")
        assert s.last_pushed_slot == "new-slot"
