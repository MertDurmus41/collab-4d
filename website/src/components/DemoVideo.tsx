import { DEMO_VIDEO } from '../downloads'

/**
 * The demo frame under the download buttons.
 * With DEMO_VIDEO.src set it plays the real recording. Until then it shows a
 * drawn, looping animation of two users editing one scene, labelled as an
 * animation so nobody takes it for footage.
 */
export default function DemoVideo() {
  if (DEMO_VIDEO.src) {
    return (
      <figure className="demo">
        <video className="demo-frame demo-video" src={DEMO_VIDEO.src} poster={DEMO_VIDEO.poster ?? undefined} autoPlay muted loop playsInline preload="auto" />
        <figcaption>Cinema 4D 2023 and R19 in the same scene, live</figcaption>
      </figure>
    )
  }

  return (
    <figure className="demo">
      <div className="demo-frame" role="img" aria-label="Animation: Ada moves the cube while Ufuk lifts the sphere, both in the same scene">
        <svg viewBox="0 0 640 360" preserveAspectRatio="xMidYMid slice">
          {/* viewport */}
          <rect width="640" height="360" fill="#2f2f33" />
          <g stroke="#3d3d43" strokeWidth="1">
            {Array.from({ length: 13 }, (_, i) => (
              <line key={`v${i}`} x1={320 + (i - 6) * 90} y1="360" x2={320 + (i - 6) * 30} y2="150" />
            ))}
            {[150, 172, 200, 236, 284, 348].map((y) => (
              <line key={`h${y}`} x1="0" y1={y} x2="640" y2={y} />
            ))}
          </g>
          <line x1="0" y1="150" x2="640" y2="150" stroke="#4a4a52" />

          {/* users list, as in the panel */}
          <g fontFamily="Segoe UI, system-ui, sans-serif" fontSize="11">
            <rect x="14" y="14" width="150" height="50" rx="6" fill="#26262a" stroke="#3d3d43" />
            <text x="24" y="33" fill="#cfcfcf">Ada [Red] - frame 24</text>
            <text x="24" y="52" fill="#cfcfcf">Ufuk [Blue] - frame 24</text>
          </g>

          {/* Ada moves the cube */}
          <g className="demo-cube">
            <polygon points="170,250 230,250 230,190 170,190" fill="#8f96a8" />
            <polygon points="170,190 196,170 256,170 230,190" fill="#b4bacb" />
            <polygon points="230,250 256,230 256,170 230,190" fill="#6d7386" />
            <g className="demo-sel demo-sel-a">
              <rect x="160" y="160" width="106" height="100" fill="none" stroke="#ff5a5a" strokeWidth="2" strokeDasharray="6 4" />
              <rect x="160" y="142" width="36" height="16" rx="3" fill="#ff5a5a" />
              <text x="178" y="154" fontSize="11" textAnchor="middle" fill="#fff" fontFamily="Segoe UI, system-ui, sans-serif">Ada</text>
            </g>
          </g>

          {/* Ufuk lifts the sphere */}
          <g className="demo-sphere">
            <circle cx="440" cy="232" r="36" fill="#3f6fe8" />
            <circle cx="428" cy="218" r="12" fill="#7fa2ff" opacity="0.7" />
            <g className="demo-sel demo-sel-b">
              <rect x="396" y="188" width="88" height="88" fill="none" stroke="#4d8dff" strokeWidth="2" strokeDasharray="6 4" />
              <rect x="396" y="170" width="40" height="16" rx="3" fill="#4d8dff" />
              <text x="416" y="182" fontSize="11" textAnchor="middle" fill="#fff" fontFamily="Segoe UI, system-ui, sans-serif">Ufuk</text>
            </g>
          </g>

          {/* cursors */}
          <g className="demo-cursor demo-cursor-a">
            <path d="M0 0 L0 16 L4.5 12 L7.5 19 L10 18 L7 11 L13 11 Z" fill="#ff5a5a" stroke="#fff" strokeWidth="1" />
          </g>
          <g className="demo-cursor demo-cursor-b">
            <path d="M0 0 L0 16 L4.5 12 L7.5 19 L10 18 L7 11 L13 11 Z" fill="#4d8dff" stroke="#fff" strokeWidth="1" />
          </g>
        </svg>
        <span className="demo-tag">Animation</span>
      </div>
      <figcaption>Two users in one scene: one moves the cube while the other lifts the sphere. A selected object is locked to that person.</figcaption>
    </figure>
  )
}
