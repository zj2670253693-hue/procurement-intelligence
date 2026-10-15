/**
 * Pure layout for the verified-relationship graph.
 *
 * The service returns nodes/edges only; every geometric decision lives here so it
 * can be tested without a browser. Design rules:
 *  - columns come from edge topology (longest-path rank), not a fixed kind order;
 *  - nodes never overlap, and a column is as wide as its widest box;
 *  - each edge leaves a node's right edge and enters the next node's left edge, so
 *    its vertical run happens strictly in the gutter between columns and can never
 *    be drawn across an unrelated node;
 *  - labels render once per (source, kind) group, on a measured backdrop.
 */

export const NODE = { width: 208, height: 40, gapX: 96, gapY: 22, paddingX: 14, minWidth: 132 }
export const LABEL = { height: 18, paddingX: 7, fontSize: 11, radius: 8, offset: 13 }

const charWidth = (character) => {
  const code = character.codePointAt(0)
  const wide = (code >= 0x1100 && code <= 0x115f) || (code >= 0x2e80 && code <= 0xa4cf) ||
    (code >= 0xac00 && code <= 0xd7a3) || (code >= 0xf900 && code <= 0xfaff) ||
    (code >= 0xfe30 && code <= 0xfe6f) || (code >= 0xff00 && code <= 0xff60) ||
    (code >= 0xffe0 && code <= 0xffe6) || (code >= 0x20000 && code <= 0x3fffd)
  return wide ? 1 : 0.56
}

/** Measured label box for a node: full text, never truncated. */
export function measureNode(label = '') {
  const text = String(label)
  const units = [...text].reduce((total, character) => total + charWidth(character), 0)
  const width = Math.max(NODE.minWidth, Math.ceil(units * 12 + NODE.paddingX * 2))
  return { text, width, height: NODE.height }
}

/** Measured chip box for an edge label. */
export function measureChip(label = '') {
  const text = String(label)
  const units = [...text].reduce((total, character) => total + charWidth(character), 0)
  return { text, width: Math.ceil(units * LABEL.fontSize + LABEL.paddingX * 2), height: LABEL.height }
}

const rankNodes = (nodes, edges) => {
  const outgoing = new Map(nodes.map(node => [node.id, []]))
  const incoming = new Map(nodes.map(node => [node.id, []]))
  for (const edge of edges) {
    if (!outgoing.has(edge.from) || !outgoing.has(edge.to)) continue
    outgoing.get(edge.from).push(edge.to)
    incoming.get(edge.to).push(edge.from)
  }
  const order = nodes.map(node => node.id)
  const rank = new Map(order.map(id => [id, 0]))
  // DFS layering: a back edge to a node already on the current path is skipped, so
  // a cycle can never drag its own source into a later column.
  const state = new Map(order.map(id => [id, 'new']))
  const visit = (id) => {
    state.set(id, 'active')
    for (const next of outgoing.get(id) || []) {
      if (state.get(next) === 'active') continue
      rank.set(next, Math.max(rank.get(next), rank.get(id) + 1))
      if (state.get(next) === 'new') visit(next)
    }
    state.set(id, 'done')
  }
  for (const id of order) if (state.get(id) === 'new') visit(id)
  // Sources pin to column 0; everything else keeps its longest incoming distance.
  for (const id of order) if (!incoming.get(id).length) rank.set(id, 0)
  return rank
}

