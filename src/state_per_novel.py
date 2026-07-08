"""
state_per_novel — 按 novel_id 拆分 state (v0.3.2 P0)
=====================================================
- 老 state.py 是单文件 (data/state.json), v0.2 单本
- v0.3.2 多本并行, 拆分为 data/state/{novel_id}.json
- 兼容老路径 (data/state.json), 启动时检测到单文件时自动迁移

设计:
- DEFAULT_STATE_DIR = data/state/ (per-novel)
- FALLBACK_OLD_STATE = data/state.json (v0.2 单文件, 启动时检测)
- load_state_for_novel(novel_id) → PublishState
- save_state_for_novel(state, novel_id)
- list_known_novels() → 从 data/state/*.json 文件名反推

依赖: 复用 src/state.py 的 PublishState / atomic write / file lock
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .state import PublishState, _state_lock, save_state as _save_state_single

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================

DEFAULT_STATE_DIR = Path("data/state")
FALLBACK_OLD_STATE = Path("data/state.json")

# novel_id 校验 (与 novel_registry.Novel.id 一致: ^[a-z0-9_]+$)
_NOVEL_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _validate_novel_id(novel_id: str) -> str:
    """novel_id 必须是 [a-z0-9_]+, 防路径穿越"""
    if not _NOVEL_ID_RE.match(novel_id):
        raise ValueError(
            f"novel_id 非法 (必须 ^[a-z0-9_]+$): {novel_id!r} "
            f"(可能是路径穿越攻击)"
        )
    return novel_id


def _per_novel_path(novel_id: str) -> Path:
    """data/state/{novel_id}.json"""
    _validate_novel_id(novel_id)
    return DEFAULT_STATE_DIR / f"{novel_id}.json"


# ============================================================
# 兼容老 v0.2 state.json
# ============================================================


@contextmanager
def _migration_log(do_migration: bool) -> Iterator[None]:
    """迁移期间打 log"""
    if do_migration:
        logger.warning(
            "⚠️  检测到 v0.2 老 state.json (%s), 自动迁移到 %s/",
            FALLBACK_OLD_STATE,
            DEFAULT_STATE_DIR,
        )
    yield


def _migrate_old_state_if_needed() -> bool:
    """如果 data/state.json 存在, 迁移到 data/state/{novel_id}.json

    Returns:
        True if migration performed, False otherwise
    """
    if not FALLBACK_OLD_STATE.exists():
        return False

    DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        old_data = json.loads(FALLBACK_OLD_STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error("老 state.json 损坏: %s, 跳过迁移 (用初始 state)", e)
        return False

    novel_id = old_data.get("novel_id", "meta_realm_obsidian")
    target = _per_novel_path(novel_id)
    if target.exists():
        logger.warning(
            "目标 %s 已存在, 跳过迁移 (避免覆盖), 老文件保留作 backup",
            target,
        )
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(old_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "✅ 迁移完成: %s → %s (next_idx=%d, last_pushed_idx=%s)",
        FALLBACK_OLD_STATE,
        target,
        old_data.get("next_idx", 1),
        old_data.get("last_pushed_idx"),
    )
    return True


# ============================================================
# 加载 / 保存
# ============================================================


def load_state_for_novel(
    novel_id: str, *, auto_migrate: bool = True
) -> PublishState:
    """加载某本小说的 state

    Args:
        novel_id: 小说 id (与 novels.yaml 中 id 一致)
        auto_migrate: 启动时是否自动迁移 v0.2 老 state.json (默认 True)

    Returns:
        PublishState (不存在时返回初始 state, novel_id 字段已设置)
    """
    _validate_novel_id(novel_id)

    if auto_migrate:
        _migrate_old_state_if_needed()

    path = _per_novel_path(novel_id)
    if not path.exists():
        logger.info("state 文件不存在 (%s), 返回初始 state", path)
        return PublishState(novel_id=novel_id)

    with _state_lock(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = PublishState.from_dict(data)
            # 兜底: 如果文件里 novel_id 与查询的 novel_id 不一致, 强制覆盖
            if state.novel_id != novel_id:
                logger.warning(
                    "state.novel_id=%s 与查询 novel_id=%s 不一致, 强制覆盖",
                    state.novel_id,
                    novel_id,
                )
                state.novel_id = novel_id
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
            return PublishState(novel_id=novel_id)


def save_state_for_novel(state: PublishState, novel_id: str | None = None) -> None:
    """保存某本小说的 state

    Args:
        state: PublishState 实例
        novel_id: 覆盖 state.novel_id (默认用 state.novel_id)
    """
    nid = novel_id or state.novel_id
    _validate_novel_id(nid)
    state.novel_id = nid
    path = _per_novel_path(nid)
    _save_state_single(state, path)


# ============================================================
# 列表 + 维护
# ============================================================


def list_known_novels() -> list[str]:
    """列 data/state/ 下所有 novel_id (从文件名反推)

    注意: 不与 novels.yaml 强一致 (yaml 没启用但 state 已存在的也列出来)
    """
    if not DEFAULT_STATE_DIR.exists():
        return []
    return sorted(
        p.stem
        for p in DEFAULT_STATE_DIR.glob("*.json")
        if p.is_file() and not p.name.startswith(".")
    )


def state_path_for(novel_id: str) -> Path:
    """返回某本 state 的路径 (供 publisher._post_with_sig 等模块用, 不实际读)"""
    return _per_novel_path(novel_id)


# ============================================================
# CLI 入口
# ============================================================


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        novels = list_known_novels()
        if not novels:
            print("📭 data/state/ 下无 state 文件 (运行一次 publisher 会自动创建)")
        else:
            print(f"📚 已知 novel state ({len(novels)} 本):")
            for nid in novels:
                state = load_state_for_novel(nid, auto_migrate=False)
                print(
                    f"  - {nid}: next_idx={state.next_idx} "
                    f"last_pushed_idx={state.last_pushed_idx} "
                    f"status={state.last_status}"
                )
    elif cmd == "show":
        if len(sys.argv) < 3:
            print("usage: python -m src.state_per_novel show <novel_id>", file=sys.stderr)
            sys.exit(1)
        nid = sys.argv[2]
        state = load_state_for_novel(nid)
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd}", file=sys.stderr)
        sys.exit(2)
