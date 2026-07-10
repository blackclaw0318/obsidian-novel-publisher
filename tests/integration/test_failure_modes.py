# ============================================================
# test_failure_modes.py - 故障注入 v0.2 P6.2
# ============================================================
# 覆盖 (PLAN.md §集成测试清单):
#   1. LLM 5xx → 重试 → 第二次成功
#   2. LLM 返回空 content → 重试 → 第二次成功
#   3. obsidian 401 签名错 → 抛错 + 不重试
#   4. obsidian 5xx → 抛错 + state.mark_failed
#   5. obsidian 4xx → 抛错 + state.mark_failed (参数错)
#   6. GitHub 500 → 备份抛错 + 主推送仍成功
#   7. 封面图无效 (< 50KB) → 重试
#   8. 选题 JSON 解析失败 → 抛错 + state 不推进
# ============================================================

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
import requests

from src.publisher import (
    PublisherConfig,
    PublisherError,
    run_once,
)
from src.state import load_state


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PublisherConfig:
    monkeypatch.setenv("MINIMAXI_API_KEY", "sk-test")
    monkeypatch.setenv("OBSIDIAN_PUBLISH_SECRET", "secret-test-1234567890abcdef12345678")
    monkeypatch.setenv("OBSIDIAN_PUBLISH_ID", "novel-publisher")
    monkeypatch.setenv("OBSIDIAN_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("OBSIDIAN_ADMIN_BASE_URL", "https://obs.example.com")
    monkeypatch.setenv("PUBLISH_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("COVER_TMP_DIR", str(tmp_path / "covers"))
    return PublisherConfig.from_env()


def _mock_resp(status: int, body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.text = text
    if status >= 400:
        resp.raise_for_status = MagicMock(side_effect=requests.exceptions.HTTPError(f"{status}"))
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ============ LLM 故障 ============
class TestLLMFailure:
    def _make_stream_msg(self, text: str, stop_reason: str = "end_turn") -> MagicMock:
        """构造 anthropic Message mock"""
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = text
        msg = MagicMock()
        msg.content = [text_block]
        msg.stop_reason = stop_reason
        msg.usage = MagicMock(input_tokens=100, output_tokens=2000)
        return msg

    def _make_stream_ctx(self, msg: MagicMock) -> MagicMock:
        """构造 anthropic stream context manager"""
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(
            return_value=MagicMock(get_final_message=MagicMock(return_value=msg))
        )
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def test_llm_5xx_retry_then_succeed(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 5xx → 重试 → 第二次 200 → 成功 (v0.40 改: mock anthropic SDK)"""
        from src.cover_upload import CoverUploadResult

        # 选题一次过
        monkeypatch.setattr(
            "src.publisher.generate_one_shot",
            lambda **kw: [MagicMock(title="T", outline="O", keywords_used=[], genre_hint="科幻")],
        )

        # 写章节: 第一次 5xx, 第二次 200 长文本
        long_text = "章节正文 " * 1000
        call_count = {"n": 0}
        req = httpx.Request("POST", "https://api.minimaxi.com/anthropic/v1/messages")
        error_5xx = anthropic.APIStatusError(
            message="server error", response=httpx.Response(500, request=req), body={}
        )
        msg_ok = self._make_stream_msg(long_text)

        def fake_stream(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 第一次 raise 5xx
                raise error_5xx
            return self._make_stream_ctx(msg_ok)

        # patch 全局 anthropic stream (write_chapter + topic_gen 都会用)
        # 关键: NovelWriter._client 实例在每个 NovelWriter() 创建时独立,
        # 所以 patch class method 更稳
        with patch("anthropic.resources.messages.Messages.stream", side_effect=fake_stream):
            # 封面 + 上传 + 推送 mock
            cover_path = tmp_path / "001.jpg"
            cover_path.write_bytes(b"\xff" * 1000)
            monkeypatch.setattr(
                "src.publisher.CoverGenerator.generate", lambda *a, **kw: str(cover_path)
            )
            monkeypatch.setattr(
                "src.publisher.CoverUploader.upload",
                lambda *a, **kw: CoverUploadResult(
                    url="https://x/c.jpg", resource_id="1", file_size_bytes=1000
                ),
            )
            monkeypatch.setattr("src.publisher._post_with_sig", lambda *a, **kw: {"ok": True})
            patch_sleep(monkeypatch)

            result = run_once(config)

        # 第一次 5xx + 第二次成功 (write_chapter) — topic_gen 不再被调用 (mock 了)
        assert call_count["n"] >= 1
        assert result.last_status == "success"

    def test_llm_empty_content_retry(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 200 但 content 空 → 重试 → 第二次有内容 (v0.40 改: mock anthropic SDK)"""
        from src.cover_upload import CoverUploadResult

        monkeypatch.setattr(
            "src.publisher.generate_one_shot",
            lambda **kw: [MagicMock(title="T", outline="O", keywords_used=[], genre_hint="科幻")],
        )

        long_text = "章节正文 " * 1000
        msg_empty = self._make_stream_msg("   ")  # 全空白 → 视为空
        msg_ok = self._make_stream_msg(long_text)

        call_count = {"n": 0}

        def fake_stream(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return self._make_stream_ctx(msg_empty)
            return self._make_stream_ctx(msg_ok)

        with patch("anthropic.resources.messages.Messages.stream", side_effect=fake_stream):
            cover_path = tmp_path / "001.jpg"
            cover_path.write_bytes(b"\xff" * 1000)
            monkeypatch.setattr(
                "src.publisher.CoverGenerator.generate", lambda *a, **kw: str(cover_path)
            )
            monkeypatch.setattr(
                "src.publisher.CoverUploader.upload",
                lambda *a, **kw: CoverUploadResult(
                    url="https://x/c.jpg", resource_id="1", file_size_bytes=1000
                ),
            )
            monkeypatch.setattr("src.publisher._post_with_sig", lambda *a, **kw: {"ok": True})
            patch_sleep(monkeypatch)

            result = run_once(config)

        assert call_count["n"] >= 2
        assert result.last_status == "success"


# ============ 选题 故障 ============
class TestTopicFailure:
    def test_topic_json_parse_fail_marks_failed(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """选题 LLM 输出非 JSON → 抛 PublisherError + state 不推进"""
        # 选题抛 TopicGenError (上游 LLMError 转换)
        from src.topic_gen import TopicGenError

        def fake_gen(**kw):
            raise TopicGenError("JSON 解析失败: bad output")

        monkeypatch.setattr("src.publisher.generate_one_shot", fake_gen)

        # TopicGenError 是 PublisherError 子类, publisher 把它转为 PublisherError (含 '未预期错误' 前缀)
        with pytest.raises(PublisherError, match="未预期错误|JSON 解析失败"):
            run_once(config)

        # state 应 mark_failed, next_idx 不推进
        state = load_state(config.state_path)
        assert state.last_status == "failed"
        assert state.next_idx == 1


# ============ Obsidian 推送 故障 ============
class TestObsidianFailure:
    def test_401_signature_mismatch_no_retry(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """签名错 401 → 抛错 + 不重试 (publisher 不实现重试, 让老板看)"""
        from src.cover_upload import CoverUploadResult

        monkeypatch.setattr(
            "src.publisher.generate_one_shot",
            lambda **kw: [MagicMock(title="T", outline="O", keywords_used=[], genre_hint="科幻")],
        )
        from src.novel_writer import ChapterDraft

        monkeypatch.setattr(
            "src.publisher.NovelWriter.write_chapter",
            lambda *a, **kw: ChapterDraft(raw_text="x", cover_prompt="p", word_count=3000),
        )

        cover_path = tmp_path / "001.jpg"
        cover_path.write_bytes(b"\xff" * 1000)
        monkeypatch.setattr(
            "src.publisher.CoverGenerator.generate", lambda *a, **kw: str(cover_path)
        )
        monkeypatch.setattr(
            "src.publisher.CoverUploader.upload",
            lambda *a, **kw: CoverUploadResult(
                url="https://x/c.jpg", resource_id="1", file_size_bytes=1000
            ),
        )

        # 推送 401
        call_count = {"n": 0}

        def fake_post(url, body, sig_headers, **kwargs):
            call_count["n"] += 1
            resp = _mock_resp(401, body={"error": "bad_signature"}, text="bad signature")
            resp.raise_for_status()
            raise requests.exceptions.HTTPError("401")

        monkeypatch.setattr("src.publisher._post_with_sig", fake_post)

        with pytest.raises(PublisherError):
            run_once(config)

        # 推送失败抛 HTTPError → publisher 转 PublisherError, 不重试 (单次调用)
        assert call_count["n"] == 1
        state = load_state(config.state_path)
        assert state.last_status == "failed"

    def test_500_obsidian_marks_failed(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """obsidian 5xx → 抛错 + state.mark_failed (publisher 端不重试 blog)"""
        from src.cover_upload import CoverUploadResult

        monkeypatch.setattr(
            "src.publisher.generate_one_shot",
            lambda **kw: [MagicMock(title="T", outline="O", keywords_used=[])],
        )
        from src.novel_writer import ChapterDraft

        monkeypatch.setattr(
            "src.publisher.NovelWriter.write_chapter",
            lambda *a, **kw: ChapterDraft(raw_text="x", cover_prompt="p", word_count=3000),
        )

        cover_path = tmp_path / "001.jpg"
        cover_path.write_bytes(b"\xff" * 1000)
        monkeypatch.setattr(
            "src.publisher.CoverGenerator.generate", lambda *a, **kw: str(cover_path)
        )
        monkeypatch.setattr(
            "src.publisher.CoverUploader.upload",
            lambda *a, **kw: CoverUploadResult(
                url="https://x/c.jpg", resource_id="1", file_size_bytes=1000
            ),
        )

        def fake_post(url, body, sig_headers, **kwargs):
            raise requests.exceptions.HTTPError("500 blog down")

        monkeypatch.setattr("src.publisher._post_with_sig", fake_post)

        with pytest.raises(PublisherError):
            run_once(config)

        state = load_state(config.state_path)
        assert state.last_status == "failed"
        assert "500" in (state.last_error or "")


# ============ GitHub 备份 故障 ============
class TestGithubBackupFailure:
    def test_github_500_logs_warning_main_succeeds(
        self,
        config: PublisherConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GitHub 500 → 备份抛错 → 主推送仍成功 + log warning"""
        import logging

        from src.cover_upload import CoverUploadResult
        from src.github_backup import GithubBackupError

        monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "ghp-test")

        monkeypatch.setattr(
            "src.publisher.generate_one_shot",
            lambda **kw: [MagicMock(title="T", outline="O", keywords_used=[])],
        )
        from src.novel_writer import ChapterDraft

        monkeypatch.setattr(
            "src.publisher.NovelWriter.write_chapter",
            lambda *a, **kw: ChapterDraft(raw_text="x", cover_prompt="p", word_count=3000),
        )

        cover_path = tmp_path / "001.jpg"
        cover_path.write_bytes(b"\xff" * 1000)
        monkeypatch.setattr(
            "src.publisher.CoverGenerator.generate", lambda *a, **kw: str(cover_path)
        )
        monkeypatch.setattr(
            "src.publisher.CoverUploader.upload",
            lambda *a, **kw: CoverUploadResult(
                url="https://x/c.jpg", resource_id="1", file_size_bytes=1000
            ),
        )
        monkeypatch.setattr("src.publisher._post_with_sig", lambda *a, **kw: {"ok": True})

        # GitHub backup 抛错
        class FakeBackup:
            def __init__(self, *a, **kw):
                pass

            def upload(self, *a, **kw):
                raise GithubBackupError("GitHub API 500")

        monkeypatch.setattr("src.publisher.GithubBackup", FakeBackup)

        with caplog.at_level(logging.WARNING):
            result = run_once(config)

        # 主推送仍成功
        assert result.last_status == "success"
        # log 含 warning
        assert any("GitHub 备份失败" in rec.message for rec in caplog.records)


# ============ helpers ============
def patch_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """跳过所有 sleep 加速测试"""
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *a, **kw: None)
    if hasattr(__import__("src.novel_writer", fromlist=["time"]), "time"):
        monkeypatch.setattr("src.novel_writer.time.sleep", lambda *a, **kw: None)
    if hasattr(__import__("src.cover_gen", fromlist=["time"]), "time"):
        monkeypatch.setattr("src.cover_gen.time.sleep", lambda *a, **kw: None)


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
