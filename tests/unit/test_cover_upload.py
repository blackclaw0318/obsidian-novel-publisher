# ============================================================
# test_cover_upload.py - 单测 v0.2 P6.1
# ============================================================
# 覆盖:
#   - CoverUploader.__init__: base_url 校验 / token 校验
#   - upload(): 2xx 成功 / 4xx 不重试立即抛 / 5xx 重试 / 网络错重试 / 重试耗尽
#   - FileNotFoundError: 文件不存在
# ============================================================

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.cover_upload import CoverUploader, CoverUploadError


@pytest.fixture
def uploader() -> CoverUploader:
    return CoverUploader(base_url="https://obs.example.com", admin_token="test-token")


@pytest.fixture
def jpg_file(tmp_path: Path) -> Path:
    p = tmp_path / "001.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 1000)
    return p


def _mock_response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.text = str(body)
    if status >= 400:
        resp.raise_for_status = MagicMock(side_effect=requests.exceptions.HTTPError(f"{status}"))
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ============ Init ============
class TestInit:
    def test_invalid_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="base_url 必须以"):
            CoverUploader(base_url="ftp://x", admin_token="t")

    def test_empty_token_raises(self) -> None:
        with pytest.raises(ValueError, match="admin_token 不能为空"):
            CoverUploader(base_url="https://x", admin_token="")

    def test_trailing_slash_stripped(self) -> None:
        u = CoverUploader(base_url="https://x.com/", admin_token="t")
        assert u.base_url == "https://x.com"


# ============ upload() 成功 ============
class TestUploadSuccess:
    def test_happy_path(self, uploader: CoverUploader, jpg_file: Path) -> None:
        ok_resp = _mock_response(
            200,
            body={"ok": True, "resource": {"id": 42, "url": "https://cdn.example.com/001.jpg"}},
        )
        with patch("src.cover_upload.requests.post", return_value=ok_resp):
            result = uploader.upload(jpg_file, title="test cover")
        assert result.url == "https://cdn.example.com/001.jpg"
        assert result.resource_id == "42"
        assert result.file_size_bytes > 0

    def test_file_not_found_raises(self, uploader: CoverUploader, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            uploader.upload(tmp_path / "nope.jpg")

    def test_default_title_uses_filename(self, uploader: CoverUploader, jpg_file: Path) -> None:
        ok_resp = _mock_response(
            200,
            body={"ok": True, "resource": {"id": 1, "url": "https://x/y.jpg"}},
        )
        with patch("src.cover_upload.requests.post", return_value=ok_resp) as mock_post:
            uploader.upload(jpg_file)
            # multipart data 应包含 title=001 (filename stem)
            data = mock_post.call_args.kwargs["data"]
            assert data["title"] == "001"


# ============ upload() 4xx 不重试 ============
class TestUpload4xx:
    def test_401_no_retry(self, uploader: CoverUploader, jpg_file: Path) -> None:
        """401 → 立即抛 CoverUploadError, 不重试"""
        resp_401 = _mock_response(401, body={"error": "unauthorized"})
        with patch("src.cover_upload.requests.post", return_value=resp_401) as mock_post:
            with patch("src.cover_upload.time.sleep"):
                with pytest.raises(CoverUploadError, match="4xx"):
                    uploader.upload(jpg_file)
                assert mock_post.call_count == 1

    def test_400_bad_request(self, uploader: CoverUploader, jpg_file: Path) -> None:
        resp_400 = _mock_response(400, body={"error": "bad"})
        with patch("src.cover_upload.requests.post", return_value=resp_400):
            with patch("src.cover_upload.time.sleep"):
                with pytest.raises(CoverUploadError, match="4xx"):
                    uploader.upload(jpg_file)

    def test_403_forbidden(self, uploader: CoverUploader, jpg_file: Path) -> None:
        resp_403 = _mock_response(403, body={"error": "forbidden"})
        with patch("src.cover_upload.requests.post", return_value=resp_403):
            with patch("src.cover_upload.time.sleep"):
                with pytest.raises(CoverUploadError, match="4xx"):
                    uploader.upload(jpg_file)


# ============ upload() 5xx 重试 ============
class TestUpload5xx:
    def test_500_retry_then_succeed(self, uploader: CoverUploader, jpg_file: Path) -> None:
        """5xx → 重试 → 第 2 次 200"""
        resp_500 = _mock_response(500, body={"err": "server"})
        resp_200 = _mock_response(
            200, body={"ok": True, "resource": {"id": 1, "url": "https://x/y.jpg"}}
        )

        with patch("src.cover_upload.requests.post", side_effect=[resp_500, resp_200]) as mock_post:
            with patch("src.cover_upload.time.sleep"):
                result = uploader.upload(jpg_file)
            assert mock_post.call_count == 2
            assert result.url == "https://x/y.jpg"

    def test_500_retry_exhausted(self, jpg_file: Path) -> None:
        u = CoverUploader(base_url="https://x", admin_token="t", max_retries=2)
        resp_500 = _mock_response(500, body={"err": "server"})
        with patch("src.cover_upload.requests.post", return_value=resp_500):
            with patch("src.cover_upload.time.sleep"):
                with pytest.raises(CoverUploadError, match="封面上传失败"):
                    u.upload(jpg_file)

    def test_503_retry(self, jpg_file: Path) -> None:
        u = CoverUploader(base_url="https://x", admin_token="t", max_retries=1)
        resp_503 = _mock_response(503, body={"err": "unavailable"})
        with patch("src.cover_upload.requests.post", return_value=resp_503):
            with patch("src.cover_upload.time.sleep"):
                with pytest.raises(CoverUploadError):
                    u.upload(jpg_file)


# ============ upload() 网络错 ============
class TestUploadNetwork:
    def test_connection_error_retry(self, uploader: CoverUploader, jpg_file: Path) -> None:
        """ConnectionError → 重试 → 第二次成功"""
        ok_resp = _mock_response(
            200, body={"ok": True, "resource": {"id": 1, "url": "https://x/y.jpg"}}
        )
        with patch(
            "src.cover_upload.requests.post",
            side_effect=[requests.exceptions.ConnectionError("net"), ok_resp],
        ) as mock_post:
            with patch("src.cover_upload.time.sleep"):
                uploader.upload(jpg_file)
            assert mock_post.call_count == 2

    def test_connection_error_exhausted(self, jpg_file: Path) -> None:
        u = CoverUploader(base_url="https://x", admin_token="t", max_retries=2)
        with (
            patch(
                "src.cover_upload.requests.post",
                side_effect=requests.exceptions.ConnectionError("net"),
            ),
            patch("src.cover_upload.time.sleep"),
        ):
            with pytest.raises(CoverUploadError, match="封面上传失败"):
                u.upload(jpg_file)


# ============ upload() 响应解析 ============
class TestUploadResponseParse:
    def test_ok_false_raises(self, uploader: CoverUploader, jpg_file: Path) -> None:
        resp = _mock_response(200, body={"ok": False, "error": "size too big"})
        with patch("src.cover_upload.requests.post", return_value=resp):
            with patch("src.cover_upload.time.sleep"):
                with pytest.raises(CoverUploadError, match="ok=false"):
                    uploader.upload(jpg_file)

    def test_missing_url_raises(self, uploader: CoverUploader, jpg_file: Path) -> None:
        resp = _mock_response(200, body={"ok": True, "resource": {"id": 1}})  # 缺 url
        with patch("src.cover_upload.requests.post", return_value=resp):
            with patch("src.cover_upload.time.sleep"):
                with pytest.raises(CoverUploadError, match="响应缺 url"):
                    uploader.upload(jpg_file)


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
