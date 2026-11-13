// Zips ../plugin/c4d_collab into public/downloads/c4d_collab.zip so the site's
// ZIP button always serves the current plugin. Runs before dev and build.
import AdmZip from 'adm-zip'
import { existsSync, mkdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = join(here, '..', '..', 'plugin', 'c4d_collab')
const outDir = join(here, '..', 'public', 'downloads')
const out = join(outDir, 'c4d_collab.zip')

if (!existsSync(src)) {
  console.warn(`pack-plugin: ${src} not found, skipping`)
  process.exit(0)
}
mkdirSync(outDir, { recursive: true })

const zip = new AdmZip()
zip.addLocalFolder(src, 'c4d_collab', (name) => !/__pycache__|\.pyc$/.test(name))
zip.writeZip(out)
console.log(`pack-plugin: ${out} (${Math.round(statSync(out).size / 1024)} KB)`)
