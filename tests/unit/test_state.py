"""
test_state.py — state.py 单元测试

覆盖:
- 初始 state (文件不存在)
- load / save round-trip
- mark_pushed (next_idx++, last_pushed_*, idempotency 记录, skip_next 清)
- mark_failed (next_idx 不动, last_status=failed)
- mark_skipped (skip_next 用一次就清)
- 损坏文件 → 自动备份 .bak + 初始 state
- schema 前向兼容 (未知字段不报错)
"""

from __future__ import annotations

import json
from pathlib import Path

from src.state import (
    DEFAULT_STATE_PATH,
    SCHEMA_VERSION,
    PublishState,
    load_state,
    save_state,
)


class TestPublishStateTransitions:
    """状态转移测试"""

    def test_mark_pushed_increments_and_records_idem(self):
        state = PublishState(next_idx=1)
        state.mark_pushed(idx=1, idem_key="abc123")
        assert state.next_idx == 2
        assert state.last_pushed_idx == 1
        assert state.last_status == "success"
        assert state.last_error is None
        assert state.idempotency_keys == {"1": "abc123"}
        assert state.last_pushed_at is not None  # ISO 8601

    def test_mark_pushed_clears_skip_next(self):
        state = PublishState(next_idx=5, skip_next=True)
        state.mark_pushed(idx=5, idem_key="k")
        assert state.skip_next is False  # 用一次就清

    def test_mark_failed_keeps_next_idx(self):
        state = PublishState(next_idx=3)
        state.mark_failed(idx=3, error="网络超时")
        assert state.next_idx == 3  # 失败不推进
        assert state.last_status == "failed"
        assert "网络超时" in (state.last_error or "")

    def test_mark_failed_truncates_long_error(self):
        state = PublishState(next_idx=1)
        long_err = "x" * 1000
        state.mark_failed(idx=1, error=long_err)
        # 截断到 500 字符
        assert state.last_error is not None
        assert len(state.last_error) <= 500

    def test_mark_skipped_advances_next_idx(self):
        state = PublishState(next_idx=7, skip_next=True)
        state.mark_skipped(idx=7, reason="skip_next")
        assert state.next_idx == 8  # 跳过的也算消耗
        assert state.last_status == "skipped"
        assert state.skip_next is False  # 自动清


class TestStateIO:
    """load / save 持久化测试"""

    def test_load_missing_file_returns_initial(self, tmp_path: Path):
        path = tmp_path / "state.json"
        state = load_state(path)
        assert state.next_idx == 1
        assert state.novel_id == "meta_realm_obsidian"
        assert state.last_status == "pending"

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "state.json"
        original = PublishState(next_idx=42, last_pushed_idx=41, last_status="success")
        save_state(original, path)
        loaded = load_state(path)
        assert loaded.next_idx == 42
        assert loaded.last_pushed_idx == 41
        assert loaded.last_status == "success"

    def test_corrupt_file_creates_backup(self, tmp_path: Path):
        path = tmp_path / "state.json"
        path.write_text("{ 损坏的 json", encoding="utf-8")
        state = load_state(path)
        # 损坏文件应被备份 + 返回初始 state
        assert state.next_idx == 1
        bak = path.with_suffix(".json.bak")
        assert bak.exists()
        # 备份应保留原损坏内容 (供人工诊断)
        assert bak.read_text(encoding="utf-8") == "{ 损坏的 json"
        # 原文件被 rename 走, 不再存在 (调用方应负责 save 新 state)

    def test_schema_version_present(self, tmp_path: Path):
        path = tmp_path / "state.json"
        state = PublishState(next_idx=2)
        save_state(state, path)
        data = json.loads(path.read_text())
        assert data["schema_version"] == SCHEMA_VERSION

    def test_forward_compat_unknown_fields(self, tmp_path: Path):
        """旧版 schema 加新字段后, 老 state 文件能加载不报错"""
        path = tmp_path / "state.json"
        # 写一个含未知字段的 state
        path.write_text(
            json.dumps(
                {
                    "next_idx": 5,
                    "unknown_future_field": "should be ignored",
                    "schema_version": 999,  # 未来版本
                }
            ),
            encoding="utf-8",
        )
        state = load_state(path)
        assert state.next_idx == 5
        # 未知字段不挂载到 dataclass
        assert not hasattr(state, "unknown_future_field")

    def test_save_creates_parent_dir(self, tmp_path: Path):
        path = tmp_path / "deep" / "nested" / "state.json"
        save_state(PublishState(), path)
        assert path.exists()


class TestPublishStateSerialization:
    """to_dict / from_dict 测试"""

    def test_to_dict_contains_all_fields(self):
        state = PublishState(next_idx=1)
        d = state.to_dict()
        for f in ("novel_id", "next_idx", "last_status", "skip_next", "schema_version"):
            assert f in d

    def test_from_dict_strips_unknown(self):
        state = PublishState.from_dict({"next_idx": 7, "foo": "bar", "schema_version": 1})
        assert state.next_idx == 7
        assert not hasattr(state, "foo")


def test_default_state_path_exists():
    """DEFAULT_STATE_PATH 应该指向 data/state.json"""
    assert str(DEFAULT_STATE_PATH).endswith("state.json")
