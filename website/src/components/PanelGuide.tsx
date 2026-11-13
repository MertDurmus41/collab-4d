import Dither from './Dither'
import { GUIDE, QUICK_START, TAILSCALE_HOST, TAILSCALE_INTRO, TAILSCALE_JOIN, TAILSCALE_NOTES, TROUBLESHOOTING } from '../guide'

/** A numbered badge that ties a spot on the panel copy to its explanation. */
const Badge = ({ n }: { n: number }) => (
  <span className="pg-badge" aria-hidden="true">{n}</span>
)

/** A copy of the Collab 4D panel as it looks in Cinema 4D, with numbered spots. */
function PanelCopy() {
  return (
    <div className="pg-window" role="img" aria-label="The Collab 4D panel inside Cinema 4D, with numbered fields">
      <div className="pg-titlebar">
        <span className="pg-title">Collab 4D 0.1.0</span>
        <span className="pg-winbtns" aria-hidden="true">— ☐ ✕</span>
      </div>
      <div className="pg-body">
        <div className="pg-row"><span className="pg-label">Your name</span><span className="pg-input">Ufuk</span><Badge n={1} /></div>
        <div className="pg-row"><span className="pg-label">Host address</span><span className="pg-input">127.0.0.1</span><Badge n={2} /></div>
        <div className="pg-row"><span className="pg-label">Port</span><span className="pg-input pg-port">47800 <i>⇕</i></span><Badge n={3} /></div>
        <div className="pg-row"><span className="pg-label">Password</span><span className="pg-input" /><Badge n={4} /></div>

        <div className="pg-buttons">
          <span className="pg-btn">Host session<Badge n={5} /></span>
          <span className="pg-btn">Join<Badge n={6} /></span>
          <span className="pg-btn is-off">Leave<Badge n={7} /></span>
        </div>

        <div className="pg-status">Not connected<Badge n={8} /></div>

        <div className="pg-list"><span className="pg-label">Users</span><Badge n={9} /><div className="pg-box" /></div>
        <div className="pg-list"><span className="pg-label">Being edited by others</span><Badge n={10} /><div className="pg-box" /></div>
        <div className="pg-list pg-log"><span className="pg-label">Log</span><Badge n={11} /><div className="pg-box" /></div>
      </div>
    </div>
  )
}

export default function PanelGuide() {
  return (
    <section className="guide" id="guide" aria-labelledby="guide-title">
      <div className="guide-glow" aria-hidden="true"><Dither waveColor="#2b5fe3" backgroundColor="transparent" ditherMode="bayer" colorNum={4} pixelSize={3} waveSpeed={0.05} waveFrequency={3} waveAmplitude={0.3} contrast={1.15} mouseEffect="erase" mouseRadius={0.3} clickRipple={false} /></div>
      <header className="guide-head">
        <h2 id="guide-title">Panel guide</h2>
        <p>
          To open the panel, choose <b>Collab 4D</b> from Cinema 4D’s <b>Plugins</b> or <b>Extensions</b> menu.
        </p>
      </header>

      <ol className="quick">
        {QUICK_START.map((step, i) => (
          <li key={step.who}>
            <span className="quick-n">{i + 1}</span>
            <div>
              <b>{step.who}</b>
              <p>{step.text}</p>
            </div>
          </li>
        ))}
      </ol>

      <section className="ts" aria-labelledby="ts-title">
        <h3 id="ts-title">Different homes: connect with Tailscale</h3>
        <p>{TAILSCALE_INTRO}</p>
        <div className="ts-cols">
          <div>
            <h4>Hosting</h4>
            <ol>{TAILSCALE_HOST.map((t) => <li key={t}>{t}</li>)}</ol>
          </div>
          <div>
            <h4>Joining</h4>
            <ol>{TAILSCALE_JOIN.map((t) => <li key={t}>{t}</li>)}</ol>
            <h4>Good to know</h4>
            <ul>{TAILSCALE_NOTES.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
        </div>
      </section>

      <div className="guide-grid">
        <ol className="guide-list gl-left">
          {GUIDE.slice(0, 6).map((item) => (
            <li key={item.n}>
              <Badge n={item.n} />
              <div>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
                {item.example && <pre>{item.example}</pre>}
              </div>
            </li>
          ))}
        </ol>

        <div className="guide-panel">
          <PanelCopy />
        </div>

        <ol className="guide-list gl-right">
          {GUIDE.slice(6).map((item) => (
            <li key={item.n}>
              <Badge n={item.n} />
              <div>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
                {item.example && <pre>{item.example}</pre>}
              </div>
            </li>
          ))}
        </ol>
      </div>

      <div className="trouble">
        <h3>Can’t connect?</h3>
        <ul>
          {TROUBLESHOOTING.map((t) => <li key={t}>{t}</li>)}
        </ul>
      </div>
    </section>
  )
}
