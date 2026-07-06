"""
test_hmac_client.py — hmac_client.py 单元测试

覆盖:
- canonical_body (sort_keys + no whitespace + Unicode)
- compute_signature (确定性 / 不同输入 → 不同签名)
- HmacClient.sign (返回所有 headers / 包含正确字段)
- HmacClient.verify (正常 / 时间漂移 / signature mismatch / timing-safe)
- new_idempotency_key (UUID v4 hex, 36 chars)
- 构造校验 (publish_id 空 / secret 太短)
"""

from __future__ import annotations

from src.hmac_client import (
    HmacClient,
    HmacConfig,
    HmacError,
    canonical_body,
    compute_signature,
    new_idempotency_key,
)

SECRET = "a" * 64  # 64 hex chars


class TestCanonicalBody:
    """canonical_body 测试"""

    def test_simple_dict(self):
        body = {"b": 2, "a": 1}
        # sort_keys + no whitespace
        assert canonical_body(body) == '{"a":1,"b":2}'

    def test_nested_dict_sorted(self):
        body = {"z": {"y": 2, "x": 1}, "a": 1}
        result = canonical_body(body)
        assert result == '{"a":1,"z":{"x":1,"y":2}}'

    def test_unicode_preserved(self):
        body = {"title": "科幻小说", "chapter": "第 1 章"}
        result = canonical_body(body)
        # ensure_ascii=False, 中文保留
        assert "科幻小说" in result
        assert "第 1 章" in result

    def test_deterministic(self):
        body = {"a": 1, "b": [1, 2, 3], "c": {"x": True}}
        s1 = canonical_body(body)
        s2 = canonical_body(body)
        assert s1 == s2

    def test_list_order_preserved(self):
        """list 元素顺序必须保留 (sort_keys 不影响 list)"""
        body = {"tags": ["c", "a", "b"]}
        assert canonical_body(body) == '{"tags":["c","a","b"]}'


class TestComputeSignature:
    """compute_signature 测试"""

    def test_deterministic(self):
        body = {"a": 1}
        sig1 = compute_signature(SECRET, 1717699200000, body)
        sig2 = compute_signature(SECRET, 1717699200000, body)
        assert sig1 == sig2
        # SHA-256 hex = 64 chars
        assert len(sig1) == 64

    def test_different_secret_different_sig(self):
        body = {"a": 1}
        sig1 = compute_signature(SECRET, 1717699200000, body)
        sig2 = compute_signature("b" * 64, 1717699200000, body)
        assert sig1 != sig2

    def test_different_ts_different_sig(self):
        body = {"a": 1}
        sig1 = compute_signature(SECRET, 1717699200000, body)
        sig2 = compute_signature(SECRET, 1717699200001, body)
        assert sig1 != sig2

    def test_different_body_different_sig(self):
        ts = 1717699200000
        sig1 = compute_signature(SECRET, ts, {"a": 1})
        sig2 = compute_signature(SECRET, ts, {"a": 2})
        assert sig1 != sig2


class TestHmacClientSign:
    """HmacClient.sign 测试"""

    def test_returns_required_headers(self):
        client = HmacClient(HmacConfig(publish_id="novel-publisher", publish_secret=SECRET))
        headers = client.sign({"a": 1})
        assert "X-Publisher-Id" in headers
        assert "X-Publisher-Signature" in headers
        assert "X-Publisher-Timestamp" in headers
        assert "X-Idempotency-Key" in headers
        assert headers["X-Publisher-Id"] == "novel-publisher"

    def test_uses_passed_timestamp(self):
        client = HmacClient(
            HmacConfig(publish_id="x", publish_secret=SECRET)
        )  # noqa: F841 验证构造即可
        headers = client.sign({"a": 1}, timestamp_ms=1234567890000)
        assert headers["X-Publisher-Timestamp"] == "1234567890000"

    def test_uses_passed_idempotency_key(self):
        client = HmacClient(
            HmacConfig(publish_id="x", publish_secret=SECRET)
        )  # noqa: F841 验证构造即可
        headers = client.sign({"a": 1}, idempotency_key="my-key-abc")
        assert headers["X-Idempotency-Key"] == "my-key-abc"

    def test_generates_idempotency_key_when_missing(self):
        client = HmacClient(
            HmacConfig(publish_id="x", publish_secret=SECRET)
        )  # noqa: F841 验证构造即可
        headers = client.sign({"a": 1})
        # UUID v4 hex = 32 chars
        assert len(headers["X-Idempotency-Key"]) == 32

    def test_signature_in_headers_matches_manual_compute(self):
        client = HmacClient(
            HmacConfig(publish_id="x", publish_secret=SECRET)
        )  # noqa: F841 验证构造即可
        body = {"chapter": 1, "title": "测试"}
        headers = client.sign(body, timestamp_ms=1717699200000)
        expected_sig = compute_signature(SECRET, 1717699200000, body)
        assert headers["X-Publisher-Signature"] == expected_sig


