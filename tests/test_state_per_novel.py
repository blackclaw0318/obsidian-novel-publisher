"""
state_per_novel 单测 (v0.3.2 P0)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.state import PublishState
from src.state_per_novel import (
    DEFAULT_STATE_DIR,
    FALLBACK_OLD_STATE,
    _migrate_old_state_if_needed,
    _per_novel_path,
    _validate_novel_id,
    list_known_novels,
    load_state_for_novel,
    save_state_for_novel,
    state_path_for,
)


@pytest.fixture
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离 data/ 目录, 避免污染真实 state"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("src.state_per_novel.DEFAULT_STATE_DIR", data_dir / "state")
    monkeypatch.setattr("src.state_per_novel.FALLBACK_OLD_STATE", data_dir / "state.json")
    monkeypatch.setattr("src.state.DEFAULT_STATE_PATH", data_dir / "state.json")
    return data_dir


# ============================================================
# _validate_novel_id (路径穿越防护)
# ============================================================


class TestNovelIdValidation:
    @pytest.mark.parametrize("good", ["meta_realm_obsidian", "abc", "a1b2_c3", "x_y_z"])
    def test_valid_ids(self, good):
        assert _validate_novel_id(good) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape",     # 路径穿越
            "a/b",           # 路径分隔符
            "with space",    # 空格
            "UPPER",         # 大写
            "with-dash",     # 连字符
            "with.dot",      # 点
            "",              # 空
        ],
    )
    def test_invalid_ids_rejected(self, bad):
        with pytest.raises(ValueError) as exc:
            _validate_novel_id(bad)
        assert "novel_id 非法" in str(exc.value)


# ============================================================
# 路径解析
# ============================================================


class TestPathResolution:
    def test_per_novel_path_format(self, isolated_data):
        p = _per_novel_path("meta_realm_obsidian")
        assert p.name == "meta_realm_obsidian.json"
        assert p.parent.name == "state"

    def test_state_path_for_returns_correct_path(self, isolated_data):
        p = state_path_for("test_obsidian")
        assert p.name == "test_obsidian.json"


# ============================================================
# 加载 / 保存
# ============================================================


class TestLoadSave:
    def test_load_nonexistent_returns_initial(self, isolated_data):
        state = load_state_for_novel("new_obsidian", auto_migrate=False)
        assert state.novel_id == "new_obsidian"
        assert state.next_idx == 1
        assert state.last_status == "pending"

    def test_save_then_load_roundtrip(self, isolated_data):
        s = PublishState(novel_id="round_obsidian", next_idx=5, last_status="success")
        save_state_for_novel(s)

        loaded = load_state_for_novel("round_obsidian", auto_migrate=False)
        assert loaded.novel_id == "round_obsidian"
        assert loaded.next_idx == 5
        assert loaded.last_status == "success"

    def test_save_creates_dir_automatically(self, isolated_data):
        # 默认 dir 不存在
        assert not (isolated_data / "state").exists()
        s = PublishState(novel_id="newdir_obsidian")
        save_state_for_novel(s)
        assert (isolated_data / "state" / "newdir_obsidian.json").exists()

    def test_save_with_explicit_novel_id_overrides(self, isolated_data):
        """save_state_for_novel(state, novel_id) 应当覆盖 state.novel_id"""
        s = PublishState(novel_id="wrong_obsidian", next_idx=3)
        save_state_for_novel(s, novel_id="correct_obsidian")

        # 用 correct 读, 拿到 next_idx=3
        loaded = load_state_for_novel("correct_obsidian", auto_migrate=False)
        assert loaded.novel_id == "correct_obsidian"
        assert loaded.next_idx == 3

        # wrong 不应该有文件
        assert not (isolated_data / "state" / "wrong_obsidian.json").exists()

    def test_load_corrects_mismatched_novel_id(self, isolated_data):
        """state 文件里 novel_id 字段与查询 id 不一致时, 强制覆盖"""
        # 直接写一个文件: 文件名 = override, 但文件里 novel_id 字段 = mismatch
        p = isolated_data / "state" / "override_obsidian.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "novel_id": "mismatch_obsidian",
                    "next_idx": 7,
                    "last_status": "success",
                    "schema_version": 1,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        loaded = load_state_for_novel("override_obsidian", auto_migrate=False)
        assert loaded.novel_id == "override_obsidian"  # 强制覆盖
        assert loaded.next_idx == 7  # 其他字段保留
        assert loaded.last_status == "success"

    def test_corrupt_state_falls_back_to_initial(self, isolated_data):
        """损坏的 state.json 应备份后返回初始 state"""
        p = isolated_data / "state" / "corrupt_obsidian.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ broken json", encoding="utf-8")

        state = load_state_for_novel("corrupt_obsidian", auto_migrate=False)
        assert state.novel_id == "corrupt_obsidian"
        assert state.next_idx == 1
        # 备份存在
        assert (isolated_data / "state" / "corrupt_obsidian.json.bak").exists()


# ============================================================
# v0.2 老 state.json 迁移
# ============================================================


class TestMigration:
    def test_migrate_old_state(self, isolated_data):
        """老 data/state.json 自动迁移到 data/state/{novel_id}.json"""
        old = {
            "novel_id": "legacy_obsidian",
            "next_idx": 9,
            "last_pushed_at": "2026-07-07T22:00:00Z",
            "last_pushed_idx": 8,
            "last_status": "success",
            "idempotency_keys": {"8": "abc123"},
            "schema_version": 1,
        }
        (isolated_data / "state.json").write_text(
            json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 触发迁移
        migrated = _migrate_old_state_if_needed()
        assert migrated is True

        # 验证迁移结果
        target = isolated_data / "state" / "legacy_obsidian.json"
        assert target.exists()
        new = json.loads(target.read_text(encoding="utf-8"))
        assert new["novel_id"] == "legacy_obsidian"
        assert new["next_idx"] == 9

        # 老文件保留 (不删, 给老板 backup 看)
        assert (isolated_data / "state.json").exists()

    def test_no_migration_when_no_old_state(self, isolated_data):
        """无老文件时, 迁移返回 False, 不报错"""
        result = _migrate_old_state_if_needed()
        assert result is False
        assert not (isolated_data / "state").exists()

    def test_no_migration_when_target_exists(self, isolated_data):
        """目标已存在时, 不覆盖, 保留老文件"""
        # 老 state.json
        old = {"novel_id": "clash_obsidian", "next_idx": 1, "schema_version": 1}
        (isolated_data / "state.json").write_text(
            json.dumps(old), encoding="utf-8"
        )
        # 目标已存在
        target = isolated_data / "state" / "clash_obsidian.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"novel_id": "clash_obsidian", "next_idx": 99}', encoding="utf-8")

        migrated = _migrate_old_state_if_needed()
        assert migrated is False

        # 目标未覆盖
        assert json.loads(target.read_text(encoding="utf-8"))["next_idx"] == 99

    def test_load_triggers_migration_automatically(self, isolated_data):
        """load_state_for_novel(auto_migrate=True) 默认触发迁移"""
        old = {"novel_id": "auto_obsidian", "next_idx": 12, "schema_version": 1}
        (isolated_data / "state.json").write_text(json.dumps(old), encoding="utf-8")

        # 不需要先调 _migrate_old_state_if_needed
        state = load_state_for_novel("auto_obsidian")
        assert state.next_idx == 12
        assert state.novel_id == "auto_obsidian"


# ============================================================
# list_known_novels
# ============================================================


class TestListKnown:
    def test_empty_dir(self, isolated_data):
        # dir 不存在
        assert list_known_novels() == []

    def test_lists_existing_states(self, isolated_data):
        save_state_for_novel(PublishState(novel_id="a_obsidian"))
        save_state_for_novel(PublishState(novel_id="b_obsidian"))
        save_state_for_novel(PublishState(novel_id="c_obsidian"))

        novels = list_known_novels()
        assert sorted(novels) == ["a_obsidian", "b_obsidian", "c_obsidian"]

    def test_ignores_hidden_files(self, isolated_data):
        save_state_for_novel(PublishState(novel_id="real_obsidian"))
        # 隐藏文件 / 临时文件 / 备份文件
        (isolated_data / "state" / ".DS_Store").write_text("")
        (isolated_data / "state" / "real_obsidian.json.bak").write_text("")
        (isolated_data / "state" / "real_obsidian.json.tmp").write_text("")

        novels = list_known_novels()
        assert novels == ["real_obsidian"]
