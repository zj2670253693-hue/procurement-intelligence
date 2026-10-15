/**
 * Render the layout module's output to a standalone SVG so the result can be
 * inspected as an image without a browser. Mirrors RelationshipsView's markup.
 */
import { writeFileSync } from 'node:fs'
import { buildGraphLayout, edgePath, NODE } from '../src/services/graphLayout.js'
import { RELATION_LABELS as KIND_LABELS } from '../src/services/graphLabels.js'
const COLORS = { project: '#d9eafa', event: '#dff2e6', bid_subject: '#ffebcc', organization: '#e9e1f4' }

// The exact shape from the screenshot the user reported, plus the extra edges the
// real query returns (some bidders are shared between packages).
const graph = {
  nodes: [
    { id: 'p', kind: 'project', label: '2026年全省离退休干部数据治理服务项目' },
    { id: 'c1', kind: 'event', label: '合同包1' },
    { id: 'c2', kind: 'event', label: '合同包2' },
    { id: 'b1', kind: 'bid_subject', label: '济南时代确信信息安全测评有限公司' },
    { id: 'b2', kind: 'bid_subject', label: '山东云企易联管理咨询有限公司' },
    { id: 'b3', kind: 'bid_subject', label: '道普信息技术有限公司' },
    { id: 'b4', kind: 'bid_subject', label: '信元网络技术股份有限公司' },
    { id: 'b5', kind: 'bid_subject', label: '山东人才发展集团信息技术有限公司' },
    { id: 'b6', kind: 'bid_subject', label: '山东巧思智能科技有限公司' },
    { id: 'b7', kind: 'bid_subject', label: '山东新潮信息技术有限公司' },
    { id: 'b8', kind: 'bid_subject', label: '山东维平信息安全测评技术有限公司' },
    { id: 'o1', kind: 'organization', label: '济南时代确信信息安全测评有限公司' },
    { id: 'o2', kind: 'organization', label: '山东云企易联管理咨询有限公司' },
    { id: 'o3', kind: 'organization', label: '道普信息技术有限公司' },
    { id: 'o4', kind: 'organization', label: '信元网络技术股份有限公司' },
    { id: 'o5', kind: 'organization', label: '山东人才发展集团信息技术有限公司' },
    { id: 'o6', kind: 'organization', label: '山东巧思智能科技有限公司' },
    { id: 'o7', kind: 'organization', label: '山东新潮信息技术有限公司' },
    { id: 'o8', kind: 'organization', label: '山东维平信息安全测评技术有限公司' },
  ],
  edges: [
    { id: 'e1', from: 'p', to: 'c1', kind: 'package_of', evidence_ids: ['x'] },
    { id: 'e2', from: 'p', to: 'c2', kind: 'package_of', evidence_ids: ['x'] },
    { id: 'e3', from: 'c1', to: 'b1', kind: 'awarded_to', evidence_ids: ['x'] },
    { id: 'e4', from: 'c1', to: 'b2', kind: 'awarded_to', evidence_ids: ['x'] },
    { id: 'e5', from: 'c2', to: 'b3', kind: 'bid_participation_unknown', evidence_ids: ['x'] },
    { id: 'e6', from: 'c2', to: 'b4', kind: 'bid_participation_unknown', evidence_ids: ['x'] },
    { id: 'e7', from: 'c2', to: 'b5', kind: 'bid_participation_unknown', evidence_ids: ['x'] },
    { id: 'e8', from: 'c1', to: 'b6', kind: 'bid_participation_unknown', evidence_ids: ['x'] },
    { id: 'e9', from: 'c1', to: 'b7', kind: 'bid_participation_unknown', evidence_ids: ['x'] },
    { id: 'e10', from: 'c2', to: 'b8', kind: 'bid_participation_unknown', evidence_ids: ['x'] },
    { id: 'e11', from: 'b1', to: 'o1', kind: 'individual_subject', evidence_ids: ['x'] },
    { id: 'e12', from: 'b2', to: 'o2', kind: 'individual_subject', evidence_ids: ['x'] },
    { id: 'e13', from: 'b3', to: 'o3', kind: 'individual_subject', evidence_ids: ['x'] },
    { id: 'e14', from: 'b4', to: 'o4', kind: 'individual_subject', evidence_ids: ['x'] },
    { id: 'e15', from: 'b5', to: 'o5', kind: 'individual_subject', evidence_ids: ['x'] },
    { id: 'e16', from: 'b6', to: 'o6', kind: 'individual_subject', evidence_ids: ['x'] },
    { id: 'e17', from: 'b7', to: 'o7', kind: 'individual_subject', evidence_ids: ['x'] },
    { id: 'e18', from: 'b8', to: 'o8', kind: 'individual_subject', evidence_ids: ['x'] },
  ],
  kindLabels: KIND_LABELS,
}

const layout = buildGraphLayout(graph)
if (process.env.GEOMDUMP) { for (const e of layout.edges) if (e.chip) console.log('GEO', e.id, e.from, '->', e.to, 'startX', e.startX, 'midX', e.midX, 'endX', e.endX, 'labelX', e.labelX, 'right', (e.labelX + e.chip.width / 2).toFixed(1), 'CLIP', e.labelX + e.chip.width / 2 > e.endX) }
const escape = (text) => String(text).replace(/[&<>]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[character]))
const parts = []
parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${layout.width + 48}" height="${layout.height + 48}" viewBox="-24 -24 ${layout.width + 48} ${layout.height + 48}" font-family="Microsoft YaHei, PingFang SC, sans-serif">`)
parts.push('<rect x="-24" y="-24" width="100%" height="100%" fill="#f6f8fc"/>')
for (const edge of layout.edges) {
  parts.push(`<path d="${edgePath(edge)}" fill="none" stroke="#98a9c0" stroke-width="1.6"/>`)
  parts.push(`<circle cx="${edge.endX}" cy="${edge.endY}" r="3.2" fill="#98a9c0"/>`)
  if (edge.chip) {
    parts.push(`<rect x="${edge.labelX - edge.chip.width / 2}" y="${edge.labelY - edge.chip.height / 2}" width="${edge.chip.width}" height="${edge.chip.height}" rx="${edge.chip.height / 2}" fill="#fff" stroke="#d6dee9"/>`)
    parts.push(`<text x="${edge.labelX}" y="${edge.labelY + 4}" text-anchor="middle" font-size="11" fill="#4a5a72">${escape(edge.kindLabel)}</text>`)
  }
}
for (const node of graph.nodes) {
  const box = layout.boxes[node.id]
  const position = layout.positions[node.id]
  parts.push(`<g transform="translate(${position.x},${position.y})">`)
  parts.push(`<rect x="${-box.width / 2}" y="${-NODE.height / 2}" width="${box.width}" height="${NODE.height}" rx="9" fill="${COLORS[node.kind] || '#eee'}" stroke="#c3cedd"/>`)
  parts.push(`<text text-anchor="middle" y="4.5" font-size="12" fill="#22304a">${escape(node.label)}</text></g>`)
}
parts.push('</svg>')
writeFileSync(process.argv[2] || 'graph-preview.svg', parts.join('\n'))
console.log(JSON.stringify({ width: layout.width, height: layout.height, nodes: graph.nodes.length,
  edges: layout.edges.length, chips: layout.edges.filter(edge => edge.chip).length }))
