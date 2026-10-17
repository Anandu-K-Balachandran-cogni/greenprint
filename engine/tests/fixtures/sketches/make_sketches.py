"""Draw the hand-sketch fixtures used by the sketch-reading tests and eval (run once; images are committed).

    uv run python tests/fixtures/sketches/make_sketches.py
"""

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
FONT = "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"
INK = (35, 45, 90)


def font(size):
    return ImageFont.truetype(FONT, size)


def wobbly_line(d, a, b, rng, width=4):
    pts, n = [], 12
    for i in range(n + 1):
        t = i / n
        pts.append((a[0] + (b[0] - a[0]) * t + rng.uniform(-2.5, 2.5), a[1] + (b[1] - a[1]) * t + rng.uniform(-2.5, 2.5)))
    d.line(pts, fill=INK, width=width, joint="curve")


def box(d, x0, y0, x1, y1, rng, width=4):
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        wobbly_line(d, a, b, rng, width)


def paper(w, h):
    return Image.new("RGB", (w, h), (250, 248, 240))


def plot_survey():
    rng = random.Random(1)
    im = paper(1400, 1000); d = ImageDraw.Draw(im)
    d.text((80, 40), "Survey No. 112/3 - my plot", font=font(44), fill=INK)
    box(d, 300, 170, 900, 830, rng, 5)
    d.text((540, 120), "30 ft", font=font(48), fill=INK)
    d.text((150, 470), "40 ft", font=font(48), fill=INK)
    d.rectangle((250, 870, 950, 950), outline=INK, width=3)
    d.text((500, 878), "ROAD  (20 ft wide)", font=font(44), fill=INK)
    wobbly_line(d, (1150, 420), (1150, 220), rng, 5)
    d.polygon([(1150, 190), (1125, 245), (1175, 245)], fill=INK)
    d.text((1132, 120), "N", font=font(56), fill=INK)
    d.text((960, 560), "house: 2 bedrooms,\nkitchen, hall,\none car parking,\nsingle floor", font=font(40), fill=INK, spacing=10)
    im.save(HERE / "plot_survey.png")


def house_sketch():
    rng = random.Random(2)
    im = paper(1500, 1100); d = ImageDraw.Draw(im)
    d.text((70, 30), "Plot 35 x 50 ft  -  G+1", font=font(50), fill=INK)
    box(d, 120, 130, 1080, 1000, rng, 6)
    for (x0, y0, x1, y1, label) in [
        (120, 130, 520, 480, "BED 1\n12x11"), (520, 130, 860, 480, "BED 2\n10x11"), (860, 130, 1080, 480, "TOILET"),
        (120, 480, 600, 800, "LIVING\n14x12"), (600, 480, 1080, 800, "KITCHEN\n8x10"), (120, 800, 1080, 1000, "PARKING (1 car)"),
    ]:
        box(d, x0, y0, x1, y1, rng, 4)
        d.multiline_text(((x0 + x1) / 2 - 90, (y0 + y1) / 2 - 40), label, font=font(42), fill=INK, spacing=6)
    d.text((1120, 300), "First floor:\n3rd bedroom\n+ balcony\n+ toilet", font=font(38), fill=INK, spacing=10)
    d.text((1120, 860), "road at bottom", font=font(38), fill=INK)
    im.save(HERE / "house_sketch.png")
    # The same sketch as a phone photo: slightly rotated, softer, uneven light and a grey table edge.
    photo = im.rotate(4, resample=Image.BICUBIC, expand=True, fillcolor=(120, 118, 112)).filter(ImageFilter.GaussianBlur(1.1))
    shade = Image.linear_gradient("L").resize(photo.size).point(lambda v: 200 + v // 5)
    photo = Image.composite(photo, Image.new("RGB", photo.size, (90, 88, 84)), shade)
    noise = Image.effect_noise(photo.size, 18).convert("RGB")
    Image.blend(photo, noise, 0.06).convert("RGB").save(HERE / "house_sketch_photo.jpg", quality=80)


if __name__ == "__main__":
    plot_survey(); house_sketch()
    print("written", sorted(p.name for p in HERE.glob("*.[pj][np]g")))
