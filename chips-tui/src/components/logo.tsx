import { Component } from "solid-js"

// Mimo-code logoThin — 纤细半块
const LOGO_THIN = {
  left: [
    "                  ",
    "                  ",
    "█▀▄▀█ █ █▀▄▀█ █▀▀█",
    "█ ▀ █ █ █ ▀ █ █  █",
    "▀   ▀ ▀ ▀   ▀ ▀▀▀▀",
  ],
  right: [
    "              chips",
    "                    ",
    "  █▀▀ █▀▀█ █▀▀▄ █▀▀▀",
    "  █   █  █ █  █ █▀▀ ",
    "  ▀▀▀ ▀▀▀▀ ▀▀▀  ▀▀▀▀",
  ],
}

// 颜色
const ORANGE = "#FB8147"
const GRAY = "#A0A0A0"

export const Logo: Component = () => {
  return (
    <box flexDirection="column" alignItems="center">
      {LOGO_THIN.left.map((left, i) => {
        const right = LOGO_THIN.right[i] || ""
        const isLabel = i === 0
        return (
          <box flexDirection="row" gap={1}>
            <text
              color={isLabel ? GRAY : ORANGE}
              bold
            >
              {left}
            </text>
            <text
              color={GRAY}
              bold
            >
              {right}
            </text>
          </box>
        )
      })}
    </box>
  )
}