class TestHmacClientVerify:
    """HmacClient.verify 服务端验签测试"""

    def test_verify_ok(self):
        _client = HmacClient(
            HmacConfig(publish_id="x", publish_secret=SECRET)
        )  # noqa: F841 验证构造即可
        body = {"a": 1}
        ts = 1717699200000
        sig = compute_signature(SECRET, ts, body)

        ok, reason = HmacClient.verify(
            secret=SECRET,
            body=body,
            timestamp_ms=ts,
            signature_hex=sig,
            now_ms=ts,  # 同一时刻
        )
        assert ok is True
        assert reason == "ok"

    def test_verify_rejects_old_timestamp(self):
        _client = HmacClient(
            HmacConfig(publish_id="x", publish_secret=SECRET)
        )  # noqa: F841 验证构造即可
        body = {"a": 1}
        ts = 1717699200000
        sig = compute_signature(SECRET, ts, body)

        # 6 分钟前 → 超 ±5min 窗口
        now = ts - 6 * 60 * 1000
        ok, reason = HmacClient.verify(
            secret=SECRET,
            body=body,
            timestamp_ms=ts,
            signature_hex=sig,
            now_ms=now,
        )
        assert ok is False
        assert "drift" in reason

    def test_verify_rejects_future_timestamp(self):
        body = {"a": 1}
        ts = 1717699200000
        sig = compute_signature(SECRET, ts, body)

        # 10 分钟后 → 超 ±5min 窗口
        now = ts + 10 * 60 * 1000
        ok, reason = HmacClient.verify(
            secret=SECRET,
            body=body,
            timestamp_ms=ts,
            signature_hex=sig,
            now_ms=now,
        )
        assert ok is False
        assert "drift" in reason

    def test_verify_rejects_wrong_signature(self):
        body = {"a": 1}
        ts = 1717699200000

        ok, reason = HmacClient.verify(
            secret=SECRET,
            body=body,
            timestamp_ms=ts,
            signature_hex="0" * 64,  # 错的
            now_ms=ts,
        )
        assert ok is False
        assert "signature mismatch" in reason

    def test_verify_within_window(self):
        body = {"a": 1}
        ts = 1717699200000
        sig = compute_signature(SECRET, ts, body)

        # 4 分钟前 → 在窗口内
        now = ts - 4 * 60 * 1000
        ok, _ = HmacClient.verify(
            secret=SECRET,
            body=body,
            timestamp_ms=ts,
            signature_hex=sig,
            now_ms=now,
        )
        assert ok is True

    def test_verify_timing_safe(self):
        """签名比较必须用 hmac.compare_digest (timing-safe)"""
        import inspect

        src = inspect.getsource(HmacClient.verify)
        assert "compare_digest" in src


class TestHmacConfigValidation:
    """构造校验测试"""

    def test_empty_publish_id_raises(self):
        try:
            HmacClient(HmacConfig(publish_id="", publish_secret=SECRET))
        except HmacError as e:
            assert "publish_id" in str(e)
        else:
            raise AssertionError("应该抛 HmacError")

    def test_short_secret_raises(self):
        try:
            HmacClient(HmacConfig(publish_id="x", publish_secret="short"))
        except HmacError as e:
            assert "secret" in str(e).lower()
        else:
            raise AssertionError("应该抛 HmacError")


class TestIdempotencyKey:
    """new_idempotency_key 测试"""

    def test_length_is_32_hex(self):
        k = new_idempotency_key()
        assert len(k) == 32
        assert all(c in "0123456789abcdef" for c in k)

    def test_unique_per_call(self):
        keys = {new_idempotency_key() for _ in range(100)}
        assert len(keys) == 100  # 100 个全唯一
