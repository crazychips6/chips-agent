import { Component, createSignal, onMount, onCleanup } from "solid-js"

// Mimo-code 星星字符
const STAR_CHARS = ["✦", "✧", "✦", "✧", "✦", "✧", "✦", " "]
const HOT_CHAR = "✶"
const HOT_THRESHOLD = 0.88
const DENSITY = 0.004
const TWINKLE_INTERVAL = 200

export const StarryBackground: Component = () => {
  const [grid, setGrid] = createSignal<string[][]>([])
  const [brightness, setBrightness] = createSignal<number[][]>([])

  const generateField = (w: number, h: number) => {
    const newGrid: string[][] = []
    const newBrightness: number[][] = []

    for (let y = 0; y < h; y++) {
      const rowChars: string[] = []
      const rowBright: number[] = []
      for (let x = 0; x < w; x++) {
        if (Math.random() < DENSITY) {
          const idx = Math.floor(Math.random() * (STAR_CHARS.length - 1))
          rowChars.push(String(idx))
          rowBright.push(0.15 + Math.random() * 0.4)
        } else {
          rowChars.push(" ")
          rowBright.push(0)
        }
      }
      newGrid.push(rowChars)
      newBrightness.push(rowBright)
    }

    setGrid(newGrid)
    setBrightness(newBrightness)
  }

  const twinkle = () => {
    const w = grid()[0]?.length || 80
    const h = grid().length || 24
    const count = Math.max(1, Math.floor(w * h * 0.008))

    const newBrightness = brightness().map(row => [...row])

    for (let i = 0; i < count; i++) {
      const y = Math.floor(Math.random() * h)
      const x = Math.floor(Math.random() * w)
      if (grid()[y]?.[x] && grid()[y][x] !== " ") {
        const r = Math.random()
        if (r < 0.12) {
          newBrightness[y][x] = 0.92 + Math.random() * 0.08
        } else if (r < 0.8) {
          newBrightness[y][x] = 0.7 + Math.random() * 0.22
        } else {
          newBrightness[y][x] = 0.05 + Math.random() * 0.2
        }
      }
    }

    setBrightness(newBrightness)
  }

  onMount(() => {
    generateField(80, 24)
    const interval = setInterval(twinkle, TWINKLE_INTERVAL)
    onCleanup(() => clearInterval(interval))
  })

  return (
    <box position="absolute" top={0} left={0} right={0} bottom={0}>
      {grid().map((row, y) => (
        <text>
          {row.map((cell, x) => {
            if (cell === " ") return " "
            const idx = parseInt(cell)
            let char = STAR_CHARS[idx] || " "
            if (brightness()[y]?.[x] >= HOT_THRESHOLD && char !== " ") {
              char = HOT_CHAR
            }
            return char
          }).join("")}
        </text>
      ))}
    </box>
  )
}
