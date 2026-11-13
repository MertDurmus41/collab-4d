import DitherAsciiBackground from './components/DitherAsciiBackground'
import ParticleLogo from './components/ParticleLogo'
import PulseHeart from './components/PulseHeart'
import RotatingText from './components/RotatingText'
import PanelGuide from './components/PanelGuide'
import DemoVideo from './components/DemoVideo'
import { DOWNLOADS, GITHUB } from './downloads'

/** Three chevrons that slide in on hover; the paths are the ones from the "NEXT" button the client picked. */
const CHEV = [
  ['one', 'M40.1543933,3.89485454 L43.9763149,0.139296592 C44.1708311,-0.0518420739 44.4826329,-0.0518571125 44.6771675,0.139262789 L65.6916134,20.7848311 C66.0855801,21.1718824 66.0911863,21.8050225 65.704135,22.1989893 C65.7000188,22.2031791 65.6958657,22.2073326 65.6916762,22.2114492 L44.677098,42.8607841 C44.4825957,43.0519059 44.1708242,43.0519358 43.9762853,42.8608513 L40.1545186,39.1069479 C39.9575152,38.9134427 39.9546793,38.5968729 40.1481845,38.3998695 C40.1502893,38.3977268 40.1524132,38.395603 40.1545562,38.3934985 L56.9937789,21.8567812 C57.1908028,21.6632968 57.193672,21.3467273 57.0001876,21.1497035 C56.9980647,21.1475418 56.9959223,21.1453995 56.9937605,21.1432767 L40.1545208,4.60825197 C39.9574869,4.41477773 39.9546013,4.09820839 40.1480756,3.90117456 C40.1501626,3.89904911 40.1522686,3.89694235 40.1543933,3.89485454 Z'],
  ['two', 'M20.1543933,3.89485454 L23.9763149,0.139296592 C24.1708311,-0.0518420739 24.4826329,-0.0518571125 24.6771675,0.139262789 L45.6916134,20.7848311 C46.0855801,21.1718824 46.0911863,21.8050225 45.704135,22.1989893 C45.7000188,22.2031791 45.6958657,22.2073326 45.6916762,22.2114492 L24.677098,42.8607841 C24.4825957,43.0519059 24.1708242,43.0519358 23.9762853,42.8608513 L20.1545186,39.1069479 C19.9575152,38.9134427 19.9546793,38.5968729 20.1481845,38.3998695 C20.1502893,38.3977268 20.1524132,38.395603 20.1545562,38.3934985 L36.9937789,21.8567812 C37.1908028,21.6632968 37.193672,21.3467273 37.0001876,21.1497035 C36.9980647,21.1475418 36.9959223,21.1453995 36.9937605,21.1432767 L20.1545208,4.60825197 C19.9574869,4.41477773 19.9546013,4.09820839 20.1480756,3.90117456 C20.1501626,3.89904911 20.1522686,3.89694235 20.1543933,3.89485454 Z'],
  ['three', 'M0.154393339,3.89485454 L3.97631488,0.139296592 C4.17083111,-0.0518420739 4.48263286,-0.0518571125 4.67716753,0.139262789 L25.6916134,20.7848311 C26.0855801,21.1718824 26.0911863,21.8050225 25.704135,22.1989893 C25.7000188,22.2031791 25.6958657,22.2073326 25.6916762,22.2114492 L4.67709797,42.8607841 C4.48259567,43.0519059 4.17082418,43.0519358 3.97628526,42.8608513 L0.154518591,39.1069479 C-0.0424848215,38.9134427 -0.0453206733,38.5968729 0.148184538,38.3998695 C0.150289256,38.3977268 0.152413239,38.395603 0.154556228,38.3934985 L16.9937789,21.8567812 C17.1908028,21.6632968 17.193672,21.3467273 17.0001876,21.1497035 C16.9980647,21.1475418 16.9959223,21.1453995 16.9937605,21.1432767 L0.15452076,4.60825197 C-0.0425130651,4.41477773 -0.0453986756,4.09820839 0.148075568,3.90117456 C0.150162624,3.89904911 0.152268631,3.89694235 0.154393339,3.89485454 Z'],
] as const

