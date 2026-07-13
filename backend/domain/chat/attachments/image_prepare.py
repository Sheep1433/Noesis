"""聊天附件图片：注入 LLM 前的压缩与格式规范化。"""
from __future__ import annotations

from io import BytesIO

from common.logging import logger

_GIF_MIME = "image/gif"


_PREVIEW_MAX_EDGE = 320
# 预览内容设限，避免单条消息元数据异常膨胀。
_PREVIEW_MAX_BASE64_LEN = 48_000


def build_image_preview_base64(data: bytes, mime: str = "") -> str | None:
    """生成 UI 缩略图 base64（JPEG），长度受 DB TEXT 列限制。"""
    if not data:
        return None

    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 未安装，跳过 preview_base64")
        return None

    import base64

    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)

            if img.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                converted = img.convert("RGBA")
                background.paste(converted, mask=converted.split()[-1])
                work = background
            else:
                work = img.convert("RGB")

            max_edge = _PREVIEW_MAX_EDGE
            while max_edge >= 64:
                thumb = work.copy()
                if max(thumb.size) > max_edge:
                    thumb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

                for quality in (85, 75, 65, 55, 45):
                    out = BytesIO()
                    thumb.save(out, format="JPEG", quality=quality, optimize=True)
                    encoded = base64.b64encode(out.getvalue()).decode("ascii")
                    if len(encoded) <= _PREVIEW_MAX_BASE64_LEN:
                        return encoded

                max_edge = max_edge * 3 // 4
    except Exception as exc:
        logger.warning(f"生成图片预览失败: {exc}")

    return None


def prepare_image_bytes_for_injection(
    data: bytes,
    mime: str,
    *,
    max_edge: int,
) -> tuple[bytes, str]:
    """
    将图片缩放到最长边 <= max_edge，尽量减小 multimodal 请求的 token/带宽。

    GIF 动图保留原样（仅当体积已小于阈值时）；其余统一为 JPEG（质量 85）。
    """
    if not data:
        return data, mime or "image/jpeg"

    normalized_mime = (mime or "image/jpeg").split(";")[0].strip().lower()
    if normalized_mime == _GIF_MIME:
        return data, normalized_mime

    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 未安装，跳过图片压缩")
        return data, normalized_mime

    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            if max(img.size) <= max_edge and normalized_mime in {"image/jpeg", "image/webp"}:
                return data, normalized_mime

            if img.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                converted = img.convert("RGBA")
                background.paste(converted, mask=converted.split()[-1])
                work = background
            else:
                work = img.convert("RGB")

            if max(work.size) > max_edge:
                work.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

            out = BytesIO()
            work.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Exception as exc:
        logger.warning(f"图片压缩失败，使用原图: {exc}")
        return data, normalized_mime
