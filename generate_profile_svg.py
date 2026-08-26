#!/usr/bin/env python3
"""
Generate a terminal-style hacker profile SVG (ASCII/halftone portrait + system-info panel)
for embedding in a GitHub profile README.md.

Usage:
    python3 generate_profile_svg.py --image assets/photo.jpg --config config.json --out profile.svg

Re-run any time you want to swap the photo or edit config.json — no manual SVG editing needed.
"""
import argparse
import colorsys
import json
import math
import xml.sax.saxutils as sax
from PIL import Image, ImageOps, ImageFilter, ImageChops

# ---- palette ----------------------------------------------------------
BG = "#05080a"
BORDER = "#1f6f4a"
DIM = "#3a7a5a"
GREEN = "#7fffa0"
WHITE = "#d8f5e3"
RED = "#ff5f56"
YELLOW = "#ffbd2e"
GREENDOT = "#27c93f"

# ---- ascii / halftone art -------------------------------------------------

def crop_frame(img, crop):
    """crop = (left, top, right, bottom) as fractions of width/height, 0..1."""
    left, top, right, bottom = crop
    w, h = img.size
    box = (int(w * left), int(h * top), int(w * right), int(h * bottom))
    return img.crop(box)


def image_to_halftone_cells(path, cols, rows, invert=False, crop=(0.0, 0.0, 1.0, 1.0),
                             edge_floor=0.30, edge_thresh=45, edge_blur=2.2,
                             highlight_cap=0.92, saturation=1.15, shadow_gamma=1.8):
    """Per-cell (rgb, brightness, is_edge) for a true-color halftone: dot size
    still comes from brightness (classic halftone), but dot color is the
    photo's own sampled color instead of a flat duotone, so the subject's
    actual coloring — not just its shape — reads at a glance.

    Two problems pure luminance halftone has are corrected here:
    - blown-out highlights (bright fur reading as flat white, no shading) are
      pulled back with `highlight_cap`.
    - a dark subject against a dark background disappearing entirely is fixed
      the same way as before: a color-edge overlay (`edge_floor`/`edge_thresh`)
      keeps silhouette boundaries visible regardless of local brightness.
    `shadow_gamma` pushes non-edge shadows/background down harder than
    midtones so the background stays quiet instead of printing as a solid
    color field.
    """
    rgb = Image.open(path).convert("RGB")
    rgb = crop_frame(rgb, crop)

    # edge magnitude from color channels (not just luminance) — a boundary
    # between two similarly dark but differently hued regions still shows up.
    # Blurred before downscaling so fine background texture doesn't survive
    # as noise while real (longer) silhouette edges do.
    edge_mag = None
    for ch in rgb.split():
        e = ch.filter(ImageFilter.FIND_EDGES)
        edge_mag = e if edge_mag is None else ImageChops.lighter(edge_mag, e)
    edge_mag = edge_mag.filter(ImageFilter.GaussianBlur(edge_blur))
    edge_mag = edge_mag.resize((cols, rows), Image.LANCZOS)
    edge_px = list(edge_mag.getdata())

    rgb = ImageOps.autocontrast(rgb, cutoff=1)
    small = rgb.resize((cols, rows), Image.LANCZOS)
    pix = list(small.getdata())

    cells = []
    for y in range(rows):
        row = []
        for x in range(cols):
            idx = y * cols + x
            r, g, b = (c / 255.0 for c in pix[idx])
            h, s, v = colorsys.rgb_to_hsv(r, g, b)
            is_edge = edge_px[idx] > edge_thresh
            if is_edge:
                v = max(v, edge_floor)
                s = max(s, 0.12)  # keep edge dots from reading as flat gray
            else:
                v = v ** shadow_gamma
            v = min(v, highlight_cap)
            if invert:
                v = highlight_cap - v
            s = min(1.0, s * saturation)
            r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
            rgb_out = (int(r2 * 255), int(g2 * 255), int(b2 * 255))
            row.append((rgb_out, v, is_edge))
        cells.append(row)
    return cells


