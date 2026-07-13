"""StarryBackground — Mimo-code 风格星空背景。"""

from __future__ import annotations

import random
from textual.widget import Widget

# Mimo-code 星星字符
STAR_CHARS = ["✦", "✧", "✦", "✧", "✦", "✧", "✦", " "]
HOT_CHAR = "✶"
HOT_THRESHOLD = 0.88
DENSITY = 0.004
TWINKLE_INTERVAL = 0.2


class StarryBackground(Widget):
    """Mimo-code 风格星空背景。"""

    CSS = """
    StarryBackground {
        layer: background;
        width: 100%;
        height: 100%;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._grid: list[list[str]] = []
        self._brightness: list[list[float]] = []

    def on_mount(self) -> None:
        self._generate_field()
        self.set_interval(TWINKLE_INTERVAL, self._twinkle)

    def _generate_field(self) -> None:
        w = self.size.width if self.size else 80
        h = self.size.height if self.size else 24
        self._grid = []
        self._brightness = []
        for _ in range(h):
            row_chars = []
            row_bright = []
            for _ in range(w):
                if random.random() < DENSITY:
                    idx = random.randint(0, len(STAR_CHARS) - 2)
                    row_chars.append(str(idx))
                    row_bright.append(0.15 + random.random() * 0.4)
                else:
                    row_chars.append(" ")
                    row_bright.append(0)
            self._grid.append(row_chars)
            self._brightness.append(row_bright)

    def _twinkle(self) -> None:
        w = self.size.width if self.size else 80
        h = self.size.height if self.size else 24
        count = max(1, int(w * h * 0.008))
        for _ in range(count):
            y = random.randint(0, h - 1)
            x = random.randint(0, w - 1)
            if y < len(self._grid) and x < len(self._grid[y]):
                if self._grid[y][x] and self._grid[y][x] != " ":
                    r = random.random()
                    if r < 0.12:
                        self._brightness[y][x] = 0.92 + random.random() * 0.08
                    elif r < 0.8:
                        self._brightness[y][x] = 0.7 + random.random() * 0.22
                    else:
                        self._brightness[y][x] = 0.05 + random.random() * 0.2
        self.refresh()

    def render(self) -> str:
        w = self.size.width if self.size else 80
        h = self.size.height if self.size else 24
        lines = []
        for y in range(min(h, len(self._grid))):
            row = []
            for x in range(min(w, len(self._grid[y]))):
                idx_str = self._grid[y][x]
                if idx_str == " ":
                    row.append(" ")
                else:
                    idx = int(idx_str)
                    char = STAR_CHARS[idx] if idx < len(STAR_CHARS) else " "
                    bright = self._brightness[y][x]
                    if bright >= HOT_THRESHOLD and char != " ":
                        char = HOT_CHAR
                    row.append(char)
            lines.append("".join(row))
        return "\n".join(lines)
