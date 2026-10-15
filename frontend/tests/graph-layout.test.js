import test from 'node:test'
import assert from 'node:assert/strict'
import { buildGraphLayout, coalesceGraph, NODE } from '../src/services/graphLayout.js'

// Mirrors the shape the API returns for one announcement: project -> packages ->
// bidders (some shared across packages) -> individual subjects.
const graph = () => ({
  nodes: [
    { id: 'p', kind: 'project', label: '2026年全省离退休干部数据治理服务项目' },
    { id: 'c1', kind: 'event', label: '合同包1' },
    { id: 'c2', kind: 'event', label: '合同包2' },
    { id: 'b1', kind: 'bid_subject', label: '济南时代确信信息安全测评有限公司' },
    { id: 'b2', kind: 'bid_subject', label: '山东云企易联管理咨询有限公司' },
    { id: 'b3', kind: 'bid_subject', label: '道普信息技术有限公司' },
    { id: 'b4', kind: 'bid_subject', label: '信元网络技术股份有限公司' },
    { id: 'o1', kind: 'organization', label: '济南时代确信信息安全测评有限公司' },
    { id: 'o2', kind: 'organization', label: '山东云企易联管理咨询有限公司' },
  ],
  edges: [
    { id: 'e1', from: 'p', to: 'c1', kind: 'package_of' },
    { id: 'e2', from: 'p', to: 'c2', kind: 'package_of' },
    { id: 'e3', from: 'c1', to: 'b1', kind: 'awarded_to' },
    { id: 'e4', from: 'c1', to: 'b2', kind: 'awarded_to' },
    { id: 'e5', from: 'c2', to: 'b3', kind: 'bid_participation_unknown' },
    { id: 'e6', from: 'c2', to: 'b4', kind: 'bid_participation_unknown' },
    { id: 'e7', from: 'b1', to: 'o1', kind: 'individual_subject' },
    { id: 'e8', from: 'b2', to: 'o2', kind: 'individual_subject' },
  ],
})

const overlaps = (a, b) => {
  const dx = Math.abs(a.x - b.x)
  const dy = Math.abs(a.y - b.y)
  return dx < NODE.width && dy < NODE.height
}

const KIND_LABELS = {
  package_of: '所属项目', awarded_to: '中标', bid_participation_unknown: '竞标·结果未知',
  individual_subject: '法人主体', bid_participation_won: '竞标·中标',
}
const layout = (value = graph()) => buildGraphLayout({ ...value, kindLabels: KIND_LABELS })

test('columns follow edge topology, not a fixed kind order', () => {
  const { positions } = layout()
  assert.ok(positions.p.x < positions.c1.x, 'project precedes its packages')
  assert.equal(positions.c1.x, positions.c2.x, 'siblings share a column')
  assert.ok(positions.c1.x < positions.b1.x, 'packages precede bidders')
  assert.ok(positions.b1.x < positions.o1.x, 'bidders precede their individual subjects')
})

test('no two node boxes overlap', () => {
  const { positions } = layout()
  const entries = Object.entries(positions)
  for (let i = 0; i < entries.length; i += 1) {
    for (let j = i + 1; j < entries.length; j += 1) {
      const [idA, a] = entries[i]
      const [idB, b] = entries[j]
      assert.ok(!overlaps(a, b), `${idA} overlaps ${idB}`)
    }
  }
})

test('edges are routed through the gutter between columns, never across a node', () => {
  const { positions, boxes, edges } = layout()
  const nodesById = Object.fromEntries(graph().nodes.map(node => [node.id, node]))
  assert.equal(edges.length, graph().edges.length)
  for (const edge of edges) {
    const from = positions[edge.from]
    const to = positions[edge.to]
    // A horizontal stub must leave the source's right edge and reach the target's
    // left edge, so the vertical run happens strictly between columns.
    assert.equal(edge.startX, from.x + boxes[edge.from].width / 2, 'leaves the source right edge')
    assert.equal(edge.endX, to.x - boxes[edge.to].width / 2, 'enters the target left edge')
    assert.equal(edge.startY, from.y, 'starts level with the source')
    assert.equal(edge.endY, to.y, 'ends level with the target')
    assert.ok(edge.midX > edge.startX && edge.midX < edge.endX, 'mid gutter is between them')
    for (const node of Object.values(nodesById)) {
      if (node.id === edge.from || node.id === edge.to) continue
      const other = positions[node.id]
      const half = boxes[node.id].width / 2
      const band = [Math.min(edge.startY, edge.endY), Math.max(edge.startY, edge.endY)]
      const crossesBand = other.y > band[0] - NODE.height / 2 && other.y < band[1] + NODE.height / 2
      const insideColumns = other.x + half > edge.startX - 1 && other.x - half < edge.endX + 1
      const inGutter = edge.midX > other.x + half || edge.midX < other.x - half
      assert.ok(!(crossesBand && insideColumns && !inGutter),
        `${edge.id} would run through ${node.id}`)
    }
  }
})

