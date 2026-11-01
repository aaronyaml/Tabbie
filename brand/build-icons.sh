#!/bin/sh
# Regenerate every Hermes logo/icon derivative from ONE source image.
#
#   ~/hermes/brand/avatar/current.png   <- the icon (drop new art here), then run:
#   sh ~/hermes/brand/build-icons.sh
#
# Optional companions (picked up automatically when present):
#   avatar/buddy-icon-square.png   opaque full-bleed square -> used for every raster
#                                  app icon instead of current.png ($SQUARE)
#   avatar/buddy-character.png     transparent bare mascot -> header, empty state,
#                                  login, assistant avatar ($CHAR)
#
# Handles both kinds of source:
#   * transparent character (e.g. concept-2): trimmed, centred on a solid tile
#     colour ($TILE, default warm off-white) with padding ($FILL = share of the
#     tile the character occupies).
#   * full-bleed app icon (opaque corners): used as-is, just resized.
# iOS / the WebUI CSS round the corners themselves, so every output is square.
#
# Outputs
#   ~/hermes/brand/icons/      favicon-16/32.png, favicon.ico, favicon.svg,
#                              apple-touch-icon.png (180), icon-192/512.png,
#                              icon-512-maskable.png, logo-256.png
#   ~/hermes/webui-extension/brand/logo-256.png   (header + empty state in the UI)
# Served at https://hermes.local by the handle routes in ~/hermes/services/Caddyfile.
# After a rerun: hard-reload the page; on iPhone remove + re-add the home-screen icon.
set -eu
BRAND="$HOME/hermes/brand"
SRC="${1:-$BRAND/avatar/current.png}"
SQUARE="${SQUARE:-$BRAND/avatar/buddy-icon-square.png}"; [ -f "$SQUARE" ] || SQUARE="$SRC"
CHAR="${CHAR:-$BRAND/avatar/buddy-character.png}"; [ -f "$CHAR" ] || CHAR=""
TILE="${TILE:-#FAF9F6}"
FILL="${FILL:-0.70}"
OUT="$BRAND/icons"
EXT="$HOME/hermes/webui-extension/brand"
mkdir -p "$OUT" "$EXT"

