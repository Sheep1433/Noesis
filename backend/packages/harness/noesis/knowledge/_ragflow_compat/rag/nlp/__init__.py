import chardet

from rag.nlp.rag_tokenizer import RagTokenizer

rag_tokenizer = RagTokenizer()

__all__ = ["rag_tokenizer", "find_codec", "concat_img"]

all_codecs = [
    "utf-8",
    "gb2312",
    "gbk",
    "utf_16",
    "ascii",
    "big5",
    "latin_1",
]


def find_codec(blob):
    detected = chardet.detect(blob[:1024])
    if detected.get("confidence", 0) > 0.5 and detected.get("encoding") == "ascii":
        return "utf-8"
    for codec in all_codecs:
        try:
            blob[:1024].decode(codec)
            return codec
        except Exception:
            pass
        try:
            blob.decode(codec)
            return codec
        except Exception:
            pass
    return "utf-8"


def concat_img(img1, img2):
    from io import BytesIO

    from PIL import Image

    from rag.utils.lazy_image import LazyImage, ensure_pil_image

    if img1 is img2:
        return img1
    if (img1 is None or isinstance(img1, LazyImage)) and (img2 is None or isinstance(img2, LazyImage)):
        if img1 and not img2:
            return img1
        if not img1 and img2:
            return img2
        if not img1 and not img2:
            return None
        return LazyImage.merge(img1, img2)

    img1 = ensure_pil_image(img1) or img1
    img2 = ensure_pil_image(img2) or img2
    if not img1:
        return img2
    if not img2:
        return img1
    if isinstance(img1, Image.Image) and isinstance(img2, Image.Image):
        width = max(img1.width, img2.width)
        height = img1.height + img2.height
        merged = Image.new("RGB", (width, height))
        merged.paste(img1, (0, 0))
        merged.paste(img2, (0, img1.height))
        return merged
    return img2
