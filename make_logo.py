"""Draws the dictation-tool logo: a dark rounded square, a glowing ring and a five-bar waveform in the
app's aurora colours. Writes dictation_tool.ico (all Windows sizes) and dictation_tool.png next to this file.

    python make_logo.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
STOPS = [(0, 229, 255), (124, 92, 255), (255, 79, 216), (255, 170, 60)]
N = 1024


def gradient(size: int) -> Image.Image:
    px = []
    for x in range(size):
        pos = x / (size - 1) * (len(STOPS) - 1)
        i = min(int(pos), len(STOPS) - 2)
        f = pos - i
        a, b = STOPS[i], STOPS[i + 1]
        px.append(tuple(int(a[j] + (b[j] - a[j]) * f) for j in range(3)))
    strip = Image.new("RGB", (size, 1))
    strip.putdata(px)
    big = strip.resize((size * 2, size * 2), Image.BILINEAR).rotate(-28, resample=Image.BICUBIC)
    return big.crop((size // 2, size // 2, size // 2 + size, size // 2 + size))  # the middle, so no empty corners


def draw(detail: bool) -> Image.Image:
    """detail=False drops the ring and fattens the bars so the mark still reads at 16 to 32 pixels."""
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    plate = Image.new("L", (N, N), 0)
    ImageDraw.Draw(plate).rounded_rectangle([40, 40, N - 40, N - 40], radius=230, fill=255)
    img.paste((13, 14, 20, 255), (0, 0), plate)
    grad = gradient(N).crop((0, 0, N, N))
    mark = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(mark)
    if detail:
        d.rounded_rectangle([40, 40, N - 40, N - 40], radius=230, outline=255, width=34)
    heights = [0.26, 0.52, 0.86, 0.52, 0.26] if detail else [0.34, 0.62, 0.92, 0.62, 0.34]
    bar_w = 86 if detail else 118
    gap = 136 if detail else 162
    span = 640 if detail else 760
    for i, h in enumerate(heights):
        x = N / 2 + (i - 2) * gap
        half = span * h / 2
        d.rounded_rectangle([x - bar_w / 2, N / 2 - half, x + bar_w / 2, N / 2 + half], radius=bar_w / 2, fill=255)
    glow = mark.filter(ImageFilter.GaussianBlur(34)).point(lambda v: int(v * 0.55))
    inside = Image.new("L", (N, N), 0)
    inside.paste(glow, (0, 0), plate)  # keep the glow inside the plate
    img.paste(grad, (0, 0), inside)
    img.paste(grad, (0, 0), mark)
    return img


def main() -> None:
    big, small = draw(True), draw(False)
    big.resize((256, 256), Image.LANCZOS).save(HERE / "dictation_tool.png")
    frames = [(small if s <= 32 else big).resize((s, s), Image.LANCZOS) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    frames[-1].save(HERE / "dictation_tool.ico", format="ICO", append_images=frames[:-1],
                    sizes=[(f.width, f.height) for f in frames])
    sheet = Image.new("RGBA", (256 + 64 + 48 + 32 + 16 + 60, 256), (40, 44, 52, 255))
    x = 0
    for s in (256, 64, 48, 32, 16):
        f = (small if s <= 32 else big).resize((s, s), Image.LANCZOS)
        sheet.alpha_composite(f, (x, (256 - s) // 2))
        x += s + 12
    sheet.save(HERE / "logo-preview.png")
    print("wrote dictation_tool.ico, dictation_tool.png")


if __name__ == "__main__":
    main()