def make_placeholder_image(path, size=(600, 700)):
    """Synthetic silhouette so the pipeline is testable before a real photo exists."""
    img = Image.new("L", size, color=10)
    px = img.load()
    w, h = size
    cx, cy = w * 0.5, h * 0.42
    for y in range(h):
        for x in range(w):
            dx, dy = (x - cx) / (w * 0.30), (y - cy) / (h * 0.34)
            d = dx * dx + dy * dy
            if d < 1.0:
                px[x, y] = int(255 * (1 - d * 0.6))
    for y in range(int(h * 0.62), h):
        for x in range(w):
            dx = (x - cx) / (w * 0.42)
            t = (y - h * 0.62) / (h * 0.38)
            if abs(dx) < 0.5 + t * 0.3:
                px[x, y] = int(200 * (1 - t * 0.5))
    img.save(path)


# ---- svg row helpers ------------------------------------------------------

def esc(s):
    return sax.escape(str(s))


def label_column(fields, min_dots=3):
    """Target character column for the value in a field list — the dot-leader
    fills from each label out to this column, so every value (labeled row or
    wrapped continuation line) starts flush in the same place regardless of
    how long that particular label or value happens to be."""
    longest = max((len(f.get("label", "")) for f in fields if f.get("label")), default=0)
    return longest + 1 + min_dots  # +1 for the space after the label


def field_row(label, value, x, y, label_col, font_size=13, label_color=GREEN,
              dot_color=DIM, value_color=WHITE):
    if not label and not value:
        return ""
    label_s = f"{label} " if label else ""
    dots_needed = max(2, label_col - len(label_s))
    dots = "." * dots_needed if label else " " * dots_needed
    return (
        f'<text x="{x}" y="{y}" font-family="ui-monospace, SFMono-Regular, Menlo, '
        f'Consolas, monospace" font-size="{font_size}" xml:space="preserve">'
        f'<tspan fill="{label_color}">{esc(label_s)}</tspan>'
        f'<tspan fill="{dot_color}">{esc(dots)}</tspan>'
        f'<tspan fill="{value_color}"> {esc(value)}</tspan>'
        f"</text>"
    )


def section_label(text, x, y, font_size=12):
    return (
        f'<text x="{x}" y="{y}" font-family="ui-monospace, SFMono-Regular, Menlo, '
        f'Consolas, monospace" font-size="{font_size}" letter-spacing="2" '
        f'fill="{DIM}">{esc(text)}</text>'
    )


def paw_print(cx, cy, scale=1.0, color=GREEN, blink=True):
    """Vector paw print (no emoji font dependency) — 4 toes + palm pad.
    Scale is baked directly into coordinates (not a transform) so it doesn't
    drag the absolute cx/cy position toward the SVG origin."""
    toes = [(-6, -6, 1.9), (-2, -8.5, 2.0), (2, -8.5, 2.0), (6, -6, 1.9)]
    circles = [
        f'<ellipse cx="{cx}" cy="{cy + 2 * scale}" rx="{5.4 * scale}" ry="{4 * scale}" fill="{color}"/>'
    ]
    for dx, dy, r in toes:
        circles.append(
            f'<circle cx="{cx + dx * scale}" cy="{cy + dy * scale}" r="{r * scale}" fill="{color}"/>'
        )
    anim = (
        f'<animate attributeName="opacity" values="1;1;0;0;1" dur="1.1s" '
        f'repeatCount="indefinite"/>' if blink else ""
    )
    return f'<g>{anim}' + "".join(circles) + "</g>"


def _slot_keyframes(index, n, slot_s, ramp_in_s, ramp_out_s, total_s):
    """Six (time, phase) keyframes for one item in an N-item cycle: rest, rest,
    ramp-in-complete, ramp-out-start, rest, rest. Reused for anything that
    should appear, hold, then disappear once per its own slot in a shared
    repeating timeline — command text width, cursor x, badge opacity all fit
    this shape. Boundary slots (first/last) produce a harmless zero-length
    duplicate keyframe rather than needing special-casing."""
    t0 = index * slot_s
    t1 = (index + 1) * slot_s
    times = [0, t0, t0 + ramp_in_s, t1 - ramp_out_s, t1, total_s]
    return [round(t / total_s, 6) for t in times]


