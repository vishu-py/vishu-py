#!/usr/bin/env python3
"""Turn the source photo into the portrait layers used by the hero, and the avatar images.

Run this locally once, whenever the photo changes. It is not part of the GitHub Action.
Needs Pillow, numpy, opencv-python and pymatting (and rembg if source/mask.png is missing):

    python scripts/portrait.py source/photo.jpg

Writes:
    data/portrait.json            embedded layers + boxes that scripts/build.py animates
    profile-picture/*.png|jpg     avatar versions to upload as the GitHub profile picture
"""
import base64
import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT_PICS = ROOT / "profile-picture"
VIOLET = np.array([167, 139, 250]) / 255
CYAN = np.array([34, 211, 238]) / 255
PINK = np.array([244, 114, 182]) / 255


# ---------------------------------------------------------------- matte

def load_mask(photo_path, size):
    mask_path = Path(photo_path).with_name("mask.png")
    if not mask_path.exists():
        from rembg import new_session, remove      # only needed the first time
        mask = remove(Image.open(photo_path).convert("RGB"), session=new_session("u2net_human_seg"),
                      only_mask=True)
        mask.save(mask_path)
    mask = Image.open(mask_path).convert("L").resize(size, Image.LANCZOS)
    return np.asarray(mask, dtype=np.float64) / 255


def clean_alpha(alpha):
    """Drop stray islands, keep the soft hair edge."""
    solid = (alpha > 0.5).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(solid, 8)
    if count > 1:
        keep = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        body = cv2.dilate((labels == keep).astype(np.uint8), np.ones((25, 25), np.uint8))
        alpha = alpha * body
    alpha[alpha < 0.03] = 0
    return np.clip(alpha, 0, 1)


