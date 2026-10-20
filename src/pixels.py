"""8x8 pixel art for the LaMetric Time, encoded as (animated) GIF data URIs.

The Time v1 display is "mixed": the 8x8 icon area is RGB, the rest white-only.
Everything here draws into the icon area.
"""

from __future__ import annotations

import base64
import struct
from typing import Sequence

RGB = tuple[int, int, int]
Frame = list[list[RGB]]  # [y][x]

SIZE = 8
BLACK: RGB = (0, 0, 0)
FRAME_DELAY_CS = 60  # per animation frame, in centiseconds

# herdr's status hues (catppuccin: idle green, working yellow, done teal,
# blocked red), saturated because pastels wash out to white on bare LEDs.
# Two animation steps per state; steady states repeat the color. Channel
# values below ~90 do not light up, so the dim steps stay above that.
STATE_COLORS: dict[str, tuple[RGB, RGB]] = {
    "working": ((255, 180, 0), (130, 90, 0)),
    "blocked": ((255, 20, 70), (140, 0, 40)),
    "done": ((0, 200, 170), (0, 200, 170)),
    "idle": ((40, 200, 40), (40, 200, 40)),
    "unknown": ((100, 100, 100), (100, 100, 100)),
}

# herdr draws idle as a ring (○) and every other state filled (●).
HOLLOW = {"idle"}

ART = {
    "disc": """
..####..
.######.
########
########
########
########
.######.
..####..
""",
    "ring": """
..####..
.#....#.
#......#
#......#
#......#
#......#
.#....#.
..####..
""",
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


# --- drawing --------------------------------------------------------------


def blank() -> Frame:
    return [[BLACK] * SIZE for _ in range(SIZE)]


def from_art(name: str, color: RGB) -> Frame:
    rows = ART[name].strip("\n").splitlines()
    return [[color if ch == "#" else BLACK for ch in row] for row in rows]


def colors(status: str) -> tuple[RGB, RGB]:
    return STATE_COLORS.get(status, STATE_COLORS["unknown"])


def agent_grid(statuses: Sequence[str]) -> list[Frame]:
    """One herdr-style status dot per agent.

    A single agent gets a full-size dot or ring. Otherwise each agent is a 2x2
    block (idle: bottom-right pixel missing), row-major: 9 fit with 1px gaps,
    16 without.
    """
    if len(statuses) == 1:
        shape = "ring" if statuses[0] in HOLLOW else "disc"
        return [from_art(shape, c) for c in colors(statuses[0])]

    frames = [blank(), blank()]
    offsets = [0, 3, 6] if len(statuses) <= 9 else [0, 2, 4, 6]
    slots = [(x, y) for y in offsets for x in offsets]
    for status, (ox, oy) in zip(statuses, slots):
        cells = [(0, 0), (1, 0), (0, 1)] + ([] if status in HOLLOW else [(1, 1)])
        for frame, color in zip(frames, colors(status)):
            for dx, dy in cells:
                frame[oy + dy][ox + dx] = color
    return frames


def alert_icon(status: str) -> list[Frame]:
    return [from_art(status, c) for c in colors(status)]


# --- public renderers -----------------------------------------------------


def clock_icon(statuses: Sequence[str]) -> str:
    return data_uri(agent_grid(statuses[:16]))


def alert_frames(status: str, text: str) -> list[dict[str, str]]:
    return [{"icon": data_uri(alert_icon(status)), "text": text}]


def ansi(frames: Sequence[Frame]) -> str:
    """Frames side by side as truecolor terminal blocks, for previews."""
    return "\n".join(
        "   ".join(
            "".join(f"\x1b[48;2;{r};{g};{b}m  " for r, g, b in frame[y]) + "\x1b[0m"
            for frame in frames
        )
        for y in range(SIZE)
    )


# --- GIF encoding ---------------------------------------------------------


def data_uri(frames: Sequence[Frame]) -> str:
    return "data:image/gif;base64," + base64.b64encode(encode_gif(frames)).decode()


def encode_gif(frames: Sequence[Frame], delay_cs: int = FRAME_DELAY_CS) -> bytes:
    """GIF89a with a 16-color global palette; animated and looping when the
    frames differ. Frames may be any size, e.g. 37x8 for the full display."""
    palette = _palette(frames)
    lookup = {c: i for i, c in enumerate(palette)}
    min_code_size = 4  # 16-entry table
    width, height = len(frames[0][0]), len(frames[0])

    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", width, height, 0b1111_0011, 0, 0)
    for rgb in palette:
        out += bytes(rgb)

    animated = len(frames) > 1 and any(f != frames[0] for f in frames[1:])
    if animated:
        out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop forever
    else:
        frames = frames[:1]

    for frame in frames:
        if animated:
            out += b"\x21\xf9\x04\x04" + struct.pack("<H", delay_cs) + b"\x00\x00"
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, width, height, 0)
        out.append(min_code_size)
        out += _sub_blocks(_lzw_uncompressed([lookup[c] for row in frame for c in row], min_code_size))

    out.append(0x3B)
    return bytes(out)


def _palette(frames: Sequence[Frame]) -> list[RGB]:
    found: list[RGB] = [BLACK]
    for frame in frames:
        for row in frame:
            for c in row:
                if c not in found:
                    found.append(c)
    if len(found) > 16:
        raise ValueError("icon uses more than 16 colors")
    return found + [BLACK] * (16 - len(found))


def _lzw_uncompressed(indices: Sequence[int], min_code_size: int) -> bytes:
    """A valid GIF LZW stream without compression: a clear code before the
    dictionary would grow, so every code keeps the initial width."""
    clear = 1 << min_code_size
    width = min_code_size + 1
    run = clear - 2  # literals per clear code; safe for all decoders

    codes: list[int] = []
    for i, idx in enumerate(indices):
        if i % run == 0:
            codes.append(clear)
        codes.append(idx)
    codes.append(clear + 1)  # end of information

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