python3 -I - "$SQUARE" "$OUT" "$EXT" "$TILE" "$FILL" "$CHAR" <<'PY'
import sys, base64, io
from PIL import Image, ImageStat, ImageDraw
src, out, ext, tile, fill, char = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]), sys.argv[6]
im = Image.open(src).convert("RGBA")
w, h = im.size
a = im.getchannel("A")
k = max(4, min(w, h) // 20)
corners = [a.crop(b) for b in ((0, 0, k, k), (w - k, 0, w, k), (0, h - k, k, h), (w - k, h - k, w, h))]
opaque = all(ImageStat.Stat(c).mean[0] / 255 > 0.9 for c in corners)
# An icon that already has its own (rounded) background: transparent corners but the
# artwork spans nearly the whole canvas. Treat it as full-bleed and fill the corners
# with the colour at the top-centre edge so iOS/WebUI rounding shows no halo.
_bb = a.point(lambda v: 255 if v > 200 else 0).getbbox()
if not opaque and _bb and (_bb[2] - _bb[0]) > 0.9 * w and (_bb[3] - _bb[1]) > 0.9 * h:
    edge = im.getpixel((w // 2, _bb[1] + h // 25))[:3]
    flat = Image.new("RGBA", im.size, edge); flat.alpha_composite(im); im = flat
    opaque = True

def _bleed():
    """Full-bleed source -> square, edge-to-edge tile. If the art carries its own
    rounded tile inside a dark/transparent margin, crop to that tile and paint the
    corners outside it with the tile's edge colour, so platform masks show no ring."""
    s = min(w, h)
    sq = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    rgb = sq.convert("RGB")
    lit = rgb.point(lambda v: 255 if v > 28 else 0).convert("L").point(lambda v: 255 if v > 0 else 0)
    bb = lit.getbbox() or (0, 0, s, s)
    if min(bb[0], bb[1], s - bb[2], s - bb[3]) <= s * 0.003:
        return rgb  # already edge to edge
    side = round(max(bb[2] - bb[0], bb[3] - bb[1]) * 0.96)  # trim the art's own dark rim
    cx, cy = (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2
    box = (cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side)
    art = rgb.crop(box)
    edge = art.getpixel((side // 2, max(1, side // 30)))
    out = Image.new("RGB", (side, side), edge)
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), round(side * 0.2), fill=255)
    out.paste(art, (0, 0), mask)
    return out

def master(size, pad_fill):
    if opaque:  # full-bleed icon
        return _bleed().resize((size, size), Image.LANCZOS)
    box = a.point(lambda v: 255 if v > 40 else 0).getbbox() or (0, 0, w, h)
    art = im.crop(box)
    scale = size * pad_fill / max(art.size)
    art = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.LANCZOS)
    bg = Image.new("RGBA", (size, size), tile)
    bg.alpha_composite(art, ((size - art.width) // 2, (size - art.height) // 2))
    return bg.convert("RGB")

def save(img, path):
    img.save(path, optimize=True)
    print("  ", path, img.size)

save(master(180, fill), f"{out}/apple-touch-icon.png")
save(master(192, fill), f"{out}/icon-192.png")
save(master(512, fill), f"{out}/icon-512.png")
save(master(512, min(fill, 0.58)), f"{out}/icon-512-maskable.png")  # Android safe zone
fav = master(256, min(0.86, fill + 0.14))  # favicons: character larger, it is tiny
save(fav.resize((32, 32), Image.LANCZOS), f"{out}/favicon-32.png")
save(fav.resize((16, 16), Image.LANCZOS), f"{out}/favicon-16.png")
fav.save(f"{out}/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
print("  ", f"{out}/favicon.ico")
logo = master(256, fill)
save(logo, f"{out}/logo-256.png")
save(logo, f"{ext}/logo-256.png")
# SVG favicon (the WebUI's preferred <link rel=icon>): rounded tile around the PNG.
buf = io.BytesIO(); fav.resize((64, 64), Image.LANCZOS).save(buf, "PNG", optimize=True)
b64 = base64.b64encode(buf.getvalue()).decode()
svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 64 64">'
       '<clipPath id="r"><rect width="64" height="64" rx="14"/></clipPath>'
       f'<image clip-path="url(#r)" width="64" height="64" href="data:image/png;base64,{b64}"/></svg>')
open(f"{out}/favicon.svg", "w").write(svg)
print("  ", f"{out}/favicon.svg", "(full-bleed source)" if opaque else "(transparent source on tile " + tile + ")")
# Bare mascot for the UI (trimmed, transparent, square canvas). Falls back to the icon.
if char:
    c = Image.open(char).convert("RGBA")
    c = c.crop(c.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox())
    side = max(c.size); sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.alpha_composite(c, ((side - c.width) // 2, (side - c.height) // 2))
    m = sq
else:
    m = logo.convert("RGBA")
if char:  # browser-tab favicons: the bare mascot reads better than a tiny tile
    save(m.resize((32, 32), Image.LANCZOS), f"{out}/favicon-32.png")
    save(m.resize((16, 16), Image.LANCZOS), f"{out}/favicon-16.png")
    m.resize((256, 256), Image.LANCZOS).save(f"{out}/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    buf = io.BytesIO(); m.resize((64, 64), Image.LANCZOS).save(buf, "PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    open(f"{out}/favicon.svg", "w").write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<image width="64" height="64" href="data:image/png;base64,{b64}"/></svg>')
    print("   favicons rebuilt from the mascot")
for n in (256, 512):
    r = m.resize((n, n), Image.LANCZOS)
    save(r, f"{out}/mascot-{n}.png"); save(r, f"{ext}/mascot-{n}.png")
PY
echo "done. Bump ?v= on logo URLs in ~/hermes/webui-extension/apple-skin.css if browsers cache the old art."
