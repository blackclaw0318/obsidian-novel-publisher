"""
Wechat Notifier — obsidian-novel-publisher 推送结果 → 老板微信审查
================================================================

设计原则 (v0.40 P1, 2026-07-10):
  - 极简: 3 行 ≤40 字/行, 老板一眼能审
  - 隔离: try/except 全包, 微信推送失败不影响 publisher 主流程
  - 幂等: 写 pending.txt + openclaw cron run + 2s 后删 (避免 schedule 兜底重推)
  - 可关: WEIXIN_NOTIFY_ENABLED=false 完全跳过 (默认 true)

推送路径:
  publisher → wechat-pending.txt → openclaw cron run <jobId>
    → OpenClaw gateway → isolated agentTurn → announce delivery fallback
    → openclaw-weixin plugin → 老板微信 (老板私聊)

依赖:
  - openclaw CLI (OpenClaw 2026.6.11+, gateway 在跑)
  - cron job `notify-publisher-wechat` (创建于 OpenClaw cron DB)
  - 微信 plugin openclaw-weixin 已 login + running

老板体验 (消息示例):
  ✅ 推送成功 · 元界 第3章
  字数 3554 · 封面 已上传
  https://www.shangkun.uk/chapters/meta-realm-ch003

  ❌ 推送失败 · 元界 第3章
  原因: LLM 调用失败 (M2.7 thinking 超时)
  查看: tail logs/publisher.log
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WechatNotifierConfig:
    """微信通知配置 (从 .env 读, 缺省走生产 cron job id)"""

    enabled: bool
    cron_job_id: str  # OpenClaw cron job id (生产: 5225d68b-8855-4c59-9a06-8d32a3f24b71)
    pending_file: Path  # 写入的 pending 文本文件 (publisher 后 2s 自动删)
    cron_run_timeout_s: float  # openclaw cron run 调用的超时

    @classmethod
    def from_env(cls) -> WechatNotifierConfig:
        return cls(
            enabled=os.environ.get("WEIXIN_NOTIFY_ENABLED", "true").lower()
            in ("true", "1", "yes"),
            cron_job_id=os.environ.get(
                "WEIXIN_CRON_JOB_ID", "5225d68b-8855-4c59-9a06-8d32a3f24b71"
            ),
            pending_file=Path(
                os.environ.get(
                    "WEIXIN_PENDING_FILE",
                    str(Path(__file__).parent.parent / "logs" / "wechat-pending.txt"),
                )
            ),
            cron_run_timeout_s=float(os.environ.get("WEIXIN_CRON_RUN_TIMEOUT_S", "15")),
        )


# --------------------------------------------------------------------------
# 消息构造 (3 行简洁格式, 老板一眼能审)
# --------------------------------------------------------------------------


def _format_success(
    *,
    novel_id: str,
    chapter_idx: int,
    title: str,
    word_count: int,
    cover_url: str,
    post_url: str,
) -> str:
    """成功消息: 3 行"""
    cover_mark = "已上传" if cover_url else "无封面"
    line1 = f"✅ 推送成功 · {novel_id} 第{chapter_idx}章"
    line2 = f"{title}"
    line3 = f"字数 {word_count} · 封面 {cover_mark}"
    line4 = post_url if post_url else "(无 URL)"
    return "\n".join([line1, line2, line3, line4])


def _format_failure(
    *,
    novel_id: str,
    chapter_idx: int,
    title: str,
    error_short: str,
) -> str:
    """失败消息: 3 行"""
    line1 = f"❌ 推送失败 · {novel_id} 第{chapter_idx}章"
    line2 = title if title else "(无标题)"
    line3 = f"原因: {error_short}"
    line4 = "查看: tail logs/publisher.log"
    return "\n".join([line1, line2, line3, line4])


# --------------------------------------------------------------------------
# 推送主函数 (写文件 + cron run + 删文件, 全部 try/except 隔离)
# --------------------------------------------------------------------------


def notify_success(
    cfg: WechatNotifierConfig,
    *,
    novel_id: str,
    chapter_idx: int,
    title: str,
    word_count: int,
    cover_url: str,
    post_url: str,
) -> bool:
    """推送成功 → 微信审查消息

    Returns: True=推送成功, False=跳过或失败 (永不抛)
    """
    if not cfg.enabled:
        logger.debug("[notify] WEIXIN_NOTIFY_ENABLED=false, 跳过")
        return False

    msg = _format_success(
        novel_id=novel_id,
        chapter_idx=chapter_idx,
        title=title,
        word_count=word_count,
        cover_url=cover_url,
        post_url=post_url,
    )
    return _send(cfg, msg)


def notify_failure(
    cfg: WechatNotifierConfig,
    *,
    novel_id: str,
    chapter_idx: int,
    title: str,
    error_short: str,
) -> bool:
    """推送失败 → 微信告警

    Returns: True=推送成功, False=跳过或失败 (永不抛)
    """
    if not cfg.enabled:
        logger.debug("[notify] WEIXIN_NOTIFY_ENABLED=false, 跳过")
        return False

    msg = _format_failure(
        novel_id=novel_id,
        chapter_idx=chapter_idx,
        title=title,
        error_short=error_short,
    )
    return _send(cfg, msg)


def _send(cfg: WechatNotifierConfig, msg: str) -> bool:
    """实际推送: 写文件 + cron run --force + 2s 后删文件"""
    try:
        # 1. 写 pending.txt (cron agent 会读这个文件)
        cfg.pending_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.pending_file.write_text(msg, encoding="utf-8")
        logger.info("[notify] pending.txt 已写: %s", cfg.pending_file)

        # 2. 强制触发 cron job (立即跑, 不等 agent 完成 — 用 sleep 等 agent 读文件)
        # race condition 修复: 不 --wait, 让 agent 异步处理
        # publisher sleep 15s 给 agent 足够时间读文件 + 处理 + runner fallback 推送
        # 然后才删文件 (避免 schedule 兜底重复推送)
        try:
            result = subprocess.run(
                ["openclaw", "cron", "run", cfg.cron_job_id],
                capture_output=True,
                text=True,
                timeout=cfg.cron_run_timeout_s,  # 15s, 只等 enqueue + CLI 返回
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "[notify] cron run CLI 超时 (%.1fs, 但 enqueue 可能成功)",
                cfg.cron_run_timeout_s,
            )
            # 即使 CLI 超时, enqueue 可能已成功, 仍 sleep 等 agent
        else:
            if result.returncode != 0:
                logger.warning(
                    "[notify] cron run 返回非零: rc=%d stderr=%s",
                    result.returncode,
                    result.stderr[:200] if result.stderr else "",
                )
                # 不返回, 仍 sleep 15s 给 agent 处理
            else:
                logger.info(
                    "[notify] cron run enqueue 成功: job=%s stdout=%s",
                    cfg.cron_job_id,
                    result.stdout[:100] if result.stdout else "",
                )

        # 3. sleep 20s 让 agent 处理 (读文件 + 输出 + runner fallback 推送)
        # 实测 isolated agentTurn 全流程需要 15-18s (系统 prompt + read + 思考 + 投递)
        # 20s 给足缓冲, 避免 race condition 导致 agent 读到空文件
        time.sleep(20)

        # 4. 删文件 (避免 schedule 兜底重复推送)
        try:
            cfg.pending_file.unlink(missing_ok=True)
            logger.info("[notify] pending.txt 已删 (避免 schedule 兜底重复推送)")
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("[notify] 删 pending.txt 失败 (无害): %s", e)

        return True

    except subprocess.TimeoutExpired:
        logger.warning("[notify] cron run 超时 (%.1fs)", cfg.cron_run_timeout_s)
        return False
    except Exception as e:
        # 推送失败绝对不能影响 publisher 主流程, 全 catch
        logger.warning("[notify] 推送失败 (无害): %s: %s", type(e).__name__, e)
        return False
