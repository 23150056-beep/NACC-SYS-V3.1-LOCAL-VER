import { execSync } from 'node:child_process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The commit this web build was made from, shown beside the API's on the ISA's
// console so a half-finished deploy is visible. Render sets RENDER_GIT_COMMIT
// at build time; anywhere else the checkout's own hash is asked for, and
// "unknown" is what is left when there is no checkout (a Docker build context
// without .git). Seven characters, the same short form the API reports. The
// question gets five seconds: a git that hangs must not hold the build, and a
// timeout throws like any other failure, which comes out as "unknown".
function webCommit() {
  const fromRender = (process.env.RENDER_GIT_COMMIT || '').trim()
  if (fromRender) return fromRender.slice(0, 7)
  try {
    return execSync('git rev-parse --short=7 HEAD', {
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 5000,
    }).toString().trim() || 'unknown'
  } catch {
    return 'unknown'
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  base: process.env.VITE_DEMO_MODE === 'true' ? '/NACC_RACCO1_SYS/' : '/',
  plugins: [react()],
  define: {
    'import.meta.env.VITE_WEB_COMMIT': JSON.stringify(webCommit()),
  },
})