def typewriter_command_line(prefix, commands, x, y, font_size=15, color=GREEN,
                             slot_s=3.2, type_s=0.55, erase_s=0.45):
    """A prompt prefix (static) followed by a command that cycles through
    `commands`, each one typing in, holding, and erasing before the next
    starts — with the paw-print cursor tracking the growing/shrinking text.
    Char width is approximated as font_size*0.6 (monospace), the same
    approximation used for the ASCII portrait grid elsewhere in this file."""
    char_w = font_size * 0.6
    prefix_w = len(prefix) * char_w
    text_x = x + prefix_w
    n = len(commands)
    total = n * slot_s
    cursor_gap = char_w  # paw print is drawn centered on its x, not flush-left,
    # so it needs a full char's width of breathing room past the last glyph
    # or its toes overlap/hide that character

    defs = []
    texts = [f'<text x="{x:.1f}" y="{y}" font-size="{font_size}" fill="{color}" '
             f'xml:space="preserve">{esc(prefix)}</text>']
    cursor_kt, cursor_vals = [], []

    for i, cmd in enumerate(commands):
        w = len(cmd) * char_w
        kt = _slot_keyframes(i, n, slot_s, type_s, erase_s, total)
        clip_id = f"cmdclip{i}"
        defs.append(
            f'<clipPath id="{clip_id}"><rect x="{text_x:.1f}" y="{y - font_size:.1f}" '
            f'width="0" height="{font_size * 1.4:.1f}">'
            f'<animate attributeName="width" keyTimes="{";".join(map(str, kt))}" '
            f'values="0;0;{w:.1f};{w:.1f};0;0" dur="{total:.2f}s" repeatCount="indefinite"/>'
            f'</rect></clipPath>'
        )
        texts.append(
            f'<text x="{text_x:.1f}" y="{y}" font-size="{font_size}" fill="{color}" '
            f'xml:space="preserve" clip-path="url(#{clip_id})">{esc(cmd)}</text>'
        )
        # Local 4-point chain for this command's slot, NOT `kt` (that spans
        # 0..1 of the *whole* cycle for one independent per-command clip
        # animation — concatenating several of those would jump backwards in
        # time at every slot boundary). Consecutive commands' rest-points
        # land on the same time and x, so they chain into one monotonic timeline.
        t0, t1 = i * slot_s, (i + 1) * slot_s
        cursor_kt += [t0, t0 + type_s, t1 - erase_s, t1]
        cursor_vals += [text_x, text_x + w + cursor_gap, text_x + w + cursor_gap, text_x]

    cursor_kt_frac = [round(t / total, 6) for t in cursor_kt]
    cursor = (
        f'<g transform="translate({cursor_vals[0]:.1f},0)">'
        f'<animateTransform attributeName="transform" type="translate" '
        f'keyTimes="{";".join(map(str, cursor_kt_frac))}" '
        f'values="{";".join(f"{v:.1f},0" for v in cursor_vals)}" '
        f'dur="{total:.2f}s" repeatCount="indefinite"/>'
        f'{paw_print(0, y - font_size * 0.3, scale=0.85, color=color, blink=True)}'
        f'</g>'
    )
    return "<defs>" + "".join(defs) + "</defs>", "".join(texts) + cursor


def cycling_badge(phrases, x, y, font_size=13, color=RED, slot_s=2.6, fade_s=0.25):
    """Status text that rotates through `phrases`, each cross-fading in/out
    within its own slot of a shared repeating timeline (same technique as
    `_slot_keyframes`, applied to opacity instead of width/position)."""
    n = len(phrases)
    total = n * slot_s
    texts = []
    for i, phrase in enumerate(phrases):
        kt = _slot_keyframes(i, n, slot_s, fade_s, fade_s, total)
        texts.append(
            f'<text text-anchor="end" font-size="{font_size}" fill="{color}" y="5" opacity="0">'
            f'{esc(phrase)}'
            f'<animate attributeName="opacity" keyTimes="{";".join(map(str, kt))}" '
            f'values="0;0;1;1;0;0" dur="{total:.2f}s" repeatCount="indefinite"/>'
            f'</text>'
        )
    return f'<g transform="translate({x},{y})">' + "".join(texts) + "</g>"


# ---- main svg build --------------------------------------------------------

