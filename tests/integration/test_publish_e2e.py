# ============================================================
# test_publish_e2e.py - 集成测 v0.2 P6.2
# ============================================================
# 覆盖:
#   1. test_full_pipeline_with_mocks — LLM → 选题 → 写章节 → 封面 → 上传 → 推送 → 备份 全链路
#   2. test_idempotency_same_key_returns_existing_post — 同 key 不重复
#   3. test_failure_in_obsidian_does_not_skip_backup — 主推送失败, 备份仍尝试 (mock 不阻塞)
#   4. test_state_persists_across_runs — state.json 跨 run 持久化
#   5. test_publisher_dry_run_doesnt_advance_state — --dry-run 不真推, state 不推进
# ============================================================

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.publisher import (
    PublisherConfig,
    PublisherError,
    run_once,
)
from src.state import load_state

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PublisherConfig:
    monkeypatch.setenv("MINIMAXI_API_KEY", "sk-test")
    monkeypatch.setenv("OBSIDIAN_PUBLISH_SECRET", "secret-test-1234567890abcdef12345678")
    monkeypatch.setenv("OBSIDIAN_PUBLISH_ID", "novel-publisher")
    monkeypatch.setenv("OBSIDIAN_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("OBSIDIAN_ADMIN_BASE_URL", "https://obs.example.com")
    monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "")  # 默认关
    monkeypatch.setenv("PUBLISH_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("COVER_TMP_DIR", str(tmp_path / "covers"))
    return PublisherConfig.from_env()


def _setup_full_mocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """设置全链路 mock, 返回 mock handle 字典供断言用"""
    from src.cover_upload import CoverUploadResult
    from src.novel_writer import ChapterDraft

    # 1. 选题 mock
    topic = MagicMock(title="测试章", outline="科幻大纲", keywords_used=["科幻"], genre_hint="科幻")
    monkeypatch.setattr("src.publisher.generate_one_shot", lambda **kw: [topic])

    # 2. 写章节 mock
    draft = ChapterDraft(
        raw_text="章节正文 " * 1000,
        cover_prompt="cyberpunk city",
        word_count=3000,
        usage={"prompt_tokens": 100, "completion_tokens": 1000},
    )

    def fake_write_chapter(*args, **kwargs):
        return draft

    monkeypatch.setattr("src.publisher.NovelWriter.write_chapter", fake_write_chapter)

    # 3. 封面生成 mock (返回真实文件)
    cover_path = tmp_path / "001.jpg"
    cover_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 1000)
    monkeypatch.setattr("src.publisher.CoverGenerator.generate", lambda *a, **kw: str(cover_path))

    # 4. 封面上传 mock
    upload_result = CoverUploadResult(
        url="https://obs.example.com/resources/001.jpg",
        resource_id="res-1",
        file_size_bytes=1000,
    )
    monkeypatch.setattr("src.publisher.CoverUploader.upload", lambda *a, **kw: upload_result)

    # 5. 推送 mock (用 list 收集调用)
    post_calls = []

    def fake_post(url, body, sig_headers, **kwargs):
        post_calls.append({"url": url, "body": body, "headers": sig_headers})
        # 2026-07-08: 推 /api/external/chapters (3-tier) 后返回 chapter 字段
        chapter_slug = body.get("chapter_slug", "test-ch")
        return {
            "ok": True,
            "chapter": {
                "id": "ch_mock_001",
                "slug": chapter_slug,
                "url": f"https://obs.example.com/chapters/{chapter_slug}",
            },
        }

    monkeypatch.setattr("src.publisher._post_with_sig", fake_post)

    return {"post_calls": post_calls, "draft": draft, "topic": topic}


