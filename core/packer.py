"""打包器：ZIP / PDF / 长图 / 排行榜封面拼图，以及发送前的图片压缩。

Pillow 采用延迟导入：没装 Pillow 时插件仍能正常启动，
只是 PDF / 长图 / 拼图功能会提示不可用。

ZIP 加密依赖 pyzipper（AES），PDF 加密依赖 pypdf + cryptography；
两者都按需导入，缺失时自动退化为不加密并在消息里说明。
"""

from __future__ import annotations

import io
import os
import secrets
import zipfile
from pathlib import Path

from astrbot.api import logger

_PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _load_pil():
    try:
        from PIL import Image, ImageDraw, ImageFont

        return Image, ImageDraw, ImageFont
    except Exception:  # pragma: no cover - 依赖缺失
        return None


def pillow_available() -> bool:
    return _load_pil() is not None


def _find_font(size: int):
    """找一个能显示中文的字体，找不到就退回默认字体。"""
    _, _, ImageFont = _load_pil()
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


# ----------------------------------------------------------------------
# 加密能力探测 / 随机密码
# ----------------------------------------------------------------------
def _load_pyzipper():
    try:
        import pyzipper

        return pyzipper
    except Exception:  # pragma: no cover - 依赖缺失
        return None


def zip_encryption_available() -> bool:
    return _load_pyzipper() is not None


def pdf_encryption_available() -> bool:
    try:
        import pypdf  # noqa: F401
        from cryptography.hazmat.primitives.ciphers import Cipher  # noqa: F401

        return True
    except Exception:  # pragma: no cover - 依赖缺失
        return False


def random_password(length: int = 6) -> str:
    """生成易读的随机密码（去掉 0/1/I/L/O 等易混字符）。"""
    length = max(4, min(int(length or 6), 24))
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


