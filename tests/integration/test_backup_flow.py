"""
P3 集成测: src/github_backup.py 端到端
=====================================
覆盖:
- 完整链路: 一次 upload 推 5 文件 (md + jpg + json + index + changelog)
- sha 覆盖: 第二次 upload 同 chapter_idx, GET sha 后 PUT
- 5xx 重试耗尽: 抛 GithubBackupError
- 失败不阻塞主推送: 在 publisher 集成里覆盖 (test_publish_e2e), 这里只验 backup 自身

依赖: pytest + requests-mock
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest
import requests_mock

from src.github_backup import (
    BackupResult,
    ChapterMeta,
    GithubBackup,
    GithubBackupError,
)

# ============================================================
# Fixture
# ============================================================


@pytest.fixture
def backup() -> GithubBackup:
    return GithubBackup(
        repo="owner/test-backups",
        token="ghp_test_fake_token",
        max_retries=3,
    )


@pytest.fixture
def meta() -> ChapterMeta:
    return ChapterMeta(
        chapter_idx=1,
        title="测试章节",
        word_count=3000,
        created_at="2026-07-06T08:00:00.000Z",
        llm_usage={"prompt_tokens": 1000, "completion_tokens": 2500, "total_tokens": 3500},
        obsidian_post_url="https://shangkun.uk/posts/test-1",
    )


@pytest.fixture
def chapter_md() -> str:
    return (
        "![cover](https://shangkun.uk/resources/001.jpg)\n\n"
        "# 测试章节\n\n"
        "第一段正文...\n\n---\n\n第二段正文..."
    )


@pytest.fixture
def cover_jpg() -> bytes:
    # JPEG magic bytes (足够测试, 不需要真图)
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"


# ============================================================
# Helper
# ============================================================


def _mock_chapter_files_get_404(m: requests_mock.Mocker) -> None:
    """3 章节文件 GET → 404 (新文件, 不传 sha)"""
    for path_suffix in ["chapters/ch-001.md", "covers/ch-001.jpg", "meta/ch-001.json"]:
        m.get(
            f"https://api.github.com/repos/owner/test-backups/contents/"
            f"truth/novels/meta_realm_obsidian/{path_suffix}",
            status_code=404,
        )


# ============================================================
# 1. 完整链路
# ============================================================


class TestFullUploadFlow:
    def test_upload_pushes_5_files_in_order(
        self,
        backup: GithubBackup,
        meta: ChapterMeta,
        chapter_md: str,
        cover_jpg: bytes,
    ) -> None:
        """upload() 推 5 个文件: 3 章节文件 + index.json + CHANGELOG.md

        第 1 次 (空仓库): index 和 changelog 不存在, 不传 sha (新文件)
        """
        with requests_mock.Mocker() as m:
            _mock_chapter_files_get_404(m)
            # 3 章节文件 GET → 404 (新文件, 不传 sha)
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/chapters/ch-001.md",
                status_code=404,
            )
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/covers/ch-001.jpg",
                status_code=404,
            )
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/meta/ch-001.json",
                status_code=404,
            )
            # index.json GET → 404 (空)
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/index.json",
                status_code=404,
            )
            # CHANGELOG.md GET → 404 (空)
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/CHANGELOG.md",
                status_code=404,
            )
            # 5 个 PUT (3 章节 + index + changelog)
            for i in range(5):
                m.put(
                    requests_mock.ANY,
                    json={"commit": {"sha": f"sha{i:03d}abc"}},
                    status_code=201,
                )

            result = backup.upload(chapter_md, cover_jpg, meta)  # noqa: F841

        # 验证返回
        assert isinstance(result, BackupResult)
        assert result.commit_sha == "sha004abc"  # 最后一次 PUT
        assert len(result.pushed_files) == 5
        # 路径顺序: 3 章节 + index + changelog
        assert result.pushed_files[0] == "truth/novels/meta_realm_obsidian/chapters/ch-001.md"
        assert result.pushed_files[1] == "truth/novels/meta_realm_obsidian/covers/ch-001.jpg"
        assert result.pushed_files[2] == "truth/novels/meta_realm_obsidian/meta/ch-001.json"
        assert result.pushed_files[3] == "truth/novels/meta_realm_obsidian/index.json"
        assert result.pushed_files[4] == "CHANGELOG.md"

    def test_upload_index_appends_chapter(
        self,
        backup: GithubBackup,
        meta: ChapterMeta,
        chapter_md: str,
        cover_jpg: bytes,
    ) -> None:
        """index.json 追加新章节 (idempotent: 同 idx 覆盖)"""
        existing_index = json.dumps(
            {
                "novel_id": "meta_realm_obsidian",
                "chapters": [
                    {
                        "idx": 99,
                        "title": "旧的",
                        "word_count": 100,
                        "created_at": "2026-07-01T00:00:00Z",
                        "obsidian_post_url": "",
                        "files": {},
                    }
                ],
                "updated_at": "2026-07-01T00:00:00Z",
            }
        )
        encoded = base64.b64encode(existing_index.encode("utf-8")).decode("ascii")

        bodies: list[dict] = []
        with requests_mock.Mocker() as m:
            _mock_chapter_files_get_404(m)
            # 3 章节文件 GET → 404 (新文件, 不传 sha)
            for path_suffix in ["chapters/ch-001.md", "covers/ch-001.jpg", "meta/ch-001.json"]:
                m.get(
                    f"https://api.github.com/repos/owner/test-backups/contents/"
                    f"truth/novels/meta_realm_obsidian/{path_suffix}",
                    status_code=404,
                )
            # index.json GET → 返回旧内容
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/index.json",
                json={"content": encoded, "sha": "old_sha_abc"},
                status_code=200,
            )
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/CHANGELOG.md",
                status_code=404,
            )

            def put_cb(request, context):
                bodies.append(json.loads(request.body))
                context.status_code = 201
                return {"commit": {"sha": f"sha{len(bodies)}"}}

            m.put(requests_mock.ANY, json=put_cb)

            result = backup.upload(chapter_md, cover_jpg, meta)  # noqa: F841

        # 验证 push 成功
        assert result.commit_sha == "sha5"
        assert len(result.pushed_files) == 5
        # index PUT body (第 4 个) 应包含新旧 2 个 chapters
        index_body = bodies[3]
        decoded = base64.b64decode(index_body["content"]).decode("utf-8")
        index_data = json.loads(decoded)
        assert len(index_data["chapters"]) == 2
        idxs = [c["idx"] for c in index_data["chapters"]]
        assert idxs == [1, 99]  # 追加, 排序

    def test_upload_existing_chapter_overwrites_files(
        self,
        backup: GithubBackup,
        meta: ChapterMeta,
        chapter_md: str,
        cover_jpg: bytes,
    ) -> None:
        """同 idx 第二次 upload: 3 章节文件 PUT 必须带 sha (覆盖)"""
        bodies: list[dict] = []
        with requests_mock.Mocker() as m:
            _mock_chapter_files_get_404(m)
            # 3 章节文件 GET → 返回 sha (已存在)
            for path_suffix in ["chapters/ch-001.md", "covers/ch-001.jpg", "meta/ch-001.json"]:
                m.get(
                    f"https://api.github.com/repos/owner/test-backups/contents/"
                    f"truth/novels/meta_realm_obsidian/{path_suffix}",
                    json={"sha": f"existing_sha_{path_suffix.replace('/', '_')}"},
                    status_code=200,
                )
            # index.json GET → 404 (新仓库)
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/index.json",
                status_code=404,
            )
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/CHANGELOG.md",
                status_code=404,
            )

            def put_cb(request, context):
                bodies.append(json.loads(request.body))
                context.status_code = 201
                return {"commit": {"sha": f"sha{len(bodies)}"}}

            m.put(requests_mock.ANY, json=put_cb)

            backup.upload(chapter_md, cover_jpg, meta)

        # 验证: 3 章节 PUT body 都含 sha (覆盖); 2 索引 PUT body 不含 sha (新文件)
        assert len(bodies) == 5
        # 章节 PUT 带 sha
        for i in range(3):
            assert "sha" in bodies[i], f"章节 PUT {i} 应含 sha, 实际: {bodies[i]}"
            assert bodies[i]["sha"].startswith("existing_sha_")
        # index + changelog PUT 不带 sha (新文件)
        for i in range(3, 5):
            assert "sha" not in bodies[i], f"索引 PUT {i} 不应含 sha, 实际: {bodies[i]}"


# ============================================================
# 2. 失败处理
# ============================================================


class TestFailureModes:
    def test_5xx_retries_3_times_then_raises(
        self,
        backup: GithubBackup,
        meta: ChapterMeta,
        chapter_md: str,
        cover_jpg: bytes,
    ) -> None:
        """5xx 重试 3 次后抛 GithubBackupError"""
        with requests_mock.Mocker() as m, patch("src.github_backup.time.sleep"):
            _mock_chapter_files_get_404(m)
            # index/changelog GET 404
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/index.json",
                status_code=404,
            )
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/CHANGELOG.md",
                status_code=404,
            )
            # 第一个章节 PUT → 500 3 次 → 抛
            put_count = {"n": 0}

            def put_cb(request, context):
                put_count["n"] += 1
                context.status_code = 500
                return {"message": "internal error"}

            m.put(requests_mock.ANY, json=put_cb, status_code=500)

            with pytest.raises(GithubBackupError, match="重试 3 次后仍失败"):
                backup.upload(chapter_md, cover_jpg, meta)
            # 第一个章节文件调了 3 次 (重试耗尽)
            assert put_count["n"] == 3

    def test_4xx_raises_immediately(
        self,
        backup: GithubBackup,
        meta: ChapterMeta,
        chapter_md: str,
        cover_jpg: bytes,
    ) -> None:
        """4xx 立即抛, 不重试"""
        with requests_mock.Mocker() as m:
            _mock_chapter_files_get_404(m)
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/index.json",
                status_code=404,
            )
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/CHANGELOG.md",
                status_code=404,
            )
            put_count = {"n": 0}

            def put_cb(request, context):
                put_count["n"] += 1
                context.status_code = 422
                return {"message": "sha mismatch"}

            m.put(requests_mock.ANY, json=put_cb, status_code=422)

            with pytest.raises(GithubBackupError, match="4xx"):
                backup.upload(chapter_md, cover_jpg, meta)
            # 4xx 不重试
            assert put_count["n"] == 1


# ============================================================
# 3. content 编码 + content body 验证
# ============================================================


class TestContentEncoding:
    def test_chapter_md_utf8_encoded(
        self,
        backup: GithubBackup,
        meta: ChapterMeta,
        chapter_md: str,
        cover_jpg: bytes,
    ) -> None:
        """章节 md 用 utf-8 → base64 编码, server 端解码回原文"""
        with requests_mock.Mocker() as m:
            _mock_chapter_files_get_404(m)
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/index.json",
                status_code=404,
            )
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/CHANGELOG.md",
                status_code=404,
            )
            bodies = []

            def put_cb(request, context):
                bodies.append(json.loads(request.body))
                context.status_code = 201
                return {"commit": {"sha": f"sha{len(bodies)}"}}

            m.put(requests_mock.ANY, json=put_cb)

            backup.upload(chapter_md, cover_jpg, meta)

        # 第一个 PUT body 是 chapter.md
        chapter_body = bodies[0]
        assert chapter_body["message"] == "backup ch-001: 测试章节"
        decoded = base64.b64decode(chapter_body["content"]).decode("utf-8")
        assert decoded == chapter_md

    def test_cover_jpg_binary_encoded(
        self,
        backup: GithubBackup,
        meta: ChapterMeta,
        chapter_md: str,
        cover_jpg: bytes,
    ) -> None:
        """封面 jpg 字节直接 base64 编码"""
        with requests_mock.Mocker() as m:
            _mock_chapter_files_get_404(m)
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/"
                "truth/novels/meta_realm_obsidian/index.json",
                status_code=404,
            )
            m.get(
                "https://api.github.com/repos/owner/test-backups/contents/CHANGELOG.md",
                status_code=404,
            )
            bodies = []

            def put_cb(request, context):
                bodies.append(json.loads(request.body))
                context.status_code = 201
                return {"commit": {"sha": f"sha{len(bodies)}"}}

            m.put(requests_mock.ANY, json=put_cb)

            backup.upload(chapter_md, cover_jpg, meta)

        # 第二个 PUT body 是 cover.jpg
        cover_body = bodies[1]
        assert cover_body["message"] == "backup ch-001: 测试章节"
        decoded = base64.b64decode(cover_body["content"])
        assert decoded == cover_jpg
        # JPEG magic bytes 验证
        assert decoded[:4] == b"\xff\xd8\xff\xe0"
