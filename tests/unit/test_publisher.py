# ============================================================
# test_publisher.py - 单测 v0.2 P6.1
# ============================================================
# 覆盖:
#   - PublisherConfig.from_env: 缺字段抛 / 占位符("***", "change...")抛
#   - run_once: 全链路 mock + state mark_pushed
#   - run_once: skip_next 跳过 (state.mark_skipped, 不推进 next_idx)
#   - run_once: force=True 绕过 skip_next
#   - run_once: LLMError → state.mark_failed + 抛 ChapterGenError
#   - run_once: CoverUploadError → state.mark_failed + 抛 CoverGenError
#   - run_once: 未知异常 → state.mark_failed + 抛 PublisherError
#   - run_once: GitHub 备份失败 → 主推送仍成功
# ============================================================

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.publisher import (
    ChapterGenError,
    CoverGenError,
    PublisherConfig,
    PublisherConfigError,
    PublisherError,
    run_once,
)
from src.state import PublishState, load_state, save_state


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PublisherConfig:
    """构造合法 PublisherConfig, state 路径用 tmp"""
    monkeypatch.setenv("MINIMAXI_API_KEY", "sk-test")
    monkeypatch.setenv(
        "OBSIDIAN_PUBLISH_SECRET", "secret-test-1234567890abcdef12345678"
    )  # ≥ 32 hex chars
    monkeypatch.setenv("OBSIDIAN_PUBLISH_ID", "novel-publisher")
    monkeypatch.setenv("OBSIDIAN_ADMIN_TOKEN", "test-admin-token")  # CoverUploader 必需
    monkeypatch.setenv("OBSIDIAN_ADMIN_BASE_URL", "https://obs.example.com")
    monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "")  # 默认关闭
    monkeypatch.setenv("PUBLISH_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("COVER_TMP_DIR", str(tmp_path / "covers"))
    return PublisherConfig.from_env()


# ============ Config ============
class TestConfig:
    def test_missing_minimax_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MINIMAXI_API_KEY", raising=False)
        monkeypatch.setenv("OBSIDIAN_PUBLISH_SECRET", "x")
        monkeypatch.setenv("OBSIDIAN_PUBLISH_ID", "x")
        with pytest.raises(PublisherConfigError, match="MINIMAXI_API_KEY"):
            PublisherConfig.from_env()

    def test_placeholder_asterisk_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIMAXI_API_KEY", "sk-real-key")
        monkeypatch.setenv("OBSIDIAN_PUBLISH_SECRET", "***")
        monkeypatch.setenv("OBSIDIAN_PUBLISH_ID", "x")
        with pytest.raises(PublisherConfigError, match="占位符"):
            PublisherConfig.from_env()

    def test_placeholder_change_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIMAXI_API_KEY", "sk-real-key")
        monkeypatch.setenv("OBSIDIAN_PUBLISH_SECRET", "change-me-please")
        monkeypatch.setenv("OBSIDIAN_PUBLISH_ID", "x")
        with pytest.raises(PublisherConfigError, match="占位符"):
            PublisherConfig.from_env()

    def test_missing_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIMAXI_API_KEY", "sk-real-key")
        monkeypatch.delenv("OBSIDIAN_PUBLISH_SECRET", raising=False)
        monkeypatch.setenv("OBSIDIAN_PUBLISH_ID", "x")
        with pytest.raises(PublisherConfigError, match="OBSIDIAN_PUBLISH_SECRET"):
            PublisherConfig.from_env()


# ============ run_once 跳过 ============
class TestRunOnceSkipped:
    def test_skip_next_marks_skipped(self, config: PublisherConfig) -> None:
        """state.skip_next=True → run_once 跳过 + 不推进 next_idx"""
        state = PublishState(skip_next=True, next_idx=3)
        save_state(state, config.state_path)

        result = run_once(config)
        assert result.last_status == "skipped"
        assert result.next_idx == 4  # 跳过的也算"消耗"了这次 (mark_skipped idx+1)

    def test_force_bypasses_skip(self, config: PublisherConfig) -> None:
        """force=True → 跳过 skip_next 标记"""
        state = PublishState(skip_next=True, next_idx=5)
        save_state(state, config.state_path)

        # 强制跑, 全链路 mock
        with patch("src.publisher.generate_one_shot") as mock_topic:
            mock_topic.return_value = [
                MagicMock(title="测试章", outline="大纲", keywords_used=[], genre_hint="科幻")
            ]
            with patch("src.publisher.NovelWriter.write_chapter") as mock_write:
                from src.novel_writer import ChapterDraft

                mock_write.return_value = ChapterDraft(
                    raw_text="短", cover_prompt="p", word_count=3000, usage={}
                )
                with patch("src.publisher.CoverGenerator.generate", return_value="/tmp/fake.jpg"):
                    with patch("src.publisher.CoverUploader.upload") as mock_upload:
                        from src.cover_upload import CoverUploadResult

                        mock_upload.return_value = CoverUploadResult(
                            url="https://x/y.jpg", resource_id="1", file_size_bytes=100
                        )
                        with patch("src.publisher._post_with_sig", return_value={"ok": True}):
                            # skip_next=True 但 force=True 应该真跑
                            Path("/tmp/fake.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
                            try:
                                result = run_once(config, force=True)
                                # 成功了
                                assert result.last_status == "success"
                                assert result.next_idx == 6  # 推进
                            finally:
                                Path("/tmp/fake.jpg").unlink(missing_ok=True)


# ============ run_once 成功路径 ============
class TestRunOnceSuccess:
    def test_full_pipeline_mocked(self, config: PublisherConfig) -> None:
        """全链路 mock → run_once 成功, state mark_pushed"""
        with patch("src.publisher.generate_one_shot") as mock_topic:
            mock_topic.return_value = [
                MagicMock(title="T", outline="O", keywords_used=[], genre_hint="科幻")
            ]
            with patch("src.publisher.NovelWriter.write_chapter") as mock_write:
                from src.novel_writer import ChapterDraft

                mock_write.return_value = ChapterDraft(
                    raw_text="章节正文 " * 1000, cover_prompt="cyberpunk city", word_count=3000
                )
                with patch("src.publisher.CoverGenerator.generate") as mock_cover:
                    mock_cover.return_value = "/tmp/cover_test.jpg"
                    Path("/tmp/cover_test.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 1000)
                    try:
                        with patch("src.publisher.CoverUploader.upload") as mock_upload:
                            from src.cover_upload import CoverUploadResult

                            mock_upload.return_value = CoverUploadResult(
                                url="https://x/c.jpg",
                                resource_id="r1",
                                file_size_bytes=1000,
                            )
                            with patch("src.publisher._post_with_sig", return_value={"ok": True}):
                                result = run_once(config)

                        assert result.last_status == "success"
                        assert result.next_idx == 2
                        assert result.last_pushed_idx == 1
                    finally:
                        Path("/tmp/cover_test.jpg").unlink(missing_ok=True)


# ============ run_once 失败路径 ============
class TestRunOnceFailure:
    def test_llm_error_marks_failed(self, config: PublisherConfig) -> None:
        """LLM 失败 → state.mark_failed + 抛 ChapterGenError"""
        from src.novel_writer import LLMError

        with patch("src.publisher.generate_one_shot") as mock_topic:
            mock_topic.return_value = [MagicMock(title="T", outline="O", keywords_used=[])]
            with patch("src.publisher.NovelWriter.write_chapter", side_effect=LLMError("M3 fail")):
                with pytest.raises(ChapterGenError, match="LLM 调用失败"):
                    run_once(config)
                # state 应 mark_failed
                state = load_state(config.state_path)
                assert state.last_status == "failed"
                assert state.next_idx == 1  # 不推进

    def test_cover_upload_error_marks_failed(self, config: PublisherConfig) -> None:
        """封面上传失败 → state.mark_failed + 抛 CoverGenError"""
        from src.cover_upload import CoverUploadError
        from src.novel_writer import ChapterDraft

        with patch("src.publisher.generate_one_shot") as mock_topic:
            mock_topic.return_value = [MagicMock(title="T", outline="O", keywords_used=[])]
            with patch("src.publisher.NovelWriter.write_chapter") as mock_write:
                mock_write.return_value = ChapterDraft(
                    raw_text="x", cover_prompt="p", word_count=3000
                )
                with patch("src.publisher.CoverGenerator.generate", return_value="/tmp/c.jpg"):
                    Path("/tmp/c.jpg").write_bytes(b"\xff" * 1000)
                    try:
                        with patch(
                            "src.publisher.CoverUploader.upload",
                            side_effect=CoverUploadError("4xx"),
                        ):
                            with pytest.raises(CoverGenError, match="封面上传失败"):
                                run_once(config)
                            state = load_state(config.state_path)
                            assert state.last_status == "failed"
                    finally:
                        Path("/tmp/c.jpg").unlink(missing_ok=True)

    def test_unexpected_error_marks_failed(self, config: PublisherConfig) -> None:
        """未知异常 → state.mark_failed + 抛 PublisherError"""
        with patch("src.publisher.generate_one_shot", side_effect=RuntimeError("炸了")):
            with pytest.raises(PublisherError, match="未预期错误"):
                run_once(config)
            state = load_state(config.state_path)
            assert state.last_status == "failed"
            assert "RuntimeError" in (state.last_error or "")


# ============ run_once + GitHub 备份 ============
class TestRunOnceGithubBackup:
    def test_backup_failure_does_not_block(
        self, config: PublisherConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GitHub 备份失败 → 主推送仍成功"""
        monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "ghp-test-token")

        from src.github_backup import GithubBackupError
        from src.novel_writer import ChapterDraft

        with patch("src.publisher.generate_one_shot") as mock_topic:
            mock_topic.return_value = [MagicMock(title="T", outline="O", keywords_used=[])]
            with patch("src.publisher.NovelWriter.write_chapter") as mock_write:
                mock_write.return_value = ChapterDraft(
                    raw_text="x", cover_prompt="p", word_count=3000
                )
                with patch("src.publisher.CoverGenerator.generate", return_value="/tmp/c2.jpg"):
                    Path("/tmp/c2.jpg").write_bytes(b"\xff" * 1000)
                    try:
                        with patch("src.publisher.CoverUploader.upload") as mock_upload:
                            from src.cover_upload import CoverUploadResult

                            mock_upload.return_value = CoverUploadResult(
                                url="https://x/c.jpg",
                                resource_id="r1",
                                file_size_bytes=1000,
                            )
                            with patch("src.publisher._post_with_sig", return_value={"ok": True}):
                                with patch("src.publisher.GithubBackup") as mock_backup_cls:
                                    mock_backup = MagicMock()
                                    mock_backup.upload.side_effect = GithubBackupError("API 500")
                                    mock_backup_cls.return_value = mock_backup

                                    # 主推送仍成功 (备份失败 logger.warning 不抛)
                                    result = run_once(config)
                                    assert result.last_status == "success"
                    finally:
                        Path("/tmp/c2.jpg").unlink(missing_ok=True)


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
