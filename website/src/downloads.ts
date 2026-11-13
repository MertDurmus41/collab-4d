/**
 * Where the buttons on the right point.
 * zip:    built from ../plugin/c4d_collab by scripts/pack-plugin.mjs (runs before dev and build).
 * exe:    there is no installer yet. Set `href` when one exists and the button turns on by itself.
 * github: the public repository. Set href to null to turn the button off again.
 */
export const DOWNLOADS = {
  zip: { href: '/downloads/c4d_collab.zip', file: 'c4d_collab.zip' },
  exe: { href: null as string | null, file: 'c4d_collab_setup.exe' },
}

export const GITHUB = { href: 'https://github.com/MertDurmus41/collab-4d' as string | null }

/**
 * The demo under the download buttons. While `src` is null the site shows a drawn animation
 * labelled "Animation". Put a real recording in public/ (for example public/demo.mp4), set
 * `src: '/demo.mp4'`, and the frame plays it instead.
 */
export const DEMO_VIDEO = { src: '/demo.mp4' as string | null, poster: null as string | null }
