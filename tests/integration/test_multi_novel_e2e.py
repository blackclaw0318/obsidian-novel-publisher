"""
test_multi_novel_e2e.py — v0.3.2 P5a 多本并行端到端集成测试
============================================================
覆盖:
  1. test_run_all_novels_full_pipeline — 多本并行: 拉 + 选题 + 写 + 封面 + 上传 + 推送 + 备份
  2. test_quota_check_per_novel_isolated — 配额检查: 一本本档写过, 其他本仍可推
  3. test_error_isolation_real_exceptions — 错误隔离: 1 本 LLM 异常不阻塞其他
  4. test_image_to_image_ch2_uses_ch1_cover — ch-2 拿 ch-1 封面 URL 作 subject_reference
  5. test_idempotency_keys_per_novel — idempotency_keys 各自累计
  6. test_cover_url_recorded_in_state — 推完 chapter URL 入 state.cover_urls (供下章 reference)

设计: 用 mock 替代真 LLM / 封面 / obsidian / GitHub, 但跑真实链路 (publisher._run_one_novel)。

关键设计:
- 全部用 monkeypatch.setattr (不用 with patch), 测试抛错也自动复原
- mock 函数签名严格匹配 publisher 真实调用 (_post_with_sig 顺序是 url, body, sig_headers)
- 隔离: tmp_path + 改 DEFAULT_STATE_DIR + 改 novels.yaml 路径
"""

from __future__ import annotations

import textwrap
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.cover_upload import CoverUploadResult
from src.novel_writer import ChapterDraft, LLMError
from src.publisher import (
    PublisherConfig,
    run_all_novels,
)
from src.state_per_novel import (
    load_state_for_novel,
    save_state_for_novel,
)

# ============================================================
# 共用: novels.yaml + config + 全链路 mock helper
# ============================================================


