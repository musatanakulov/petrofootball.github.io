"""Сжатие картинок: уменьшение размера и перекодирование в WebP.

Логотипы и фото показываются маленькими (22–110 px), поэтому оригиналы в мегабайтах не нужны.
WebP поддерживает прозрачность, весит в разы меньше PNG/JPEG и открывается во всех браузерах и в Telegram.
"""
import io

from PIL import Image, ImageOps

# Крупные фото с телефона/камеры допустимы, «бомбы» (гигантские картинки для зависания сервера) — нет
Image.MAX_IMAGE_PIXELS = 150_000_000

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # больше — не принимаем
LOGO_MAX_SIDE = 256                  # логотипы показываются до ~52 px (с запасом на ретина-экраны)
PHOTO_MAX_SIDE = 400                 # фото игроков показываются до ~110 px
WEBP_QUALITY = 82


class NotAnImage(ValueError):
    """Файл не удалось открыть как картинку."""


def optimize_image(data: bytes, max_side: int, quality: int = WEBP_QUALITY) -> bytes:
    """Возвращает WebP-байты: картинка уменьшена так, чтобы большая сторона была не больше max_side
    (маленькие не увеличиваются), повёрнута по EXIF, прозрачность сохранена."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise NotAnImage("не удалось прочитать файл как картинку") from None
    img = ImageOps.exif_transpose(img)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=quality, method=6)
    return out.getvalue()
