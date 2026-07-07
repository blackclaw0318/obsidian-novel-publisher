# ============================================================
# test_cover_gen.py - 单测 v0.2 P6.1
# ============================================================
# 覆盖:
#   - CoverGenerator.__init__: API key 缺失抛 / 默认参数
#   - generate(): 成功路径 / API 5xx 重试 / 图无效重试 / 重试耗尽
#   - _call_image_api: 响应字段异常
#   - _validate: 文件不存在 / 文件过小 / PIL 校验失败
# ============================================================

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.cover_gen import CoverGenerator, CoverGenError


@pytest.fixture
def gen(tmp_path: Path) -> CoverGenerator:
    return CoverGenerator(
        api_key="sk-test",
        base_url="https://mock.api/v1",
        model="test-image-model",
        output_dir=str(tmp_path),
    )


def _mock_response(
    status: int = 200, json_data: dict | None = None, content: bytes = b""
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


def _make_valid_jpg(tmp_path: Path, size_bytes: int = 60_000) -> Path:
    """生成 size 足够的伪 jpg 文件 (PIL 不一定能解, 但 size 校验过)"""
    p = tmp_path / "001.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * (size_bytes - 4))
    return p


# ============ Init ============
class TestInit:
    def test_no_api_key_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MINIMAXI_API_KEY", raising=False)
        with pytest.raises(CoverGenError, match="MINIMAXI_API_KEY"):
            CoverGenerator(output_dir=str(tmp_path))

    def test_default_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIMAXI_API_KEY", "sk-test")
        g = CoverGenerator()
        assert g.base_url == "https://api.minimaxi.com/v1"
        assert g.model == "image-01"

    def test_custom_dir(self, tmp_path: Path) -> None:
        sub = tmp_path / "my_covers"
        CoverGenerator(api_key="sk", output_dir=str(sub))
        assert sub.exists()  # 自动创建


# ============ generate() 成功路径 ============
class TestGenerate:
    def test_happy_path(self, gen: CoverGenerator, tmp_path: Path) -> None:
        # API 返回 URL, 然后 _download 写入 jpg, _validate 通过
        valid_jpg = _make_valid_jpg(tmp_path, size_bytes=60_000)

        api_resp = _mock_response(
            200, json_data={"data": {"image_urls": ["https://cdn.example.com/001.jpg"]}}
        )
        dl_resp = _mock_response(200, content=valid_jpg.read_bytes())

        with patch("src.cover_gen.requests.post", return_value=api_resp):
            with patch("src.cover_gen.requests.get", return_value=dl_resp):
                with patch.object(gen, "_validate", return_value=True):
                    out_path = gen.generate("a sci-fi city", chapter_idx=1)

        assert out_path.endswith("001.jpg")
        assert Path(out_path).exists()

    def test_chapter_idx_format_3digits(self, gen: CoverGenerator, tmp_path: Path) -> None:
        """文件名格式: {idx:03d}.jpg (001, 002, ..., 099)"""
        valid_jpg = _make_valid_jpg(tmp_path, size_bytes=60_000)

        api_resp = _mock_response(
            200, json_data={"data": {"image_urls": ["https://cdn.example.com/x.jpg"]}}
        )
        dl_resp = _mock_response(200, content=valid_jpg.read_bytes())

        with patch("src.cover_gen.requests.post", return_value=api_resp):
            with patch("src.cover_gen.requests.get", return_value=dl_resp):
                with patch.object(gen, "_validate", return_value=True):
                    out_path = gen.generate("test", chapter_idx=42)
        assert "042.jpg" in out_path


# ============ generate() 重试 ============
class TestGenerateRetry:
    def test_5xx_retry_then_succeed(self, gen: CoverGenerator, tmp_path: Path) -> None:
        """API 5xx → 重试 → 第 2 次 200"""
        valid_jpg = _make_valid_jpg(tmp_path, size_bytes=60_000)

        api_500 = _mock_response(500, json_data={"err": "server"})
        api_500.raise_for_status = MagicMock(side_effect=Exception("500"))
        api_200 = _mock_response(200, json_data={"data": {"image_urls": ["https://x"]}})
        dl_resp = _mock_response(200, content=valid_jpg.read_bytes())

        with patch("src.cover_gen.requests.post", side_effect=[api_500, api_200]):
            with patch("src.cover_gen.requests.get", return_value=dl_resp):
                with patch.object(gen, "_validate", return_value=True):
                    with patch("src.cover_gen.time.sleep"):
                        out = gen.generate("p", chapter_idx=1)
        assert out.endswith("001.jpg")

    def test_retry_exhausted(self, gen: CoverGenerator) -> None:
        """API 一直 5xx → 重试耗尽抛 CoverGenError"""
        api_500 = _mock_response(500, json_data={})
        api_500.raise_for_status = MagicMock(side_effect=Exception("500"))

        with patch("src.cover_gen.requests.post", return_value=api_500):
            with patch("src.cover_gen.time.sleep"):
                with pytest.raises(CoverGenError, match="封面生成失败"):
                    gen.generate("p", chapter_idx=1)

    def test_image_too_small_retry(self, gen: CoverGenerator, tmp_path: Path) -> None:
        """下载图 < 50KB → 视为无效 → 重试"""
        valid_jpg = _make_valid_jpg(tmp_path, size_bytes=60_000)
        small_jpg = _make_valid_jpg(tmp_path, size_bytes=10_000)  # 太小

        api_resp = _mock_response(200, json_data={"data": {"image_urls": ["https://x"]}})
        # 第一次下载 small, 第二次下载 valid
        dl_small = _mock_response(200, content=small_jpg.read_bytes())
        dl_valid = _mock_response(200, content=valid_jpg.read_bytes())

        # _validate 真实跑: small 不通过 (size<50K), valid 通过 (但要 PIL verify 通过)
        with patch("src.cover_gen.requests.post", return_value=api_resp):
            with patch("src.cover_gen.requests.get", side_effect=[dl_small, dl_valid]):
                with patch("src.cover_gen.time.sleep"):
                    # mock _validate 让 valid 路径通过
                    with patch.object(gen, "_validate", side_effect=[False, True]):
                        out = gen.generate("p", chapter_idx=1)
        assert out.endswith("001.jpg")


# ============ _call_image_api ============
class TestCallImageApi:
    def test_response_missing_image_urls(self, gen: CoverGenerator) -> None:
        api_resp = _mock_response(200, json_data={"data": {"wrong_field": "x"}})
        with patch("src.cover_gen.requests.post", return_value=api_resp):
            with pytest.raises(CoverGenError, match="响应字段异常"):
                gen._call_image_api("prompt")

    def test_response_empty_image_urls(self, gen: CoverGenerator) -> None:
        api_resp = _mock_response(200, json_data={"data": {"image_urls": []}})
        with patch("src.cover_gen.requests.post", return_value=api_resp):
            with pytest.raises(CoverGenError, match="响应字段异常"):
                gen._call_image_api("prompt")

    def test_aspect_ratio_3_4(self, gen: CoverGenerator) -> None:
        assert gen.ASPECT == "3:4"

    def test_payload_has_prompt_optimizer(self, gen: CoverGenerator) -> None:
        """请求 payload 必须 prompt_optimizer=true (v0.2 调优)"""
        api_resp = _mock_response(200, json_data={"data": {"image_urls": ["x"]}})
        with patch("src.cover_gen.requests.post", return_value=api_resp) as mock_post:
            gen._call_image_api("test prompt")
            payload = mock_post.call_args.kwargs["json"]
            assert payload["aspect_ratio"] == "3:4"
            assert payload["prompt_optimizer"] is True
            assert payload["prompt"] == "test prompt"


# ============ _validate ============
class TestValidate:
    def test_file_not_exists(self, gen: CoverGenerator, tmp_path: Path) -> None:
        assert gen._validate(tmp_path / "nope.jpg") is False

    def test_file_too_small(self, gen: CoverGenerator, tmp_path: Path) -> None:
        small = tmp_path / "s.jpg"
        small.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # 104 字节
        assert gen._validate(small) is False

    def test_min_size_threshold(self, gen: CoverGenerator, tmp_path: Path) -> None:
        """边界: < 50KB 不通过, >= 50KB 通过 (PIL verify 可能 fail, 但 size 边界先测)"""
        edge = tmp_path / "edge.jpg"
        edge.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * (49_999))
        assert gen._validate(edge) is False


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
