# -*- coding: utf-8 -*-
"""Uploaded company logos are served with their blank border cropped, the same
way in every market (app/logo_trim.py).

Images are drawn in memory; the stores live in a temporary folder; no network.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_logo_trim
"""
import io
import pathlib
import tempfile
import unittest

from PIL import Image

from app import jp_companies, logo_trim, us_companies


def png(size, bg, box=None, ink=(200, 0, 0, 255), fmt="PNG"):
    im = Image.new("RGBA", size, bg)
    if box:
        im.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), ink), box[:2])
    buf = io.BytesIO()
    (im.convert("RGB") if fmt == "JPEG" else im).save(buf, fmt)
    return buf.getvalue()


def size_of(data):
    return Image.open(io.BytesIO(data)).size


# Toyota's upload: 617x324, the emblem in the middle 265x181
TOYOTA = png((617, 324), (255, 255, 255, 255), (171, 70, 436, 251), fmt="JPEG")


class Crop(unittest.TestCase):
    def test_a_small_emblem_on_a_wide_white_canvas_is_cropped_to_the_emblem(self):
        data, ctype = logo_trim.crop(TOYOTA)
        self.assertEqual(ctype, "image/png")
        w, h = size_of(data)
        # the emblem with a thin margin (297x213), no longer twice as wide as
        # it is tall, then brought within MAX_SIDE
        self.assertEqual((w, h), (256, 184))

    def test_a_transparent_surround_is_cropped(self):
        data, _ = logo_trim.crop(png((400, 400), (0, 0, 0, 0), (150, 150, 250, 250)))
        self.assertEqual(size_of(data), (112, 112))

    def test_a_coloured_tile_is_part_of_the_mark_and_is_kept(self):
        self.assertIsNone(logo_trim.crop(png((64, 64), (0, 0, 0, 255), (16, 24, 48, 40), ink=(255, 255, 255, 255))))

    def test_a_logo_that_already_fills_its_file_is_left_alone(self):
        self.assertIsNone(logo_trim.crop(png((100, 100), (255, 255, 255, 255), (3, 3, 97, 97))))

    def test_a_large_crop_is_scaled_down(self):
        data, _ = logo_trim.crop(png((3000, 1000), (255, 255, 255, 255), (500, 100, 2500, 900)))
        self.assertEqual(max(size_of(data)), logo_trim.MAX_SIDE)

    def test_a_blank_or_unreadable_file_is_served_as_uploaded(self):
        self.assertIsNone(logo_trim.crop(png((50, 50), (255, 255, 255, 255))))
        junk = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        self.assertEqual(logo_trim.served("junk", junk, "image/png"), (junk, "image/png"))


class EveryMarket(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for mod in (jp_companies, us_companies):
            saved = mod.STORE_PATH
            self.addCleanup(lambda m=mod, s=saved: (setattr(m, "STORE_PATH", s), setattr(m, "_CACHE", None)))
            mod.STORE_PATH = pathlib.Path(tmp.name) / (mod.__name__.rsplit(".", 1)[1] + ".json")
            mod._CACHE = None

    def test_japan_and_us_serve_the_same_crop_and_keep_the_upload(self):
        jp_companies.set_logo("7203", TOYOTA, "test")
        us_companies.set_logo(1094517, TOYOTA, "test")
        for url, read, store in ((jp_companies.logo("7203"), jp_companies.logo_file, jp_companies),
                                 (us_companies.logo_url(1094517), us_companies.logo_file, us_companies)):
            # the crop's version is in the address, so a year-long cached copy is not reused
            self.assertIn("-%d.jpg" % logo_trim.VERSION, url)
            data, ctype = read(url.rsplit("/", 1)[1])
            self.assertEqual((ctype, size_of(data)), ("image/png", (256, 184)))
            # an address written before the crop existed still finds the logo
            self.assertEqual(read(url.rsplit("/", 1)[1].replace("-%d." % logo_trim.VERSION, ".")), (data, ctype))
            self.assertEqual(store.load()["companies"]["7203" if store is jp_companies else "1094517"]["logo_bytes"],
                             len(TOYOTA))


if __name__ == "__main__":
    unittest.main()