# ----------------------------------------------------------------------
# ZIP
# ----------------------------------------------------------------------
def zip_images(
    paths: list[Path],
    out_path: Path,
    arc_prefix: str = "",
    password: str = "",
) -> Path:
    """把图片打成 ZIP（存储压缩）。

    Args:
        password: 非空且安装了 pyzipper 时使用 AES-256 加密。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pyzipper = _load_pyzipper() if password else None

    if pyzipper is not None:
        with pyzipper.AESZipFile(
            out_path,
            "w",
            compression=zipfile.ZIP_STORED,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(password.encode("utf-8"))
            _write_zip_entries(zf, paths, arc_prefix)
        return out_path

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED) as zf:
        _write_zip_entries(zf, paths, arc_prefix)
    return out_path


def _write_zip_entries(zf, paths: list[Path], arc_prefix: str) -> None:
    total = 0
    for path in paths:
        size = path.stat().st_size
        # ZIP32 单文件 / 总量限制
        if total + size > 3_800_000_000:
            logger.warning("ZIP 体积接近上限，剩余图片未加入：%s", path.name)
            break
        zf.write(path, arcname=f"{arc_prefix}{path.name}" if arc_prefix else path.name)
        total += size


def encrypt_pdf_file(path: Path, password: str) -> Path:
    """用 pypdf 给 PDF 加打开密码（AES-128，原地替换）。"""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    try:
        writer.encrypt(password, algorithm="AES-128")
    except TypeError:  # 老版本 pypdf 不支持 algorithm 参数
        writer.encrypt(password)
    tmp_path = path.with_suffix(".enc.pdf")
    with open(tmp_path, "wb") as fh:
        writer.write(fh)
    tmp_path.replace(path)
    return path


# ----------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------
def pdf_from_images(
    paths: list[Path], out_path: Path, max_side: int = 1600, password: str = ""
) -> Path:
    """把图片合成为单个 PDF（最多 30 页，便于测试与小批量使用）。"""
    out = _pdf_chunk(list(paths[:30]), out_path, max_side)
    if password and pdf_encryption_available():
        encrypt_pdf_file(out, password)
    return out


def pdf_from_images_parts(
    paths: list[Path],
    out_dir: Path,
    stem: str,
    max_side: int = 1200,
    chunk_size: int = 30,
    password: str = "",
) -> list[Path]:
    """分卷生成 PDF，避免几百页一次性载入内存导致 OOM。

    Args:
        password: 非空且 pypdf/cryptography 可用时，给每个 PDF 加打开密码。

    Returns:
        生成好的 PDF 路径列表（按页序）
    """
    if not paths:
        raise RuntimeError("没有可写入 PDF 的图片")
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_size = max(1, chunk_size)
    total = (len(paths) + chunk_size - 1) // chunk_size
    outputs: list[Path] = []
    for index in range(total):
        chunk = paths[index * chunk_size : (index + 1) * chunk_size]
        suffix = f"_{index + 1}" if total > 1 else ""
        out_path = out_dir / f"{stem}{suffix}.pdf"
        _pdf_chunk(chunk, out_path, max_side)
        if password and pdf_encryption_available():
            encrypt_pdf_file(out_path, password)
        outputs.append(out_path)
    return outputs


def _pdf_chunk(paths: list[Path], out_path: Path, max_side: int) -> Path:
    pil = _load_pil()
    if pil is None:
        raise RuntimeError("未安装 Pillow，无法生成 PDF")
    Image, _, _ = pil

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pages = []
    try:
        for path in paths:
            with Image.open(path) as img:
                img = img.convert("RGB")
                if max(img.size) > max_side:
                    ratio = max_side / max(img.size)
                    new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
                    img = img.resize(new_size, Image.LANCZOS)
                pages.append(img.copy())
        if not pages:
            raise RuntimeError("没有可写入 PDF 的图片")
        first, rest = pages[0], pages[1:]
        first.save(
            out_path,
            "PDF",
            save_all=True,
            append_images=rest,
            resolution=100.0,
            quality=85,
        )
    finally:
        for page in pages:
            try:
                page.close()
            except Exception:
                pass
    return out_path


# ----------------------------------------------------------------------
# 长图
# ----------------------------------------------------------------------
def long_image_from_images(
    paths: list[Path],
    out_dir: Path,
    stem: str,
    width: int = 900,
    max_height: int = 12000,
) -> list[Path]:
    """把图片纵向拼接成长图，过高时自动切成多张。"""
    pil = _load_pil()
    if pil is None:
        raise RuntimeError("未安装 Pillow，无法生成长图")
    Image, _, _ = pil

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    chunk: list = []
    chunk_height = 0
    index = 0

    def flush() -> None:
        nonlocal chunk, chunk_height, index
        if not chunk:
            return
        canvas = Image.new("RGB", (width, chunk_height), "white")
        y = 0
        for img in chunk:
            canvas.paste(img, ((width - img.width) // 2, y))
            y += img.height
        index += 1
        out_path = out_dir / f"{stem}_long_{index}.jpg"
        canvas.save(out_path, "JPEG", quality=88)
        canvas.close()
        for img in chunk:
            img.close()
        outputs.append(out_path)
        chunk = []
        chunk_height = 0

    try:
        for path in paths:
            with Image.open(path) as raw:
                img = raw.convert("RGB")
                if img.width != width:
                    ratio = width / img.width
                    img = img.resize((width, max(1, int(img.height * ratio))), Image.LANCZOS)
                if img.height > max_height:
                    # 单页都超高，先分片
                    top = 0
                    while top < img.height:
                        piece = img.crop((0, top, img.width, min(top + max_height, img.height)))
                        flush()
                        chunk.append(piece.copy())
                        chunk_height = piece.height
                        flush()
                        top += max_height
                    img.close()
                    continue
                if chunk_height + img.height > max_height:
                    flush()
                chunk.append(img.copy())
                chunk_height += img.height
                img.close()
        flush()
    finally:
        pass
    return outputs


# ----------------------------------------------------------------------
# 排行榜封面拼图
# ----------------------------------------------------------------------
def rank_collage(
    covers: list[bytes],
    out_path: Path,
    cols: int = 5,
    cell_w: int = 220,
    cell_h: int = 293,
    padding: int = 6,
) -> Path:
    """把排行榜前 N 名的封面拼成一张图，并标上名次。"""
    pil = _load_pil()
    if pil is None:
        raise RuntimeError("未安装 Pillow，无法生成封面拼图")
    Image, ImageDraw, _ = pil

    covers = [c for c in covers if c]
    if not covers:
        raise RuntimeError("没有可用的封面")

    rows = (len(covers) + cols - 1) // cols
    width = cols * (cell_w + padding) + padding
    height = rows * (cell_h + padding) + padding
    canvas = Image.new("RGB", (width, height), (24, 24, 28))
    draw = ImageDraw.Draw(canvas)
    font = _find_font(26)

    for i, data in enumerate(covers):
        row, col = divmod(i, cols)
        x = padding + col * (cell_w + padding)
        y = padding + row * (cell_h + padding)
        try:
            with Image.open(io.BytesIO(data)) as img:
                img = img.convert("RGB")
                ratio = max(cell_w / img.width, cell_h / img.height)
                new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
                img = img.resize(new_size, Image.LANCZOS)
                left = (img.width - cell_w) // 2
                top = (img.height - cell_h) // 2
                img = img.crop((left, top, left + cell_w, top + cell_h))
                canvas.paste(img, (x, y))
        except Exception as exc:  # pragma: no cover
            logger.warning("封面拼图失败（第 %s 张）：%s", i + 1, exc)

        label = str(i + 1)
        box_w = 20 if len(label) == 1 else 30
        draw.rectangle([x, y, x + box_w + 10, y + 34], fill=(237, 151, 183))
        draw.text((x + 7, y + 4), label, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=88)
    canvas.close()
    return out_path


# ----------------------------------------------------------------------
# 发送前压缩
# ----------------------------------------------------------------------
def prepare_for_send(
    path: Path,
    max_bytes: int = 5 * 1024 * 1024,
    max_side: int = 2400,
) -> Path:
    """图片过大时压缩成临时 JPEG，避免聊天平台拒收或超时。"""
    try:
        size = path.stat().st_size
    except OSError:
        return path

    pil = _load_pil()
    if size <= max_bytes and pil is None:
        return path
    if pil is None:
        return path

    Image, _, _ = pil
    try:
        with Image.open(path) as img:
            need_resize = max(img.size) > max_side
            if not need_resize and size <= max_bytes:
                return path
            img = img.convert("RGB")
            if need_resize:
                ratio = max_side / max(img.size)
                img = img.resize(
                    (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
                    Image.LANCZOS,
                )
            quality = 85
            while quality >= 60:
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=quality, optimize=True)
                data = buf.getvalue()
                if len(data) <= max_bytes:
                    break
                quality -= 8
            out_path = path.with_suffix(".send.jpg")
            out_path.write_bytes(data)
            return out_path
    except Exception as exc:  # pragma: no cover
        logger.warning("压缩图片失败 %s: %s", path.name, exc)
        return path
