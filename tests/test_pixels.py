import base64
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pixels  # noqa: E402

BLACK = pixels.BLACK


def decode_gif(data: bytes):
    """Just enough of a GIF decoder to read back what encode_gif writes."""
    assert data[:6] == b"GIF89a" and data[-1] == 0x3B
    width, height, packed = struct.unpack("<HHB", data[6:11])
    size = 2 ** ((packed & 0b111) + 1)
    palette = [tuple(data[13 + 3 * i: 16 + 3 * i]) for i in range(size)]
    pos = 13 + 3 * size
    frames = []
    while data[pos] != 0x3B:
        if data[pos] == 0x21:  # extension: skip its sub-blocks
            pos += 2
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
            continue
        assert data[pos] == 0x2C
        pos += 10
        min_code_size = data[pos]
        pos += 1
        stream = b""
        while data[pos]:
            stream += data[pos + 1: pos + 1 + data[pos]]
            pos += data[pos] + 1
        pos += 1
        frames.append(_lzw_decode(stream, min_code_size, palette, width, height))
    return width, height, frames


def _lzw_decode(stream, min_code_size, palette, width, height):
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    bits = int.from_bytes(stream, "little")
    pos, code_size = 0, min_code_size + 1
    table = {}
    out, prev = [], None
    while True:
        code = (bits >> pos) & ((1 << code_size) - 1)
        pos += code_size
        if code == clear:
            table = {i: [i] for i in range(clear)}
            code_size, prev = min_code_size + 1, None
            continue
        if code == end:
            break
        entry = table[code] if code in table else table[prev] + table[prev][:1]
        if prev is not None:
            table[len(table) + 2] = table[prev] + entry[:1]
            if len(table) + 2 == 1 << code_size and code_size < 12:
                code_size += 1
        out += entry
        prev = code
    return [[palette[i] for i in out[y * width:(y + 1) * width]] for y in range(height)]


class GifTest(unittest.TestCase):
    def test_round_trip_static(self):
        frame = pixels.agent_grid(["done", "idle", "done"])[0]
        _, _, frames = decode_gif(pixels.encode_gif([frame]))
        self.assertEqual(frames, [frame])

    def test_round_trip_animated(self):
        frames = pixels.agent_grid(["working", "blocked"])
        width, height, decoded = decode_gif(pixels.encode_gif(frames))
        self.assertEqual((width, height), (8, 8))
        self.assertEqual(decoded, frames)

    def test_full_display_size(self):
        frame = [[(255, 255, 255)] * 37 for _ in range(8)]
        width, height, decoded = decode_gif(pixels.encode_gif([frame]))
        self.assertEqual((width, height, decoded), (37, 8, [frame]))

    def test_data_uri(self):
        uri = pixels.clock_icon(["idle"])
        self.assertTrue(uri.startswith("data:image/gif;base64,"))
        base64.b64decode(uri.split(",", 1)[1], validate=True)

    def test_too_many_colors(self):
        frame = [[(i, i, i) for i in range(1, 18)]]
        with self.assertRaises(ValueError):
            pixels.encode_gif([frame])


class GridTest(unittest.TestCase):
    def lit(self, frame):
        return {(x, y) for y, row in enumerate(frame) for x, c in enumerate(row) if c != BLACK}

    def test_empty(self):
        self.assertEqual([self.lit(f) for f in pixels.agent_grid([])], [set(), set()])

    def test_single_agent_is_a_ring_or_disc(self):
        ring = self.lit(pixels.agent_grid(["idle"])[0])
        disc = self.lit(pixels.agent_grid(["done"])[0])
        self.assertNotIn((3, 3), ring)
        self.assertIn((3, 3), disc)
        self.assertLess(ring, disc)

    def test_blocks_per_agent(self):
        frame = pixels.agent_grid(["done"] * 9)[0]
        self.assertEqual(len(self.lit(frame)), 9 * 4)
        self.assertEqual(frame[0][2], BLACK)  # 1px gap between blocks

    def test_idle_block_misses_one_pixel(self):
        lit = self.lit(pixels.agent_grid(["idle", "done"])[0])
        self.assertEqual(lit, {(0, 0), (1, 0), (0, 1), (3, 0), (4, 0), (3, 1), (4, 1)})

    def test_more_than_nine_agents_pack_without_gaps(self):
        lit = self.lit(pixels.agent_grid(["done"] * 16)[0])
        self.assertEqual(len(lit), 64)

    def test_blocked_pulses(self):
        frames = pixels.agent_grid(["blocked", "idle"])
        self.assertNotEqual(frames[0][0][0], frames[1][0][0])
        self.assertNotEqual(frames[1][0][0], BLACK)  # dims, never off


if __name__ == "__main__":
    unittest.main()
