import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { buildGraphLayout } from '../src/services/graphLayout.js'
import { RELATION_LABELS, relationLabel, relationTone } from '../src/services/graphLabels.js'

test('unknown bid outcomes remain explicit and do not claim a win or a verified legal identity', () => {
  assert.equal(relationTone('bid_participation_unknown'), 'unknown')
  assert.match(relationLabel('bid_participation_unknown'), /结果未明确/)
  assert.doesNotMatch(relationLabel('bid_participation_unknown'), /已中标|未中标/)
  assert.equal(RELATION_LABELS.individual_subject, '对应组织')
  assert.match(relationLabel('new_relation_kind'), /其他关系/)
})

test('actual Vue template uses the collision-adjusted coordinates and labels every fact', async () => {
    // Compile and render the actual SFC template, not a separately maintained SVG
    // imitation. No dev server, browser install, network or temporary file needed.
    const url = new URL('../src/components/RelationshipGraph.vue', import.meta.url)
    const {descriptor} = parse(readFileSync(url, 'utf8'))
    const compiled = compileScript(descriptor, {id: 'graph-label-test', inlineTemplate: true,
      templateOptions: {ssr: true}}).content
    const require = createRequire(import.meta.url)
    const code = compiled.replace(/from (['"])([^'"]+)\1/g, (whole, quote, specifier) => {
      const target = specifier.startsWith('.') ? new URL(specifier + '.js', url).href
        : pathToFileURL(require.resolve(specifier)).href
      return 'from ' + JSON.stringify(target)
    })
    const {default: Graph} = await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
    const graph = {
      nodes: [{id:'a',kind:'event',label:'合同包1',evidence_ids:['ev']},
        {id:'b',kind:'bid_subject',label:'某投标公司',evidence_ids:['ev']}],
      edges: Array.from({length: 10}, (_, i) => ({id:'edge-'+i,from:'a',to:'b',
        kind:i%2 ? 'awarded_to' : 'bid_participation_unknown',evidence_ids:['ev']})),
    }
    const app = createSSRApp(Graph, {graph})
    app.component('el-empty', {render: () => null})
    const html = await renderToString(app)
    const layout = buildGraphLayout({...graph,kindLabels:RELATION_LABELS})
    assert.equal((html.match(/class="edge-chip"/g)||[]).length, graph.edges.length)
    for (const edge of layout.edges) {
      const encoded = edge.id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const group = html.match(new RegExp('<g[^>]*data-edge-id="'+encoded+'"[^>]*>([\\s\\S]*?)</g>'))?.[1]
      assert.ok(group, edge.id + ' is rendered')
      const rect = group.match(/<rect[^>]*class="edge-chip"[^>]*>/)?.[0]
      assert.ok(rect, edge.id + ' has a label backdrop')
      assert.equal(Number(rect.match(/\sy="([^"]+)"/)[1]), edge.labelY-edge.chip.height/2,
        'Vue must render labelY, not the pre-collision startY')
      assert.match(group, new RegExp('y="'+(edge.labelY+4)+'"[^>]*class="edge-label"'))
    }
    assert.match(html, /结果未明确不代表未中标/)
    assert.match(html, /参与投标但原文未明确结果/)
    assert.match(html, /aria-label="已验证关系图"/)
    assert.match(html, /data-node-id="a"/)
})