def build_svg(cfg, halftone_cells):
    W, H = 1200, 660
    pad = 24
    header_h = 52
    inner_x0, inner_y0 = pad, pad
    inner_x1, inner_y1 = W - pad, H - pad

    left_x0 = inner_x0 + 24
    divider_x = inner_x0 + 500
    right_x0 = divider_x + 32
    body_y0 = inner_y0 + header_h + 30
    body_y1 = inner_y1 - 20

    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">'
    )
    card_w, card_h = inner_x1 - inner_x0, inner_y1 - inner_y0
    scan_h = 90
    parts.append(
        "<defs>"
        f'<linearGradient id="scanGrad" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{GREEN}" stop-opacity="0"/>'
        f'<stop offset="45%" stop-color="{GREEN}" stop-opacity="0.05"/>'
        f'<stop offset="50%" stop-color="{WHITE}" stop-opacity="0.5"/>'
        f'<stop offset="55%" stop-color="{GREEN}" stop-opacity="0.05"/>'
        f'<stop offset="100%" stop-color="{GREEN}" stop-opacity="0"/>'
        "</linearGradient>"
        f'<clipPath id="cardClip"><rect x="{inner_x0}" y="{inner_y0}" width="{card_w}" '
        f'height="{card_h}" rx="14"/></clipPath>'
        "</defs>"
    )
    parts.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    parts.append(
        f'<rect x="{inner_x0}" y="{inner_y0}" width="{card_w}" '
        f'height="{card_h}" rx="14" fill="#070c0a" stroke="{BORDER}" stroke-width="1.5"/>'
    )

    # header bar
    dot_y = inner_y0 + header_h / 2
    for i, c in enumerate([RED, YELLOW, GREENDOT]):
        parts.append(f'<circle cx="{inner_x0 + 30 + i * 24}" cy="{dot_y}" r="6" fill="{c}"/>')

    prefix = f"{cfg.get('handle', 'user@host')} {cfg.get('prompt', '~ %')} "
    commands = cfg.get("commands") or [cfg.get("command_line", "./profile.sh --live")]
    cmd_defs, cmd_markup = typewriter_command_line(
        prefix, commands, inner_x0 + 130, dot_y + 5, font_size=15, color=GREEN,
    )
    parts.append(cmd_defs)
    parts.append(cmd_markup)

    badges = cfg.get("status_badges") or [cfg.get("status_badge", "LIVE")]
    badge_x = inner_x1 - 24
    parts.append(cycling_badge(badges, badge_x, dot_y))
    parts.append(
        f'<g transform="translate({badge_x},{dot_y})">'
        f'<circle cx="12" cy="0" r="4" fill="{RED}">'
        f'<animate attributeName="opacity" values="1;0.15;1" dur="1.6s" repeatCount="indefinite"/>'
        f"</circle></g>"
    )

    parts.append(
        f'<line x1="{inner_x0}" y1="{inner_y0 + header_h}" x2="{inner_x1}" '
        f'y2="{inner_y0 + header_h}" stroke="{BORDER}" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{divider_x}" y1="{inner_y0 + header_h + 8}" x2="{divider_x}" '
        f'y2="{body_y1 + 8}" stroke="{BORDER}" stroke-width="1" opacity="0.6"/>'
    )

    # left panel: halftone portrait, rendered as vector dots (no font dependency),
    # contain-fit + centered inside the panel so cells stay square (no distortion).
    parts.append(section_label("VISUAL.MAP", left_x0, inner_y0 + header_h + 18))

    grid_rows = len(halftone_cells)
    grid_cols = len(halftone_cells[0]) if grid_rows else 0
    panel_w = (divider_x - 12) - left_x0
    panel_h = body_y1 - body_y0
    cell_size = min(panel_w / max(grid_cols, 1), panel_h / max(grid_rows, 1))
    grid_w = cell_size * grid_cols
    grid_h = cell_size * grid_rows
    off_x = left_x0 + (panel_w - grid_w) / 2
    off_y = body_y0 + (panel_h - grid_h) / 2
    max_r = cell_size * 0.5
    min_r = cell_size * 0.15   # edge dots still read as a thin outline
    min_visible = 0.11         # brightness below this, on non-edge cells, stays background

    dots = []
    for ry, row in enumerate(halftone_cells):
        for cx_, (rgb_fill, v, is_edge) in enumerate(row):
            if v < min_visible and not is_edge:
                continue
            r = max_r * math.sqrt(min(1.0, v))  # dot area ~ brightness
            if is_edge:
                r = max(r, min_r)
            cx = off_x + (cx_ + 0.5) * cell_size
            cy = off_y + (ry + 0.5) * cell_size
            dots.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.2f}" fill="rgb{rgb_fill}"/>')
    parts.append(f'<g>{"".join(dots)}</g>')

    # right panel: system info — one shared label column across both field
    # lists so every value (SYSTEM.INFO and CONTACT alike) starts flush.
    fields = cfg.get("fields", [])
    contact = cfg.get("contact", [])
    label_col = label_column(fields + contact)

    ry = inner_y0 + header_h + 18
    parts.append(section_label("SYSTEM.INFO", right_x0, ry))
    fy = ry + 30
    line_h = 22
    for f in fields:
        parts.append(field_row(f.get("label", ""), f.get("value", ""), right_x0, fy, label_col))
        fy += line_h

    fy += 10
    parts.append(section_label("- CONTACT", right_x0, fy))
    fy += 26
    for f in contact:
        parts.append(field_row(f.get("label", ""), f.get("value", ""), right_x0, fy, label_col))
        fy += line_h

    footer = cfg.get("footer_note", "")
    if footer:
        parts.append(
            f'<text x="{right_x0}" y="{body_y1 - 6}" font-size="12" fill="{DIM}">'
            f'&gt; {esc(footer)}</text>'
        )

    # scanner sweep: a soft horizontal bar cycling top-to-bottom over the whole
    # card, screen-blended so it brightens what's under it instead of hiding it.
    parts.append(
        f'<g clip-path="url(#cardClip)">'
        f'<rect x="{inner_x0}" y="{inner_y0 - scan_h}" width="{card_w}" height="{scan_h}" '
        f'fill="url(#scanGrad)" opacity="0.8" style="mix-blend-mode:screen">'
        f'<animateTransform attributeName="transform" type="translate" '
        f'from="0 0" to="0 {card_h + scan_h}" dur="4.5s" repeatCount="indefinite"/>'
        f"</rect></g>"
    )

    parts.append("</svg>")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=None, help="path to source photo")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="profile.svg")
    ap.add_argument("--cols", type=int, default=125)
    ap.add_argument("--invert", action="store_true", help="invert the brightness mapping")
    ap.add_argument("--crop", default="0,0,1,1",
                     help="left,top,right,bottom as fractions of the source image, e.g. 0.02,0,0.92,0.86 "
                          "to trim distracting background out of frame")
    ap.add_argument("--edge-floor", type=float, default=0.30,
                     help="minimum brightness for edge-only dots (keeps dark-on-dark subject edges visible)")
    ap.add_argument("--edge-thresh", type=int, default=45, help="color-edge sensitivity, 0-255")
    ap.add_argument("--edge-blur", type=float, default=2.2,
                     help="blur radius applied to the edge map before downscaling, to suppress background texture noise")
    ap.add_argument("--highlight-cap", type=float, default=0.92,
                     help="max brightness for any dot, so bright fur keeps shading instead of blowing out to flat white")
    ap.add_argument("--saturation", type=float, default=1.15, help="color saturation multiplier")
    ap.add_argument("--shadow-gamma", type=float, default=1.8,
                     help="how hard non-edge shadows/background are pushed down, keeping the background quiet")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    image_path = args.image
    if not image_path:
        image_path = "assets/_placeholder.png"
        make_placeholder_image(image_path)
        print(f"[info] no --image given, using synthetic placeholder: {image_path}")

    crop = tuple(float(x) for x in args.crop.split(","))
    img = Image.open(image_path)
    w, h = crop_frame(img, crop).size
    cols = args.cols
    rows = max(1, round(cols * (h / w)))

    cells = image_to_halftone_cells(
        image_path, cols, rows, invert=args.invert, crop=crop,
        edge_floor=args.edge_floor, edge_thresh=args.edge_thresh, edge_blur=args.edge_blur,
        highlight_cap=args.highlight_cap, saturation=args.saturation, shadow_gamma=args.shadow_gamma,
    )
    svg = build_svg(cfg, cells)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"[ok] wrote {args.out} ({cols}x{rows} halftone grid)")


if __name__ == "__main__":
    main()
