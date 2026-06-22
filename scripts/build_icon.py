"""Generate Kee's app icon as a multi-resolution .ico file.

The icon mirrors the Brand.svelte glyph: a stylised K formed by a vertical
stroke + two opposing curves (the "nervous system trace" idea) with a small
solid dot at the apex. Cyan on a near-black rounded square.

Output: assets/kee.ico (16/32/48/64/128/256 px), assets/kee.png (256 px).

Re-run when the brand changes::

    .venv\\Scripts\\python.exe scripts/build_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets"
OUT_DIR.mkdir(exist_ok=True)

# Dashboard palette
BG = (8, 9, 11, 255)          # bg-[#08090b]
CYAN = (34, 211, 238, 255)    # text-cyan-400
CYAN_DIM = (34, 211, 238, 140)
CYAN_HOT = (34, 211, 238, 215)
GLOW = (34, 211, 238, 90)


def _quad_points(p0, p1, p2, n=24):
    """Sample a quadratic Bezier — replicates the SVG `5 12, 19 4` arc-feel."""
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def _rounded_rect_mask(size: int, radius: int) -> Image.Image:
    """Alpha mask for rounded-square background."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=255)
    return mask


def render(size: int) -> Image.Image:
    """Draw the Kee glyph at `size x size`, supersampled then downscaled."""
    SS = 4  # 4× supersample for anti-alias
    s = size * SS

    # Background: rounded square, near-black with a faint inner halo.
    bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    body = Image.new("RGBA", (s, s), BG)
    radius = int(s * 0.22)
    bg.paste(body, (0, 0), _rounded_rect_mask(s, radius))

    d = ImageDraw.Draw(bg)

    # Coordinate system: SVG viewBox is 0..24, map onto our canvas.
    def P(x, y):
        return (x / 24 * s, y / 24 * s)

    stroke_w = max(2, int(s * 0.06))   # vertical bar
    curve_w = max(2, int(s * 0.05))    # curves slightly thinner

    # --- soft cyan glow behind the whole glyph ---
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse(
        [P(2, 6)[0], P(2, 6)[1], P(22, 18)[0], P(22, 18)[1]],
        fill=GLOW,
    )
    glow = glow.filter(_blur_filter(int(s * 0.05)))
    bg.alpha_composite(glow)

    # --- vertical bar (the spine of the K) ---
    d.line([P(5, 4), P(5, 20)], fill=CYAN, width=stroke_w)

    # --- upper diagonal curve (5,12) → (19,4), via (12,7) ---
    upper = _quad_points(P(5, 12), P(12, 7), P(19, 4), n=40)
    for i in range(len(upper) - 1):
        d.line([upper[i], upper[i + 1]], fill=CYAN_DIM, width=curve_w)

    # --- lower diagonal curve (5,12) → (19,20), via (12,17) ---
    lower = _quad_points(P(5, 12), P(12, 17), P(19, 20), n=40)
    for i in range(len(lower) - 1):
        d.line([lower[i], lower[i + 1]], fill=CYAN_HOT, width=curve_w)

    # --- pivot dot at (5,12) ---
    r = int(s * 0.035)
    cx, cy = P(5, 12)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=CYAN)

    # Hot center: a small extra-bright pixel cluster on the dot.
    rh = max(1, int(s * 0.015))
    d.ellipse(
        [cx - rh, cy - rh, cx + rh, cy + rh],
        fill=(255, 255, 255, 255),
    )

    # Downscale with Lanczos for crisp anti-alias.
    return bg.resize((size, size), Image.LANCZOS)


def _blur_filter(radius: int):
    from PIL import ImageFilter
    return ImageFilter.GaussianBlur(radius=max(1, radius))


def main() -> None:
    sizes = [16, 32, 48, 64, 128, 256]
    images = [render(s) for s in sizes]

    ico_path = OUT_DIR / "kee.ico"
    # PIL writes multi-resolution ICO when you save the largest and pass
    # the sizes via `sizes=`. To honour our hand-rendered versions per
    # size (each gets a fresh supersample pass), use append_images.
    images[-1].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )

    png_path = OUT_DIR / "kee.png"
    images[-1].save(png_path, format="PNG")

    print(f"wrote {ico_path} ({ico_path.stat().st_size:,} bytes, "
          f"resolutions: {sizes})")
    print(f"wrote {png_path}")


if __name__ == "__main__":
    main()
