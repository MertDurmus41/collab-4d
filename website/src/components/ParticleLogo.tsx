import { useEffect, useRef } from 'react'

/**
 * The Cinema 4D logo rebuilt from tens of thousands of small square particles.
 *  - Shedding: particles on the windward (right / upper) rim keep breaking off
 *    and drifting away in curling wisps, then re-form, so the edge always looks
 *    like it is coming apart.
 *  - The cursor pushes nearby particles aside.
 */

type Props = { src: string }

const STEP = 2.6 // sampling step, css px
const FADE_IN_SECONDS = 0.9
const FILL = 0.7 // share of the non-shedding particles drawn one pixel bigger, which closes the grid gaps

// source image: 400x400, the sphere sits at its centre
const SRC_CENTER = 0.5
const SRC_RADIUS = 0.418 // of image width

type Particles = {
  hx: Float32Array; hy: Float32Array // home, relative to logo centre (css px)
  rgb: Uint32Array // packed ABGR without alpha
  shed: Float32Array // 0 = never sheds, else travel distance
  phase: Float32Array; speed: Float32Array; swirl: Float32Array
  count: number
}

// white page: the logo's near-white body would vanish, so every sampled colour is pulled darker
const ink = (c: number) => Math.round(c * 0.76)

const smooth = (a: number, b: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}

function hash(n: number) {
  const s = Math.sin(n * 12.9898) * 43758.5453
  return s - Math.floor(s)
}

