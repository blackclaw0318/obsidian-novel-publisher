# ============================================================
# tests/mocks/helpers.py - 公共 mock 工厂 (v0.2 P6.3)
# ============================================================
# 给单测/集成测复用:
#   - MockLLM       - 模拟 minimax M3 + image-01
#   - MockObsidian  - 模拟 obsidian-journal /api/external/posts
#   - MockGithub    - 模拟 GitHub Contents API
#   - setup_publisher_mocks - 一键装配 + 返回 handle 字典
# ============================================================

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch


# ============ MockLLM ============
class MockLLM:
    """模拟 minimax M3 + image-01 API

    Usage:
        llm = MockLLM()
        with llm.patch():
            # 全链路 mock LLM
            ...
    """

    def __init__(
        self,
        *,
        chapter_text: str | None = None,
        topic_json: list[dict] | None = None,
        cover_url: str = "https://cdn.example.com/cover.jpg",
        llm_fail_times: int = 0,
    ):
        # 默认 3000 字章节 (3000 个 "字" 重复)
        self.chapter_text = chapter_text or ("章节正文 " * 1000)
        # 默认选题 JSON
        self.topic_json = topic_json or [
            {"title": "测试章", "outline": "未来人类探索银河系, 遭遇外星文明", "genre_hint": "科幻"}
        ]
        self.cover_url = cover_url
        self.llm_fail_times = llm_fail_times  # 前 N 次返回 500

    def patch(self):
        """返回 context manager (含全部 patch)"""
        from contextlib import ExitStack

        stack = ExitStack()

        topic_str = json.dumps(self.topic_json, ensure_ascii=False)
        call_count = {"n": 0}

        def fake_post(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] <= self.llm_fail_times:
                resp = MagicMock()
                resp.status_code = 500
                resp.raise_for_status = MagicMock(side_effect=Exception("500 server error"))
                resp.json = MagicMock(side_effect=ValueError("bad"))
                return resp
            # 判 URL: image_generation vs chat/completions
            if "image_generation" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"data": {"image_urls": [self.cover_url]}}
                resp.raise_for_status = MagicMock()
                return resp
            # chat/completions
            messages = kwargs.get("json", {}).get("messages", [])
            user_content = ""
            if len(messages) >= 2:
                user_content = messages[1].get("content", "")
            is_topic = "outline" in user_content.lower() and len(user_content) < 3000
            content = topic_str if is_topic else self.chapter_text
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 1000},
            }
            resp.raise_for_status = MagicMock()
            return resp

        stack.enter_context(patch("requests.post", side_effect=fake_post))

        # image download (GET)
        valid_jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 1000

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = valid_jpg
            resp.raise_for_status = MagicMock()
            return resp

        stack.enter_context(patch("requests.get", side_effect=fake_get))

        return stack


# ============ MockObsidian ============
class MockObsidian:
    """模拟 obsidian-journal /api/external/chapters (v0.38+) + /api/admin/resources"""

    def __init__(self, *, fail_count: int = 0, response_status: int = 201):
        self.fail_count = fail_count
        self.response_status = response_status
        self.chapters_received: list[dict] = []
        self.resources_received: list[dict] = []

    def patch(self):
        from contextlib import ExitStack

        stack = ExitStack()
        counter = {"chapters": 0, "resources": 0}

        def fake_post(url, **kwargs):
            counter["chapters" if "/api/external/chapters" in url else "resources"] += 1

            # 2026-07-08: publisher 改推 /api/external/chapters (3-tier)
            if "api/external/chapters" in url:
                self.chapters_received.append({"url": url, "kwargs": kwargs})
                if counter["chapters"] <= self.fail_count:
                    resp = MagicMock()
                    resp.status_code = 500
                    resp.text = "server error"
                    resp.raise_for_status = MagicMock(side_effect=Exception("500"))
                    return resp
                # 成功: 返回 chapter 字段
                resp = MagicMock()
                resp.status_code = self.response_status
                body = kwargs.get("data") or kwargs.get("json") or {}
                if isinstance(body, bytes):
                    try:
                        import json as _json

                        body = _json.loads(body.decode("utf-8"))
                    except Exception:
                        body = {}
                chapter_slug = body.get("chapter_slug", "test-ch")
                resp.json.return_value = {
                    "ok": True,
                    "chapter": {
                        "id": "ch_mock_001",
                        "slug": chapter_slug,
                        "url": f"https://obs.example.com/chapters/{chapter_slug}",
                        "novel_slug": body.get("novel_slug", "meta-realm"),
                        "volume_order": body.get("volume_order", 1),
                        "chapter_order": 1,
                    },
                }
                resp.raise_for_status = MagicMock()
                return resp

            if "/api/admin/resources" in url:
                self.resources_received.append({"url": url, "kwargs": kwargs})
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "ok": True,
                    "resource": {"id": "res-1", "url": "https://obs.example.com/resources/001.jpg"},
                }
                resp.raise_for_status = MagicMock()
                return resp

            # 未识别 URL → 404
            resp = MagicMock()
            resp.status_code = 404
            resp.raise_for_status = MagicMock(side_effect=Exception("404"))
            return resp

        stack.enter_context(patch("requests.post", side_effect=fake_post))
        return stack


