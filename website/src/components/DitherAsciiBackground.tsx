import { useEffect, useRef } from 'react'

/**
 * Full-screen WebGL background mixing Gradient ASCII and Dither: a horizon glow
 * along the top edge, drawn as letters, over a haze quantised with an ordered
 * (Bayer) dither. The cursor does not affect it.
 */

const RAMP = ' .xX0S8#@' // letters, as in the Gradient ASCII "Letters" set
const GLYPH_PX = 32 // atlas resolution per glyph

const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`

const FRAG = `
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform float uCell;      // ascii cell size, device px
uniform float uPix;       // dither pixel size, device px
uniform sampler2D uAtlas;
uniform float uGlyphs;
uniform vec2 uLogo;       // logo centre in 0..1 screen space, origin bottom-left

float bayer2(vec2 a) { a = floor(a); return fract(a.x / 2.0 + a.y * a.y * 0.75); }
#define bayer4(a) (bayer2(0.5 * (a)) * 0.25 + bayer2(a))
#define bayer8(a) (bayer4(0.5 * (a)) * 0.25 + bayer2(a))

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x),
             mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + 17.1; a *= 0.5; }
  return v;
}

// palette read from the React Bits Gradient ASCII preview
const vec3 BLUE  = vec3(0.114, 0.247, 0.867); // deep blue
const vec3 LIGHT = vec3(0.659, 0.847, 1.000); // light blue
const vec3 CYAN  = vec3(0.220, 0.741, 0.973); // cyan
const vec3 PAPER = vec3(0.985, 0.986, 0.992);

// a bright arc along the top edge that fades downwards and drifts slowly
float horizon(vec2 uv, float t) {
  float h = 1.0 - uv.y;                                  // 0 at the top
  float arc = pow(abs(uv.x - 0.5) * 1.5, 2.0) * 0.45;    // the sides fall away
  float n = fbm(vec2(uv.x * 3.0 + t * 0.04, uv.y * 2.2 - t * 0.03));
  return clamp(1.0 - smoothstep(0.0, 0.85, h * 1.0 + arc + (n - 0.5) * 0.35), 0.0, 1.0);
}