export function buildGraphLayout(graph = {}) {
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : []
  const rawEdges = Array.isArray(graph.edges) ? graph.edges : []
  const known = new Set(nodes.map(node => node.id))
  const edges = rawEdges.filter(edge => known.has(edge.from) && known.has(edge.to))
  if (!nodes.length) return { positions: {}, boxes: {}, edges: [], width: 640, height: 240 }

  const rank = rankNodes(nodes, edges)
  const columns = new Map()
  for (const node of nodes) {
    const column = Math.max(0, rank.get(node.id) || 0)
    if (!columns.has(column)) columns.set(column, [])
    columns.get(column).push(node)
  }

  const boxes = {}
  for (const node of nodes) boxes[node.id] = measureNode(node.label)

  // Reserve gutter width for the widest chip, so a label can always sit beside its
  // own vertical run without being clipped by the next column.
  const kindText = (kind) => graph.kindLabels?.[kind] || ''
  const widestChip = Math.max(0, ...edges.map(edge => measureChip(kindText(edge.kind)).width))
  // A chip never covers the target box, so the gutter only has to fit a half chip.
  const gutter = NODE.gapX + (widestChip ? widestChip / 2 + LABEL.offset + 8 : 0)

  // Column x offsets: each column is as wide as its widest box.
  const columnWidths = new Map()
  for (const [column, members] of columns) {
    columnWidths.set(column, Math.max(...members.map(node => boxes[node.id].width)))
  }
  const columnX = new Map()
  let cursor = 0
  for (const column of [...columns.keys()].sort((a, b) => a - b)) {
    cursor += columnWidths.get(column) / 2
    columnX.set(column, cursor)
    cursor += columnWidths.get(column) / 2 + gutter
  }

  // Vertical: keep declaration order per column, but aim each node at the mean of
  // its already-placed neighbours, then separate overlapping boxes in one pass.
  const positions = {}
  const pitch = NODE.height + NODE.gapY
  const parentOf = new Map()
  for (const edge of edges) {
    const fromColumn = Math.max(0, rank.get(edge.from) || 0)
    const toColumn = Math.max(0, rank.get(edge.to) || 0)
    // Only a forward edge may claim parenthood; a back edge must never overwrite it.
    if (toColumn > fromColumn && !parentOf.has(edge.to)) parentOf.set(edge.to, edge.from)
  }
  for (const column of [...columns.keys()].sort((a, b) => a - b)) {
    const members = columns.get(column)
    const desired = members.map(node => {
      const parent = parentOf.get(node.id)
      if (parent && positions[parent]) return positions[parent].y
      return (members.indexOf(node) + 1) * pitch
    })
    let previous = -Infinity
    members.forEach((node, index) => {
      const minimum = Number.isFinite(previous) ? previous + pitch : pitch
      const y = Math.max(desired[index], minimum)
      positions[node.id] = { x: columnX.get(column), y }
      previous = y
    })
  }

  const labelled = new Set()
  const laidOutEdges = edges.map(edge => {
    const from = positions[edge.from]
    const to = positions[edge.to]
    const startX = from.x + boxes[edge.from].width / 2
    const endX = to.x - boxes[edge.to].width / 2
    // Direction follows real geometry, not rank: a wider box can share a rank and
    // still sit to the right of its neighbour.
    const forward = endX >= startX
    const label = kindText(edge.kind) || edge.kindLabel || ''
    const group = `${edge.from}|${edge.kind}`
    const showLabel = Boolean(label) && !labelled.has(group)
    if (showLabel) labelled.add(group)
    const chip = showLabel ? measureChip(label) : null
    const midX = forward ? startX + Math.max(18, (endX - startX) * 0.55) : startX + 18
    // The chip hangs beside the vertical run (never centred on it, so the edge is
    // not drawn through its own label). The reserved gutter guarantees it fits.
    const half = (chip?.width || 0) / 2
    const gap = 7
    // Ahead of the vertical run, clamped so the chip clears the target box (its own
    // right edge may reach the target's left edge, but never past it).
    const preferred = midX + gap + half
    const cap = endX + NODE.gapX / 2 - half
    const lowest = startX + half + 2
    const labelX = chip ? Math.min(Math.max(preferred, Math.min(lowest, cap)), cap) : (startX + endX) / 2
    return {
      ...edge,
      kindLabel: label,
      direction: forward ? 'forward' : 'backward',
      startX, startY: from.y, endX, endY: to.y, midX,
      chip,
      labelX: Number.isFinite(labelX) ? labelX : midX,
      labelY: from.y - LABEL.offset,
      labelWidth: chip?.width || 0,
      labelHeight: chip?.height || 0,
    }
  })

  const width = Math.max(640, ...[...columnX.values()].map(x => x + Math.max(...nodes.map(node => boxes[node.id].width)) / 2 + 24))
  const height = Math.max(240, ...Object.values(positions).map(position => position.y + NODE.height))
  return { positions, boxes, edges: laidOutEdges, width, height }
}

/** Rounded orthogonal path: horizontal stub, vertical run in the gutter, horizontal stub. */
export function edgePath(edge, radius = 10) {
  const { startX, startY, endX, endY, midX } = edge
  const direction = endX >= startX ? 1 : -1
  const run = Math.abs(endY - startY)
  const corner = Math.max(0, Math.min(radius, Math.abs(midX - startX), Math.abs(endX - midX), run / 2))
  if (run < 1) return `M ${startX} ${startY} L ${endX} ${endY}`
  return [
    `M ${startX} ${startY}`,
    `L ${midX - direction * corner} ${startY}`,
    `Q ${midX} ${startY} ${midX} ${startY + Math.sign(endY - startY) * corner}`,
    `L ${midX} ${endY - Math.sign(endY - startY) * corner}`,
    `Q ${midX} ${endY} ${midX + direction * corner} ${endY}`,
    `L ${endX} ${endY}`,
  ].join(' ')
}
