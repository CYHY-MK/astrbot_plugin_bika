"""通用工具函数：签名、图片地址拼接、数值格式化等。"""

from __future__ import annotations

import hashlib
import hmac
import random
import re
import time
from urllib.parse import urlsplit

from .constants import (
    NONCE_ALPHABET,
    NONCE_LENGTH,
    PICA_API_KEY,
    PICA_SECRET,
)

# 部分图片 / 静态资源域名在大陆被污染，需要换成镜像线路的域名
_MIRRORABLE_HOSTS = ("picacomic.com", "manhuabika.com", "diwodiwo.xyz")


def new_nonce() -> str:
    """生成符合网页版格式的 32 位 nonce（Cloudflare 会校验该格式）。"""
    return "".join(random.choice(NONCE_ALPHABET) for _ in range(NONCE_LENGTH))


def new_time() -> str:
    """当前 Unix 时间戳（秒，字符串）。"""
    return str(int(time.time()))


def build_signature(path: str, method: str, ts: str, nonce: str) -> str:
    """计算接口签名。

    Args:
        path: 不含前导斜杠、包含查询串的路径，如 ``comics/leaderboard?tt=H24&ct=VC``
        method: HTTP 方法，如 ``GET`` / ``POST``
        ts: 秒级时间戳字符串
        nonce: 32 位随机串
    """
    raw = f"{path}{ts}{nonce}{method}{PICA_API_KEY}".lower()
    return hmac.new(
        PICA_SECRET.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_image_url(file_server: str, path: str) -> str:
    """根据 ``fileServer`` + ``path`` 拼出完整图片地址。

    网页版的拼接规则：若 fileServer 不含 ``/static``，先补一个 ``/static``。
    """
    server = (file_server or "").strip().rstrip("/")
    if not server:
        return ""
    if "/static" not in server:
        server = f"{server}/static"
    p = (path or "").lstrip("/")
    if not p:
        return server
    return f"{server}/{p}"


def host_of(url: str) -> str:
    """取 URL 的 host。"""
    try:
        return urlsplit(url).hostname or ""
    except Exception:  # pragma: no cover - 极端的非法输入
        return ""


def domain_of(base_url: str) -> str:
    """取接口域名的根域，如 ``https://picaapi.go2778.com`` -> ``go2778.com``。"""
    host = host_of(base_url)
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def to_mirror_url(url: str, mirror_domain: str) -> str:
    """把图片地址里的官方域名替换成可直连的镜像域名。

    例如 ``https://storage-b.picacomic.com/static/x.jpg``
      -> ``https://storage-b.<mirror_domain>/static/x.jpg``
    """
    if not url or not mirror_domain:
        return url
    out = url
    for host in _MIRRORABLE_HOSTS:
        out = out.replace(host, mirror_domain)
    # 镜像站可能把静态资源挂在 storage1.<domain> 下
    if "/static/static/" in out:
        out = out.replace("/static/static/", "/static/")
    return out


def human_count(num) -> str:
    """把数字格式化成 1.2K / 3.4M 之类的短形式。"""
    try:
        n = int(num or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def human_size(num_bytes: int) -> str:
    """把字节数格式化成人类可读的形式。"""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}GB"


_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_filename(name: str, fallback: str = "comic", max_len: int = 60) -> str:
    """清理文件名中的非法字符（Windows 下尤其需要）。"""
    text = (name or "").strip()
    text = _INVALID_FILENAME.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    return text[:max_len]


def clamp_text(text: str, limit: int = 120, suffix: str = "…") -> str:
    """截断过长的文本。"""
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix
