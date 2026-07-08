"""
大任务 P2-2: HMAC 签名客户端
==============================================
对每次 POST 请求生成 HMAC-SHA256 签名 over (timestamp + canonical_body)

签名协议 (与 obsidian-journal P4 接收侧契约):
1. canonical_body = JSON.stringify(body, sort_keys recursively, no whitespace)
2. timestamp = unix milliseconds (int)
3. message = f"{timestamp}.{canonical_body}"
4. signature = HMAC-SHA256(secret, message).hexdigest()
5. Headers:
   - X-Publisher-Id:        <publish_id>             (e.g. 'novel-publisher')
   - X-Publisher-Signature: <signature hex>
   - X-Publisher-Timestamp: <timestamp str>
   - X-Idempotency-Key:     <uuid hex>               (防双发)
   - Content-Type:          application/json

为什么这套:
- ✅ timestamp ±5min 防 replay attack
- ✅ 完整 body 签名防篡改
- ✅ sort_keys 防字典序攻击 (Python 默认 dict 保序但反序列化时不保证)
- ✅ timing-safe 比较 (服务端验签用)
- ✅ idempotency_key 防网络重试导致双发
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

# 协议常量
TIMESTAMP_WINDOW_MS = 5 * 60 * 1000  # ±5 分钟 (老板本机时钟漂移 + 重放窗口平衡)
SIGNATURE_ALGO = "sha256"


@dataclass(frozen=True)
class HmacConfig:
    """HMAC 客户端配置"""

    publish_id: str  # 'novel-publisher'
    publish_secret: str  # hex 字符串, 推荐 64 chars (openssl rand -hex 32)
    timestamp_window_ms: int = TIMESTAMP_WINDOW_MS


class HmacError(Exception):
    """HMAC 签名 / 验签失败"""


def canonical_body(body: dict[str, Any]) -> str:
    """序列化 body: sort_keys recursively + ensure_ascii=False + 无空白

    为什么 sort_keys: Python dict 本身保序, 但跨语言/网络层反序列化后不保证。
    签名基于规范化字符串, 避免字典序差异导致签名失败。
    """
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_signature(secret: str, timestamp_ms: int, body: dict[str, Any] | None = None, *, body_bytes: str | None = None) -> str:
    """计算 HMAC-SHA256 hexdigest

    二选一: body (dict, 会 canonical 化) 或 body_bytes (str, 原样签名)。
    生产请用 body_bytes (与 HTTP 实际发送字节一致, 与 obsidian-journal 服务端 rawBody 验签契约对齐)。
    """
    if body_bytes is None:
        if body is None:
            raise HmacError("compute_signature 需传 body 或 body_bytes")
        body_bytes = canonical_body(body)
    message = f"{timestamp_ms}.{body_bytes}".encode()
    sig = hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return sig


def new_idempotency_key() -> str:
    """UUID v4 hex (无连字符) — 用于 X-Idempotency-Key header"""
    return uuid.uuid4().hex


class HmacClient:
    """HMAC 签名生成器 (publisher 侧)"""

    def __init__(self, config: HmacConfig):
        if not config.publish_id:
            raise HmacError("publish_id 不能为空")
        if not config.publish_secret or len(config.publish_secret) < 32:
            raise HmacError(
                f"publish_secret 太短 ({len(config.publish_secret)} 字符), 建议 ≥ 32 字符 hex"
            )
        self.config = config

    def sign(
        self,
        body: dict[str, Any],
        *,
        timestamp_ms: int | None = None,
        idempotency_key: str | None = None,
        raw_body: str | None = None,
    ) -> dict[str, str]:
        """生成签名 headers 字典

        Args:
            body:                  要发送的请求体 (dict)
            timestamp_ms:          可选, 不传则用当前时间 (毫秒)
            idempotency_key:       可选, 不传则生成新的 UUID v4 hex
            raw_body:              实际发到服务器的字节串 (str); 需与 requests.post(data=raw_body) 完全一致。
                                   不传则自动用 json.dumps(body) (即 requests json= 的默认序列化)。
                                   **与 obsidian-journal 服务端契约**: 服务端在 rawBody 上验签,
                                   所以签名时必须用真实 HTTP body 字节, 不能用 sort_keys/无空白 canonical。

        Returns:
            headers dict (X-Publisher-Id, X-Publisher-Signature, X-Publisher-Timestamp, X-Idempotency-Key)
        """
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        body_bytes = raw_body if raw_body is not None else json.dumps(body, ensure_ascii=False)
        sig = compute_signature(self.config.publish_secret, ts, body_bytes=body_bytes)
        idem = idempotency_key or new_idempotency_key()

        return {
            "X-Publisher-Id": self.config.publish_id,
            "X-Publisher-Signature": sig,
            "X-Publisher-Timestamp": str(ts),
            "X-Idempotency-Key": idem,
        }

    # ------------------------------------------------------------------
    # 服务端验证 (与 obsidian-journal 接收侧复用同一逻辑)
    # ------------------------------------------------------------------
    @staticmethod
    def verify(
        secret: str,
        body: dict[str, Any],
        timestamp_ms: int,
        signature_hex: str,
        *,
        now_ms: int | None = None,
        window_ms: int = TIMESTAMP_WINDOW_MS,
    ) -> tuple[bool, str]:
        """服务端验签: timing-safe + timestamp window check

        Returns:
            (ok, reason) — reason 仅在 ok=False 时有意义
        """
        # 1. 时间戳窗口检查 (防 replay)
        now = now_ms if now_ms is not None else _now_ms()
        drift = abs(now - timestamp_ms)
        if drift > window_ms:
            return False, f"timestamp drift {drift}ms 超 ±{window_ms}ms 窗口"

        # 2. 签名校验 (timing-safe)
        expected = compute_signature(secret, timestamp_ms, body)
        if not hmac.compare_digest(expected, signature_hex):
            return False, "signature mismatch"

        return True, "ok"


def _now_ms() -> int:
    """当前 Unix 毫秒时间戳"""
    return int(time.time() * 1000)
