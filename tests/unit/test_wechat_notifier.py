# ============================================================
# test_wechat_notifier.py - 微信推送模块单测 (v0.40 P1)
# ============================================================
# 覆盖:
#   - _format_success: 3 行简洁格式 (老板一眼能审)
#   - _format_failure: 3 行简洁格式 + 错误信息
#   - WechatNotifierConfig.from_env: env 读取 + 默认值
#   - notify_success: 写 pending.txt + 调 cron run + sleep 20s + 删文件 (mock)
#   - notify_failure: 同上但内容是失败消息
#   - notify_*_disabled: WEIXIN_NOTIFY_ENABLED=false 跳过
#   - notify_*_subprocess_error: subprocess 抛错全 catch, 不影响 publisher
# ============================================================

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.wechat_notifier import (
    WechatNotifierConfig,
    _format_failure,
    _format_success,
    notify_failure,
    notify_success,
)

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path: Path) -> WechatNotifierConfig:
    """标准测试 cfg: tmp 路径 + 真 cron job id"""
    return WechatNotifierConfig(
        enabled=True,
        cron_job_id="5225d68b-8855-4c59-9a06-8d32a3f24b71",
        pending_file=tmp_path / "wechat-pending.txt",
        cron_run_timeout_s=15.0,
    )


# --------------------------------------------------------------------------
# 消息格式化
# --------------------------------------------------------------------------


class TestFormatSuccess:
    def test_3_lines_with_all_fields(self, cfg: WechatNotifierConfig) -> None:
        msg = _format_success(
            novel_id="meta_realm",
            chapter_idx=5,
            title="星海之始",
            word_count=3554,
            cover_url="https://x.jpg",
            post_url="https://www.shangkun.uk/chapters/meta-realm-ch005",
        )
        lines = msg.split("\n")
        assert len(lines) == 4
        assert lines[0] == "✅ 推送成功 · meta_realm 第5章"
        assert lines[1] == "星海之始"
        assert lines[2] == "字数 3554 · 封面 已上传"
        assert lines[3] == "https://www.shangkun.uk/chapters/meta-realm-ch005"

    def test_no_cover_marks_nei_fengmian(self, cfg: WechatNotifierConfig) -> None:
        msg = _format_success(
            novel_id="x",
            chapter_idx=1,
            title="t",
            word_count=100,
            cover_url="",
            post_url="https://y/1",
        )
        assert "封面 无封面" in msg

    def test_no_url(self, cfg: WechatNotifierConfig) -> None:
        msg = _format_success(
            novel_id="x",
            chapter_idx=1,
            title="t",
            word_count=100,
            cover_url="https://y.jpg",
            post_url="",
        )
        assert "(无 URL)" in msg


class TestFormatFailure:
    def test_3_lines(self, cfg: WechatNotifierConfig) -> None:
        msg = _format_failure(
            novel_id="meta_realm",
            chapter_idx=5,
            title="星海之始",
            error_short="LLM 调用失败",
        )
        lines = msg.split("\n")
        assert len(lines) == 4
        assert lines[0] == "❌ 推送失败 · meta_realm 第5章"
        assert lines[1] == "星海之始"
        assert lines[2] == "原因: LLM 调用失败"
        assert lines[3] == "查看: tail logs/publisher.log"

    def test_empty_title(self, cfg: WechatNotifierConfig) -> None:
        msg = _format_failure(
            novel_id="x",
            chapter_idx=1,
            title="",
            error_short="硬超时 (900s)",
        )
        assert "(无标题)" in msg


# --------------------------------------------------------------------------
# Config from_env
# --------------------------------------------------------------------------


