"""Ảnh bìa: thu về 400x600 webp; vẽ bìa tạm (nền chuyển màu + tên truyện)."""
import io
import secrets
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import settings

COVER_SIZE = (400, 600)
MAX_UPLOAD = 8 * 1024 * 1024

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
]


def save_cover(data: bytes, story_id: int) -> str:
    """Lưu ảnh bìa, trả tên file trong data/covers. Lỗi ảnh → ValueError."""
    if len(data) > MAX_UPLOAD:
        raise ValueError("Ảnh quá lớn (tối đa 8 MB)")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("File không phải ảnh hợp lệ")
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, COVER_SIZE, Image.Resampling.LANCZOS, centering=(0.5, 0.4))
    name = f"{story_id}-{secrets.token_hex(4)}.webp"
    img.save(settings.data_dir / "covers" / name, "WEBP", quality=84, method=5)
    return name


def save_avatar(data: bytes, user_id: int) -> str:
    if len(data) > MAX_UPLOAD:
        raise ValueError("Ảnh quá lớn (tối đa 8 MB)")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("File không phải ảnh hợp lệ")
    img = ImageOps.fit(ImageOps.exif_transpose(img).convert("RGB"), (160, 160), Image.Resampling.LANCZOS)
    name = f"{user_id}-{secrets.token_hex(4)}.webp"
    img.save(settings.data_dir / "avatars" / name, "WEBP", quality=85)
    return name


def remove_file(sub: str, name: str | None):
    if name:
        try:
            (settings.data_dir / sub / Path(name).name).unlink(missing_ok=True)
        except OSError:
            pass


def _font(size):
    for f in FONT_CANDIDATES:
        if Path(f).is_file():
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


PALETTES = [
    ((36, 59, 85), (20, 30, 48)), ((131, 58, 180), (40, 20, 80)), ((194, 65, 12), (80, 20, 10)),
    ((15, 118, 110), (8, 47, 73)), ((190, 24, 93), (60, 10, 40)), ((71, 85, 105), (15, 23, 42)),
    ((161, 98, 7), (66, 32, 6)), ((30, 64, 175), (23, 23, 70)),
]


def placeholder_cover(title: str, author: str, story_id: int, seed: int = 0) -> str:
    """Vẽ bìa tạm cho dữ liệu mẫu, trả tên file."""
    w, h = COVER_SIZE
    top, bottom = PALETTES[seed % len(PALETTES)]
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        px.line([(0, y), (w, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    # vòng tròn trang trí
    over = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    od.ellipse((w * 0.35, -h * 0.12, w * 1.3, h * 0.5), fill=(255, 255, 255, 28))
    od.ellipse((-w * 0.4, h * 0.62, w * 0.55, h * 1.25), fill=(255, 255, 255, 18))
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    d = ImageDraw.Draw(img)
    font = _font(44)
    lines = _wrap(d, title.upper(), font, w - 60)[:5]
    y = h * 0.42 - len(lines) * 26
    for ln in lines:
        tw = d.textlength(ln, font=font)
        d.text(((w - tw) / 2 + 2, y + 2), ln, font=font, fill=(0, 0, 0))
        d.text(((w - tw) / 2, y), ln, font=font, fill=(255, 250, 240))
        y += 54
    d.line([(w / 2 - 40, y + 14), (w / 2 + 40, y + 14)], fill=(255, 220, 160), width=3)
    small = _font(22)
    tw = d.textlength(author, font=small)
    d.text(((w - tw) / 2, y + 32), author, font=small, fill=(255, 235, 210))
    name = f"{story_id}-seed.webp"
    img.save(settings.data_dir / "covers" / name, "WEBP", quality=85)
    return name
