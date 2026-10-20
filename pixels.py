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

# Two brightness steps per state: (bright, dim). Steady states use the same
# color twice so every icon animates over the same two frames.
STATE_COLORS: Dict[str, Tuple[RGB, RGB]] = {
    "working": ((0, 170, 255), (0, 50, 90)),
    "blocked": ((255, 140, 0), (70, 30, 0)),
    "done": ((0, 210, 70), (0, 210, 70)),
    "idle": ((28, 28, 28), (28, 28, 28)),
    "unknown": ((110, 0, 150), (110, 0, 150)),
}

FRAME_DELAY_CS = 60  # centiseconds per animation frame


def blank() -> Frame:
    return [[BLACK] * SIZE for _ in range(SIZE)]


def _cells(count: int) -> Tuple[int, List[int]]:
    """Block size and offsets for a grid that fits `count` agents."""
    if count <= 4:
        return 3, [0, 5]
    if count <= 9:
        return 2, [0, 3, 6]
    return 2, [0, 2, 4, 6]


def agent_grid(statuses: Sequence[str]) -> List[Frame]:
    """One block per agent, row-major, colored by status. Max 16 agents."""
    frames = [blank(), blank()]
    if not statuses:
        frames[0][3][3] = frames[1][3][3] = STATE_COLORS["idle"][0]
        return frames
    block, offsets = _cells(len(statuses))
    slots = [(x, y) for y in offsets for x in offsets]
    for status, (ox, oy) in zip(statuses, slots):
        bright, dim = STATE_COLORS.get(status, STATE_COLORS["unknown"])
        for i, color in enumerate((bright, dim)):
            for dy in range(block):
                for dx in range(block):
                    frames[i][oy + dy][ox + dx] = color
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

    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", SIZE, SIZE, 0b1111_0011, 0, 0)
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
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, SIZE, SIZE, 0)
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