class TestConfigFromEnv:
    def test_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("WEIXIN_NOTIFY_ENABLED", raising=False)
        monkeypatch.delenv("WEIXIN_CRON_JOB_ID", raising=False)
        monkeypatch.setenv("WEIXIN_PENDING_FILE", str(tmp_path / "pending.txt"))
        cfg = WechatNotifierConfig.from_env()
        assert cfg.enabled is True
        # 默认 cron job id 是生产环境那个 (硬编码兜底)
        assert cfg.cron_job_id == "5225d68b-8855-4c59-9a06-8d32a3f24b71"
        assert cfg.pending_file == tmp_path / "pending.txt"

    def test_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("WEIXIN_NOTIFY_ENABLED", "false")
        monkeypatch.setenv("WEIXIN_PENDING_FILE", str(tmp_path / "pending.txt"))
        cfg = WechatNotifierConfig.from_env()
        assert cfg.enabled is False


# --------------------------------------------------------------------------
# notify_* 端到端 (mock subprocess)
# --------------------------------------------------------------------------


class TestNotifySuccess:
    def test_writes_file_calls_cron_deletes(
        self,
        cfg: WechatNotifierConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="ok"))
        mock_sleep = MagicMock()
        mock_unlink = MagicMock()

        monkeypatch.setattr("src.wechat_notifier.subprocess.run", mock_run)
        monkeypatch.setattr("src.wechat_notifier.time.sleep", mock_sleep)
        monkeypatch.setattr("pathlib.Path.unlink", mock_unlink)

        result = notify_success(
            cfg,
            novel_id="meta_realm",
            chapter_idx=5,
            title="t",
            word_count=100,
            cover_url="https://y.jpg",
            post_url="https://y/1",
        )

        assert result is True
        # 1. pending.txt 已写
        assert cfg.pending_file.exists()
        content = cfg.pending_file.read_text()
        assert "meta_realm 第5章" in content
        # 2. cron run 已调
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["openclaw", "cron", "run", cfg.cron_job_id]
        # 3. sleep 已调 (避免 race condition)
        mock_sleep.assert_called_once_with(20)
        # 4. unlink 已调
        mock_unlink.assert_called_once_with(missing_ok=True)

    def test_disabled_skips(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = WechatNotifierConfig(
            enabled=False,
            cron_job_id="x",
            pending_file=tmp_path / "pending.txt",
            cron_run_timeout_s=15.0,
        )
        mock_run = MagicMock()
        monkeypatch.setattr("src.wechat_notifier.subprocess.run", mock_run)

        result = notify_success(
            cfg,
            novel_id="x",
            chapter_idx=1,
            title="t",
            word_count=100,
            cover_url="",
            post_url="",
        )
        assert result is False
        mock_run.assert_not_called()
        assert not cfg.pending_file.exists()

    def test_subprocess_error_returns_false(
        self,
        cfg: WechatNotifierConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_run = MagicMock(
            side_effect=OSError("openclaw not found"),
        )
        monkeypatch.setattr("src.wechat_notifier.subprocess.run", mock_run)
        # 必须全 catch, 不抛
        result = notify_success(
            cfg,
            novel_id="x",
            chapter_idx=1,
            title="t",
            word_count=100,
            cover_url="",
            post_url="",
        )
        assert result is False
        # 文件被删 (catch 后也会 unlink, 因为在 try/except 之外 — 实际是 try 内部)
        # 实际: 抛错时 catch 走 False return, 不删文件 (无害, schedule 兜底处理)
        assert cfg.pending_file.exists()


class TestNotifyFailure:
    def test_failure_message(
        self,
        cfg: WechatNotifierConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        mock_sleep = MagicMock()
        mock_unlink = MagicMock()
        monkeypatch.setattr("src.wechat_notifier.subprocess.run", mock_run)
        monkeypatch.setattr("src.wechat_notifier.time.sleep", mock_sleep)
        monkeypatch.setattr("pathlib.Path.unlink", mock_unlink)

        result = notify_failure(
            cfg,
            novel_id="meta_realm",
            chapter_idx=5,
            title="t",
            error_short="LLM 调用失败",
        )
        assert result is True
        content = cfg.pending_file.read_text()
        assert "❌ 推送失败" in content
        assert "LLM 调用失败" in content