# ============ MockGithub ============
class MockGithub:
    """模拟 GitHub Contents API (PUT + GET)"""

    def __init__(self, *, fail_count: int = 0):
        self.fail_count = fail_count
        self.uploads: list[dict] = []
        self.counter = {"n": 0}

    def patch(self):
        from contextlib import ExitStack

        stack = ExitStack()

        def fake_request(method, url, **kwargs):
            if method == "GET":
                resp = MagicMock()
                resp.status_code = 404  # 默认不存在, 让 PUT 创建
                resp.json.return_value = {"message": "Not Found"}
                resp.raise_for_status = MagicMock()
                return resp
            if method == "PUT":
                self.counter["n"] += 1
                self.uploads.append({"url": url, "kwargs": kwargs})
                if self.counter["n"] <= self.fail_count:
                    resp = MagicMock()
                    resp.status_code = 500
                    resp.text = "server error"
                    resp.raise_for_status = MagicMock(side_effect=Exception("500"))
                    return resp
                resp = MagicMock()
                resp.status_code = 200 if self.fail_count == 0 else 201
                resp.json.return_value = {"commit": {"sha": "abc123def456"}}
                resp.raise_for_status = MagicMock()
                return resp
            # 未知方法
            resp = MagicMock()
            resp.status_code = 405
            return resp

        stack.enter_context(patch("requests.request", side_effect=fake_request))
        return stack


