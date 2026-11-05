# -*- coding: utf-8 -*-
"""An uploaded company logo as pages show it: blank border cropped away.

People upload whatever file they find, and a press-kit logo is often a small
emblem in the middle of a wide white canvas: Toyota's was 617x324 pixels with
the emblem in the middle 43%, so a 44px header square showed it at about
14x9px. Every market's logo upload (app/us_companies.py, app/jp_companies.py
and any market after them) is served through here, so the rule is the same on
every page:

  - the border is cropped when the four corners are all white (or near it)
    or all see-through, and the logo's content stands out from them; a
    coloured tile (Nike's black square, Airbnb's pink one) is part of the
    mark and is left as it is, as is a full-bleed picture
  - a thin margin (MARGIN of the longer side) is kept so the emblem does not
    touch the frame the page draws round it
  - a crop that would enlarge the logo in its square slot by less than
    MIN_GAIN is not made, so a logo that already fills its file is untouched
  - the result is a PNG at most MAX_SIDE pixels on its longer side

It runs when the logo is served, not when it is stored: the stored file stays
exactly as uploaded, a logo uploaded before this existed is cropped too, and a
better rule here reaches every logo at once. VERSION is part of each logo's
address so browsers and Cloudflare, which keep a logo for a year, fetch the
new crop when it changes. Without Pillow, or for a file it cannot read, the
upload is served unchanged.
"""
import io
import threading

VERSION = 1           # bump when the crop changes, so cached copies are refetched
MAX_SIDE = 256        # the largest slot is 56px; 256 is sharp at 4x
MARGIN = 0.06         # kept round the content, as a share of its longer side
MIN_GAIN = 1.15       # crop only when the logo shows at least this much larger
LIGHT = 225           # a corner is white when every channel is at least this (0-255)
INK_TOL = 40          # a pixel further than this from the background is content (JPEG noise is below it)
MAX_PIXELS = 25_000_000

_cache = {}
_lock = threading.Lock()


def served(sha, data, ctype):
    """(bytes, content type) to send for an upload with this SHA-256."""
    with _lock:
        hit = _cache.get(sha)
    if hit is None:
        hit = crop(data) or (data, ctype)
        with _lock:
            _cache[sha] = hit
    return hit


def crop(data):
    """(PNG bytes, 'image/png') with the border cropped, or None to serve as is."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(io.BytesIO(data))
        im.seek(0)                      # an animated GIF shows its first frame
        im = im.convert("RGBA")
    except Exception:  # noqa: BLE001 — an unreadable file is served as uploaded
        return None
    w, h = im.size
    corners = [im.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    if all(c[3] < 16 for c in corners):
        # transparent surround: whatever is not see-through is the logo
        box = im.getchannel("A").point(lambda v: 255 if v >= 16 else 0).getbbox()
        fill = (0, 0, 0, 0)
    elif all(min(c[:3]) >= LIGHT for c in corners):
        bg = tuple(sorted(c[i] for c in corners)[1] for i in range(4))
        diff = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, bg[:3]))
        ink = diff.convert("L").point(lambda v: 255 if v > INK_TOL else 0)
        # a see-through pixel is background, whatever colour it carries
        box = ImageChops.multiply(ink, im.getchannel("A").point(lambda v: 255 if v >= 16 else 0)).getbbox()
        fill = bg
    else:
        return None                     # a coloured tile or a full-bleed picture
    if not box:
        return None                     # nothing but background
    im = im.crop(box)
    cw, ch = im.size
    pad = int(round(MARGIN * max(cw, ch)))
    if max(w, h) < MIN_GAIN * (max(cw, ch) + 2 * pad):
        return None                     # it already fills its file
    out = Image.new("RGBA", (cw + 2 * pad, ch + 2 * pad), fill)
    out.paste(im, (pad, pad))
    if max(out.size) > MAX_SIDE:
        k = MAX_SIDE / float(max(out.size))
        out = out.resize((max(1, round(out.width * k)), max(1, round(out.height * k))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue(), "image/png"
