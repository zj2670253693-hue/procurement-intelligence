import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

let caseId = 0
async function apiWithClient(client) {
  const key = `__sourceTestClient${++caseId}`
  globalThis[key] = client
  const source = readFileSync(new URL('../src/api/task1.js', import.meta.url), 'utf8')
    .replace("import client from './client'", `const client = globalThis[${JSON.stringify(key)}]`)
  return import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
}

test('official source uses the frozen-source adapter and preserves raw text', async () => {
  const calls = []
  const api = await apiWithClient({ get: async path => {
    calls.push(path)
    return { upload_dataset_id: 'official', announcement_id: 'example', html_text: '<script>alert(1)</script><p>真实原文</p>', attachments: ['原附件.pdf'] }
  } })
  const value = await api.getAnnouncement('example')
  assert.equal(calls[0], '/platform/datasets/official/announcements/example')
  assert.equal(value.source_exists, true)
  assert.equal(value.html_text, '<script>alert(1)</script><p>真实原文</p>')
  assert.deepEqual(value.attachment_files, ['原附件.pdf'])
  assert.equal(value.html_length, value.html_text.length)
})

test('source and attachment failures remain errors instead of claiming no files', async () => {
  const api = await apiWithClient({ get: async () => { throw new Error('official_source_integrity') } })
  await assert.rejects(() => api.getAnnouncement('example'), /official_source_integrity/)
  await assert.rejects(() => api.getAnnouncementAttachments('example'), /official_source_integrity/)
})

test('official attachment listing uses the safe archive directory endpoint', async () => {
  const calls = []
  const api = await apiWithClient({ get: async path => { calls.push(path); return { items: [{ name: '原附件.pdf', text: '', length: 0 }], total: 1, message: '使用原始附件下载查看' } } })
  const value = await api.getAnnouncementAttachments('example')
  assert.equal(calls[0], '/platform/datasets/official/announcements/example/attachments')
  assert.equal(value.total, 1)
  assert.match(value.message, /原始附件/)
})

test('archive download returns the original ZIP blob without parsing or re-encoding', async () => {
  const zip = new Blob([new Uint8Array([80, 75, 3, 4, 0, 0])], { type: 'application/zip' })
  const calls = []
  const api = await apiWithClient({ get: async (path, options) => { calls.push({ path, options }); return zip } })
  assert.equal(typeof api.getAnnouncementArchive, 'function', 'original archive download must be available')
  const value = await api.getAnnouncementArchive('example', 'official')
  assert.equal(value, zip)
  assert.deepEqual(calls[0], { path: '/platform/datasets/official/announcements/example/attachments/download', options: { responseType: 'blob' } })
})
