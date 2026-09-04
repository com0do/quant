"""Read the red/green bars of the "强龙出现" (Strong Dragon Appears) sub-chart out of the deck's screenshots.

Every case slide is a 3200x1440 phone screenshot: a daily candlestick panel with N bars (the chart prints "周期数N个", i.e. "N bars")
and, below it, the indicator panel where +100 red bars and -100 green bars are drawn per bar.
We fit an evenly spaced N-bar grid to the candle panel, then sample the indicator panel at each
grid position. Output: one row per (case, bar index) with red/green flags.

usage: uv run python research/strong_dragon/extract_slide_signals.py [--pptx PATH] [--debug]
"""
from __future__ import annotations

import argparse
import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from sd_data import HERE, OUT, REPO, load_cases

PRICE_ROWS = (260, 930)
IND_ROWS = (995, 1250)
PLOT_COLS = (600, 3170)


def slide_images(pptx: Path) -> dict[int, bytes]:
    """Presentation-order slide number -> bytes of the largest picture on that slide."""
    z = zipfile.ZipFile(pptx)
    pres = z.read("ppt/presentation.xml").decode("utf-8")
    pres_rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8")
    rid_to_target = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', pres_rels))
    rid_to_target.update({k: v for v, k in re.findall(r'Target="([^"]+)"[^>]*Id="(rId\d+)"', pres_rels)})
    order = re.findall(r'<p:sldId [^>]*r:id="(rId\d+)"', pres)
    out = {}
    for n, rid in enumerate(order, 1):
        slide_file = "ppt/" + rid_to_target[rid]
        rels_file = slide_file.replace("slides/", "slides/_rels/") + ".rels"
        rels = z.read(rels_file).decode("utf-8")
        media = [t for t in re.findall(r'Target="\.\./media/([^"]+)"', rels)]
        if not media:
            continue
        best = max(media, key=lambda m: z.getinfo("ppt/media/" + m).file_size)
        out[n] = z.read("ppt/media/" + best)
    return out


def masks(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r, g, b = (img[..., i].astype(int) for i in range(3))
    red = (r > 170) & (g < 120) & (b < 120)
    green = (g > 150) & (r < 170) & (b < 150) & (g - r > 40)
    return red, green


def fit_grid(colored_cols: np.ndarray, n_bars: int) -> tuple[float, float, float]:
    """Best (first_center, step) so that an n-bar comb hits the most coloured candle columns."""
    x0, x1 = PLOT_COLS
    prof = colored_cols.astype(float)
    smooth = np.maximum.reduce([np.roll(prof, k) for k in (-2, -1, 0, 1, 2)])
    guess = (x1 - x0 - 40) / n_bars
    best = (-1.0, 0.0, 0.0)
    idx = np.arange(n_bars)
    for step in np.linspace(0.94 * guess, 1.06 * guess, 241):
        for c0 in np.arange(x0, x0 + step, 0.5):
            pos = np.round(c0 + idx * step).astype(int)
            if pos[-1] >= len(smooth):
                continue
            score = smooth[pos].sum()
            if score > best[0]:
                best = (score, c0, step)
    return best[1], best[2], best[0] / n_bars


def extract(img_bytes: bytes, n_bars: int, case_id: str, debug_dir: Path | None) -> pd.DataFrame:
    img = np.asarray(Image.open(io.BytesIO(img_bytes)).convert("RGB"))
    red, green = masks(img)
    pr0, pr1 = PRICE_ROWS
    ir0, ir1 = IND_ROWS
    colored = (red | green)[pr0:pr1].any(axis=0)
    c0, step, hit = fit_grid(colored, n_bars)
    half = max(2, int(step * 0.3))
    rows = []
    for i in range(n_bars):
        c = int(round(c0 + i * step))
        win_r = red[ir0:ir1, c - half:c + half + 1]
        win_g = green[ir0:ir1, c - half:c + half + 1]
        rows.append({"case_id": case_id, "bar": i, "x": c, "red_px": int(win_r.sum()), "green_px": int(win_g.sum())})
    df = pd.DataFrame(rows)
    # A real bar covers dozens of rows over several columns; arrows alone are much smaller.
    min_px = max(40, int(0.15 * (ir1 - ir0) * half))
    df["red"] = (df["red_px"] >= min_px).astype(int)
    df["green"] = (df["green_px"] >= min_px).astype(int)
    df["grid_hit_rate"] = round(hit, 3)
    df["step_px"] = round(step, 2)
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        im = Image.fromarray(img.copy())
        d = ImageDraw.Draw(im)
        for _, r in df.iterrows():
            col = (255, 0, 255) if r["red"] else (0, 160, 255) if r["green"] else (180, 180, 180)
            d.line([(r["x"], pr0), (r["x"], ir1)], fill=col, width=1)
        im.resize((1600, 720)).save(debug_dir / f"{case_id}_grid.png")
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pptx", default=str(REPO / "强龙出现运营方案.pptx"))
    ap.add_argument("--out", default=str(HERE / "slide_signals.csv"))
    ap.add_argument("--debug", action="store_true", help="write grid overlays to output/strong_dragon/slides/")
    args = ap.parse_args()

    cases = load_cases()
    images = slide_images(Path(args.pptx))
    frames = []
    for _, c in cases.iterrows():
        blob = images.get(int(c["slide"]))
        if blob is None:
            print(f"{c['case_id']}: no picture on slide {c['slide']}")
            continue
        df = extract(blob, int(c["chart_bars"]), c["case_id"], OUT / "slides" if args.debug else None)
        frames.append(df)
        print(f"{c['case_id']} {c['name']}: bars={len(df)} red={df['red'].sum()} green={df['green'].sum()} "
              f"grid_hit={df['grid_hit_rate'].iat[0]} step={df['step_px'].iat[0]}px")
    out = pd.concat(frames, ignore_index=True)
    out[["case_id", "bar", "red", "green", "red_px", "green_px", "x", "step_px", "grid_hit_rate"]].to_csv(args.out, index=False)
    print(f"wrote {args.out} ({len(out)} rows)")


if __name__ == "__main__":
    main()
