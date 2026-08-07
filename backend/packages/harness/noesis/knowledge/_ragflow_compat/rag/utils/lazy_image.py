import logging
from io import BytesIO

from PIL import Image

from rag.nlp import concat_img


class LazyImage:
    def __init__(self, blobs, source=None):
        self._blobs = [b for b in (blobs or []) if b]
        self.source = source
        self._pil = None

    def __bool__(self):
        return bool(self._blobs)

    def to_pil(self):
        if self._pil is not None:
            try:
                self._pil.load()
                return self._pil
            except Exception:
                self._pil = None
        res_img = None
        for blob in self._blobs:
            try:
                image = Image.open(BytesIO(blob)).convert("RGB")
            except Exception as exc:
                logging.info(f"LazyImage: skip bad image blob: {exc}")
                continue
            if res_img is None:
                res_img = image
                continue
            new_img = concat_img(res_img, image)
            if new_img is not res_img:
                try:
                    res_img.close()
                except Exception:
                    pass
            res_img = new_img
        self._pil = res_img
        return self._pil

    def to_pil_detached(self):
        pil = self.to_pil()
        self._pil = None
        return pil

    def close(self):
        if self._pil is not None:
            try:
                self._pil.close()
            except Exception:
                pass
            self._pil = None

    def __getattr__(self, name):
        pil = self.to_pil()
        if pil is None:
            raise AttributeError(name)
        return getattr(pil, name)

    @staticmethod
    def merge(a, b):
        a_blobs = a._blobs if isinstance(a, LazyImage) else []
        b_blobs = b._blobs if isinstance(b, LazyImage) else []
        combined = a_blobs + b_blobs
        if not combined:
            return None
        return LazyImage(combined)


LazyDocxImage = LazyImage


def ensure_pil_image(img):
    if isinstance(img, Image.Image):
        return img
    if isinstance(img, LazyImage):
        return img.to_pil()
    return None


def is_image_like(img):
    return isinstance(img, Image.Image) or isinstance(img, LazyImage)


def open_image_for_processing(img, *, allow_bytes=False):
    if isinstance(img, Image.Image):
        return img, False
    if isinstance(img, LazyImage):
        return img.to_pil_detached(), True
    if allow_bytes and isinstance(img, (bytes, bytearray)):
        try:
            pil = Image.open(BytesIO(img)).convert("RGB")
            return pil, True
        except Exception as exc:
            logging.info(f"open_image_for_processing: bad bytes: {exc}")
            return None, False
    return img, False
