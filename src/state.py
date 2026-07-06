"""
大任务 P2-1: 状态机 State
==============================================
JSON 持久化 publisher 状态:
- next_idx:       下一章要写的编号 (从 1 开始)
- last_pushed_at: ISO 8601 时间戳
- last_pushed_idx: 最近成功推送的章节号
- last_status:    'success' / 'failed' / 'skipped'
- last_error:     最近错误信息 (failed 时记录)
- skip_next:      老板手动设置跳过下一章
- idempotency_keys: {chapter_idx: uuid} 防双发

设计要点:
- 原子写 (tmp + rename) 防止崩在半写状态
- 不存在文件 → 返回初始 state (next_idx=1)
- 失败不推进 next_idx (下次接着来)
- skip_next 用一次后自动清
- 并发保护: 文件锁 (fcntl.flock) 防止 systemd timer 重复触发时撞车

依赖: 无 (stdlib only)
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 状态文件 schema 版本 (升级时方便迁移)
SCHEMA_VERSION = 1


@dataclass
class PublishState:
    """publisher 状态对象 (单本小说)"""

    novel_id: str = "meta_realm_obsidian"
    next_idx: int = 1
    last_pushed_at: str | None = None
    last_pushed_idx: int | None = None
    last_status: str = "pending"  # pending | success | failed | skipped
    last_error: str | None = None
    skip_next: bool = False
    idempotency_keys: dict[str, str] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema_version"] = SCHEMA_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PublishState:
        """从 dict 构造, 缺失字段用默认 (前向兼容)"""
        # 过滤掉 schema 里没有的字段 (向后兼容旧版本文件)
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    # ------------------------------------------------------------------
    # 状态转移
    # ------------------------------------------------------------------
    def mark_pushed(self, idx: int, idem_key: str) -> None:
        """推送成功后调用: next_idx++, last_pushed_* 更新, idempotency 记录"""
        self.next_idx = idx + 1
        self.last_pushed_idx = idx
        self.last_pushed_at = _now_iso()
        self.last_status = "success"
        self.last_error = None
        self.idempotency_keys[str(idx)] = idem_key
        # skip_next 用一次就清 (不论成功失败, 一次性)
        if self.skip_next:
            self.skip_next = False

    def mark_failed(self, idx: int, error: str) -> None:
        """推送失败后调用: next_idx 不动, last_status=failed, last_error 记"""
        self.last_status = "failed"
        self.last_error = error[:500]  # 截断防爆
        # 失败不推进, 不清 skip_next (老板可能想跳过这章)

    def mark_skipped(self, idx: int, reason: str = "skip_next") -> None:
        """跳过本次 (老板手动设了 skip_next)"""
        self.last_status = "skipped"
        self.last_error = reason
        self.next_idx = idx + 1  # 跳过的也算"消耗"了这次
        if self.skip_next:
            self.skip_next = False


# --------------------------------------------------------------------------
# 持久化 (atomic write + 文件锁)
# --------------------------------------------------------------------------

DEFAULT_STATE_PATH = Path("data/state.json")


def _now_iso() -> str:
    """ISO 8601 UTC timestamp, 秒精度"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextlib.contextmanager
def _state_lock(path: Path) -> Iterator[Path]:
    """文件锁 (跨进程): 同一 state.json 只允许 1 个 publisher 进程"""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # 阻塞等锁
        yield path
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_state(path: Path = DEFAULT_STATE_PATH) -> PublishState:
    """加载 state.json; 不存在返回初始 state"""
    if not path.exists():
        logger.info("state 文件不存在 (%s), 返回初始 state", path)
        return PublishState()

    with _state_lock(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = PublishState.from_dict(data)
            logger.info(
                "state 加载: novel_id=%s next_idx=%d last_status=%s last_pushed_idx=%s",
                state.novel_id,
                state.next_idx,
                state.last_status,
                state.last_pushed_idx,
            )
            return state
        except json.JSONDecodeError as e:
            logger.error("state.json 损坏: %s, 备份到 .bak 后用初始 state", e)
            backup = path.with_suffix(".json.bak")
            path.rename(backup)
            return PublishState()


def save_state(state: PublishState, path: Path = DEFAULT_STATE_PATH) -> None:
    """原子写 state.json: 写 tmp + rename (崩溃半写也不会破坏原文件)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with _state_lock(path):
        tmp_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)  # 原子替换 (Path.replace)
        logger.debug("state 已写入: %s (next_idx=%d)", path, state.next_idx)


def new_idempotency_key() -> str:
    """生成 idempotency_key (UUID v4, hex 无连字符)"""
    return uuid.uuid4().hex