export default function ParticleLogo({ src }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const wrap = wrapRef.current!
    const canvas = canvasRef.current!
    const ctx = canvas.getContext('2d')!
    const img = new Image()
    img.src = src

    let W = 0, H = 0, dpr = 1, size = 0, cx = 0, cy = 0
    let parts: Particles | null = null
    let image: ImageData | null = null
    let buf32: Uint32Array | null = null
    let raf = 0
    let shownAt = 0 // when the logo became ready, for the fade-in
    const mouse = { x: -1e4, y: -1e4, sx: -1e4, sy: -1e4 }
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    const build = () => {
      const rect = wrap.getBoundingClientRect()
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      W = Math.max(1, Math.floor(rect.width))
      H = Math.max(1, Math.floor(rect.height))
      canvas.width = Math.floor(W * dpr)
      canvas.height = Math.floor(H * dpr)
      canvas.style.width = W + 'px'
      canvas.style.height = H + 'px'
      image = ctx.createImageData(canvas.width, canvas.height)
      buf32 = new Uint32Array(image.data.buffer)

      const narrow = window.innerWidth < 820 // page width: the logo column is narrower than the page
      size = Math.min(W * 0.74, H * (narrow ? 0.64 : 0.58), 620)
      cx = W * 0.46
      cy = narrow ? H * 0.56 : H * 0.42 // on phones leave room for the top bar
      if (!img.complete || !img.naturalWidth) return

      // draw the source so that the sphere fills `size`
      const scale = size / (img.naturalWidth * SRC_RADIUS * 2)
      const drawW = img.naturalWidth * scale
      const off = document.createElement('canvas')
      off.width = Math.ceil(size)
      off.height = Math.ceil(size)
      const octx = off.getContext('2d', { willReadFrequently: true })!
      const ox = size / 2 - drawW * SRC_CENTER
      octx.drawImage(img, ox, ox, drawW, drawW)
      const data = octx.getImageData(0, 0, off.width, off.height).data
      const R = size / 2

      const hx: number[] = [], hy: number[] = [], rgb: number[] = []
      const shed: number[] = [], phase: number[] = [], speed: number[] = [], swirl: number[] = []
      const wind = { x: 0.94, y: -0.34 }
      let n = 0
      for (let y = 0; y < off.height; y += STEP) {
        for (let x = 0; x < off.width; x += STEP) {
          const jx = x + (hash(n * 3.1 + 1) - 0.5) * STEP * 0.8
          const jy = y + (hash(n * 7.7 + 2) - 0.5) * STEP * 0.8
          n++
          const dx = jx - R, dy = jy - R
          const d = Math.hypot(dx, dy) / R
          if (d > 0.985) continue
          const i = (Math.floor(Math.min(off.height - 1, Math.max(0, jy))) * off.width +
            Math.floor(Math.min(off.width - 1, Math.max(0, jx)))) * 4
          const r = data[i], g = data[i + 1], b = data[i + 2]
          hx.push(dx); hy.push(dy)
          rgb.push((ink(b) << 16) | (ink(g) << 8) | ink(r))
          // windward rim sheds; the further out and the more downwind, the likelier
          const nx = dx / R, ny = dy / R
          const score = 0.8 * (nx * wind.x + ny * wind.y) + 0.85 * d - 0.5
          // only the outer rim sheds: inside the logo a particle that left its place just left a white speck
          const p = Math.min(1, Math.max(0, score)) ** 1.3 * smooth(0.6, 0.88, d)
          const k = hash(n * 1.37 + 9)
          shed.push(k < p * 2.4 ? R * (0.1 + 1.9 * hash(n * 2.9 + 4) ** 2.0) : 0)
          phase.push(hash(n * 5.3 + 3))
          speed.push(0.06 + 0.12 * hash(n * 8.1 + 5))
          swirl.push((hash(n * 4.4 + 6) - 0.5) * 2)
        }
      }
      parts = {
        hx: Float32Array.from(hx), hy: Float32Array.from(hy),
        rgb: Uint32Array.from(rgb), shed: Float32Array.from(shed),
        phase: Float32Array.from(phase), speed: Float32Array.from(speed),
        swirl: Float32Array.from(swirl), count: hx.length,
      }
    }

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame)
      if (!parts || !buf32 || !image) return
      const t = reduce ? 0 : now / 1000
      buf32.fill(0)
      mouse.sx += (mouse.x - mouse.sx) * 0.15
      mouse.sy += (mouse.y - mouse.sy) * 0.15
      const mx = mouse.sx - cx, my = mouse.sy - cy
      const R = size / 2
      const hoverR = R * 0.32
      const cw = canvas.width, ch = canvas.height
      const sq = Math.max(3, Math.round(2.7 * dpr))
      // plain fade-in; with reduced motion the logo is simply there
      const fade = reduce ? 1 : smooth(0, 1, (now - shownAt) / 1000 / FADE_IN_SECONDS)

      const { hx, hy, rgb, shed, phase, speed, swirl, count } = parts
      for (let i = 0; i < count; i++) {
        let a = fade
        let x = hx[i], y = hy[i]
        // pixel size; dust shrinks as it drifts away. Some of the particles that never shed are one pixel bigger: on a
        // jittered grid, squares of exactly the sampling step leave uncovered pixels, and in the dark inside of the
        // logo those show the white page through as specks. FILL is the share made bigger (1 = no specks left).
        let s = shed[i] > 0 || phase[i] >= FILL ? sq : sq + 1
        let tint = 0 // dust turns Cinema 4D blue so it stays visible on white
        // tiny shimmer
        x += Math.sin(t * 0.8 + phase[i] * 40) * 0.1
        y += Math.cos(t * 0.6 + phase[i] * 31) * 0.1

        // shedding
        const travel = shed[i]
        if (travel > 0 && !reduce) {
          const p = (t * speed[i] + phase[i]) % 1
          const e = p * p * p
          const dist = e * travel
          const sw = swirl[i]
          x += 0.94 * dist + Math.sin(p * 5 + sw * 3) * dist * 0.28
          y += -0.34 * dist - dist * 0.25 * (1 + sw) + Math.cos(p * 4 + sw * 5) * dist * 0.18
          a *= Math.min(1, (1 - p) * 1.6) * (1 - e * 0.5)
          s = Math.max(1, Math.round(sq * (1 - 0.7 * Math.min(1, p * 1.4))))
          tint = Math.min(1, p * 1.6) * 0.9
        }

        // cursor: push the particles aside
        const dx = x - mx, dy = y - my
        const dm = Math.hypot(dx, dy)
        if (dm < hoverR * 1.3) {
          const f = 1 - dm / (hoverR * 1.3)
          const push = f * f * 26
          x += (dx / (dm + 0.001)) * push
          y += (dy / (dm + 0.001)) * push
        }
        if (a <= 0.02) continue

        const px = Math.round((cx + x) * dpr), py = Math.round((cy + y) * dpr)
        if (px < 0 || py < 0 || px + s >= cw || py + s >= ch) continue
        let c = rgb[i]
        if (tint > 0) {
          const r0 = c & 255, g0 = (c >> 8) & 255, b0 = (c >> 16) & 255
          c = Math.round(r0 + (43 - r0) * tint) | (Math.round(g0 + (95 - g0) * tint) << 8) | (Math.round(b0 + (227 - b0) * tint) << 16)
        }
        const col = c | (Math.round(a * 255) << 24)
        for (let yy = 0; yy < s; yy++) {
          const row = (py + yy) * cw + px
          for (let xx = 0; xx < s; xx++) buf32[row + xx] = col
        }
      }
      ctx.putImageData(image, 0, 0)
    }

    const onMove = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect()
      mouse.x = e.clientX - r.left
      mouse.y = e.clientY - r.top
      if (mouse.sx < -9000) { mouse.sx = mouse.x; mouse.sy = mouse.y }
    }
    // cursor left the window: park it far away without easing across the logo
    const onLeave = () => { mouse.x = mouse.y = mouse.sx = mouse.sy = -1e4 }

    wrap.classList.add('loading')
    img.onload = () => { build(); wrap.classList.remove('loading'); shownAt = performance.now() }
    if (img.complete && img.naturalWidth) img.onload(new Event('load'))
    build()
    raf = requestAnimationFrame(frame)
    const ro = new ResizeObserver(() => build())
    ro.observe(wrap)
    window.addEventListener('pointermove', onMove)
    document.documentElement.addEventListener('pointerleave', onLeave)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('pointermove', onMove)
      document.documentElement.removeEventListener('pointerleave', onLeave)
    }
  }, [src])

  return (
    <div ref={wrapRef} className="logo-wrap">
      <canvas ref={canvasRef} className="logo-canvas" aria-label="Cinema 4D logosu" role="img" />
    </div>
  )
}
