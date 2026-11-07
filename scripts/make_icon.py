"""Draws music_manager/assets/icon.png and icon.ico (run once; needs Pillow)."""

from pathlib import Path

from PIL import Image, ImageDraw

out = Path(__file__).resolve().parents[1] / "music_manager" / "assets"
S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=52, fill=(229, 160, 13))
ink = (30, 32, 35)
# Two eighth notes joined by a beam.
d.ellipse([52, 160, 112, 206], fill=ink)
d.ellipse([140, 140, 200, 186], fill=ink)
d.rectangle([98, 66, 112, 184], fill=ink)
d.rectangle([186, 46, 200, 164], fill=ink)
d.polygon([(98, 66), (200, 46), (200, 82), (98, 102)], fill=ink)
img.save(out / "icon.png")
img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
