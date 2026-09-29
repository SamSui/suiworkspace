/**
 * 冒烟运行器：打包 scripts/cases/*.test.ts 并逐一执行。
 * 运行：node scripts/smoke-runner.mjs
 */
import { build } from 'esbuild'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const casesDir = path.join(__dirname, 'cases')
const cases = fs.readdirSync(casesDir).filter((f) => f.endsWith('.test.ts')).sort()

let allPass = true
for (const c of cases) {
  const entry = path.join(casesDir, c)
  const out = path.join(__dirname, '__bundle_' + c.replace('.ts', '.mjs'))
  const label = c.replace('.test.ts', '')
  console.log('\n===== 用例 ' + label + ' =====')
  try {
    await build({
      entryPoints: [entry],
      outfile: out,
      bundle: true,
      platform: 'node',
      format: 'esm',
      absWorkingDir: root,
      define: { 'import.meta.env': '{"VITE_TOKEN_STORAGE":"none"}' },
    })
    const r = spawnSync(process.execPath, [out], { encoding: 'utf-8' })
    console.log((r.stdout || '') + (r.stderr || ''))
    if (r.status !== 0) allPass = false
  } catch (e) {
    console.log('BUILD/RUN FAIL:', e && e.message)
    allPass = false
  } finally {
    fs.rmSync(out, { force: true })
  }
}
console.log('\n===== 汇总: ' + (allPass ? '全部 PASS' : '存在 FAIL / 异常') + ' =====')
process.exit(allPass ? 0 : 1)