const CtaArrow = () => (
  <span className="cta-arrow" aria-hidden="true">
    <svg width="66" height="43" viewBox="0 0 66 43">
      {CHEV.map(([name, d]) => <path key={name} className={name} d={d} fill="#fff" />)}
    </svg>
  </span>
)
const GitHubIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path
      fill="currentColor"
      d="M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.52-1.33-1.28-1.69-1.28-1.69-1.05-.71.08-.7.08-.7 1.15.08 1.76 1.19 1.76 1.19 1.03 1.76 2.7 1.25 3.36.96.1-.75.4-1.25.73-1.54-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.18-3.09-.12-.29-.51-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.62 1.59.23 2.76.11 3.05.74.81 1.18 1.83 1.18 3.09 0 4.42-2.69 5.39-5.26 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .5Z"
    />
  </svg>
)

export default function App() {
  const exe = DOWNLOADS.exe

  return (
    <>
      <DitherAsciiBackground />

      <main className="stage">
        <section className="left">
          <ParticleLogo src="/c4d-logo.jpg" />
        </section>

        <aside className="right">
          <div className="hero-text">
            <h1>
              <span className="title-word">Collab</span>
              {/* React Bits RotatingText: one fixed text that slides in once and stays */}
              <RotatingText
                texts={['4D']}
                effect="slide"
                direction="up"
                highlight
                highlightColor="#5b9bff"
                auto={false}
                animatePresenceInitial
                staggerFrom="last"
                staggerDuration={0.06}
                splitLevelClassName="title-mask"
                mainClassName="title-block"
              />
            </h1>
            <p className="hero-sub">The same scene in Cinema 4D, at the same time.</p>
            <p>
              A free, open-source live collaboration plugin for Cinema 4D. Several people model and animate in
              the same scene at once, and everyone sees who is editing what.
            </p>
          </div>

          <div className="actions">
            <a
              className={'cta cta-dark' + (GITHUB.href ? '' : ' is-idle')}
              href={GITHUB.href ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              aria-disabled={GITHUB.href ? undefined : 'true'}
              title={GITHUB.href ? undefined : 'Opens once the repository address is added'}
            >
              <span className="cta-label"><b><GitHubIcon />Star on GitHub</b></span>
              <CtaArrow />
            </a>

            <a className="cta" href={DOWNLOADS.zip.href} download={DOWNLOADS.zip.file}>
              <span className="cta-label"><b>Download ZIP</b><small>Plugin folder</small></span>
              <CtaArrow />
            </a>

            {exe.href ? (
              <a className="cta" href={exe.href} download={exe.file}>
                <span className="cta-label"><b>Download EXE</b><small>Windows installer</small></span>
                <CtaArrow />
              </a>
            ) : (
              <span className="cta cta-off" role="link" aria-disabled="true" title="The installer will be here when it is ready">
                <span className="cta-label"><b>Download EXE</b><small>Coming soon</small></span>
                <CtaArrow />
              </span>
            )}

            {/* React Bits PulseHeart, config as given */}
            <PulseHeart
              count={16}
              defaultLiked={false}
              showCount
              icon="heart"
              idleOutline
              size={52}
              corner={40}
              likedColor="#ff4d6d"
              idleColor="#8b8b93"
              pillColor="#232326"
              textColor="#f5f5f5"
              duration={560}
              dotSize={0.3}
              overshoot={1.7}
              beat={3}
              rollDuration={350}
            />
          </div>

          <DemoVideo />
        </aside>
        <a className="scroll-hint" href="#guide">Panel guide <span aria-hidden="true">↓</span></a>
      </main>

      <PanelGuide />
    </>
  )
}
