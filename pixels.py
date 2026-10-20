"""8x8 pixel art for the LaMetric display, encoded as (animated) GIFs.

LaMetric accepts icons as `data:image/gif;base64,...`. GIF is used for
everything, including static icons, so there is a single encoder.
"""

from __future__ import annotations

import base64
import struct
from typing import Dict, List, Sequence, Tuple

RGB = Tuple[int, int, int]
Frame = List[List[RGB]]  # [y][x]

SIZE = 8
BLACK: RGB = (0, 0, 0)

# herdr's status hues (catppuccin: idle green, working yellow, done teal,
# blocked red), saturated because pastels wash out to white on bare LEDs.
# Two animation steps per state; steady states repeat the color. Channels
# below ~90 do not light up on the Time v1, so "dim" stays above that.
STATE_COLORS: Dict[str, Tuple[RGB, RGB]] = {
    "working": ((255, 180, 0), (130, 90, 0)),
    "blocked": ((255, 20, 70), (140, 0, 40)),
    "done": ((0, 200, 170), (0, 200, 170)),
    "idle": ((40, 200, 40), (40, 200, 40)),
    "unknown": ((100, 100, 100), (100, 100, 100)),
}

# herdr draws idle as a ring (○) and every other state filled (●); in a 2x2
# block the "ring" is the block with its bottom-right pixel missing.
HOLLOW = {"idle"}

FRAME_DELAY_CS = 60  # centiseconds per animation frame

DISC = """
..####..
.######.
########
########
########
########
.######.
..####..
"""

RING = """
..####..
.#....#.
#......#
#......#
#......#
#......#
.#....#.
..####..
"""


def blank() -> Frame:
    return [[BLACK] * SIZE for _ in range(SIZE)]


def _offsets(count: int) -> List[int]:
    """2x2 blocks with a 1px gap fit 9 agents; up to 16 without gaps."""
    return [0, 3, 6] if count <= 9 else [0, 2, 4, 6]


def _block_mask(hollow: bool) -> List[Tuple[int, int]]:
    return [(0, 0), (1, 0), (0, 1)] if hollow else [(0, 0), (1, 0), (0, 1), (1, 1)]


def agent_grid(statuses: Sequence[str]) -> List[Frame]:
    """herdr-style status dots, one per agent, row-major. Max 16 agents."""
    if not statuses:
        return [blank(), blank()]
    if len(statuses) == 1:
        status = statuses[0]
        art = RING if status in HOLLOW else DISC
        return [from_ascii(art, {"#": c}) for c in STATE_COLORS.get(status, STATE_COLORS["unknown"])]

    frames = [blank(), blank()]
    offsets = _offsets(len(statuses))
    slots = [(x, y) for y in offsets for x in offsets]
    for status, (ox, oy) in zip(statuses, slots):
        colors = STATE_COLORS.get(status, STATE_COLORS["unknown"])
        for frame, color in zip(frames, colors):
            for dx, dy in _block_mask(status in HOLLOW):
                frame[oy + dy][ox + dx] = color
    return frames


def from_ascii(art: str, palette: Dict[str, RGB]) -> Frame:
    rows = [r for r in art.strip("\n").splitlines()]
    assert len(rows) == SIZE and all(len(r) == SIZE for r in rows), art
    return [[palette.get(ch, BLACK) for ch in row] for row in rows]


ALERT_ART = {
    "blocked": """
...##...
...##...
...##...
...##...
...##...
........
...##...
...##...
""",
    "done": """
........
.......#
......##
#....##.
##..##..
.####...
..##....
........
""",
}


def alert_icon(status: str) -> List[Frame]:
    bright, dim = STATE_COLORS[status]
    art = ALERT_ART[status]
    return [from_ascii(art, {"#": bright}), from_ascii(art, {"#": dim})]


# --- GIF encoding -----------------------------------------------------------


def _palette(frames: Sequence[Frame]) -> List[RGB]:
    colors: List[RGB] = [BLACK]
    for frame in frames:
        for row in frame:
            for c in row:
                if c not in colors:
                    colors.append(c)
    if len(colors) > 16:
        raise ValueError("icon uses more than 16 colors")
    return colors + [BLACK] * (16 - len(colors))


def _lzw_uncompressed(indices: Sequence[int], min_code_size: int) -> bytes:
    """Valid GIF LZW stream without compression.

    Emits a clear code before the dictionary would grow past the initial code
    width, so every code stays `min_code_size + 1` bits wide.
    """
    clear = 1 << min_code_size
    end = clear + 1
    width = min_code_size + 1
    run = (1 << min_code_size) - 2  # literals per clear, safe for all decoders

    codes: List[int] = []
    for i, idx in enumerate(indices):
        if i % run == 0:
            codes.append(clear)
        codes.append(idx)
    codes.append(end)

    out = bytearray()
    acc = bits = 0
    for code in codes:
        acc |= code << bits
        bits += width
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    if bits:
        out.append(acc & 0xFF)
    return bytes(out)


def _sub_blocks(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i : i + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)
    return bytes(out)


def encode_gif(frames: Sequence[Frame], delay_cs: int = FRAME_DELAY_CS) -> bytes:
    palette = _palette(frames)
    lookup = {c: i for i, c in enumerate(palette)}
    min_code_size = 4  # 16-entry table

    width, height = len(frames[0][0]), len(frames[0])
    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", width, height, 0b1111_0011, 0, 0)
    for r, g, b in palette:
        out += bytes((r, g, b))

    animated = len(frames) > 1 and frames[0] != frames[1]
    if animated:
        out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    else:
        frames = frames[:1]

    for frame in frames:
        if animated:
            out += b"\x21\xf9\x04\x04" + struct.pack("<H", delay_cs) + b"\x00\x00"
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, width, height, 0)
        indices = [lookup[c] for row in frame for c in row]
        out.append(min_code_size)
        out += _sub_blocks(_lzw_uncompressed(indices, min_code_size))

    out.append(0x3B)
    return bytes(out)


def data_uri(frames: Sequence[Frame]) -> str:
    return "data:image/gif;base64," + base64.b64encode(encode_gif(frames)).decode()


def ansi(frames: Sequence[Frame]) -> str:
    """Render frames side by side as truecolor terminal blocks (for preview)."""
    lines = []
    for y in range(SIZE):
        parts = []
        for frame in frames:
            parts.append(
                "".join(f"\x1b[48;2;{r};{g};{b}m  " for r, g, b in frame[y]) + "\x1b[0m"
            )
        lines.append("   ".join(parts))
    return "\n".join(lines)
