"""章节图片下载器：并发下载 + 断点续传（已存在文件跳过）。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from astrbot.api import logger

from .client import BikaClient, BikaError
from .util import human_size, to_mirror_url

ProgressCallback = Callable[[int, int, int], Awaitable[None]]


def detect_image_ext(data: bytes) -> str:
    """根据文件头判断图片扩展名。"""
    if not data:
        return ".jpg"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


@dataclass
class ChapterResult:
    """一话的下载结果。"""

    order: int
    title: str
    images: list[Path] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    total_pages: int = 0

    @property
    def size_bytes(self) -> int:
        total = 0
        for path in self.images:
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    @property
    def size_text(self) -> str:
        return human_size(self.size_bytes)


class ChapterDownloader:
    """把某一话的所有图片下载到本地目录。"""

    def __init__(
        self,
        client: BikaClient,
        concurrency: int = 5,
        url_transform: Callable[[str], str] | None = None,
    ) -> None:
        self.client = client
        self.concurrency = max(1, min(int(concurrency or 5), 16))
        if url_transform is None:
            domain = client.mirror_domain

            def url_transform(url: str) -> str:
                return to_mirror_url(url, domain) if domain else url

        self.url_transform = url_transform

    async def download(
        self,
        comic_id: str,
        order: int,
        out_dir: Path,
        title: str = "",
        progress: ProgressCallback | None = None,
        limit: int = 0,
    ) -> ChapterResult:
        """下载指定话的全部图片。

        Args:
            limit: 大于 0 时只下载前 N 张（用于试看 / 限制流量）
        """
        pages = await self.client.all_pages(comic_id, order)
        if limit > 0:
            pages = pages[:limit]
        result = ChapterResult(order=order, title=title or f"第{order}话", total_pages=len(pages))
        if not pages:
            return result

        out_dir.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(self.concurrency)
        done = 0
        failed = 0
        failed_indices: list[int] = []
        lock = asyncio.Lock()
        total = len(pages)

        async def mark_failed(index: int) -> None:
            nonlocal done, failed
            async with lock:
                failed += 1
                done += 1
                failed_indices.append(index + 1)

        async def fetch(index: int, page: dict) -> Path | None:
            nonlocal done
            url = self.client.page_image_url(page)
            if not url:
                await mark_failed(index)
                return None
            target_prefix = f"{index + 1:03d}"
            existing = next(out_dir.glob(f"{target_prefix}.*"), None)
            if existing is not None and existing.stat().st_size > 0:
                async with lock:
                    done += 1
                return existing

            final_url = self.url_transform(url)
            async with semaphore:
                try:
                    data = await self.client.download_image(final_url)
                except BikaError as exc:
                    logger.warning("下载图片失败 %s: %s", final_url, exc)
                    await mark_failed(index)
                    return None

            suffix = detect_image_ext(data)
            path = out_dir / f"{target_prefix}{suffix}"
            await asyncio.to_thread(path.write_bytes, data)
            async with lock:
                done += 1
                if progress is not None:
                    await progress(done, total, failed)
            return path

        tasks = [fetch(index, page) for index, page in enumerate(pages)]
        paths = await asyncio.gather(*tasks)
        result.images = sorted([p for p in paths if p is not None], key=lambda p: p.name)
        result.failed = [str(i) for i in sorted(failed_indices)]
        return result
