#!/usr/bin/env python3
"""
<summary>
Draws the README banner (docs/screenshots/banner.png, 1280 by 320) and the
GitHub social preview (docs/screenshots/social.png, 1280 by 640) for Engage.
</summary>
<remarks>
Everything is drawn from plain shapes: a flat navy background, the name and
tagline on the left, and three rounded tiles on the right that sketch what the
add-on really shows. The cyan tile is the slim countdown banner, the amber tile
the Manage slots list, and the violet tile the Full Monty panel with a plain
shape where a poster would sit. No library art, poster or fanart is used, so
the pictures are fit for anyone.

The cyan is the blue of the add-on's own icon (resources/icon.png).

Needs Pillow and the Noto Sans fonts (DejaVu Sans is the fallback). This is a
working tool for the repository only: it is not part of the add-on, never runs
inside Kodi, and the Dist zip does not carry it.

Drawing happens at twice the final size and is scaled down once, which gives
smooth edges on the rounded shapes without an antialiasing library.

Usage:
    make_banner.py              write both images into docs/screenshots/
    make_banner.py <folder>     write them into another folder instead
</remarks>
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

NAME = 'Engage'
TAGLINE = 'Your weekly watch schedule, inside Kodi.'

BACKGROUND = (13, 22, 43)
PANEL = (8, 14, 29)
NAME_COLOUR = (240, 244, 250)
TAGLINE_COLOUR = (148, 163, 184)
TEXT_BAR = (196, 206, 222)
DIM_BAR = (74, 88, 116)
CYAN = (24, 181, 255)
AMBER = (245, 179, 49)
VIOLET = (167, 139, 250)

SCALE = 2

BOLD_FONTS = ('/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
REGULAR_FONTS = ('/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')


def load_font(candidates, size):
    """
    <summary>
    The first font in the list that exists, at the given pixel size.
    </summary>
    <param name="candidates">Font file paths, most preferred first.</param>
    <param name="size">Size in final image pixels; the drawing scale is applied here.</param>
    <returns>A Pillow TrueType font.</returns>
    <exception cref="SystemExit">None of the fonts is installed.</exception>
    """
    for path in candidates:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size * SCALE)
    sys.exit('No usable font found; install fonts-noto-core or fonts-dejavu-core')


def mix(colour, amount):
    """
    <summary>
    Blends an accent colour into the tile panel colour.
    </summary>
    <param name="colour">The accent as an RGB tuple.</param>
    <param name="amount">How much accent, from 0 (all panel) to 1 (all accent).</param>
    <returns>The blended RGB tuple.</returns>
    <remarks>Used instead of alpha so the image can be saved as plain RGB.</remarks>
    """
    return tuple(int(p + (c - p) * amount) for p, c in zip(PANEL, colour))


class Tile(object):
    """
    <summary>
    One square tile: a rounded dark panel with drawing helpers that take
    coordinates in a 0 to 100 grid across the tile.
    </summary>
    <remarks>
    The grid lets each sketch be written once and drawn at any tile size, so
    the banner and the social preview share the same drawings.
    </remarks>
    """

    def __init__(self, draw, x, y, size, accent):
        """
        <summary>Draws the panel and keeps what the helpers need.</summary>
        <param name="draw">The ImageDraw to paint on, already at drawing scale.</param>
        <param name="x">Left edge in final image pixels.</param>
        <param name="y">Top edge in final image pixels.</param>
        <param name="size">Width and height in final image pixels.</param>
        <param name="accent">The tile's one accent colour.</param>
        """
        self.draw = draw
        self.x = x * SCALE
        self.y = y * SCALE
        self.unit = size * SCALE / 100.0
        self.accent = accent
        self.box(0, 0, 100, 100, PANEL, radius=12, outline=mix(accent, 0.35))

    def point(self, gx, gy):
        """
        <summary>Converts a grid position to drawing pixels.</summary>
        <param name="gx">Across, 0 to 100.</param>
        <param name="gy">Down, 0 to 100.</param>
        <returns>An (x, y) tuple in drawing pixels.</returns>
        """
        return (self.x + gx * self.unit, self.y + gy * self.unit)

    def box(self, gx, gy, gw, gh, fill, radius=3, outline=None):
        """
        <summary>A rounded rectangle on the grid.</summary>
        <param name="gx">Left, in grid units.</param>
        <param name="gy">Top, in grid units.</param>
        <param name="gw">Width, in grid units.</param>
        <param name="gh">Height, in grid units.</param>
        <param name="fill">Fill colour, or None for an outline only.</param>
        <param name="radius">Corner radius in grid units.</param>
        <param name="outline">Optional outline colour, drawn one grid unit wide.</param>
        """
        left, top = self.point(gx, gy)
        right, bottom = self.point(gx + gw, gy + gh)
        width = max(1, int(self.unit)) if outline else 0
        self.draw.rounded_rectangle((left, top, right, bottom), radius=radius * self.unit,
                                    fill=fill, outline=outline, width=width)

    def text(self, gx, gy, words, size, fill):
        """
        <summary>Short text on the grid, its top left at the given point.</summary>
        <param name="gx">Left, in grid units.</param>
        <param name="gy">Top, in grid units.</param>
        <param name="words">The text; keep it to a few characters, it is decoration.</param>
        <param name="size">Font size in grid units.</param>
        <param name="fill">Text colour.</param>
        """
        font = ImageFont.truetype(BOLD_FONTS[0] if os.path.isfile(BOLD_FONTS[0]) else BOLD_FONTS[1],
                                  max(8, int(size * self.unit)))
        self.draw.text(self.point(gx, gy), words, font=font, fill=fill)


def countdown_tile(tile):
    """
    <summary>
    The slim countdown banner: a poster block, a title and episode line, the
    countdown, a progress bar running down, and three button pills.
    </summary>
    <param name="tile">A Tile drawn in cyan.</param>
    """
    tile.box(8, 22, 84, 56, mix(tile.accent, 0.12), radius=5)
    tile.box(13, 28, 18, 26, mix(tile.accent, 0.55), radius=2)
    tile.box(36, 30, 34, 5, TEXT_BAR, radius=2)
    tile.box(36, 39, 24, 4, DIM_BAR, radius=2)
    tile.text(36, 45, '0:45', 10, tile.accent)
    tile.box(13, 59, 74, 3, DIM_BAR, radius=1.5)
    tile.box(13, 59, 50, 3, tile.accent, radius=1.5)
    for index in range(3):
        fill = tile.accent if index == 0 else DIM_BAR
        tile.box(13 + index * 26, 66, 22, 7, fill, radius=3.5)


def slots_tile(tile):
    """
    <summary>
    The Manage slots list: five rows, each with a day block, a title bar and
    a time bar, with the first row focused.
    </summary>
    <param name="tile">A Tile drawn in amber.</param>
    """
    for row in range(5):
        top = 12 + row * 16
        if row == 0:
            tile.box(7, top - 2, 86, 14, mix(tile.accent, 0.22), radius=3)
        tile.box(11, top + 1, 10, 8, tile.accent if row == 0 else mix(tile.accent, 0.5), radius=2)
        tile.box(26, top + 1, 38 - row * 3, 3.5, TEXT_BAR if row == 0 else DIM_BAR, radius=1.5)
        tile.box(26, top + 6, 20, 2.5, DIM_BAR, radius=1.2)
        tile.box(74, top + 2, 14, 5, tile.accent if row == 0 else DIM_BAR, radius=2.5)


def full_monty_tile(tile):
    """
    <summary>
    The Full Monty panel: a tall poster block holding a plain circle and
    hills, detail lines beside it, and the buttons underneath.
    </summary>
    <param name="tile">A Tile drawn in violet.</param>
    <remarks>The poster block is a plain shape on purpose, never real art.</remarks>
    """
    tile.box(9, 12, 34, 50, mix(tile.accent, 0.3), radius=3)
    cx, cy = tile.point(26, 26)
    r = 6 * tile.unit
    tile.draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tile.accent)
    tile.draw.polygon([tile.point(9.5, 61.5), tile.point(22, 42), tile.point(31, 52),
                       tile.point(36, 46), tile.point(42.5, 56), tile.point(42.5, 61.5)],
                      fill=mix(tile.accent, 0.7))
    tile.box(49, 14, 40, 5, TEXT_BAR, radius=2)
    tile.box(49, 23, 26, 3.5, tile.accent, radius=1.5)
    for line in range(5):
        tile.box(49, 31 + line * 6.5, 42 - (line % 2) * 8, 3, DIM_BAR, radius=1.5)
    for index in range(3):
        fill = tile.accent if index == 0 else DIM_BAR
        tile.box(9 + index * 28, 72, 24, 9, fill, radius=4.5)


def render(width, height, name_size, tagline_size, tiles):
    """
    <summary>
    Draws one image: background, name and tagline, then the tiles.
    </summary>
    <param name="width">Final width in pixels.</param>
    <param name="height">Final height in pixels.</param>
    <param name="name_size">Name font size in final pixels.</param>
    <param name="tagline_size">Tagline font size in final pixels.</param>
    <param name="tiles">(x, y, size, accent, sketch) for each tile, in final pixels.</param>
    <returns>The finished RGB image at final size.</returns>
    <remarks>
    The text block is centred vertically on the image using the fonts' real
    ink boxes, so a change of font does not push it off centre.
    </remarks>
    """
    image = Image.new('RGB', (width * SCALE, height * SCALE), BACKGROUND)
    draw = ImageDraw.Draw(image)
    name_font = load_font(BOLD_FONTS, name_size)
    tag_font = load_font(REGULAR_FONTS, tagline_size)
    name_box = draw.textbbox((0, 0), NAME, font=name_font)
    tag_box = draw.textbbox((0, 0), TAGLINE, font=tag_font)
    gap = int(tagline_size * 0.7) * SCALE
    block = (name_box[3] - name_box[1]) + gap + (tag_box[3] - tag_box[1])
    top = (height * SCALE - block) // 2
    left = 80 * SCALE
    draw.text((left - name_box[0], top - name_box[1]), NAME, font=name_font, fill=NAME_COLOUR)
    tag_top = top + (name_box[3] - name_box[1]) + gap
    draw.text((left - tag_box[0], tag_top - tag_box[1]), TAGLINE, font=tag_font, fill=TAGLINE_COLOUR)
    for x, y, size, accent, sketch in tiles:
        sketch(Tile(draw, x, y, size, accent))
    return image.resize((width, height), Image.LANCZOS)


def save(image, path):
    """
    <summary>Writes a PNG with no text or time chunks.</summary>
    <param name="image">The finished image.</param>
    <param name="path">Where to write it.</param>
    <remarks>
    Quantised to 256 colours: the art is flat, so this costs nothing visible
    and keeps the banner well under 200 KB.
    </remarks>
    """
    image.quantize(256, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)
    print('{} {}x{} {:.1f} KB'.format(path, image.width, image.height, os.path.getsize(path) / 1024.0))


def main(argv):
    """
    <summary>Builds both images into the chosen folder.</summary>
    <param name="argv">Command line; an optional folder follows the script name.</param>
    <returns>0 on success.</returns>
    """
    here = os.path.dirname(os.path.abspath(__file__))
    out = argv[1] if len(argv) > 1 else os.path.join(here, '..', 'docs', 'screenshots')
    out = os.path.abspath(out)
    if not os.path.isdir(out):
        os.makedirs(out)

    size, step = 168, 186
    first = 1280 - 72 - (size + step * 2)
    banner = render(1280, 320, 92, 28, [
        (first, 76, size, CYAN, countdown_tile),
        (first + step, 76, size, AMBER, slots_tile),
        (first + step * 2, 76, size, VIOLET, full_monty_tile),
    ])
    save(banner, os.path.join(out, 'banner.png'))

    size = 224
    social = render(1280, 640, 128, 30, [
        (760, 84, size, CYAN, countdown_tile),
        (760 + 244, 84, size, AMBER, slots_tile),
        (760 + 122, 84 + 248, size, VIOLET, full_monty_tile),
    ])
    save(social, os.path.join(out, 'social.png'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