# ============ E2E ============
class TestFullPipeline:
    def test_full_pipeline_with_mocks(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """全链路 mock: 选题 → 写 → 封面 → 上传 → 推送 → 状态"""
        mocks = _setup_full_mocks(monkeypatch, tmp_path)

        result = run_once(config)

        # state 推进
        assert result.last_status == "success"
        assert result.next_idx == 2
        assert result.last_pushed_idx == 1

        # 推送发生
        assert len(mocks["post_calls"]) == 1
        call = mocks["post_calls"][0]
        # 2026-07-08: publisher 改推 /api/external/chapters (3-tier Novel + Volume + Chapter)
        # 7-8 fix: 7-6 域名迁移 dev.shangkun.uk → www.shangkun.uk, 改 www
        assert call["url"] == "https://www.shangkun.uk/api/external/chapters"
        assert call["body"]["novel_slug"] == "meta-realm"
        assert call["body"]["novel_title"] == "元界"
        assert call["body"]["volume_title"] == "第一卷 · 星海之始"
        assert call["body"]["volume_order"] == 1
        assert call["body"]["chapter_slug"] == "meta-realm-ch001"
        assert call["body"]["chapter_title"]
        assert call["body"]["chapter_content"]
        assert call["body"]["external_id"] == "meta_realm_obsidian-ch001"
        assert "idempotency_key" in call["body"]
        assert call["body"]["chapter_published"] is True
        # HMAC headers
        assert "X-Publisher-Signature" in call["headers"]

    def test_idempotency_key_unique_per_run(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """每次 run_once 用新 idem_key (UUID)"""
        _setup_full_mocks(monkeypatch, tmp_path)

        # 第一次
        state1 = run_once(config)
        # 第二次 (next_idx 已是 2)
        state2 = run_once(config)

        key1 = list(state1.idempotency_keys.values())[0]  # idx=1
        # state2 应含 idx=1 和 idx=2 两个 key
        assert len(state2.idempotency_keys) == 2
        key2 = state2.idempotency_keys["2"]  # 取 idx=2 的 key (新生成的)
        assert key1 != key2
        assert len(key2) >= 32  # UUID hex 32 chars

    def test_state_persists_across_runs(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """state.json 跨 run 持久化 (next_idx 累计推进)"""
        _setup_full_mocks(monkeypatch, tmp_path)

        run_once(config)
        # 模拟"另起进程": 从文件重新加载
        state_loaded = load_state(config.state_path)
        assert state_loaded.next_idx == 2
        assert state_loaded.last_status == "success"

        run_once(config)
        state_loaded2 = load_state(config.state_path)
        assert state_loaded2.next_idx == 3


# ============ 主推送失败 vs 备份 ============
class TestFailureIsolation:
    def test_obsidian_failure_skips_backup_no_block(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """obsidian 推送失败 → backup 应被跳过 (因 backup 在 push 之后)"""
        # 本测试验证: 推送失败抛错, backup 不会被调用 (因为 backup 在 push 成功后)
        _setup_full_mocks(monkeypatch, tmp_path)

        # 把推送改成抛错
        monkeypatch.setattr(
            "src.publisher._post_with_sig",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blog 500")),
        )

        # backup mock 应不被调
        backup_called = []

        class FakeBackup:
            def __init__(self, *a, **kw):
                pass

            def upload(self, *a, **kw):
                backup_called.append(True)
                from src.github_backup import BackupResult

                return BackupResult(commit_sha="abc", pushed_files=[])

        monkeypatch.setattr("src.publisher.GithubBackup", FakeBackup)
        monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "ghp-test")

        with pytest.raises(PublisherError):
            run_once(config)

        # backup 因为在 push 之后, push 失败所以 backup 不跑
        # 这是正确语义: 推送失败, 备份不应该写 (数据未成功推送, 备份没意义)
        assert backup_called == []

    def test_obsidian_success_backup_failure_does_not_block(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """推送成功 + 备份失败 → 主推送仍算成功"""
        _setup_full_mocks(monkeypatch, tmp_path)

        from src.github_backup import GithubBackupError

        class FakeBackup:
            def __init__(self, *a, **kw):
                pass

            def upload(self, *a, **kw):
                raise GithubBackupError("API 500")

        monkeypatch.setattr("src.publisher.GithubBackup", FakeBackup)
        monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "ghp-test")

        # 主推送仍成功
        result = run_once(config)
        assert result.last_status == "success"


# ============ Dry-run 不真推 ============
class TestDryRun:
    def test_dry_run_doesnt_advance_state(
        self, config: PublisherConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--dry-run 模式: state 不推进, 不真推送"""
        _setup_full_mocks(monkeypatch, tmp_path)
        monkeypatch.setattr("src.publisher._post_with_sig", lambda *a, **kw: {"ok": True})

        # 跑 CLI --dry-run (子进程, 不污染当前进程 state)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.publisher",
                "--dry-run",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **__import__("os").environ,
                "PUBLISH_STATE_PATH": str(tmp_path / "dry_run_state.json"),
                "MINIMAXI_API_KEY": "sk-test",
                "OBSIDIAN_PUBLISH_SECRET": "secret-test-1234567890abcdef12345678",
                "OBSIDIAN_PUBLISH_ID": "novel-publisher",
            },
        )
        # dry-run 退出码 0
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "DRY RUN" in result.stdout
        # dry-run 不创建 state.json (因为 dry-run 不调用 run_once)
        assert not (tmp_path / "dry_run_state.json").exists()


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
