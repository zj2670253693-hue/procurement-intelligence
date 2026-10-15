/**
 * Audit a rendered graph SVG for the defects that only show up visually:
 *  - a chip that is crossed by an edge whose vertical run is not its own;
 *  - chips overlapping each other;
 *  - too many vertical runs packed into one narrow band.
 * Usage: node tools/audit-graph-svg.mjs <payload.json> <scene> [<scene> ...]
 */
import { readFileSync } from 'node:fs'
import { buildGraphLayout, coalesceGraph } from '../src/services/graphLayout.js'
import { RELATION_LABELS as KIND_LABELS } from '../src/services/graphLabels.js'

const payload = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const withGraph = payload.samples.filter(entry => entry.graph)
const requested = process.argv.slice(3)
const chosen = requested.length
  ? withGraph.filter(entry => requested.includes(entry.kind))
  : withGraph
const report = []

for (const sample of chosen) {
  const scene = sample.kind + ':' + String(sample.selector).slice(0, 10)
  const graph = sample.graph
  const merged = coalesceGraph(graph)
  const layout = buildGraphLayout({ nodes: merged.nodes, edges: merged.edges, kindLabels: KIND_LABELS })

  const runs = layout.edges.map(edge => ({
    id: edge.id, from: edge.from, to: edge.to,
    x: edge.midX, yTop: Math.min(edge.startY, edge.endY), yBottom: Math.max(edge.startY, edge.endY),
  }))
  const chips = layout.edges.filter(edge => edge.chip).map(edge => ({
    id: edge.id, kind: edge.kindLabel,
    left: edge.labelX - edge.chip.width / 2, right: edge.labelX + edge.chip.width / 2,
    top: edge.labelY - edge.chip.height / 2, bottom: edge.labelY + edge.chip.height / 2,
  }))

  const crossed = []
  for (const chip of chips) {
    for (const run of runs) {
      if (run.id === chip.id) continue
      const insideX = run.x > chip.left + 1 && run.x < chip.right - 1
      const overlapsY = run.yBottom > chip.top + 1 && run.yTop < chip.bottom - 1
      if (insideX && overlapsY) crossed.push({ chip: chip.kind, chipEdge: chip.id, crossedBy: run.id })
    }
  }

  const chipCollisions = []
  for (let i = 0; i < chips.length; i += 1) {
    for (let j = i + 1; j < chips.length; j += 1) {
      const a = chips[i]
      const b = chips[j]
      if (Math.abs(a.left + a.right - b.left - b.right) / 2 < (a.right - a.left + b.right - b.left) / 2
        && Math.abs(a.top + a.bottom - b.top - b.bottom) / 2 < (a.bottom - a.top + b.bottom - b.top) / 2) {
        chipCollisions.push([a.kind, b.kind])
      }
    }
  }

  // vertical runs grouped into tight bands
  const sorted = [...new Set(runs.map(run => Number(run.x.toFixed(1))))].sort((a, b) => a - b)
  const bands = []
  for (const x of sorted) {
    const band = bands[bands.length - 1]
    if (band && x - band.max <= 6) { band.max = x; band.count += 1 } else bands.push({ min: x, max: x, count: 1 })
  }
  const widestBand = bands.reduce((worst, band) => (band.count > (worst?.count || 0) ? band : worst), null)

  report.push({
    scene, selector: String(sample.selector).slice(0, 24),
    nodes: merged.nodes.length, rawNodes: graph.nodes.length, mergedAway: merged.merged,
    edges: merged.edges.length, rawEdges: graph.edges.length, chips: chips.length,
    canvas: { width: Math.round(layout.width), height: Math.round(layout.height) },
    nodeKinds: graph.nodes.reduce((acc, node) => ({ ...acc, [node.kind]: (acc[node.kind] || 0) + 1 }), {}),
    chipsCrossedByForeignEdge: crossed,
    chipCollisions,
    laneBands: bands.map(band => ({ width: Number((band.max - band.min).toFixed(1)), count: band.count })),
    widestLaneBand: widestBand ? { width: Number((widestBand.max - widestBand.min).toFixed(1)), count: widestBand.count } : null,
  })
}

console.log(JSON.stringify(report, null, 1))
