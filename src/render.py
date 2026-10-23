import cv2
import numpy as np
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont


@lru_cache(maxsize=8)
def _load_font(font_size):
    for font_path in ["C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/tahomabd.ttf"]:
        try:
            return ImageFont.truetype(font_path, font_size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text_on_cv_img(img_cv, text, pos, font_size=24, text_color=(255, 255, 255), bg_color=(0, 180, 0), bg_alpha=1.0):
    x, y = pos
    img_pil = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)

    font = _load_font(font_size)

    bbox = draw.textbbox((x, y), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    padding = 4
    bg_box = [x, max(0, y - h - padding * 2), x + w + padding * 2, max(h + padding * 2, y)]
    if bg_alpha >= 1.0:
        draw.rectangle(bg_box, fill=bg_color)
    else:
        overlay = Image.new("RGBA", img_pil.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(bg_box, fill=(*bg_color, int(255 * bg_alpha)))
        img_pil = Image.alpha_composite(img_pil.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img_pil)
    draw.text((x + padding, max(0, y - h - padding)), text, font=font, fill=text_color)

    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)