"""
state.cover_urls + mark_pushed(cover_url=) 单测 (v0.3.2 P2.5)
=============================================================
- cover_urls 字段默认空 dict
- mark_pushed(cover_url=X) → cover_urls[idx] = X
- 老 state.json 无此字段, from_dict 兼容
- cover_url="" 不覆盖
"""
from __future__ import annotations

import json

from src.state import PublishState


class TestCoverUrlsField:
    def test_default_empty_dict(self):
        s = PublishState()
        assert s.cover_urls == {}
        assert isinstance(s.cover_urls, dict)

    def test_mark_pushed_with_cover_url(self):
        s = PublishState()
        url = "https://www.shangkun.uk/uploads/abc.jpg"
        s.mark_pushed(1, "key-1", cover_url=url)
        assert s.cover_urls["1"] == url
        assert s.cover_urls == {"1": url}

    def test_mark_pushed_empty_cover_url_does_not_overwrite(self):
        """cover_url="" 不写 cover_urls (老调用点)"""
        s = PublishState(cover_urls={"1": "old-url"})
        s.mark_pushed(2, "key-2", cover_url="")
        assert "1" in s.cover_urls
        assert "2" not in s.cover_urls  # 空不覆盖

    def test_mark_pushed_records_multiple_chapters(self):
        s = PublishState()
        s.mark_pushed(1, "k1", cover_url="https://cdn/001.jpg")
        s.mark_pushed(2, "k2", cover_url="https://cdn/002.jpg")
        s.mark_pushed(3, "k3", cover_url="https://cdn/003.jpg")
        assert s.cover_urls == {
            "1": "https://cdn/001.jpg",
            "2": "https://cdn/002.jpg",
            "3": "https://cdn/003.jpg",
        }

    def test_from_dict_old_state_no_cover_urls(self):
        """老 v0.2 state.json 没 cover_urls 字段, from_dict 兼容"""
        old = {
            "novel_id": "meta_realm_obsidian",
            "next_idx": 4,
            "last_pushed_at": "2026-07-08T16:00:00Z",
            "last_pushed_idx": 3,
            "last_status": "success",
            "schema_version": 1,
        }
        s = PublishState.from_dict(old)
        assert s.cover_urls == {}  # 默认空

    def test_to_dict_includes_cover_urls(self):
        s = PublishState()
        s.cover_urls["1"] = "url"
        d = s.to_dict()
        assert "cover_urls" in d
        assert d["cover_urls"] == {"1": "url"}

    def test_roundtrip_via_json(self):
        """JSON 序列化 / 反序列化 保留 cover_urls"""
        s1 = PublishState()
        s1.cover_urls["1"] = "https://cdn/001.jpg"
        s1.cover_urls["2"] = "https://cdn/002.jpg"
        d = s1.to_dict()
        json_str = json.dumps(d)
        d2 = json.loads(json_str)
        s2 = PublishState.from_dict(d2)
        assert s2.cover_urls == s1.cover_urls
