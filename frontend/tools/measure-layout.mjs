/**
 * Measure the client-side work a relationship graph costs in the browser bundle.
 *
 * A real browser paint cannot be measured from here, so this times the pure functions the
 * view actually runs on a real API payload: coalesceGraph + buildGraphLayout + edgePath.
 * Run with: node tools/measure-layout.mjs <graph-payload.json>
 */
import { readFileSync } from 'node:fs'
import { buildGraphLayout, coalesceGraph, edgePath } from '../src/services/graphLayout.js'
import { RELATION_LABELS as KIND_LABELS } from '../src/services/graphLabels.js'

const source = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const samples = (source.samples || []).filter(entry => entry.graph && entry.graph.nodes?.length)

function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))]
}

function measure(graph) {
  const timings = []
  let last = null
  for (let index = 0; index < 60; index += 1) {
    const started = process.hrtime.bigint()
    const merged = coalesceGraph(graph)
    const layout = buildGraphLayout({ nodes: merged.nodes, edges: merged.edges, kindLabels: KIND_LABELS })
    for (const edge of layout.edges) edgePath(edge)
    timings.push(Number(process.hrtime.bigint() - started) / 1e6)
    if (index === 59) last = layout
  }
  return { timings, layout: last }
}

const rows = []
for (const sample of samples) {
  const merged = coalesceGraph(sample.graph)
  const { timings, layout } = measure(sample.graph)
  rows.push({
    kind: sample.kind,
    raw_nodes: sample.graph.nodes.length,
    raw_edges: sample.graph.edges.length,
    merged_nodes: merged.nodes.length,
    merged_edges: merged.edges.length,
    chips: layout.edges.filter(edge => edge.chip).length,
    p50_ms: Number(percentile(timings, 0.5).toFixed(2)),
    p95_ms: Number(percentile(timings, 0.95).toFixed(2)),
    max_ms: Number(Math.max(...timings).toFixed(2)),
  })
}

const all = rows.flatMap(row => [row.p50_ms, row.p95_ms, row.max_ms])
const summary = {
  graphs: rows.length,
  worst_p50_ms: Math.max(...rows.map(row => row.p50_ms)),
  worst_p95_ms: Math.max(...rows.map(row => row.p95_ms)),
  worst_max_ms: Math.max(...rows.map(row => row.max_ms)),
  note: 'coalesceGraph + buildGraphLayout + edgePath for every edge, 60 runs per graph; '
      + 'this is layout computation only, not browser paint.',
}
console.log(JSON.stringify({ summary, rows }, null, 1))
