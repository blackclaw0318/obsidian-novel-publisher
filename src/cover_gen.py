"""
大任务 D-1: 封面生成器 CoverGenerator
====================================
基于 minimax image-01 生成 3:4 封面图。

输出契约:
- 文件路径: data/covers/{idx:03d}.jpg  (相对工作区)
- 尺寸: 3:4 (1080×1440 等比, 由 API 决定)
- 大小: > 50KB (有效图)
- 内容: 由 prompt 决定, prompt 严禁含真人/IP/品牌

设计要点:
- minimax 实测响应字段: {"data": {"image_urls": [url]}} (不是 data.url)
- prompt_optimizer=true 提升质量
- 失败重试: 网络层 3 次 + 内容层 1 次 (size < 50KB 视为失败)
"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class CoverGenError(Exception):
    """封面生成失败 (API 错误 / 鉴权 / 图无效)"""


class CoverGenerator:
    """v0.2 minimax image-01 封面生成器

    环境变量 (从 .env 读):
        MINIMAXI_API_KEY      - 文本/图像 API 通用 key
        MINIMAXI_BASE_URL     - 默认 https://api.minimaxi.com/v1
        MINIMAXI_IMAGE_MODEL  - 默认 image-01
        OUTPUT_DIR            - 默认 data/covers (相对工作区)
    """

    ASPECT = "3:4"
    MIN_SIZE_BYTES = 50_000  # 50KB, 小于此视为无效图
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # 秒
    # 7-10: image API 仍走 OpenAI 兼容 /v1, 跟 text API (anthropic 兼容) 分开
    # 不能用 MINIMAXI_BASE_URL (text 端点), 否则 text=anthropic 串到 image
    DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        output_dir: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("MINIMAXI_API_KEY")
        # 7-10: 显式走 image OpenAI 兼容端点, 不读 MINIMAXI_BASE_URL (那是 text 端点)
        self.base_url = (
            base_url
            or os.environ.get("MINIMAXI_IMAGE_BASE_URL", self.DEFAULT_BASE_URL)
        ).rstrip("/")
        self.model = model or os.environ.get("MINIMAXI_IMAGE_MODEL", "image-01")
        self.output_dir = pathlib.Path(output_dir or os.environ.get("OUTPUT_DIR", "data/covers"))

        if not self.api_key:
            raise CoverGenError("MINIMAXI_API_KEY 未配置, 请检查 .env (凭据安全协议: 值不入 git)")

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        prompt: str,
        chapter_idx: int,
        *,
        subject_reference_url: str | None = None,
    ) -> str:
        """生成封面, 返回本地绝对路径。

        Args:
            prompt: 封面描述 (英文, 已套模板; 不含真人/IP/品牌)
            chapter_idx: 章节序号 (1-based), 用作文件名
            subject_reference_url: minimax image-to-image 参考图 URL (ch-2+ 用)
                - 必须是 minimax 服务端能访问的公网 URL (e.g. https://www.shangkun.uk/uploads/abc.jpg)
                - None / 空 / localhost 退化: 走纯 text-to-image (ch-1 默认路径)
                - 7-8 P2.5 老板拍: 拿上一章封面公网 URL 作 subject_reference, 锁角色

        Returns:
            本地绝对路径 (str), 例如 /workspace/data/covers/001.jpg

        Raises:
            CoverGenError: API 调用失败 / 重试耗尽 / 返回图无效
        """
        last_err = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.info(f"[CoverGen] 第 {attempt}/{self.MAX_RETRIES} 次: idx={chapter_idx}")
                url = self._call_image_api(prompt, subject_reference_url=subject_reference_url)
                out_path = self._download(url, chapter_idx)
                if self._validate(out_path):
                    logger.info(f"[CoverGen] ✅ 成功: {out_path}")
                    return str(out_path)
                # 图无效 (太小或 PIL 校验失败), 触发下一次重试
                # 7-7 fix: 区分错误信息, 不再误导说 "size < 50000"
                actual_size = out_path.stat().st_size if out_path.exists() else 0
                if actual_size < self.MIN_SIZE_BYTES:
                    raise CoverGenError(
                        f"封面图过小 (size={actual_size} < {self.MIN_SIZE_BYTES}), 视为无效"
                    )
                else:
                    raise CoverGenError(f"封面图 PIL 校验失败 (size={actual_size}), 视为无效")
            except Exception as e:
                last_err = e
                # 清除上一轮残文件
                with contextlib.suppress(Exception):
                    (self.output_dir / f"{chapter_idx:03d}.jpg").unlink(missing_ok=True)
                logger.warning(f"[CoverGen] 第 {attempt} 次失败: {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY * attempt)

        raise CoverGenError(f"封面生成失败 (重试 {self.MAX_RETRIES} 次): {last_err}")

    def _call_image_api(self, prompt: str, *, subject_reference_url: str | None = None) -> str:
        """调 minimax /image_generation, 返回图片 URL。

        v0.5 fix: timeout 60s → 120s (image-01 实测 70-80s, 60s timeout 频繁误杀)
        v0.3.2 P2.5: 老板拍, ch-2+ 加 subject_reference (image-to-image 锁角色)
        """
        url = f"{self.base_url}/image_generation"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "prompt": prompt,
            "aspect_ratio": self.ASPECT,
            "response_format": "url",
            "n": 1,
            "prompt_optimizer": True,
        }
        # 7-8 P2.5: 老板拍, image-to-image 锁角色
        # minimax 官方支持: subject_reference = [{"type": "character", "image_file": "<公网 URL>"}]
        # 当前 1 张/次限制, 我们只需锁主角 1 人
        if subject_reference_url and self._is_minimax_accessible_url(subject_reference_url):
            payload["subject_reference"] = [
                {
                    "type": "character",
                    "image_file": subject_reference_url,
                }
            ]
            logger.info(
                "[CoverGen] 启用 image-to-image, subject_reference: %s",
                subject_reference_url[:60] + "...",
            )
        elif subject_reference_url:
            logger.warning(
                "[CoverGen] subject_reference_url 是 %s (minimax 不可访问), 退化 text-to-image",
                subject_reference_url[:60],
            )

        resp = requests.post(url, headers=headers, json=payload, timeout=(10, 120))
        resp.raise_for_status()
        body = resp.json()

        # 实测响应: {"data": {"image_urls": [url]}}
        try:
            image_url = body["data"]["image_urls"][0]
        except (KeyError, IndexError, TypeError) as e:
            raise CoverGenError(f"响应字段异常: {body}") from e
        return image_url

    @staticmethod
    def _is_minimax_accessible_url(url: str) -> bool:
        """判断 URL minimax 服务端能否访问

        必须 http(s) 且不是 localhost / 127.0.0.1 / 0.0.0.0 / 内网 IP
        """
        if not url:
            return False
        u = url.lower()
        if not (u.startswith("http://") or u.startswith("https://")):
            return False
        # 黑名单: 本地/内网 (minimax 是公网, 访问不到)
        for prefix in ("http://localhost", "http://127.0.0.1", "http://0.0.0.0", "http://192.168.", "http://10."):
            if u.startswith(prefix):
                return False
        return True

    def _download(self, url: str, chapter_idx: int) -> pathlib.Path:
        """下载远程图到本地。"""
        out_path = self.output_dir / f"{chapter_idx:03d}.jpg"
        resp = requests.get(url, timeout=(10, 30))
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        return out_path

    def _validate(self, path: pathlib.Path) -> bool:
        """校验图有效性 (存在 + size > 阈值 + PIL 能打开)。"""
        if not path.exists():
            return False
        sz = path.stat().st_size
        if sz < self.MIN_SIZE_BYTES:
            # 7-7 debug: 打印实际文件大小 (排查"size < 50000" 误杀)
            logger.warning(f"[CoverGen] _validate 失败: {path} size={sz} < {self.MIN_SIZE_BYTES}")
            return False
        try:
            from PIL import Image

            with Image.open(path) as img:
                img.verify()  # 校验格式
            return True
        except Exception as e:
            logger.warning(f"[CoverGen] _validate PIL 失败: {path} err={e}")
            return False
