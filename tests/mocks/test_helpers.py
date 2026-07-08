# ============================================================
# test_helpers.py - Mock 工厂本身的冒烟测试
# ============================================================
# 验证:
#   - MockLLM: 真接 requests.post 返回有效 M3 / image-01 响应
#   - MockObsidian: 真接 /api/external/posts + /api/admin/resources
#   - MockGithub: 真接 PUT/GET Contents API
#   - setup_publisher_mocks: 一键装配无冲突
# ============================================================

from __future__ import annotations

import pytest
import requests

from tests.mocks.helpers import (
    MockGithub,
    MockLLM,
    MockObsidian,
    setup_publisher_mocks,
    teardown_publisher_mocks,
)


# ============ MockLLM ============
class TestMockLLM:
    def test_m3_chat_response_valid(self) -> None:
        llm = MockLLM(chapter_text="fake chapter " * 100)
        with llm.patch():
            resp = requests.post(
                "https://api.minimaxi.com/v1/chat/completions",
                json={
                    "model": "MiniMax-M3",
                    "messages": [
                        {"role": "system", "content": "s"},
                        {"role": "user", "content": "u"},
                    ],
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "choices" in data
        content = data["choices"][0]["message"]["content"]
        assert "fake chapter" in content

    def test_image_api_response_valid(self) -> None:
        llm = MockLLM(cover_url="https://cdn.test/x.jpg")
        with llm.patch():
            resp = requests.post(
                "https://api.minimaxi.com/v1/image_generation",
                json={"model": "image-01", "prompt": "test"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["image_urls"] == ["https://cdn.test/x.jpg"]

    def test_llm_fail_times(self) -> None:
        """前 N 次 500, 后续 200"""
        llm = MockLLM(llm_fail_times=2)
        with llm.patch():
            r1 = requests.post("https://api.minimaxi.com/v1/chat/completions", json={})
            r2 = requests.post("https://api.minimaxi.com/v1/chat/completions", json={})
            r3 = requests.post("https://api.minimaxi.com/v1/chat/completions", json={})
        assert r1.status_code == 500
        assert r2.status_code == 500
        assert r3.status_code == 200


# ============ MockObsidian ============
class TestMockObsidian:
    def test_external_chapters_success(self) -> None:
        obs = MockObsidian()
        with obs.patch():
            resp = requests.post(
                "https://obs.example.com/api/external/chapters",
                json={
                    "chapter_slug": "test",
                    "novel_slug": "n",
                    "volume_title": "v",
                    "chapter_title": "t",
                    "chapter_content": "c",
                    "external_id": "e1",
                },
            )
        assert resp.status_code == 201
        assert obs.chapters_received[0]["kwargs"]["json"]["chapter_slug"] == "test"

    def test_admin_resources_success(self) -> None:
        obs = MockObsidian()
        with obs.patch():
            resp = requests.post(
                "https://obs.example.com/api/admin/resources",
                files={"file": ("a.jpg", b"jpg")},
                data={"category": "image"},
            )
        assert resp.status_code == 200
        assert obs.resources_received

    def test_fail_count(self) -> None:
        obs = MockObsidian(fail_count=1)
        with obs.patch():
            r1 = requests.post("https://obs.example.com/api/external/chapters", json={})
            r2 = requests.post("https://obs.example.com/api/external/chapters", json={})
        assert r1.status_code == 500
        assert r2.status_code == 201


# ============ MockGithub ============
class TestMockGithub:
    def test_put_creates(self) -> None:
        gh = MockGithub()
        with gh.patch():
            resp = requests.request(
                "PUT",
                "https://api.github.com/repos/x/y/contents/z.md",
                json={"content": "aGk="},
            )
        assert resp.status_code in (200, 201)
        assert "commit" in resp.json()
        assert len(gh.uploads) == 1

    def test_get_returns_404(self) -> None:
        gh = MockGithub()
        with gh.patch():
            resp = requests.request("GET", "https://api.github.com/repos/x/y/contents/nope.md")
        assert resp.status_code == 404

    def test_fail_count(self) -> None:
        gh = MockGithub(fail_count=2)
        with gh.patch():
            r1 = requests.request("PUT", "https://api.github.com/repos/x/y/contents/a", json={})
            r2 = requests.request("PUT", "https://api.github.com/repos/x/y/contents/b", json={})
            r3 = requests.request("PUT", "https://api.github.com/repos/x/y/contents/c", json={})
        assert r1.status_code == 500
        assert r2.status_code == 500
        assert r3.status_code == 201


# ============ 一键装配 ============
class TestSetupHelpers:
    def test_setup_and_teardown(self) -> None:
        handles = setup_publisher_mocks(
            llm_fail_times=1, obsidian_fail_count=0, github_fail_count=0
        )
        try:
            # LLM
            r = requests.post("https://api.minimaxi.com/v1/chat/completions", json={})
            assert r.status_code == 500  # 配 fail_times=1
            # obsidian
            r = requests.post("https://obs.example.com/api/external/chapters", json={})
            assert r.status_code == 201
            # github
            r = requests.request(
                "PUT",
                "https://api.github.com/repos/x/y/contents/a",
                json={"content": "x"},
            )
            assert r.status_code in (200, 201)
        finally:
            teardown_publisher_mocks(handles)


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
