"""Generate the moreader app icon (SVG source -> PNG + multi-size Windows ICO).

Run from the repo root:  python scripts/make_icon.py
Outputs into moreader/assets/ so the icon is packaged with the app and loadable
at runtime (Path(__file__).parent / "assets").
"""

import random
from pathlib import Path

import cairosvg
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "moreader" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

BG0, BG1 = "#16212e", "#0b121b"
BORDER = "#2d8cf0"
DARK = "#0b121b"
GREEN0, GREEN1 = "#33d67a", "#1ea85a"


def _build_svg() -> str:
    random.seed(7)
    x0, x1 = 104, 408
    y_top, y_bot = 320, 404
    widths = [7, 11, 15, 9]
    bars, x, i = [], x0, 0
    while x < x1 - 6:
        w = widths[i % len(widths)]
        if x + w > x1:
            break
        bars.append((x, w))
        x += w + random.choice([7, 9, 11])
        i += 1
    bar_rects = "\n".join(
        f'<rect x="{bx}" y="{y_top}" width="{bw}" height="{y_bot - y_top}" rx="2" fill="url(#bar)"/>'
        for bx, bw in bars
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="bgg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{BG0}"/><stop offset="1" stop-color="{BG1}"/>
    </linearGradient>
    <linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#c7d3df"/>
    </linearGradient>
    <linearGradient id="mo" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#d5e2ef"/>
    </linearGradient>
    <linearGradient id="badge" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{GREEN0}"/><stop offset="1" stop-color="{GREEN1}"/>
    </linearGradient>
  </defs>
  <rect x="24" y="24" width="464" height="464" rx="104" fill="url(#bgg)"/>
  <rect x="24" y="24" width="464" height="464" rx="104" fill="none" stroke="{BORDER}" stroke-width="10" stroke-opacity="0.9"/>
  <!-- the letters MO: the hero of the mark -->
  <text x="256" y="270" text-anchor="middle" fill="url(#mo)"
        font-family="DejaVu Sans, Arial, Helvetica, sans-serif" font-weight="bold"
        font-size="230" letter-spacing="-6">MO</text>
  <!-- barcode strip beneath, with a blue scan line -->
  <g>
    {bar_rects}
  </g>
  <rect x="90" y="357" width="332" height="9" rx="4" fill="{BORDER}" fill-opacity="0.9"/>
  <!-- verified badge, lower-right, with a dark halo to lift it off the barcode -->
  <circle cx="392" cy="392" r="92" fill="{DARK}"/>
  <circle cx="392" cy="392" r="78" fill="url(#badge)"/>
  <path d="M354 394 l26 26 l50 -58" fill="none" stroke="#ffffff" stroke-width="22"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''


def main() -> None:
    svg = _build_svg()
    (OUT / "icon.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT / "icon.png"),
                     output_width=256, output_height=256)
    sizes = [256, 128, 64, 48, 32, 24, 16]
    base = Image.open(OUT / "icon.png").convert("RGBA").resize((256, 256))
    base.save(OUT / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes])
    print(f"wrote {OUT/'icon.svg'}, {OUT/'icon.png'}, {OUT/'icon.ico'} (sizes {sizes})")


if __name__ == "__main__":
    main()
