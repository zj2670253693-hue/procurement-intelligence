import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, ref, nextTick } from 'vue'
import { createDatasetContext } from '../src/services/datasetContext.js'

let caseId = 0
async function loadView(overrides = {}) {
  const url = new URL('../src/views/EntitiesView.vue', import.meta.url)
  const key = `__announcementView${++caseId}`
  const api = {
    searchEntities: async () => ({ items: [], total: 0 }),
    getEntitiesByAnnouncement: async () => ({ items: [] }),
    getAnnouncement: async () => ({ html_text: '原文', attachment_files: ['附件.pdf'], result: { entities: [], extra_info: {} } }),
    getStandardItems: async () => ({ headers: [], items: [] }),
    getOfficialNotes: async () => ({}),
    getAttachmentDetails: async () => ({ items: [], total: 0 }),
    getAnnouncementAttachments: async () => ({ items: [], total: 0 }),
    getAnnouncementArchive: async () => new Blob([]),
    ...overrides,
  }
  const dataset = { datasetId: ref('official'), context: createDatasetContext() }
  globalThis[key] = { api, dataset }
  const moduleUrl = code => 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
  const apiUrl = moduleUrl(Object.keys(api).map(name => `export const ${name} = (...args) => globalThis[${JSON.stringify(key)}].api.${name}(...args)`).join('\n'))
  const datasetUrl = moduleUrl(`export const useDataset = () => globalThis[${JSON.stringify(key)}].dataset`)
  const { descriptor } = parse(readFileSync(url, 'utf8'))
  const script = compileScript(descriptor, { id: key }).content.replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    const target = name === '../api/task1' ? apiUrl : name === '../services/useDataset' ? datasetUrl
      : name.startsWith('.') ? new URL(name + '.js', url).href : import.meta.resolve(name)
    return 'from ' + JSON.stringify(target)
  })
  const { default: Component } = await import(moduleUrl(script))
  let state
  const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
    insert() {}, remove() {}, patchProp() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null })
  const app = renderer.createApp({ setup() { state = Component.setup({}, { expose() {} }); return () => null } })
  app.mount({})
  return { state, dataset, close: () => { app.unmount(); delete globalThis[key] } }
}

test('a successful original source remains readable when the entity endpoint fails', async () => {
  const view = await loadView({ getEntitiesByAnnouncement: async () => { throw new Error('entity lookup failed') } })
  try {
    await view.state.openAnnouncement('example')
    assert.equal(view.state.announcement.value?.html_text, '原文')
    assert.deepEqual(view.state.announcement.value.attachment_files, ['附件.pdf'])
    assert.match(view.state.drawer.error, /entity lookup failed/)
  } finally { view.close() }
})

test('closing the actual drawer invalidates an in-flight detail page', async () => {
  let release
  const view = await loadView({ getAttachmentDetails: () => new Promise(resolve => { release = resolve }) })
  try {
    await view.state.openAnnouncement('example')
    view.state.drawer.visible = false
    release({ items: [{ row: 101 }], total: 201 })
    await nextTick()
    assert.deepEqual(view.state.attachmentDetails.items, [])
    assert.equal(view.state.attachmentDetails.loading, false)
  } finally { view.close() }
})

test('an original request group cannot overwrite a successful source retry', async () => {
  let release, attempt = 0
  const view = await loadView({
    getEntitiesByAnnouncement: () => new Promise(resolve => { release = resolve }),
    getAnnouncement: async () => {
      if (++attempt === 1) throw new Error('source temporarily unavailable')
      return { html_text: '恢复的原文', attachment_files: ['附件.pdf'] }
    },
  })
  try {
    const pending = view.state.openAnnouncement('example')
    await new Promise(resolve => setImmediate(resolve))
    assert.match(view.state.drawer.sourceError, /temporarily unavailable/)
    await view.state.retrySource()
    release({ items: [] })
    await pending
    assert.equal(view.state.announcement.value?.html_text, '恢复的原文')
    assert.equal(view.state.drawer.sourceError, '')
  } finally { view.close() }
})

test('retrying private source restores its extracted rows and notes', async () => {
  let attempt = 0
  const view = await loadView({ getAnnouncement: async () => {
    if (++attempt === 1) throw new Error('source temporarily unavailable')
    return { html_text: '恢复的原文', attachment_files: [], result: { entities: [{ product_name: '恢复的产品' }], extra_info: { remark: '原备注' } } }
  } })
  try {
    view.dataset.datasetId.value = 'up_' + 'a'.repeat(32)
    await nextTick()
    await view.state.openAnnouncement('example')
    assert.match(view.state.drawer.error, /temporarily unavailable/)
    await view.state.retrySource()
    assert.deepEqual(view.state.drawer.rows, [{ product_name: '恢复的产品' }])
    assert.match(view.state.officialNotes.value, /原备注/)
    assert.equal(view.state.drawer.error, '')
  } finally { view.close() }
})