test('a backwards edge is routed beside the nodes instead of through them', () => {
  const chained = {
    nodes: [{ id: 'a', kind: 'project', label: '甲' },
            { id: 'b', kind: 'event', label: '乙' },
            { id: 'c', kind: 'bid_subject', label: '丙' }],
    edges: [{ id: 'x', from: 'a', to: 'b', kind: 'package_of' },
            { id: 'y', from: 'b', to: 'c', kind: 'awarded_to' },
            { id: 'z', from: 'c', to: 'a', kind: 'awarded_to' }],
  }
  const { positions, boxes, edges, width } = layout(chained)
  const edge = edges.find(item => item.id === 'z')
  assert.equal(edge.direction, 'backward')
  assert.equal(edge.startX, positions.c.x + boxes.c.width / 2)
  assert.equal(edge.endX, positions.a.x - boxes.a.width / 2)
  assert.ok(edge.endX < edge.startX, 'the target really is behind the source')
  assert.ok(edge.midX > edge.startX, 'the vertical run stays clear to the right')
  assert.ok(edge.midX < width, 'and does not run off the canvas')
})

test('every relation has its own label placed off the line', () => {
  const { edges } = layout()
  const labels = edges.filter(edge => edge.chip)
  // Distinct fact IDs are distinct relations, even when source/kind match.
  assert.deepEqual(labels.map(edge => edge.kindLabel).sort(),
    ['所属项目', '所属项目', '中标', '中标', '竞标·结果未知', '竞标·结果未知', '法人主体', '法人主体'].sort())
  for (const edge of labels) {
    // The chip hangs beside the vertical run, offset off the line itself.
    assert.ok(edge.labelY < edge.endY && edge.endY - edge.labelY >= 10, 'chip sits above the target segment')
    assert.ok(edge.labelX - edge.labelWidth / 2 >= edge.startX - 1, 'chip does not cover the source node')
    assert.ok(edge.labelX + edge.labelWidth / 2 <= edge.endX, 'chip is never clipped by the target box')
    assert.ok(edge.labelX - edge.labelWidth / 2 > edge.midX, 'chip sits to the right of its vertical run')
    assert.ok(edge.midX > edge.startX && edge.midX < edge.endX, 'the run stays inside the gutter')
    assert.ok(edge.labelY < edge.endY, 'label is offset off the target segment, not on it')
    assert.ok(edge.labelWidth > 0 && edge.labelHeight > 0, 'label has a measurable box for its backdrop')
    assert.ok(edge.chip && edge.chip.width === edge.labelWidth, 'chip and label agree')
  }
  const groups = {}
  for (const edge of edges) {
    const key = `${edge.from}|${edge.kind}`
    groups[key] = (groups[key] || 0) + 1
  }
  const multi = Object.entries(groups).filter(([, count]) => count > 1)
  assert.ok(multi.length >= 2, 'the fixture really does contain shared labels')
  for (const [key] of multi) {
    const shown = edges.filter(edge => `${edge.from}|${edge.kind}` === key && edge.chip).length
    assert.equal(shown, groups[key], `${key} must label every separate fact, saw ${shown}`)
  }
})

