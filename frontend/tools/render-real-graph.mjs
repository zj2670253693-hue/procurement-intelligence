/**
 * Render a REAL API graph payload (saved by the server-side fetch) to SVG/PNG,
 * so layout changes are checked against the data the user actually sees.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { buildGraphLayout, coalesceGraph, edgePath, NODE } from '../src/services/graphLayout.js'

const KIND_LABELS = {
  procured_by: '采购单位', package_of: '所属项目', individual_subject: '法人主体',
  consortium_member: '联合体成员', awarded_to: '中标', product_supplier: '明示产品供应商',
  award_only_won: '仅中标证据', bid_participation_won: '竞标·中标',
  bid_participation_lost: '竞标·未中标', bid_participation_unknown: '竞标·结果未知',
}
const COLORS = { project: '#d9eafa', event: '#dff2e6', bid_subject: '#ffebcc', organization: '#e9e1f4' }

const source = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const wanted = process.argv[4] || 'buyer_bidders'
const sample = source.samples.find(entry => entry.kind === wanted && entry.graph)
if (!sample) {
  console.error('no graph for ' + wanted)
  process.exit(1)
}
const graph = coalesceGraph(sample.graph)  // same merge the view performs
const layout = buildGraphLayout({ nodes: graph.nodes, edges: graph.edges, kindLabels: KIND_LABELS })
const escape = (text) => String(text).replace(/[&<>]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[character]))

const parts = []
parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${layout.width + 48}" height="${layout.height + 48}" viewBox="-24 -24 ${layout.width + 48} ${layout.height + 48}" font-family="Microsoft YaHei, PingFang SC, sans-serif">`)
parts.push(`<rect x="-24" y="-24" width="100%" height="100%" fill="#f6f8fc"/>`)
for (const edge of layout.edges) {
  parts.push(`<path d="${edgePath(edge)}" fill="none" stroke="#98a9c0" stroke-width="1.6"/>`)
  parts.push(`<circle cx="${edge.endX}" cy="${edge.endY}" r="3" fill="#98a9c0"/>`)
  if (edge.chip) {
    parts.push(`<rect x="${edge.labelX - edge.chip.width / 2}" y="${edge.startY - 13 - edge.chip.height / 2}" width="${edge.chip.width}" height="${edge.chip.height}" rx="${edge.chip.height / 2}" fill="#fff" stroke="#d6dee9"/>`)
    parts.push(`<text x="${edge.labelX}" y="${edge.startY - 13 + 4}" text-anchor="middle" font-size="11" fill="#4a5a72">${escape(edge.kindLabel)}</text>`)
  }
}
const problems = []
for (const node of graph.nodes) {
  const box = layout.boxes[node.id]
  const position = layout.positions[node.id]
  parts.push(`<g transform="translate(${position.x},${position.y})">`)
  parts.push(`<rect x="${-box.width / 2}" y="${-NODE.height / 2}" width="${box.width}" height="${NODE.height}" rx="9" fill="${COLORS[node.kind] || '#eee'}" stroke="#c3cedd"/>`)
  parts.push(`<text text-anchor="middle" y="4.5" font-size="12" fill="#22304a">${escape(node.label)}</text></g>`)
  // collision audit: does any other node's box overlap this one?
  for (const other of graph.nodes) {
    if (other.id === node.id) continue
    const o = layout.positions[other.id]
    const ob = layout.boxes[other.id]
    if (Math.abs(position.x - o.x) < (box.width + ob.width) / 2 && Math.abs(position.y - o.y) < NODE.height) {
      problems.push(`${node.id} overlaps ${other.id}`)
    }
  }
}
for (const edge of layout.edges) {
  if (edge.chip && edge.labelX + edge.chip.width / 2 > edge.endX + 0.01) problems.push(`${edge.id} label clipped`)
  if (edge.chip && edge.labelX - edge.chip.width / 2 <= edge.midX) problems.push(`${edge.id} label over its run`)
}
parts.push('</svg>')
writeFileSync(process.argv[3] || 'real-graph.svg', parts.join('\n'))
console.log(JSON.stringify({ scene: wanted, selector: sample.selector, nodes: graph.nodes.length,
  edges: graph.edges.length, chips: layout.edges.filter(e => e.chip).length,
  width: Math.round(layout.width), height: Math.round(layout.height), problems }, null, 1))
