# Website polish plan

## Primary goal

A visitor who opens the page sees the Cinema 4D logo breaking apart like the tein.ai banana, on a calm white page, within the first second. It reads well on any screen, and every effect feels deliberate rather than busy.

## Phase 0: Reference discovery

**How this serves the primary goal:** the effects were written from the reference pages' look alone. Reading the real implementations tells us what to copy and what we got wrong, before we tune anything.

- Look in the local `js-libs` folder (cloned graphics libraries) for React Bits: `HalftoneReveal`, `Dither`, `GradientAscii`, `Magnet`, `PulseHeart`. If absent, read the source of each page on reactbits.dev.
- Write a short "Allowed APIs and techniques" list: how each one does its sampling, ordered dither matrix, ASCII ramp, magnet falloff and heart timing.
- Open tein.ai in the browser pane and record how the banana sheds: particle size, density falloff, speed, direction.
- Anti-pattern: do not assume a technique exists; cite the file and line it comes from.

## Phase 1: Logo effect fidelity

**How this serves the primary goal:** the logo is the hero. Getting the shedding and the halftone reveal right is most of the page's impact.

- Make the shedding match the tein.ai reference: finer particles, denser at the core, thinning into wisps with a clear wind direction.
- Make the halftone reveal follow the reference's dot grid (rotated 45 degrees, dot size from brightness) instead of the current plain grid.
- Move particle drawing to WebGL points or instanced quads if frame time on a mid laptop is above 8 ms; otherwise keep the canvas version.
- Verify: 60 fps idle at 1440x900, no gaps in the logo body.

## Phase 2: Typography and layout

**How this serves the primary goal:** the page should read as one system on any screen, not a canvas with text on top.

- Self-host Inter (woff2) so the heading does not depend on the system font; define one type scale and one spacing scale in `styles.css`.
- Rework the mobile layout: logo on top, button below, headline fixed to the bottom without overlap.
- Check overlaps at 1280x720, 1440x900, 1920x1080, 820 wide and 390 wide.

## Phase 3: Background refinement

**How this serves the primary goal:** the background should add depth but never compete with the logo.

- Replace the continuous field with the reference's ordered dither (Bayer) steps so the gradient visibly bands into dots.
- Add a soft ripple where the cursor moves; keep the area behind the logo calm.
- Tune the ink strength so text contrast stays at or above 4.5:1 everywhere.

## Phase 4: Interaction and edge states

**How this serves the primary goal:** careful details (focus, touch, failures) make the page feel finished.

- Magnet button: keyboard focus ring, touch devices skip the pull, `aria-label` that says it opens a new tab.
- Logo: a quiet loading state while the image decodes; a static, non-animated version for `prefers-reduced-motion`.
- Background: a plain paper fallback when WebGL is unavailable (today it silently shows nothing).
- Tab hidden: pause all animation loops.

## Phase 5: Verification

**How this serves the primary goal:** proves the polish did not cost speed or accessibility.

- `npm run build` passes; initial JS stays under 100 KB gzipped.
- Screenshots at the five sizes above, compared with the previous commit.
- Contrast check on all text; keyboard-only pass over the button.
- Console clean on a fresh page load.

## Status (Phases 0-5 done)

Measured in the preview pane on one frame, worst pixel behind each text:

| Check | Result |
|---|---|
| Initial JS | 75.96 KB gzip (budget 100 KB) |
| Text contrast | tagline body 7.4:1, headline 10.3:1, label 10.8:1, brand 7.1:1 (all above 4.5:1) |
| Overlaps (logo vs headline vs button) | none at 390, 820x1000, 1280x720, 1440x900, 1920x1080 |
| Keyboard | one focusable element (the link), focus ring visible |
| Console | clean on a fresh tab |

Not measured: real frame rate (the pane throttles animation while hidden; the one reading of 3 fps is not valid), touch input, and the reduced-motion path on a real device.