test('dense incoming relations have visible nonoverlapping labels inside the canvas', () => {
  const graph = {
    nodes: [{id:'source',kind:'event',label:'合同包1'}, {id:'target',kind:'bid_subject',label:'某公司'}],
    edges: Array.from({length: 16}, (_, index) => ({id:'fact-'+index,from:'source',to:'target',
      kind:index % 2 ? 'package_of' : 'bid_participation_unknown'})),
  }
  const value = layout(graph)
  const chips = value.edges.filter(edge => edge.chip)
  assert.equal(chips.length, graph.edges.length)
  for (const chip of chips) {
    assert.ok(chip.labelY - chip.labelHeight / 2 >= 0, 'top chip must not be clipped')
    assert.ok(chip.labelY + chip.labelHeight / 2 <= value.height, 'bottom chip is inside the canvas')
    for (const other of chips) {
      if (chip.id >= other.id) continue
      const horizontal = Math.abs(chip.labelX - other.labelX) < (chip.labelWidth + other.labelWidth) / 2
      const vertical = Math.abs(chip.labelY - other.labelY) < (chip.labelHeight + other.labelHeight) / 2
      assert.ok(!(horizontal && vertical), chip.id + ' overlaps ' + other.id)
    }
  }
})

test('labels are not truncated and long names keep their full text', () => {
  const { positions, boxes } = layout()
  const title = '2026年全省离退休干部数据治理服务项目'
  const company = '济南时代确信信息安全测评有限公司'
  assert.equal(boxes.p.text, title, 'long project title is kept in full')
  assert.equal(boxes.b1.text, company, 'a 16-character company name is kept in full')
  assert.equal(company.length, 16, 'this is exactly where the old view started truncating')
  assert.ok(boxes.p.width > NODE.width, 'box grows for a long label')
  assert.ok(positions.p.x - boxes.p.width / 2 >= 0, 'long box stays inside the canvas')
})

test('chips never overlap each other, even from different sources', () => {
  // Two edges into the same column whose chips would otherwise land on top of one
  // another: one long label from a nearer source, one short label from further away.
  const crowded = {
    nodes: [{ id: 'a', kind: 'project', label: '甲项目' },
            { id: 'b', kind: 'project', label: '乙项目' },
            { id: 'x', kind: 'event', label: '合同包一' },
            { id: 'y', kind: 'event', label: '合同包二' },
            { id: 'z', kind: 'bid_subject', label: '某投标公司' }],
    edges: [{ id: 'p', from: 'a', to: 'z', kind: 'procured_by' },
            { id: 'q', from: 'b', to: 'z', kind: 'bid_participation_unknown' },
            { id: 'r', from: 'x', to: 'y', kind: 'package_of' }],
  }
  const { edges } = layout(crowded)
  const chips = edges.filter(edge => edge.chip)
  assert.ok(chips.length >= 2, 'fixture must produce at least two chips')
  for (let i = 0; i < chips.length; i += 1) {
    for (let j = i + 1; j < chips.length; j += 1) {
      const a = chips[i]
      const b = chips[j]
      const horizontal = Math.abs(a.labelX - b.labelX) < (a.labelWidth + b.labelWidth) / 2
      const vertical = Math.abs(a.labelY - b.labelY) < (a.labelHeight + b.labelHeight) / 2
      assert.ok(!(horizontal && vertical), a.id + ' chip overlaps ' + b.id)
    }
  }
})
test('same-label packages keep their distinct fact identities and evidence', () => {
  const repeated = {
    nodes: [{ id: 'e1', kind: 'event', label: '合同包1', evidence_ids: ['a'] },
            { id: 'e2', kind: 'event', label: '合同包1', evidence_ids: ['b'] },
            { id: 'e3', kind: 'event', label: '合同包2', evidence_ids: ['c'] },
            { id: 'w', kind: 'bid_subject', label: '某公司', evidence_ids: ['d'] }],
    edges: [{ id: 'x', from: 'e1', to: 'w', kind: 'awarded_to', evidence_ids: ['a1'] },
            { id: 'y', from: 'e2', to: 'w', kind: 'awarded_to', evidence_ids: ['a2'] },
            { id: 'z', from: 'e3', to: 'w', kind: 'awarded_to', evidence_ids: ['a3'] }],
  }
  const merged = coalesceGraph(repeated)
  assert.equal(merged.nodes.length, 4, 'same label is not proof of the same package')
  assert.equal(merged.merged, 0)
  const packages = merged.nodes.filter(node => node.label === '合同包1')
  assert.equal(packages.length, 2)
  assert.deepEqual(packages.map(p => [p.id, p.evidence_ids]), [['e1', ['a']], ['e2', ['b']]])
  assert.equal(merged.edges.length, 3, 'separate package awards remain separate')
  const winnerEdge = merged.edges.find(edge => edge.from === packages[0].id)
  assert.deepEqual(winnerEdge.evidence_ids, ['a1'])
  const layout = buildGraphLayout({ ...merged, kindLabels: KIND_LABELS })
  assert.equal(layout.edges.length, 3)
  for (const edge of layout.edges) {
    assert.notEqual(edge.startX, edge.endX, 'a merged edge still spans real distance')
    assert.ok(edge.midX > edge.startX && edge.midX < edge.endX)
  }
})

