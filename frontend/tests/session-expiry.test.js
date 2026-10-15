import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'

// client.js builds an axios instance at import time; loading the real package would
// need node_modules, so provide a minimal stub with the same shape it uses.
async function loadClientWithStub() {
  const root = mkdtempSync(join(tmpdir(), 't3-client-'))
  const stubDir = join(root, 'node_modules', 'axios')
  mkdirSync(stubDir, { recursive: true })
  writeFileSync(join(stubDir, 'package.json'), JSON.stringify({ name: 'axios', version: '0.0.0', type: 'module', main: 'index.js' }))
  writeFileSync(join(stubDir, 'index.js'), `
    const interceptors = { request: { use() {} }, response: { use() {} } }
    export default { create: () => ({ interceptors, get: async () => ({}), post: async () => ({}) }) }
  `)
  const routerStub = join(root, 'router.js')
  writeFileSync(routerStub, 'export default { replace() {} }\n')
  const source = await import('node:fs').then(fs => fs.promises.readFile(new URL('../src/api/client.js', import.meta.url), 'utf8'))
  const patched = source.replace("from 'axios'", `from ${JSON.stringify(pathToFileURL(join(stubDir, 'index.js')).href)}`)
    .replace("from '../router'", `from ${JSON.stringify(pathToFileURL(routerStub).href)}`)
  const target = join(root, 'client.mjs')
  writeFileSync(target, patched)
  return import(pathToFileURL(target).href)
}

const unauthorized = () => ({ response: { status: 401, data: { code: 'session_required' } } })
const forbidden = () => ({ response: { status: 403, data: { code: 'csrf_forbidden' } } })

test('a 401 sends the user back to login instead of failing silently', async () => {
  const { handleUnauthorized } = await loadClientWithStub()
  const seen = []
  const result = handleUnauthorized(unauthorized(), { navigate: target => seen.push(target) })
  assert.equal(seen.length, 1, 'exactly one navigation')
  assert.equal(seen[0].path, '/login')
  assert.equal(seen[0].query.reason, 'expired')
  assert.ok(seen[0].query.next, 'remembers where the user was')
  assert.ok(result instanceof Error)
  assert.equal(result.expired, true)
  assert.match(result.message, /会话已失效|未登录/)
  assert.match(result.message, /没有提交到服务器/, 'says plainly that nothing was sent')
})

test('non-session failures are left alone', async () => {
  const { handleUnauthorized } = await loadClientWithStub()
  const seen = []
  const original = forbidden()
  const result = handleUnauthorized(original, { navigate: target => seen.push(target) })
  assert.equal(result, original, '403 must not be rewritten as a session problem')
  assert.equal(seen.length, 0, '403 must not navigate away')
})