def key_out_grass(img, alpha):
    """The matting model keeps lawn that shows between hair strands. Nothing on the person is
    green, so a green-dominance key removes it and leaves mixed hair/lawn pixels semi-transparent."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    green = (g - np.maximum(r, b)) / (g + 0.05)      # relative, so shaded lawn counts too
    keep = 1 - np.clip((green - 0.03) / 0.09, 0, 1)
    keep = cv2.GaussianBlur(keep, (0, 0), 0.8)
    alpha = alpha * keep
    # brick wall above the head: orange, brighter than hair, and only near the top edge of the matte
    top = np.zeros_like(alpha, dtype=bool)
    cols = np.where((alpha > 0.5).any(0))[0]
    for x in cols:
        y = np.argmax(alpha[:, x] > 0.5)
        top[max(0, y - 6):y + 14, x] = True
    brick = top & (r > g * 1.25) & (r > 0.32)
    alpha[brick] *= 0.15
    # keep only what hangs off the solid body: ragged lawn fragments drift away from it
    core = cv2.morphologyEx((alpha > 0.6).astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    near = cv2.GaussianBlur(cv2.dilate(core, np.ones((7, 7), np.uint8)).astype(np.float64), (0, 0), 2.5)
    return clean_alpha(alpha * np.clip(near * 1.2, 0, 1))


def decontaminate(img, alpha):
    """Recover the true hair/shirt colour at the edge, then kill green-grass and brick spill."""
    from pymatting import estimate_foreground_ml
    fg = estimate_foreground_ml(img, alpha)
    band = cv2.dilate(((alpha > 0.02) & (alpha < 0.985)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    r, g, b = fg[..., 0], fg[..., 1], fg[..., 2]
    green_cap = np.maximum(r, b) * 1.01
    g_new = np.where(band & (g > green_cap), green_cap, g)
    luma = 0.3 * r + 0.59 * g_new + 0.11 * b
    dark = band & (luma < 0.42)                     # hair: dark brown, never orange
    red_cap = g_new * 1.18 + 0.02
    r_new = np.where(dark & (r > red_cap), red_cap, r)
    out = np.stack([r_new, g_new, b], -1)
    # flyaway strands over the lawn come out olive: give them the colour of the solid hair instead
    lum = out @ np.array([0.3, 0.59, 0.11])
    solid_hair = (alpha > 0.97) & (lum < 0.22)
    hair = np.median(out[solid_hair], axis=0) if solid_hair.sum() > 500 else np.array([0.12, 0.09, 0.09])
    hair_lum = hair @ np.array([0.3, 0.59, 0.11])
    wide_band = cv2.dilate(band.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    strands = wide_band & (lum < 0.4) & (out[..., 1] >= out[..., 0] * 0.92)
    target = hair[None, None, :] * np.clip(lum / hair_lum, 0.6, 2.2)[..., None]
    out = np.where(strands[..., None], 0.2 * out + 0.8 * target, out)
    return np.clip(out, 0, 1)


def enhance(fg):
    """Gentle, natural enhancement: local contrast, a touch of warmth and vibrance, crisp detail."""
    u8 = (fg * 255).astype(np.uint8)
    lab = cv2.cvtColor(u8, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(l)
    out = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2RGB).astype(np.float64) / 255
    out = 0.65 * out + 0.35 * fg                    # keep it from looking processed
    mean = out.mean(-1, keepdims=True)
    sat = np.abs(out - mean).max(-1, keepdims=True)
    out = mean + (out - mean) * (1 + 0.14 * (1 - np.clip(sat * 3, 0, 1)))   # vibrance
    out = out * np.array([1.012, 1.0, 0.985])        # a little warmth
    out = np.clip(out, 0, 1) ** 0.97
    u8 = (np.clip(out, 0, 1) * 255).astype(np.uint8)
    soft = cv2.bilateralFilter(u8, 5, 18, 5)         # tame JPEG noise without plastic skin
    blur = cv2.GaussianBlur(soft, (0, 0), 1.3)
    sharp = cv2.addWeighted(soft, 1.55, blur, -0.55, 0)
    return sharp.astype(np.float64) / 255


# ---------------------------------------------------------------- helpers

def rgba_image(rgb, alpha):
    arr = np.dstack([np.clip(rgb, 0, 1), np.clip(alpha, 0, 1)])
    return Image.fromarray((arr * 255).round().astype(np.uint8), "RGBA")


def data_uri(img, fmt="WEBP", **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    mime = {"WEBP": "image/webp", "PNG": "image/png"}[fmt]
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


FACE_BOX = (382, 738, 156, 178)   # x, y, w, h in source/photo.jpg; used when no detector is available


def find_face(rgb, alpha):
    if not hasattr(cv2, "CascadeClassifier"):        # OpenCV 5 dropped the Haar API from the main module
        return FACE_BOX
    gray = cv2.cvtColor((rgb * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, 1.08, 6, minSize=(90, 90))
    if len(faces) == 0:
        return FACE_BOX
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return int(x), int(y), int(w), int(h)


def gradient(h, w, top, bottom, angle_mix=0.35):
    yy, xx = np.mgrid[0:h, 0:w]
    t = np.clip((yy / h) * (1 - angle_mix) + (xx / w) * angle_mix, 0, 1)[..., None]
    return top * (1 - t) + bottom * t


# ---------------------------------------------------------------- avatars

def avatars(rgb, alpha, face):
    OUT_PICS.mkdir(exist_ok=True)
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh * 0.62
    side = int(fw * 3.2)
    x0, y0 = int(cx - side / 2), int(cy - side * 0.44)
    h, w = alpha.shape
    pad = side                                       # allow the square to run past the photo edges
    big_rgb = np.zeros((h + 2 * pad, w + 2 * pad, 3))
    big_a = np.zeros((h + 2 * pad, w + 2 * pad))
    big_rgb[pad:pad + h, pad:pad + w] = rgb
    big_a[pad:pad + h, pad:pad + w] = alpha
    crop_rgb = big_rgb[pad + y0:pad + y0 + side, pad + x0:pad + x0 + side]
    crop_a = big_a[pad + y0:pad + y0 + side, pad + x0:pad + x0 + side]
    S = 1024
    person = rgba_image(crop_rgb, crop_a).resize((S, S), Image.LANCZOS)
    person = person.filter(ImageFilter.UnsharpMask(radius=1.6, percent=55, threshold=2))
    person.save(OUT_PICS / "avatar-transparent.png", optimize=True)

    a = np.asarray(person.split()[3], dtype=np.float64) / 255

    def finish(bg_rgb, name, ring=None, glow=None):
        canvas = Image.fromarray((np.clip(bg_rgb, 0, 1) * 255).astype(np.uint8), "RGB").convert("RGBA")
        if glow is not None:                         # coloured rim light hugging the silhouette
            halo = cv2.GaussianBlur((a * 255).astype(np.uint8), (0, 0), 26).astype(np.float64) / 255
            layer = np.dstack([np.broadcast_to(glow, (S, S, 3)), np.clip(halo * 0.9, 0, 1)])
            canvas.alpha_composite(Image.fromarray((layer * 255).astype(np.uint8), "RGBA"))
        canvas.alpha_composite(person)
        if ring:
            ring_img = Image.new("RGBA", (S, S))
            d = ImageDraw.Draw(ring_img)
            d.ellipse([18, 18, S - 18, S - 18], outline=ring, width=22)
            canvas.alpha_composite(ring_img)
        canvas.convert("RGB").save(OUT_PICS / name, quality=94)

    yy, xx = np.mgrid[0:S, 0:S] / S
    # 1) aurora: violet -> cyan wash with a soft spotlight
    aurora = gradient(S, S, np.array([46, 16, 101]) / 255, np.array([8, 145, 178]) / 255)
    spot = np.exp(-(((xx - 0.5) ** 2) / 0.09 + ((yy - 0.38) ** 2) / 0.07))[..., None]
    finish(aurora * (0.78 + 0.35 * spot), "avatar-aurora.jpg", glow=PINK)
    # 2) studio: near-black radial with a violet rim light
    radial = np.sqrt((xx - 0.5) ** 2 + (yy - 0.42) ** 2)[..., None]
    studio = np.array([30, 27, 46]) / 255 * (1.25 - radial * 1.4)
    finish(studio, "avatar-studio.jpg", glow=VIOLET)
    # 3) studio + gradient ring, made for GitHub's circular crop
    finish(studio, "avatar-studio-ring.jpg", glow=VIOLET, ring=(167, 139, 250, 255))
    # 4) light: clean pale lilac for light-theme sites (LinkedIn, resumes)
    light = gradient(S, S, np.array([243, 237, 255]) / 255, np.array([224, 247, 252]) / 255)
    finish(light, "avatar-light.jpg")


# ---------------------------------------------------------------- hero layers

def hero_layers(rgb, alpha, face):
    """Crop head-to-waist and build the three layers the hero animates."""
    h, w = alpha.shape
    ys, xs = np.where(alpha > 0.5)
    fx, fy, fw, fh = face
    cw = 600
    ch = 640
    x0 = int(np.clip(fx + fw / 2 - cw / 2, 0, w - cw))
    y0 = int(np.clip(ys.min() - 18, 0, h - ch))
    rgb_c = rgb[y0:y0 + ch, x0:x0 + cw]
    a_c = alpha[y0:y0 + ch, x0:x0 + cw]

    # fade the bottom edge so the crop does not end in a hard line
    fade = np.clip((ch - np.arange(ch)) / 70, 0, 1)[:, None]
    a_c = a_c * fade

    colour = rgba_image(rgb_c, a_c)

    # Canny edges inside the person, coloured top-to-bottom violet -> cyan
    gray = cv2.cvtColor((rgb_c * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (0, 0), 1.4)
    edges = cv2.Canny(gray, 34, 92)
    inside = cv2.erode((a_c > 0.35).astype(np.uint8), np.ones((5, 5), np.uint8))
    edges = edges * inside
    outline = cv2.Canny((a_c > 0.4).astype(np.uint8) * 255, 50, 150)
    lines = cv2.dilate(np.maximum(edges, outline), np.ones((2, 2), np.uint8)).astype(np.float64) / 255
    lines = cv2.GaussianBlur(lines, (0, 0), 0.6)
    tint = gradient(ch, cw, VIOLET, CYAN, 0.2)
    edge_img = rgba_image(tint, np.clip(lines * 1.4, 0, 1))

    sil = Image.fromarray(((a_c > 0.25) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(1))
    sil_rgba = Image.merge("RGBA", [sil.point(lambda _: 255)] * 3 + [sil])

    fbox = [fx - x0, fy - y0, fw, fh]
    pys, pxs = np.where(a_c > 0.5)
    pbox = [int(pxs.min()), int(pys.min()), int(pxs.max() - pxs.min()), int(min(pys.max(), ch - 40) - pys.min())]

    return {
        "w": cw, "h": ch,
        "face": [int(v) for v in fbox],
        "person": pbox,
        "colour": data_uri(colour, "WEBP", quality=86, method=6),
        "edges": data_uri(edge_img, "WEBP", quality=80, method=6),
        "silhouette": data_uri(sil_rgba, "WEBP", quality=60, method=6),
    }


def main():
    photo = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "source" / "photo.jpg")
    src = Image.open(photo).convert("RGB")
    img = np.asarray(src, dtype=np.float64) / 255
    alpha = key_out_grass(img, clean_alpha(load_mask(photo, src.size)))
    fg = decontaminate(img, alpha)
    rgb = enhance(fg)
    rgb = rgb * (alpha[..., None] > 0) + fg * (alpha[..., None] == 0)
    face = find_face(rgb, alpha)
    print("face box", face)

    OUT_PICS.mkdir(exist_ok=True)
    rgba_image(rgb, alpha).save(OUT_PICS / "cutout-full.png", optimize=True)
    avatars(rgb, alpha, face)
    layers = hero_layers(rgb, alpha, face)
    (ROOT / "data" / "portrait.json").write_text(json.dumps(layers), encoding="utf-8")
    kb = {k: len(v) // 1024 for k, v in layers.items() if isinstance(v, str)}
    print("hero layers (KB of base64):", kb)


if __name__ == "__main__":
    main()