# ============ 一键装配 ============
def setup_publisher_mocks(
    *,
    llm_fail_times: int = 0,
    obsidian_fail_count: int = 0,
    github_fail_count: int = 0,
    topic_json: list[dict] | None = None,
    chapter_text: str | None = None,
) -> dict[str, Any]:
    """一键装配 publisher 全链路 mock

    三个 mock 合并到同一个 requests.post side_effect, 避免 patch 覆盖。

    Returns:
        dict 含 llm/obsidian/github 实例, 供断言用
    """
    from contextlib import ExitStack

    llm = MockLLM(
        chapter_text=chapter_text,
        topic_json=topic_json,
        llm_fail_times=llm_fail_times,
    )
    obsidian = MockObsidian(fail_count=obsidian_fail_count)
    github = MockGithub(fail_count=github_fail_count)

    topic_str = json.dumps(llm.topic_json, ensure_ascii=False)
    valid_jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 1000

    # 合并 counter
    llm_count = {"n": 0}
    obs_chapters_count = {"n": 0}
    obs_res_count = {"n": 0}
    gh_put_count = {"n": 0}

    def combined_post(url, **kwargs):
        # GitHub
        if "api.github.com" in url:
            gh_put_count["n"] += 1
            github.uploads.append({"url": url, "kwargs": kwargs})
            if gh_put_count["n"] <= github.fail_count:
                resp = MagicMock()
                resp.status_code = 500
                resp.raise_for_status = MagicMock(side_effect=Exception("500"))
                return resp
            resp = MagicMock()
            resp.status_code = 200 if github.fail_count == 0 else 201
            resp.json.return_value = {"commit": {"sha": "abc123"}}
            resp.raise_for_status = MagicMock()
            return resp

        # Obsidian /api/external/chapters (v0.38+, 取代 /api/external/posts)
        if "/api/external/chapters" in url:
            obs_chapters_count["n"] += 1
            obsidian.chapters_received.append({"url": url, "kwargs": kwargs})
            if obs_chapters_count["n"] <= obsidian.fail_count:
                resp = MagicMock()
                resp.status_code = 500
                resp.raise_for_status = MagicMock(side_effect=Exception("500"))
                return resp
            resp = MagicMock()
            resp.status_code = obsidian.response_status
            body = kwargs.get("data") or kwargs.get("json") or {}
            if isinstance(body, bytes):
                try:
                    import json as _json

                    body = _json.loads(body.decode("utf-8"))
                except Exception:
                    body = {}
            chapter_slug = (
                body.get("chapter_slug", "test-ch") if isinstance(body, dict) else "test-ch"
            )
            resp.json.return_value = {
                "ok": True,
                "chapter": {
                    "id": "ch_mock_001",
                    "slug": chapter_slug,
                    "url": f"https://obs.example.com/chapters/{chapter_slug}",
                    "novel_slug": (
                        body.get("novel_slug", "meta-realm")
                        if isinstance(body, dict)
                        else "meta-realm"
                    ),
                    "volume_order": body.get("volume_order", 1) if isinstance(body, dict) else 1,
                    "chapter_order": 1,
                },
            }
            resp.raise_for_status = MagicMock()
            return resp

        # Obsidian /api/admin/resources
        if "/api/admin/resources" in url:
            obs_res_count["n"] += 1
            obsidian.resources_received.append({"url": url, "kwargs": kwargs})
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "ok": True,
                "resource": {"id": "res-1", "url": "https://obs/c.jpg"},
            }
            resp.raise_for_status = MagicMock()
            return resp

        # LLM chat/completions (前 N 次 fail)
        if "chat/completions" in url:
            llm_count["n"] += 1
            if llm_count["n"] <= llm.llm_fail_times:
                resp = MagicMock()
                resp.status_code = 500
                resp.raise_for_status = MagicMock(side_effect=Exception("500"))
                resp.json = MagicMock(side_effect=ValueError("bad"))
                return resp
            messages = kwargs.get("json", {}).get("messages", [])
            user_content = messages[1].get("content", "") if len(messages) >= 2 else ""
            is_topic = "outline" in user_content.lower() and len(user_content) < 3000
            content = topic_str if is_topic else llm.chapter_text
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 1000},
            }
            resp.raise_for_status = MagicMock()
            return resp

        # LLM image_generation
        if "image_generation" in url:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"data": {"image_urls": [llm.cover_url]}}
            resp.raise_for_status = MagicMock()
            return resp

        # 未知 → 404
        resp = MagicMock()
        resp.status_code = 404
        resp.raise_for_status = MagicMock(side_effect=Exception("404"))
        return resp

    def combined_get(url, **kwargs):
        # GitHub GET
        if "api.github.com" in url:
            resp = MagicMock()
            resp.status_code = 404
            resp.json.return_value = {"message": "Not Found"}
            resp.raise_for_status = MagicMock()
            return resp
        # LLM image download (valid jpg)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = valid_jpg
        resp.raise_for_status = MagicMock()
        return resp

    def combined_request(method, url, **kwargs):
        if method == "GET":
            return combined_get(url, **kwargs)
        if method == "PUT":
            return combined_post(url, **kwargs)
        resp = MagicMock()
        resp.status_code = 405
        return resp

    stack = ExitStack()
    stack.enter_context(patch("requests.post", side_effect=combined_post))
    stack.enter_context(patch("requests.get", side_effect=combined_get))
    stack.enter_context(patch("requests.request", side_effect=combined_request))

    return {
        "llm": llm,
        "obsidian": obsidian,
        "github": github,
        "_stack": stack,
    }


def teardown_publisher_mocks(handles: dict[str, Any]) -> None:
    """清理 mock stack"""
    handles["_stack"].__exit__(None, None, None)
