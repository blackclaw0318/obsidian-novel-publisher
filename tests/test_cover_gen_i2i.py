"""
cover_gen image-to-image 单测 (v0.3.2 P2.5)
============================================
- _is_minimax_accessible_url 黑白名单
- _call_image_api 加 subject_reference (用 requests_mock)
- 退化: localhost / 不可访问 URL 走 text-to-image
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.cover_gen import CoverGenerator


@pytest.fixture
def gen() -> CoverGenerator:
    return CoverGenerator(
        api_key="sk-test",
        base_url="https://api.test/v1",
        model="image-01",
        output_dir="/tmp/covers_test",
    )


# ============================================================
# _is_minimax_accessible_url
# ============================================================


class TestIsMinimaxAccessible:
    @pytest.mark.parametrize(
        "good",
        [
            "https://www.shangkun.uk/uploads/abc.jpg",
            "https://cdn.example.com/cover.png",
            "http://api.example.com/files/123.jpg",  # http 也算 (老板域可能是 http)
        ],
    )
    def test_public_urls_accessible(self, gen, good):
        assert gen._is_minimax_accessible_url(good) is True

    @pytest.mark.parametrize(
        "bad",
        [
            "",                                      # 空
            "http://localhost:3000/uploads/abc.jpg", # localhost
            "http://127.0.0.1:8080/x.jpg",          # 127.0.0.1
            "http://0.0.0.0:3000/x.jpg",            # 0.0.0.0
            "http://192.168.1.10:3000/x.jpg",       # 内网 192.168
            "http://10.0.0.5/x.jpg",                # 内网 10.x
            "ftp://example.com/cover.jpg",          # 非 http(s)
            "not-a-url",                            # 烂格式
        ],
    )
    def test_local_or_bad_urls_rejected(self, gen, bad):
        assert gen._is_minimax_accessible_url(bad) is False


# ============================================================
# _call_image_api (subject_reference 集成)
# ============================================================


class TestCallImageApi:
    def test_no_subject_reference_when_url_none(self, gen):
        """url=None → payload 不含 subject_reference"""
        with patch("src.cover_gen.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "data": {"image_urls": ["https://cdn.test/x.jpg"]}
            }
            mock_post.return_value.raise_for_status = MagicMock()

            gen._call_image_api("test prompt", subject_reference_url=None)
            call_kwargs = mock_post.call_args.kwargs
            payload = call_kwargs["json"]
            assert "subject_reference" not in payload

    def test_no_subject_reference_when_localhost(self, gen):
        """url 是 localhost → 退化, 不加 subject_reference"""
        with patch("src.cover_gen.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "data": {"image_urls": ["https://cdn.test/x.jpg"]}
            }
            mock_post.return_value.raise_for_status = MagicMock()

            gen._call_image_api("test prompt", subject_reference_url="http://localhost:3000/x.jpg")
            payload = mock_post.call_args.kwargs["json"]
            assert "subject_reference" not in payload

    def test_subject_reference_added_for_public_url(self, gen):
        """公网 URL → payload 加 subject_reference"""
        with patch("src.cover_gen.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "data": {"image_urls": ["https://cdn.test/x.jpg"]}
            }
            mock_post.return_value.raise_for_status = MagicMock()

            url = "https://www.shangkun.uk/uploads/abc123.jpg"
            gen._call_image_api("test prompt", subject_reference_url=url)
            payload = mock_post.call_args.kwargs["json"]
            assert "subject_reference" in payload
            assert len(payload["subject_reference"]) == 1
            assert payload["subject_reference"][0]["type"] == "character"
            assert payload["subject_reference"][0]["image_file"] == url

    def test_payload_still_contains_base_fields(self, gen):
        """subject_reference 不破坏原有 payload"""
        with patch("src.cover_gen.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "data": {"image_urls": ["https://cdn.test/x.jpg"]}
            }
            mock_post.return_value.raise_for_status = MagicMock()

            url = "https://cdn.example.com/prev.jpg"
            gen._call_image_api("prompt text", subject_reference_url=url)
            payload = mock_post.call_args.kwargs["json"]
            assert payload["model"] == "image-01"
            assert payload["prompt"] == "prompt text"
            assert payload["aspect_ratio"] == "3:4"
            assert payload["response_format"] == "url"
            assert payload["n"] == 1
            assert payload["prompt_optimizer"] is True
            assert payload["subject_reference"][0]["image_file"] == url


# ============================================================
# generate() 端到端 (mock _call_image_api + _download)
# ============================================================


class TestGenerateWithSubjectRef:
    def test_generate_ch1_text_to_image(self, gen, tmp_path):
        """ch-1: subject_reference_url=None → payload 无 subject_reference"""
        with patch.object(gen, "_call_image_api", return_value="https://cdn.test/x.jpg") as mock_api, \
             patch.object(gen, "_download", return_value=tmp_path / "001.jpg") as mock_dl, \
             patch.object(gen, "_validate", return_value=True):
            # 写一个 dummy file 给 _validate
            (tmp_path / "001.jpg").write_bytes(b"\xff" * 60000)
            gen.output_dir = tmp_path

            out = gen.generate("test prompt", chapter_idx=1)
            # _call_image_api 被调时, subject_reference_url=None
            assert mock_api.call_args.kwargs["subject_reference_url"] is None

    def test_generate_ch2_uses_prev_url(self, gen, tmp_path):
        """ch-2: 传 prev_url → _call_image_api 收到"""
        with patch.object(gen, "_call_image_api", return_value="https://cdn.test/y.jpg") as mock_api, \
             patch.object(gen, "_download", return_value=tmp_path / "002.jpg") as mock_dl, \
             patch.object(gen, "_validate", return_value=True):
            (tmp_path / "002.jpg").write_bytes(b"\xff" * 60000)
            gen.output_dir = tmp_path

            prev_url = "https://www.shangkun.uk/uploads/ch-001-cover.jpg"
            gen.generate("ch2 prompt", chapter_idx=2, subject_reference_url=prev_url)
            assert mock_api.call_args.kwargs["subject_reference_url"] == prev_url

    def test_generate_ch2_localhost_falls_back(self, gen, tmp_path):
        """ch-2: localhost URL → _is_minimax_accessible_url False → 退化"""
        with patch.object(gen, "_call_image_api", return_value="https://cdn.test/y.jpg") as mock_api, \
             patch.object(gen, "_download", return_value=tmp_path / "002.jpg") as mock_dl, \
             patch.object(gen, "_validate", return_value=True):
            (tmp_path / "002.jpg").write_bytes(b"\xff" * 60000)
            gen.output_dir = tmp_path

            # localhost → _is_minimax_accessible_url False → payload 不加 subject_reference
            gen.generate("ch2 prompt", chapter_idx=2, subject_reference_url="http://localhost:3000/x.jpg")
            # _call_image_api 仍被调 (内部判断)
            assert mock_api.called
