"""Generates icon.ico (a chip with an upgrade arrow) without third-party libraries."""
import struct
import sys
import zlib
from pathlib import Path

BG = (31, 111, 235)       # blue
CHIP = (255, 255, 255)
ACCENT = (31, 111, 235)


def render(n: int) -> bytes:
    """RGBA pixels, 4x supersampled for smooth edges."""
    ss = 4
    big = n * ss
    px = bytearray(big * big * 4)

    def inside_round(x, y, r, lo, hi):
        if not (lo <= x < hi and lo <= y < hi):
            return False
        cx = min(max(x, lo + r), hi - 1 - r)
        cy = min(max(y, lo + r), hi - 1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    for y in range(big):
        for x in range(big):
            u, v = x / big, y / big
            col = None
            if inside_round(x, y, int(big * 0.2), 0, big):
                col = BG
                # chip body
                if inside_round(x, y, int(big * 0.05), int(big * 0.26), int(big * 0.74)):
                    col = CHIP
                    # upgrade arrow inside the chip
                    ax, ay = u - 0.5, v - 0.5
                    head = -0.20 <= ay <= -0.02 and abs(ax) <= (ay + 0.20) * 0.95
                    shaft = -0.02 <= ay <= 0.2 and abs(ax) <= 0.06
                    if head or shaft:
                        col = ACCENT
                # chip pins
                else:
                    for k in range(3):
                        c = 0.38 + k * 0.12
                        if (0.18 <= v < 0.26 or 0.74 <= v < 0.82) and abs(u - c) < 0.025:
                            col = CHIP
                        if (0.18 <= u < 0.26 or 0.74 <= u < 0.82) and abs(v - c) < 0.025:
                            col = CHIP
            i = (y * big + x) * 4
            if col:
                px[i:i + 4] = bytes((*col, 255))
    out = bytearray()
    for y in range(n):
        for x in range(n):
            r = g = b = a = 0
            for dy in range(ss):
                for dx in range(ss):
                    i = ((y * ss + dy) * big + x * ss + dx) * 4
                    pa = px[i + 3]
                    r += px[i] * pa; g += px[i + 1] * pa; b += px[i + 2] * pa; a += pa
            if a:
                out += bytes((r // a, g // a, b // a, a // (ss * ss)))
            else:
                out += b"\0\0\0\0"
    return bytes(out)


def png(n: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\0" + rgba[y * n * 4:(y + 1) * n * 4] for y in range(n))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main(dest: Path):
    sizes = [16, 32, 48, 256]
    images = [png(s, render(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, img in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    dest.write_bytes(head + entries + b"".join(images))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "icon.ico")
