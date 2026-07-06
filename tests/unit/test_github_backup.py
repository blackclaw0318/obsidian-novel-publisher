"""
P3 单测: src/github_backup.py
============================
覆盖:
- 路径生成 (4 个)
- base64 编码正确
- ChapterMeta 字段校验 + .now() 构造
- 错误: repo 格式错 / token 空 → ValueError
- 4xx 不重试 (mock 400, 调 1 次)
- 5xx 重试 3 次后抛 GithubBackupError

依赖: pytest + pytest-mock
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.github_backup import (
    ChapterMeta,
    GithubBackup,
    GithubBackupError,
)

# ============================================================
# Fixture
# ============================================================


@pytest.fixture
def backup() -> GithubBackup:
    """基础 GithubBackup 实例"""
    return GithubBackup(
        repo="owner/test-backups",
        token="github_pat_FAKE_TOKEN_xxxxxxxx",
    )


@pytest.fixture
def meta() -> ChapterMeta:
    """基础 ChapterMeta 实例"""
    return ChapterMeta(
        chapter_idx=1,
        title="测试章节",
        word_count=3000,
        created_at="2026-07-06T08:00:00.000Z",
        llm_usage={"prompt_tokens": 1000, "completion_tokens": 2500, "total_tokens": 3500},
        obsidian_post_url="https://shangkun.uk/posts/test-1",
    )


# ============================================================
# 1. 路径生成
# ============================================================


class TestPathGeneration:
    def test_chapter_path_zero_padded_3_digits(self, backup: GithubBackup) -> None:
        """章节路径: ch-001.md (zero-padded 3 位)"""
        assert backup._chapter_path(1) == "truth/novels/meta_realm_obsidian/chapters/ch-001.md"
        assert backup._chapter_path(42) == "truth/novels/meta_realm_obsidian/chapters/ch-042.md"
        assert backup._chapter_path(999) == "truth/novels/meta_realm_obsidian/chapters/ch-999.md"

    def test_cover_path(self, backup: GithubBackup) -> None:
        """封面路径: ch-NNN.jpg"""
        assert backup._cover_path(1) == "truth/novels/meta_realm_obsidian/covers/ch-001.jpg"
        assert backup._cover_path(100) == "truth/novels/meta_realm_obsidian/covers/ch-100.jpg"

    def test_meta_path(self, backup: GithubBackup) -> None:
        """meta 路径: ch-NNN.json"""
        assert backup._meta_path(1) == "truth/novels/meta_realm_obsidian/meta/ch-001.json"

    def test_index_path(self, backup: GithubBackup) -> None:
        """index 路径: index.json (与 novel_id 绑定)"""
        assert backup._index_path() == "truth/novels/meta_realm_obsidian/index.json"

    def test_custom_novel_id(self) -> None:
        """自定义 novel_id 隔离路径"""
        b = GithubBackup(repo="owner/test", token="t", novel_id="fantasy_realm")
        assert b._chapter_path(1) == "truth/novels/fantasy_realm/chapters/ch-001.md"
        assert b._index_path() == "truth/novels/fantasy_realm/index.json"


# ============================================================
# 2. 构造校验
# ============================================================


class TestConstructorValidation:
    def test_repo_must_be_owner_slash_name(self) -> None:
        """repo 必须 'owner/name' 格式"""
        with pytest.raises(ValueError, match="owner/name"):
            GithubBackup(repo="just-a-name", token="t")
        with pytest.raises(ValueError, match="owner/name"):
            GithubBackup(repo="", token="t")

    def test_token_must_not_be_empty(self) -> None:
        """token 不能为空字符串 / 纯空白"""
        with pytest.raises(ValueError, match="token 不能为空"):
            GithubBackup(repo="owner/repo", token="")
        with pytest.raises(ValueError, match="token 不能为空"):
            GithubBackup(repo="owner/repo", token="   ")
        with pytest.raises(ValueError, match="token 不能为空"):
            GithubBackup(repo="owner/repo", token=None)  # type: ignore[arg-type]
        # 非空 placeholder ("***" / "change…") 是 truthy + 有内容, 不挡 (调用方负责替换)
        b = GithubBackup(repo="owner/repo", token="***")
        assert b.token == "***"

    def test_default_branch_is_main(self) -> None:
        """默认分支 main"""
        b = GithubBackup(repo="owner/repo", token="t")
        assert b.branch == "main"
        assert b.max_retries == 3
        assert b.timeout_s == 30

    def test_custom_branch_and_retries(self) -> None:
        """自定义分支 + 重试次数"""
        b = GithubBackup(repo="owner/repo", token="t", branch="dev", max_retries=5)
        assert b.branch == "dev"
        assert b.max_retries == 5


# ============================================================
# 3. ChapterMeta 模型
# ============================================================


class TestChapterMeta:
    def test_basic_fields_required(self) -> None:
        """chapter_idx / title / word_count / created_at 必填"""
        m = ChapterMeta(chapter_idx=1, title="t", word_count=100, created_at="2026-07-06T00:00:00Z")
        assert m.chapter_idx == 1
        assert m.title == "t"
        assert m.word_count == 100
        assert m.created_at == "2026-07-06T00:00:00Z"
        assert m.llm_usage == {}  # default
        assert m.obsidian_post_url == ""  # default
        assert m.extra == {}  # default

    def test_chapter_idx_must_be_non_negative(self) -> None:
        """chapter_idx >= 0"""
        with pytest.raises(ValueError):
            ChapterMeta(
                chapter_idx=-1, title="t", word_count=100, created_at="2026-07-06T00:00:00Z"
            )

    def test_word_count_must_be_non_negative(self) -> None:
        """word_count >= 0"""
        with pytest.raises(ValueError):
            ChapterMeta(chapter_idx=1, title="t", word_count=-10, created_at="2026-07-06T00:00:00Z")

    def test_now_helper_fills_timestamp(self) -> None:
        """.now() 自动填 created_at = UTC now (ISO 8601)"""
        m = ChapterMeta.now(chapter_idx=1, title="t", word_count=100)
        # ISO 格式 YYYY-MM-DDTHH:MM:SS[.ffffff][+ZZ:ZZ|Z]
        # 验证: 解析回 datetime 不报错, 且与 now 接近
        parsed = datetime.fromisoformat(m.created_at.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        # 允许 5s 误差
        assert abs((now - parsed).total_seconds()) < 5

    def test_now_helper_passes_extra_kwargs(self) -> None:
        """.now() 接受 extra kwargs, 存到 .extra"""
        m = ChapterMeta.now(
            chapter_idx=1,
            title="t",
            word_count=100,
            novel_id="meta_realm",
            model="MiniMax-M3",
            duration_ms=4200,
        )
        assert m.extra == {"novel_id": "meta_realm", "model": "MiniMax-M3", "duration_ms": 4200}

    def test_model_dump_json_roundtrip(self, meta: ChapterMeta) -> None:
        """model_dump_json 可序列化, 再 parse 回 dict 数据一致"""
        dumped = meta.model_dump_json(indent=2)
        reparsed = ChapterMeta.model_validate_json(dumped)
        assert reparsed.chapter_idx == meta.chapter_idx
        assert reparsed.title == meta.title
        assert reparsed.word_count == meta.word_count
        assert reparsed.llm_usage == meta.llm_usage


# ============================================================
# 4. 错误处理: 4xx 不重试, 5xx 重试
# ============================================================


class TestPutFileRetryBehavior:
    def test_4xx_does_not_retry(self, backup: GithubBackup, meta: ChapterMeta) -> None:
        """4xx 立即抛 GithubBackupError, 不重试"""
        # Mock: 第一次 PUT 返 422 (Unprocessable, e.g. sha mismatch)
        resp_422 = MagicMock(status_code=422, text="sha mismatch")
        with patch("src.github_backup.requests.put", return_value=resp_422) as mock_put:
            with pytest.raises(GithubBackupError, match="4xx"):
                backup._put_file("test/path.md", "content", "text/md", meta)
            # 只调了 1 次 (4xx 不重试)
            assert mock_put.call_count == 1

    def test_5xx_retries_3_times_then_raises(self, backup: GithubBackup, meta: ChapterMeta) -> None:
        """5xx 重试 3 次后抛 GithubBackupError"""
        # Mock: 连续 3 次返 500
        resp_500 = MagicMock(status_code=500, text="server error")
        with (
            patch("src.github_backup.requests.put", return_value=resp_500) as mock_put,
            patch("src.github_backup.time.sleep") as mock_sleep,
        ):  # 不真睡
            with pytest.raises(GithubBackupError, match="重试 3 次后仍失败"):
                backup._put_file("test/path.md", "content", "text/md", meta)
            # 调了 3 次 (重试耗尽)
            assert mock_put.call_count == 3
            # 退避 sleep 2 次 (第 3 次失败后不睡)
            assert mock_sleep.call_count == 2
            # 退避秒数 1, 2
            sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
            assert sleep_args == [1, 2]

    def test_success_on_second_attempt_after_5xx(
        self, backup: GithubBackup, meta: ChapterMeta
    ) -> None:
        """5xx 第一次失败, 第二次成功"""
        resp_500 = MagicMock(status_code=500, text="err")
        resp_200 = MagicMock(
            status_code=200,
            json=lambda: {"commit": {"sha": "abc123def456"}},
            raise_for_status=lambda: None,
        )
        with (
            patch("src.github_backup.requests.put", side_effect=[resp_500, resp_200]),
            patch("src.github_backup.time.sleep"),
        ):
            result = backup._put_file("test/path.md", "content", "text/md", meta)
            assert result["commit_sha"] == "abc123def456"

    def test_network_error_treated_as_5xx(self, backup: GithubBackup, meta: ChapterMeta) -> None:
        """网络错 (ConnectionError/Timeout) 也走重试"""
        with patch(
            "src.github_backup.requests.put",
            side_effect=requests.exceptions.ConnectionError("timeout"),
        ), patch("src.github_backup.time.sleep"), pytest.raises(GithubBackupError, match="重试 3 次后仍失败"):
            backup._put_file("test/path.md", "content", "text/md", meta)


# ============================================================
# 5. base64 编码 (隐式通过 PUT body 验证)
# ============================================================


class TestBase64Encoding:
    def test_text_content_encoded_as_utf8(self, backup: GithubBackup, meta: ChapterMeta) -> None:
        """str 内容按 utf-8 → base64 编码"""
        content = "中文测试 🎉\n# Chapter 1"
        resp_200 = MagicMock(
            status_code=200,
            json=lambda: {"commit": {"sha": "x"}},
            raise_for_status=lambda: None,
        )
        with patch("src.github_backup.requests.put", return_value=resp_200) as mock_put:
            backup._put_file("test.md", content, "text/md", meta, allow_overwrite=False)
            # 解码 mock 收到的 body['content']
            call_kwargs = mock_put.call_args.kwargs
            body = call_kwargs["json"]
            encoded = body["content"]
            decoded = base64.b64decode(encoded).decode("utf-8")
            assert decoded == content

    def test_binary_content_encoded_directly(self, backup: GithubBackup, meta: ChapterMeta) -> None:
        """bytes 内容直接 base64 编码"""
        content = b"\xff\xd8\xff\xe0\x00\x10JFIF"  # JPEG 头
        resp_200 = MagicMock(
            status_code=200,
            json=lambda: {"commit": {"sha": "x"}},
            raise_for_status=lambda: None,
        )
        with patch("src.github_backup.requests.put", return_value=resp_200) as mock_put:
            backup._put_file("test.jpg", content, "image/jpeg", meta, allow_overwrite=False)
            body = mock_put.call_args.kwargs["json"]
            decoded = base64.b64decode(body["content"])
            assert decoded == content


# ============================================================
# 6. commit message 生成
# ============================================================


class TestCommitMessage:
    def test_new_file_message(self, backup: GithubBackup, meta: ChapterMeta) -> None:
        """新文件: 'backup ch-001: 标题'"""
        msg = backup._commit_message("truth/.../chapters/ch-001.md", meta, is_overwrite=False)
        assert msg == "backup ch-001: 测试章节"

    def test_index_overwrite_message(self, backup: GithubBackup, meta: ChapterMeta) -> None:
        """index 覆盖: 'update index.json for ch-001: 标题'"""
        msg = backup._commit_message("truth/.../index.json", meta, is_overwrite=True)
        assert msg == "update index.json for ch-001: 测试章节"

    def test_changelog_overwrite_message(self, backup: GithubBackup, meta: ChapterMeta) -> None:
        """CHANGELOG 覆盖: 'update CHANGELOG.md for ch-001: 标题'"""
        msg = backup._commit_message("CHANGELOG.md", meta, is_overwrite=True)
        assert msg == "update CHANGELOG.md for ch-001: 测试章节"
