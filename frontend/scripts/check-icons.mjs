// Fails when a source file names an icon that src/ui/icons.js does not hold.
//
// <Icon name="..."> looks icons up in an explicit map (so the bundle carries
// only the icons used), and an unknown name renders a placeholder circle
// instead of failing. This scans src for icon-name string literals and fails
// on any that are missing from the map, and on any map entry that is not a
// real lucide-react icon.
//
// What counts as an icon name: ANY string literal anywhere in src that is a
// lowercase kebab or single word and whose Pascal form is a lucide-react
// export. Each must be in the map unless NOT_ICONS lists it with the reason it
// is something else (a CSS value, an ARIA role, a key). Inspect new hits
// before allowlisting them.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as Lucide from 'lucide-react'
import { ICONS, toPascal } from '../src/ui/icons.js'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src')
const NOT_ICONS = new Set([
  'signal', // AbortController option name in a comment
  'share', // BroadcastChannel message type
  'menu', // ARIA role
  'baseline', // CSS align-items value
  'pointer', // CSS cursor value
  'ghost', // Button variant name
  'radio', // input type / ARIA role
  'grid', // CSS display value
  'group', // ARIA role
  'italic', // CSS font-style / editor command
  'book', // search keyword text
  'option', // ARIA role
  'text', // input type / field kind
  'list', // ARIA value / field kind
  'focus', // React key fallback label
  'disc', // CSS list-style-type
  'underline', // editor command name
  'table', // case-study section kind
  'monitor', // nav item id (its icon is 'activity')
  'columns', // case-study config property
  'key', // JSON property name in case-study config
  'languages', // case-study section id
  'cat', // table column key
  'edit', // menu item id (its icon is 'pencil')
  'code', // comment text about verification state
  'scan', // FormData field name
  'files', // tab id
  'landmark', // address field key
  'scroll', // DOM event name
  'box', // CSS class name in print HTML
])

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name)
    return e.isDirectory() ? walk(p) : [p]
  })
}

const problems = []

for (const [name, cmp] of Object.entries(ICONS)) {
  if (Lucide[toPascal(name)] !== cmp) problems.push(`map entry "${name}" is not lucide-react's ${toPascal(name)}`)
}

const used = new Map()
for (const file of walk(SRC).filter((f) => /\.jsx?$/.test(f) && !f.endsWith(path.join('ui', 'icons.js')))) {
  const lines = fs.readFileSync(file, 'utf8').split('\n')
  lines.forEach((line, i) => {
    for (const m of line.matchAll(/(['"`])([a-z][a-z0-9]*(?:-[a-z0-9]+)*)\1/g)) {
      const s = m[2]
      if (NOT_ICONS.has(s)) continue
      if (typeof Lucide[toPascal(s)] !== 'object') continue
      if (!used.has(s)) used.set(s, `${path.relative(SRC, file)}:${i + 1}`)
    }
  })
}

for (const [name, where] of used) {
  if (!ICONS[name]) problems.push(`"${name}" (${where}) is not in src/ui/icons.js`)
}

if (problems.length) {
  console.error(problems.join('\n'))
  console.error(`\nicon check FAILED: ${problems.length} problem(s)`)
  process.exit(1)
}
console.log(`icon check ok: ${used.size} names used, ${Object.keys(ICONS).length} icons in the map`)