void main() {
  vec2 frag = gl_FragCoord.xy;
  float aspect = uRes.x / uRes.y;
  float t = uTime;

  // ---- ascii layer: letters, densest where the glow is strongest ----------
  vec2 cell = floor(frag / uCell);
  vec2 cellUv = (cell + 0.5) * uCell / uRes;
  float v = horizon(cellUv, t);

  // keep the area behind the logo calm so it reads
  float dLogo = length((cellUv - uLogo) * vec2(aspect, 1.0));
  float calm = smoothstep(0.45, 0.12, dLogo);
  v *= 1.0 - calm * 0.85;

  float levels = uGlyphs - 1.0;
  float idx = floor(v * levels + bayer4(cell) * 0.999);
  idx = clamp(idx, 0.0, levels);

  vec2 local = fract(frag / uCell);
  float glyph = texture2D(uAtlas, vec2((idx + local.x) / uGlyphs, 1.0 - local.y)).r;
  // letters are dark on the bright top and light inside the blue band, as in the preview
  float blueZone = smoothstep(0.0, 0.3, v) * (1.0 - smoothstep(0.3, 0.6, v));
  vec3 glyphCol = mix(vec3(0.07, 0.18, 0.70), vec3(0.90, 0.96, 1.0), blueZone);
  float flicker = 0.85 + 0.15 * hash(cell + floor(t * 8.0));
  float asciiA = glyph * (0.3 + 0.65 * v) * flicker;

  // ---- gradient haze, quantised with an ordered (Bayer) dither -------------
  vec2 pix = floor(frag / uPix);
  vec2 pUv = (pix + 0.5) * uPix / uRes;
  float hv = horizon(pUv, t);
  // clear the haze behind the logo so its pale body does not blend into it
  float dLogoPix = length((pUv - uLogo) * vec2(aspect, 1.0));
  float clearScale = clamp(aspect / 1.4, 0.55, 1.0);      // smaller clearing on tall (phone) screens
  hv *= 1.0 - 0.9 * smoothstep(0.5 * clearScale, 0.3 * clearScale, dLogoPix);
  float steps = 5.0;
  float q = floor(hv * steps + bayer8(pix)) / steps;     // dithered level
  q = clamp(q, 0.0, 1.0);
  // white-hot at the top, cyan, then a deep blue band that dithers out into the paper
  vec3 hc = mix(BLUE, CYAN, smoothstep(0.1, 0.7, q));
  hc = mix(hc, LIGHT, smoothstep(0.65, 0.92, q));
  hc = mix(hc, vec3(1.0), smoothstep(0.9, 1.0, q) * 0.75);
  vec3 col = mix(PAPER, hc, smoothstep(0.0, 0.2, q) * 0.97);

  col = mix(col, glyphCol, asciiA);
  col += (hash(frag + fract(t)) - 0.5) * 0.012;           // film grain
  gl_FragColor = vec4(col, 1.0);
}
`

function buildAtlas(): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = GLYPH_PX * RAMP.length
  c.height = GLYPH_PX
  const ctx = c.getContext('2d')!
  ctx.fillStyle = '#000'
  ctx.fillRect(0, 0, c.width, c.height)
  ctx.fillStyle = '#fff'
  ctx.font = `bold ${GLYPH_PX * 0.9}px ui-monospace, "Cascadia Mono", Consolas, monospace`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (let i = 0; i < RAMP.length; i++) {
    ctx.fillText(RAMP[i], i * GLYPH_PX + GLYPH_PX / 2, GLYPH_PX / 2 + 1)
  }
  return c
}

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const s = gl.createShader(type)!
  gl.shaderSource(s, src)
  gl.compileShader(s)
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(s) ?? 'shader error')
  }
  return s
}

export default function DitherAsciiBackground() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current!
    const gl = canvas.getContext('webgl', { antialias: false })
    if (!gl || gl.isContextLost()) {
      canvas.dataset.fallback = 'true' // no WebGL: CSS draws a still dotted paper
      return
    }

    const prog = gl.createProgram()!
    try {
      gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT))
      gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG))
    } catch (err) {
      console.error(err) // background is decoration; never take the page down
      canvas.dataset.fallback = 'true'
      return
    }
    gl.linkProgram(prog)
    gl.useProgram(prog)

    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const aPos = gl.getAttribLocation(prog, 'aPos')
    gl.enableVertexAttribArray(aPos)
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)

    const tex = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, tex)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, buildAtlas())
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)

    const u = (n: string) => gl.getUniformLocation(prog, n)
    const uRes = u('uRes'), uTime = u('uTime')
    const uCell = u('uCell'), uPix = u('uPix')
    gl.uniform1i(u('uAtlas'), 0)
    gl.uniform1f(u('uGlyphs'), RAMP.length)
    const uLogo = u('uLogo')

    // where the logo sits, so the field stays calm behind it
    const placeLogo = () => {
      const wrap = document.querySelector('.logo-wrap')?.getBoundingClientRect()
      const narrow = window.innerWidth < 820
      const x = wrap ? wrap.left + wrap.width * 0.46 : window.innerWidth * 0.3
      const y = wrap ? wrap.top + wrap.height * (narrow ? 0.56 : 0.42) : window.innerHeight * 0.45
      gl.uniform2f(uLogo, x / window.innerWidth, 1 - y / window.innerHeight)
    }

    let dpr = 1
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.floor(window.innerWidth * dpr)
      canvas.height = Math.floor(window.innerHeight * dpr)
      gl.viewport(0, 0, canvas.width, canvas.height)
      gl.uniform2f(uRes, canvas.width, canvas.height)
      gl.uniform1f(uCell, Math.round(13 * dpr))
      gl.uniform1f(uPix, Math.max(2, Math.round(2.5 * dpr)))
      placeLogo()
    }
    resize()
    window.addEventListener('resize', resize)

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const start = performance.now()
    let raf = 0
    const frame = () => {
      const t = reduce ? 10 : (performance.now() - start) / 1000
      gl.uniform1f(uTime, t)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      raf = requestAnimationFrame(frame)
    }
    frame()

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      gl.deleteProgram(prog)
      gl.deleteTexture(tex)
      gl.deleteBuffer(buf)
    }
  }, [])

  return <canvas ref={ref} className="bg-canvas" aria-hidden="true" />
}
