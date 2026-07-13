"""Logo — Mimo-code 风格 Logo。"""

from __future__ import annotations

from textual.widgets import Static

# Mimo-code logoThin — 纤细半块
LOGO_THIN = {
    "left": [
        "                  ",
        "                  ",
        "█▀▄▀█ █ █▀▄▀█ █▀▀█",
        "█ ▀ █ █ █ ▀ █ █  █",
        "▀   ▀ ▀ ▀   ▀ ▀▀▀▀",
    ],
    "right": [
        "              chips",
        "                    ",
        "  █▀▀ █▀▀█ █▀▀▄ █▀▀▀",
        "  █   █  █ █  █ █▀▀ ",
        "  ▀▀▀ ▀▀▀▀ ▀▀▀  ▀▀▀▀",
    ],
}


class Logo(Static):
    """Mimo-code 风格 Logo。"""

    CSS = """
    Logo {
        height: auto;
        color: #A0A0A0;
        text-style: bold;
    }
    """

    def __init__(self, **kwargs) -> None:
        # 组合左右两部分
        lines = []
        for i in range(len(LOGO_THIN["left"])):
            left = LOGO_THIN["left"][i]
            right = LOGO_THIN["right"][i] if i < len(LOGO_THIN["right"]) else ""
            lines.append(f"{left}{right}")
        content = "\n".join(lines)
        super().__init__(content, **kwargs)