test('same package names in different projects cannot create cross-project awards', () => {
  const input = {
    nodes: [{id:'pa',kind:'project',label:'甲项目'}, {id:'pb',kind:'project',label:'乙项目'},
      {id:'ea',kind:'event',label:'合同包1'}, {id:'eb',kind:'event',label:'合同包1'},
      {id:'wa',kind:'bid_subject',label:'甲公司'}, {id:'wb',kind:'bid_subject',label:'乙公司'}],
    edges: [{id:'a',from:'ea',to:'pa',kind:'package_of'}, {id:'b',from:'eb',to:'pb',kind:'package_of'},
      {id:'c',from:'ea',to:'wa',kind:'awarded_to'}, {id:'d',from:'eb',to:'wb',kind:'awarded_to'}],
  }
  const result = coalesceGraph(input)
  assert.deepEqual(result.nodes.map(n=>n.id), input.nodes.map(n=>n.id))
  assert.deepEqual(result.edges.map(e=>[e.from,e.to]), input.edges.map(e=>[e.from,e.to]))
  assert.deepEqual(result.edges.filter(e=>e.from==='ea').map(e=>e.to), ['pa','wa'])
  assert.deepEqual(result.edges.filter(e=>e.from==='eb').map(e=>e.to), ['pb','wb'])
})

test('same-label unresolved projects and companies remain distinct', () => {
  const nodes = ['project','organization','bid_subject'].flatMap(kind=>[
    {id:kind+'1',kind,label:'同名记录',identity_status:'identity_unresolved'},
    {id:kind+'2',kind,label:'同名记录',identity_status:'identity_unresolved'},
  ])
  assert.deepEqual(coalesceGraph({nodes,edges:[]}).nodes.map(n=>n.id), nodes.map(n=>n.id))
})

test('only duplicate fact IDs may combine evidence without rewriting edge identity', () => {
  const input = {
    nodes:[{id:'p',kind:'event',label:'合同包1',evidence_ids:['a']},
      {id:'p',kind:'event',label:'合同包1',evidence_ids:['b']},
      {id:'w',kind:'bid_subject',label:'甲公司'}],
    edges:[{id:'award-a',from:'p',to:'w',kind:'awarded_to',evidence_ids:['a']},
      {id:'award-b',from:'p',to:'w',kind:'awarded_to',evidence_ids:['b']}],
  }
  const result = coalesceGraph(input)
  assert.equal(result.nodes.length,2)
  assert.deepEqual(result.nodes[0].evidence_ids,['a','b'])
  assert.deepEqual(result.edges.map(e=>e.id),['award-a','award-b'])
})
test('layout is deterministic and empty input is safe', () => {
  const first = layout()
  const second = layout()
  assert.deepEqual(first.positions, second.positions)
  const empty = buildGraphLayout({ nodes: [], edges: [] })
  assert.deepEqual(empty.positions, {})
  assert.deepEqual(empty.edges, [])
  assert.ok(empty.width > 0 && empty.height > 0)
})

test('cycles and unknown node references do not hang or throw', () => {
  const cyclic = {
    nodes: [{ id: 'a', kind: 'project', label: 'A' }, { id: 'b', kind: 'event', label: 'B' }],
    edges: [{ id: 'x', from: 'a', to: 'b', kind: 'package_of' },
            { id: 'y', from: 'b', to: 'a', kind: 'package_of' },
            { id: 'z', from: 'a', to: 'missing', kind: 'package_of' }],
  }
  const result = layout(cyclic)
  assert.ok(result.positions.a && result.positions.b)
  assert.equal(result.edges.length, 2, 'the dangling edge is dropped, not rendered')
})