VALID_NOVELS_YAML = textwrap.dedent("""
    novels:
      - id: a_obsidian
        title: 玻璃海
        slug: glass-sea
        status: ongoing
        enabled: true
        daily_chapter: true
        target_word_count: 3000
        category: 玄幻
        keywords: [退潮, 海市蜃楼]
        volumes:
          - {order: 1, title: 卷一, start_chapter: 1, end_chapter: 50}
        chapter_slug_template: "{novel_slug}-ch{idx:03d}"
        paths:
          outline: novels/a/outline.md
          outline_meta: novels/a/outline.meta.json
          style_guide: novels/a/style_guide.md
          characters: novels/a/characters.md
          state: novels/a/state.json
          chapters_dir: novels/a/chapters/
        created_at: 2026-07-08T00:00:00Z
      - id: b_obsidian
        title: 元界
        slug: meta-realm
        status: ongoing
        enabled: true
        daily_chapter: true
        target_word_count: 3000
        category: 科幻
        keywords: [意识上传, AI 觉醒]
        volumes:
          - {order: 1, title: 第一卷, start_chapter: 1, end_chapter: 50}
        chapter_slug_template: "{novel_slug}-ch{idx:03d}"
        paths:
          outline: novels/b/outline.md
          outline_meta: novels/b/outline.meta.json
          style_guide: novels/b/style_guide.md
          characters: novels/b/characters.md
          state: novels/b/state.json
          chapters_dir: novels/b/chapters/
        created_at: 2026-07-08T00:00:00Z
    schedule:
      hours: [8, 12, 18]
      per_run_novel_limit: null
      daily_chapter_target: 3
    """)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 data/ + 真实 novels.yaml + cache"""
    # 1. 隔离 state dir (per-novel)
    data_dir = tmp_path / "data"
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True)
    cache_dir = data_dir / "cache"
    cache_dir.mkdir()

    monkeypatch.setattr("src.state_per_novel.DEFAULT_STATE_DIR", state_dir)
    monkeypatch.setattr("src.state_per_novel.FALLBACK_OLD_STATE", data_dir / "state.json")
    monkeypatch.setattr("src.state.DEFAULT_STATE_PATH", data_dir / "state.json")
    monkeypatch.setattr("src.publisher.DEFAULT_STATE_DIR", state_dir)
    monkeypatch.setattr("src.novel_outline.DEFAULT_CACHE_DIR", cache_dir)
    monkeypatch.setattr("src.style_guide.DEFAULT_CACHE_DIR", cache_dir)
    monkeypatch.setattr("src.character_loader.DEFAULT_CACHE_DIR", cache_dir)

    # 2. 写 novels.yaml 到 data_dir
    yaml_path = data_dir / "novels.yaml"
    yaml_path.write_text(VALID_NOVELS_YAML, encoding="utf-8")

    # 3. 让 publisher 从 yaml_path 加载
    monkeypatch.setattr("src.publisher.DEFAULT_NOVELS_YAML", yaml_path)
    monkeypatch.setattr("src.novel_registry.DEFAULT_NOVELS_YAML", yaml_path)

    return data_dir


@pytest.fixture
def config() -> PublisherConfig:
    """最小 config, 不开 GitHub 备份 (token="")"""
    return PublisherConfig(
        minimaxi_api_key="sk-test",
        minimaxi_base_url="https://api.test/v1",
        minimaxi_text_model="M3",
        minimaxi_image_model="image-01",
        obsidian_publish_url="https://test.example/api/external/chapters",
        obsidian_publish_id="test",
        obsidian_publish_secret="a-long-secret-for-hmac-signing-1234567890ab",
        obsidian_admin_token="test-admin-token",
        obsidian_admin_base_url="https://test.example",
        state_path=Path("/tmp/state.json"),  # 单本模式兜底, 多本模式用 state_dir
        cover_tmp_dir=Path("/tmp/covers"),
        github_backup_repo="",
        github_backup_token="",
    )


def _setup_full_mocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cover_url: str = "https://test.example/uploads/abc.jpg",
    topic_titles: list[str] | None = None,
    draft_texts: list[str] | None = None,
) -> dict:
    """全链路 mock (monkeypatch.setattr, 测试结束自动复原)

    Args:
        topic_titles: 每本对应 topic.title, 默认 ["a_ch1", "b_ch1"]
        draft_texts:  每本对应 draft.raw_text, 默认各 1500 字

    Returns:
        dict with mock handles for assertions
    """
    handles: dict = {}

    if topic_titles is None:
        topic_titles = ["玻璃海第1章", "元界第1章"]
    if draft_texts is None:
        draft_texts = [
            "玻璃海。\n\n「退潮。」林深说。\n她没回头。" + "正文续写。" * 200,
            "元界。\n\n「上传。」陈砚说。\n银色实验室。" + "正文续写。" * 200,
        ]

    # 1. 选题 mock: 每次 generate_one_shot(n=1) 返 1 个 topic list
    topics = [
        MagicMock(title=t, outline=f"大纲{i}", keywords_used=["x"], genre_hint="科幻")
        for i, t in enumerate(topic_titles)
    ]
    call_log: list = []

    def fake_topic(*args, **kwargs):
        idx = len(call_log)
        call_log.append(idx)
        return [topics[idx]] if idx < len(topics) else [topics[-1]]

    monkeypatch.setattr("src.publisher.generate_one_shot", fake_topic)
    handles["topic_log"] = call_log

    # 2. 写章节 mock: NovelWriter 实例, write_chapter 返 draft
    drafts = [
        ChapterDraft(
            raw_text=draft_texts[i],
            cover_prompt=f"A book cover scene {i}. Color: purple.",
            word_count=1500,
            usage={"total_tokens": 2000},
        )
        for i in range(len(topic_titles))
    ]
    writer_inst = MagicMock()
    writer_inst.write_chapter.side_effect = drafts
    writer_cls = MagicMock(return_value=writer_inst)
    monkeypatch.setattr("src.publisher.NovelWriter", writer_cls)
    handles["writer"] = writer_inst
    handles["drafts"] = drafts

    # 3. 封面 mock
    dummy_jpg = tmp_path / "dummy.jpg"
    dummy_jpg.write_bytes(b"\xff" * 60000)
    cover_inst = MagicMock()
    cover_inst.generate.return_value = str(dummy_jpg)
    cover_cls = MagicMock(return_value=cover_inst)
    monkeypatch.setattr("src.publisher.CoverGenerator", cover_cls)
    handles["cover"] = cover_inst

    # 4. 上传 mock
    uploader_inst = MagicMock()
    uploader_inst.upload.return_value = CoverUploadResult(
        url=cover_url,
        resource_id="abc123",
        file_size_bytes=60000,
    )
    uploader_cls = MagicMock(return_value=uploader_inst)
    monkeypatch.setattr("src.publisher.CoverUploader", uploader_cls)
    handles["uploader"] = uploader_inst

    # 5. POST mock: 严格匹配真实调用 (url, body, sig_headers, *, raw_body=None)
    post_log: list = []

    def fake_post(url, body, sig_headers, *, raw_body=None):
        post_log.append({"url": url, "body": body, "headers": sig_headers})
        return {
            "ok": True,
            "chapter": {
                "url": cover_url,
                "id": f"ch_test_{len(post_log):03d}",
                "slug": body.get("chapter_slug", "test-ch"),
            },
        }

    monkeypatch.setattr("src.publisher._post_with_sig", fake_post)
    handles["post_log"] = post_log

    return handles


# ============================================================
# Test 1: 多本并行全链路
# ============================================================


def test_run_all_novels_full_pipeline(isolated, config, monkeypatch: pytest.MonkeyPatch) -> None:
    """多本并行: 拉 + 选题 + 写 + 封面 + 上传 + 推送 (token="" 不走备份)"""
    handles = _setup_full_mocks(monkeypatch, isolated)

    from src.novel_registry import load_novels

    registry = load_novels(isolated / "novels.yaml")
    agg = run_all_novels(config, registry=registry)

    # 1. 汇总: 2 本都成功
    assert agg.total == 2
    assert agg.success == 2
    assert agg.failed == 0
    assert agg.skipped == 0

    # 2. 选题 2 次 (每本 1 次)
    assert len(handles["topic_log"]) == 2

    # 3. 写章节 2 次
    assert handles["writer"].write_chapter.call_count == 2

    # 4. 封面生成 2 次 (idx=1, 无 subject_reference_url)
    assert handles["cover"].generate.call_count == 2
    for call in handles["cover"].generate.call_args_list:
        # idx=1 时 subject_reference_url 是 None (idx < 2 条件)
        assert call.kwargs.get("subject_reference_url") is None

    # 5. POST 2 次
    assert len(handles["post_log"]) == 2

    # 6. POST body 字段 (3-tier + 关键字段)
    bodies = [c["body"] for c in handles["post_log"]]
    by_novel = {b["novel_slug"]: b for b in bodies}
    assert "glass-sea" in by_novel
    assert "meta-realm" in by_novel
    assert by_novel["glass-sea"]["chapter_slug"] == "glass-sea-ch001"
    assert by_novel["meta-realm"]["chapter_slug"] == "meta-realm-ch001"
    assert by_novel["glass-sea"]["external_id"] == "a_obsidian-ch001"
    assert by_novel["meta-realm"]["external_id"] == "b_obsidian-ch001"
    assert "idempotency_key" in by_novel["glass-sea"]
    assert "idempotency_key" in by_novel["meta-realm"]
    assert by_novel["glass-sea"]["idempotency_key"] != by_novel["meta-realm"]["idempotency_key"]

    # 6.5 v0.38 P6: 版权声明 4 字段 (随每章推送, obsidian 端入库)
    for body in bodies:
        assert body["license"] == "CC BY-NC-SA 4.0"
        assert body["license_url"] == "https://creativecommons.org/licenses/by-nc-sa/4.0/"
        assert body["copyright_holder"] == "上坤"
        assert body["aigc_disclosure"] == 1

    # 7. state 各自推进
    state_a = load_state_for_novel("a_obsidian", auto_migrate=False)
    state_b = load_state_for_novel("b_obsidian", auto_migrate=False)
    assert state_a.next_idx == 2
    assert state_b.next_idx == 2
    assert state_a.last_pushed_idx == 1
    assert state_b.last_pushed_idx == 1
    assert state_a.last_status == "success"
    assert state_b.last_status == "success"

    # 8. slot 已写入
    assert state_a.last_pushed_slot != ""
    assert state_b.last_pushed_slot != ""


# ============================================================
# Test 2: 配额检查 — 一本本档写过, 其他本仍可推
# ============================================================


def test_quota_check_per_novel_isolated(isolated, config, monkeypatch: pytest.MonkeyPatch) -> None:
    """a 本档写过 → skip; b 没写过 → push"""
    import zoneinfo
    from datetime import datetime

    from src.novel_registry import Schedule
    from src.publisher import _current_slot

    schedule = Schedule(hours=[8, 12, 18])
    sh = zoneinfo.ZoneInfo("Asia/Shanghai")
    slot = _current_slot(datetime.now(sh).astimezone(UTC), schedule)

    # a: 预写 state, last_pushed_slot = 当前 slot (本档已推过)
    from src.state import PublishState

    save_state_for_novel(
        PublishState(
            novel_id="a_obsidian",
            next_idx=2,
            last_pushed_idx=1,
            last_status="success",
            last_pushed_slot=slot,
        ),
        novel_id="a_obsidian",
    )

    handles = _setup_full_mocks(monkeypatch, isolated)

    from src.novel_registry import load_novels

    registry = load_novels(isolated / "novels.yaml")
    agg = run_all_novels(config, registry=registry)

    # a skip, b push
    assert agg.total == 2
    assert agg.success == 1
    assert agg.skipped == 1
    assert agg.failed == 0

    a_result = next(r for r in agg.details if r.novel_id == "a_obsidian")
    b_result = next(r for r in agg.details if r.novel_id == "b_obsidian")
    assert a_result.status == "skipped"
    assert b_result.status == "success"

    # 选题/写章节/封面/POST 都只跑 b
    assert len(handles["topic_log"]) == 1
    assert handles["writer"].write_chapter.call_count == 1
    assert handles["cover"].generate.call_count == 1
    assert len(handles["post_log"]) == 1

    # b state 推进
    state_b = load_state_for_novel("b_obsidian", auto_migrate=False)
    assert state_b.next_idx == 2
    assert state_b.last_status == "success"

    # a state: mark_skipped 不推进 next_idx (v0.41 fix, 跟 mark_failed 一致)
    state_a = load_state_for_novel("a_obsidian", auto_migrate=False)
    assert state_a.next_idx == 2  # v0.41 fix: 不推进,下次仍从 idx=2 起
    assert state_a.last_status == "skipped"
    assert "slot_already_pushed" in (state_a.last_error or "")


# ============================================================
# Test 3: 错误隔离 — 1 本 LLM 异常不阻塞其他
# ============================================================


def test_error_isolation_real_exceptions(isolated, config, monkeypatch: pytest.MonkeyPatch) -> None:
    """a LLM 失败 → ChapterGenError → a 状态 failed; b 仍成功"""
    # 1. mock 选题: 返 2 个 topic (a/b 各一)
    topic_a = MagicMock(title="a 第1章", outline="a 大纲", keywords_used=["x"], genre_hint="x")
    topic_b = MagicMock(title="b 第1章", outline="b 大纲", keywords_used=["y"], genre_hint="y")

    # 2. 关键: writer 第 1 次 (a) 抛 LLMError, 第 2 次 (b) 返正常 draft
    writer_inst = MagicMock()
    writer_inst.write_chapter.side_effect = [
        LLMError("M3 网络超时"),
        ChapterDraft(
            raw_text="元界正常。\n\n「继续。」" + "正文" * 200,
            cover_prompt="A sci-fi cover.",
            word_count=1500,
            usage={"total_tokens": 2000},
        ),
    ]
    writer_cls = MagicMock(return_value=writer_inst)

    # 3. 封面 mock (a 抛错不走到封面, 但 mock 仍返安全值; b 走封面)
    dummy_jpg = isolated / "dummy.jpg"
    dummy_jpg.write_bytes(b"\xff" * 60000)
    cover_inst = MagicMock()
    cover_inst.generate.return_value = str(dummy_jpg)
    cover_cls = MagicMock(return_value=cover_inst)

    # 4. 上传 mock
    uploader_inst = MagicMock()
    uploader_inst.upload.return_value = CoverUploadResult(
        url="https://test.example/uploads/x.jpg",
        resource_id="x",
        file_size_bytes=60000,
    )
    uploader_cls = MagicMock(return_value=uploader_inst)

    # 5. POST mock
    post_log: list = []

    def fake_post(url, body, sig_headers, *, raw_body=None):
        post_log.append(body.get("novel_slug"))
        return {
            "ok": True,
            "chapter": {"url": "https://test.example/uploads/x.jpg", "id": "ch_x"},
        }

    # 6. 选题 mock: 第一次 a, 第二次 b
    topic_log: list = []

    def fake_topic(*args, **kwargs):
        topic_log.append(len(topic_log))
        return [topic_a if len(topic_log) == 1 else topic_b]

    # 全部 monkeypatch.setattr
    monkeypatch.setattr("src.publisher.generate_one_shot", fake_topic)
    monkeypatch.setattr("src.publisher.NovelWriter", writer_cls)
    monkeypatch.setattr("src.publisher.CoverGenerator", cover_cls)
    monkeypatch.setattr("src.publisher.CoverUploader", uploader_cls)
    monkeypatch.setattr("src.publisher._post_with_sig", fake_post)

    from src.novel_registry import load_novels

    registry = load_novels(isolated / "novels.yaml")
    agg = run_all_novels(config, registry=registry)

    # 1. 汇总: a 失败, b 成功
    assert agg.total == 2
    assert agg.success == 1
    assert agg.failed == 1
    assert agg.skipped == 0

    # 2. detail 字段
    a_result = next(r for r in agg.details if r.novel_id == "a_obsidian")
    b_result = next(r for r in agg.details if r.novel_id == "b_obsidian")
    assert a_result.status == "failed"
    assert a_result.error is not None
    assert "LLM" in a_result.error or "M3" in a_result.error
    assert b_result.status == "success"

    # 3. 选题调了 2 次 (a/b 各一, a 抛错前选题已成功)
    assert len(topic_log) == 2

    # 4. writer 调了 2 次 (a 抛, b 成功)
    assert writer_inst.write_chapter.call_count == 2

    # 5. POST 只 1 次 (b 成功推)
    assert post_log == ["meta-realm"]

    # 6. b state 推进
    state_b = load_state_for_novel("b_obsidian", auto_migrate=False)
    assert state_b.next_idx == 2
    assert state_b.last_status == "success"

    # 7. a state 标 failed
    state_a = load_state_for_novel("a_obsidian", auto_migrate=False)
    assert state_a.last_status == "failed"
    assert "LLM" in (state_a.last_error or "")


# ============================================================
# Test 4: image-to-image — ch-2 拿 ch-1 封面 URL 作 subject_reference
# ============================================================


def test_image_to_image_ch2_uses_ch1_cover(
    isolated, config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b 本已推 ch-1 (state.cover_urls['1'] 有公网 URL), 推 ch-2 时 cover_gen 拿该 URL 作 subject_reference"""
    # 1. 预写 b state: next_idx=2, cover_urls["1"] = ch-1 封面公网 URL
    from src.state import PublishState

    save_state_for_novel(
        PublishState(
            novel_id="b_obsidian",
            next_idx=2,
            last_pushed_idx=1,
            last_status="success",
            last_pushed_slot="",
        ),
        novel_id="b_obsidian",
    )
    state_b = load_state_for_novel("b_obsidian", auto_migrate=False)
    state_b.cover_urls["1"] = "https://www.shangkun.uk/uploads/ch-001-cover.jpg"
    save_state_for_novel(state_b, novel_id="b_obsidian")

    # 2. 只跑 b (临时改 yaml, 让 a disabled)
    yaml_text = VALID_NOVELS_YAML.replace("enabled: true", "enabled: false", 1)  # 只 a 第 1 个
    (isolated / "novels.yaml").write_text(yaml_text, encoding="utf-8")

    # 3. mock (只返 b topic)
    topic_b = MagicMock(title="元界第2章", outline="续", keywords_used=["意识"], genre_hint="科幻")
    draft_b = ChapterDraft(
        raw_text="元界 ch-2。\n\n「继续上传。」" + "正文" * 200,
        cover_prompt="A sci-fi ch-2 cover.",
        word_count=1500,
        usage={"total_tokens": 2000},
    )

    writer_inst = MagicMock()
    writer_inst.write_chapter.return_value = draft_b
    writer_cls = MagicMock(return_value=writer_inst)

    dummy_jpg = isolated / "dummy2.jpg"
    dummy_jpg.write_bytes(b"\xff" * 60000)
    cover_inst = MagicMock()
    cover_inst.generate.return_value = str(dummy_jpg)
    cover_cls = MagicMock(return_value=cover_inst)

    uploader_inst = MagicMock()
    uploader_inst.upload.return_value = CoverUploadResult(
        url="https://www.shangkun.uk/uploads/ch-002-cover.jpg",
        resource_id="y",
        file_size_bytes=60000,
    )
    uploader_cls = MagicMock(return_value=uploader_inst)

    def fake_post(url, body, sig_headers, *, raw_body=None):
        return {
            "ok": True,
            "chapter": {"url": "https://www.shangkun.uk/uploads/ch-002-cover.jpg", "id": "ch_002"},
        }

    monkeypatch.setattr("src.publisher.generate_one_shot", lambda *a, **kw: [topic_b])
    monkeypatch.setattr("src.publisher.NovelWriter", writer_cls)
    monkeypatch.setattr("src.publisher.CoverGenerator", cover_cls)
    monkeypatch.setattr("src.publisher.CoverUploader", uploader_cls)
    monkeypatch.setattr("src.publisher._post_with_sig", fake_post)

    from src.novel_registry import load_novels

    registry = load_novels(isolated / "novels.yaml")
    agg = run_all_novels(config, registry=registry)

    # 1. b 推成功
    assert agg.total == 1
    assert agg.success == 1
    assert agg.failed == 0

    # 2. **核心断言**: cover_gen.generate 收到 subject_reference_url = ch-1 URL
    assert cover_inst.generate.call_count == 1
    call_kwargs = cover_inst.generate.call_args.kwargs
    assert (
        call_kwargs.get("subject_reference_url")
        == "https://www.shangkun.uk/uploads/ch-001-cover.jpg"
    )

    # 3. ch-2 URL 写入 state.cover_urls["2"]
    state_b_after = load_state_for_novel("b_obsidian", auto_migrate=False)
    assert state_b_after.cover_urls.get("2") == "https://www.shangkun.uk/uploads/ch-002-cover.jpg"
    # ch-1 仍保留
    assert state_b_after.cover_urls.get("1") == "https://www.shangkun.uk/uploads/ch-001-cover.jpg"
    # next_idx 推到 3
    assert state_b_after.next_idx == 3


