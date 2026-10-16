import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'

let caseId = 0
async function apiWithClient(client) {
  const key = `__attachmentTestClient${++caseId}`
  globalThis[key] = client
  const source = readFileSync(new URL('../src/api/task1.js', import.meta.url), 'utf8')
    .replace("import client from './client'", `const client = globalThis[${JSON.stringify(key)}]`)
  return import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
}
async function controller(fetch, accepts = () => true) {
  const url = new URL('../src/services/detailPagination.js', import.meta.url)
  assert.ok(existsSync(url), 'paged detail state must exist to protect the selected page')
  const { createDetailPagination } = await import(url)
  const state = {}
  return { state, pager: createDetailPagination({ fetch, state, accepts }) }
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const response = (page, items, total = 201) => ({ page, page_size: 100, items, total, detail_status: 'completed', detail_error_message: '' })

test('detail page three reaches the server for both official and uploaded datasets', async () => {
  const calls = []
  const api = await apiWithClient({ get: async (path, options) => { calls.push({ path, options }); return response(3, [{ product_name: '第201条' }]) } })
  for (const dataset of ['official', 'up_' + 'a'.repeat(32)]) {
    const value = await api.getAttachmentDetails('example', dataset, { page: 3, page_size: 100 })
    assert.equal(value.items[0].product_name, '第201条')
    assert.deepEqual(calls.at(-1), { path: `/platform/datasets/${dataset}/announcements/example/details`, options: { params: { page: 3, page_size: 100 } } })
  }
})

test('pagination commits only the newest requested page', async () => {
  const first = deferred(), second = deferred()
  const { state, pager } = await controller((id, dataset, params) => params.page === 1 ? first.promise : second.promise)
  pager.select({ announcementId: 'one', datasetId: 'official' })
  const old = pager.load(1), current = pager.load(2)
  second.resolve(response(2, [{ row: 101 }]))
  await current
  first.resolve(response(1, [{ row: 1 }]))
  await old
  assert.equal(state.page, 2)
  assert.equal(state.total, 201)
  assert.deepEqual(state.items, [{ row: 101 }])
  assert.equal(state.loading, false)
})

test('switching announcement or dataset rejects earlier detail responses', async () => {
  const old = deferred()
  const { state, pager } = await controller((id, dataset) => id === 'old' ? old.promise : Promise.resolve(response(1, [{ id, dataset }])))
  pager.select({ announcementId: 'old', datasetId: 'official' })
  const pending = pager.load(3)
  pager.select({ announcementId: 'new', datasetId: 'up_' + 'b'.repeat(32) })
  await pager.load()
  old.resolve(response(3, [{ id: 'old' }]))
  await pending
  assert.equal(state.page, 1)
  assert.deepEqual(state.items, [{ id: 'new', dataset: 'up_' + 'b'.repeat(32) }])
})

test('closing the drawer prevents late results and errors from repopulating it', async () => {
  for (const fail of [false, true]) {
    const late = deferred()
    const { state, pager } = await controller(() => late.promise)
    pager.select({ announcementId: 'one', datasetId: 'official' })
    const pending = pager.load(2)
    pager.cancel()
    if (fail) late.reject(new Error('late failure'))
    else late.resolve(response(2, [{ row: 101 }]))
    await pending
    assert.deepEqual(state.items, [])
    assert.equal(state.error, '')
    assert.equal(state.loading, false)
  }
})

test('failed pages clear stale rows and retry the same page with the selected scope', async () => {
  let fail = false
  const seen = []
  const { state, pager } = await controller(async (id, dataset, params) => {
    seen.push({ id, dataset, ...params })
    if (fail) throw new Error('connection lost')
    return response(params.page, [{ row: params.page === 3 ? 201 : 1 }])
  })
  pager.select({ announcementId: 'one', datasetId: 'official' })
  await pager.load()
  fail = true
  await pager.load(3)
  assert.deepEqual(state.items, [])
  assert.match(state.error, /connection lost/)
  assert.equal(state.page, 3)
  assert.equal(state.total, 201)
  fail = false
  await pager.retry()
  assert.deepEqual(state.items, [{ row: 201 }])
  assert.equal(state.error, '')
  assert.deepEqual(seen.at(-1), { id: 'one', dataset: 'official', page: 3, page_size: 100 })
})

test('invalidated dataset context cannot commit even before the view reset runs', async () => {
  let current = true
  const late = deferred()
  const { state, pager } = await controller(() => late.promise, () => current)
  pager.select({ announcementId: 'one', datasetId: 'official' })
  const pending = pager.load()
  current = false
  late.resolve(response(1, [{ row: 1 }]))
  await pending
  assert.deepEqual(state.items, [])
})