# ============================================================
# Test 5: idempotency_keys 各自累计 + 不串
# ============================================================


def test_idempotency_keys_per_novel(isolated, config, monkeypatch: pytest.MonkeyPatch) -> None:
    """每本各累计 1 个 key, 互不相同, 各为 UUID hex (32 chars)"""
    handles = _setup_full_mocks(monkeypatch, isolated)

    from src.novel_registry import load_novels

    registry = load_novels(isolated / "novels.yaml")
    run_all_novels(config, registry=registry)

    state_a = load_state_for_novel("a_obsidian", auto_migrate=False)
    state_b = load_state_for_novel("b_obsidian", auto_migrate=False)
    assert "1" in state_a.idempotency_keys
    assert "1" in state_b.idempotency_keys

    key_a = state_a.idempotency_keys["1"]
    key_b = state_b.idempotency_keys["1"]
    # 各自的 key 是不同 UUID
    assert key_a != key_b
    # 各自的 key 长度 ≥ 32 (uuid4 hex = 32 chars)
    assert len(key_a) >= 32
    assert len(key_b) >= 32

    # POST 时 idempotency_key 与 state 一致
    bodies = [c["body"] for c in handles["post_log"]]
    by_novel = {b["novel_slug"]: b for b in bodies}
    assert by_novel["glass-sea"]["idempotency_key"] == key_a
    assert by_novel["meta-realm"]["idempotency_key"] == key_b


# ============================================================
# Test 6: 推完 chapter URL 入 state.cover_urls (供下章 reference)
# ============================================================


def test_cover_url_recorded_in_state(isolated, config, monkeypatch: pytest.MonkeyPatch) -> None:
    """推完 ch-1, state.cover_urls["1"] = obsidian 公网 URL"""
    test_url = "https://www.shangkun.uk/uploads/abc-test.jpg"

    _setup_full_mocks(monkeypatch, isolated, cover_url=test_url)

    from src.novel_registry import load_novels

    registry = load_novels(isolated / "novels.yaml")
    run_all_novels(config, registry=registry)

    # 两本 cover_urls["1"] 都写入
    state_a = load_state_for_novel("a_obsidian", auto_migrate=False)
    state_b = load_state_for_novel("b_obsidian", auto_migrate=False)
    assert state_a.cover_urls.get("1") == test_url
    assert state_b.cover_urls.get("1") == test_url


# ============================================================
# 入口
# ============================================================


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
