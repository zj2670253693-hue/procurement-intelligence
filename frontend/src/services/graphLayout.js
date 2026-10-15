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
 *  - every fact has its own measured label, with collision-adjusted coordinates.
 */

export const NODE = { width: 208, height: 40, gapX: 96, gapY: 22, paddingX: 14, minWidth: 132 }
export const LABEL = { height: 18, paddingX: 7, fontSize: 11, radius: 8, offset: 13, gap: 4 }

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
  const order = nodes.map(node => node.id)
  const outgoing = new Map(order.map(id => [id, []]))
  for (const edge of edges) {
    if (!outgoing.has(edge.from) || !outgoing.has(edge.to)) continue
    outgoing.get(edge.from).push(edge.to)
  }
  // Pass A: deterministic DFS that records the edges which close a cycle. Those are
  // the only edges that may legitimately point backwards.
  const mark = new Map(order.map(id => [id, 'new']))
  const closers = new Set()
  const visit = (id) => {
    mark.set(id, 'active')
    for (const next of outgoing.get(id) || []) {
      if (mark.get(next) === 'active') closers.add(id + '>' + next)
      else if (mark.get(next) === 'new') visit(next)
    }
    mark.set(id, 'done')
  }
  for (const id of order) if (mark.get(id) === 'new') visit(id)

  // Pass B: longest-path layering over the acyclic remainder, in a stable order, so
  // no node can land in a column earlier than the forward edge that reaches it.
  const forward = edges.filter(edge => outgoing.has(edge.from) && outgoing.has(edge.to)
    && !closers.has(edge.from + '>' + edge.to))
  const indegree = new Map(order.map(id => [id, 0]))
  for (const edge of forward) indegree.set(edge.to, indegree.get(edge.to) + 1)
  const position = new Map(order.map((id, index) => [id, index]))
  const sorted = [...order].sort((a, b) => {
    const difference = indegree.get(a) - indegree.get(b)
    return difference !== 0 ? difference : position.get(a) - position.get(b)
  })
  const rank = new Map(order.map(id => [id, 0]))
  for (const id of sorted) {
    for (const edge of forward) {
      if (edge.from !== id) continue
      const candidate = rank.get(id) + 1
      if (candidate > rank.get(edge.to)) rank.set(edge.to, candidate)
    }
  }
  for (const id of order) if (!indegree.get(id)) rank.set(id, 0)
  return rank
}

/**
 * Coalesce repeated copies of the SAME source fact ID only. A display label is
 * not an identity: different projects can each have a "合同包1", and unresolved
 * organizations can have the same name. Keep each edge's own fact identity too.
 */
export function coalesceGraph(graph = {}) {
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : []
  const edges = Array.isArray(graph.edges) ? graph.edges : []
  const memberOf = new Map()
  const merged = new Map()
  for (const node of nodes) {
    const key = node.id
    const first = merged.get(key)
    if (!first) {
      const copy = { ...node, evidence_ids: [...(node.evidence_ids || [])] }
      merged.set(key, copy)
      memberOf.set(node.id, copy.id)
    } else {
      first.evidence_ids = [...new Set([...first.evidence_ids, ...(node.evidence_ids || [])])]
      memberOf.set(node.id, first.id)
    }
  }
  const byKey = new Map()
  const mergedEdges = []
  for (const edge of edges) {
    const from = memberOf.get(edge.from)
    const to = memberOf.get(edge.to)
    if (!from || !to) continue
    const key = edge.id
    const existing = byKey.get(key)
    if (existing) {
      existing.evidence_ids = [...new Set([...existing.evidence_ids, ...(edge.evidence_ids || [])])]
      continue
    }
    const copy = { ...edge, from, to, evidence_ids: [...(edge.evidence_ids || [])] }
    byKey.set(key, copy)
    mergedEdges.push(copy)
  }
  return { nodes: [...merged.values()], edges: mergedEdges, truncated: graph.truncated,
           merged: nodes.length - merged.size }
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
  // Gutter layout, left to right: [vertical lanes][gap][chip][clearance]. Reserving a
  // FULL chip width is what stops long labels from being clipped by the next column.
  const laneCount = Math.max(1, ...[...new Set(edges.map(edge => (rank.get(edge.from) || 0) + '->' + (rank.get(edge.to) || 0)))]
    .map(key => edges.filter(edge => (rank.get(edge.from) || 0) + '->' + (rank.get(edge.to) || 0) === key).length))
  const laneSpan = Math.min(48, 12 + laneCount * 8)
  const chipGap = 6
  const clearance = 12
  const gutter = NODE.gapX + laneSpan + chipGap + widestChip + clearance

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

  // One vertical lane per edge inside each gutter, so converging edges do not stack
  // into a single thick line.
  const lanes = new Map()
  for (const edge of edges) {
    const fromColumn = Math.max(0, rank.get(edge.from) || 0)
    const toColumn = Math.max(0, rank.get(edge.to) || 0)
    if (toColumn <= fromColumn) continue
    const key = fromColumn + '->' + toColumn
    if (!lanes.has(key)) lanes.set(key, [])
    if (!lanes.get(key).includes(edge.id)) lanes.get(key).push(edge.id)
  }
  const laneIndex = new Map()
  for (const ids of lanes.values()) ids.forEach((id, index) => laneIndex.set(id, index))

  const laidOutEdges = edges.map(edge => {
    const from = positions[edge.from]
    const to = positions[edge.to]
    const fromColumn = Math.max(0, rank.get(edge.from) || 0)
    const toColumn = Math.max(0, rank.get(edge.to) || 0)
    const startX = from.x + boxes[edge.from].width / 2
    const endX = to.x - boxes[edge.to].width / 2
    // Direction follows real geometry, not rank: a wider box can share a rank and
    // still sit to the right of its neighbour.
    const forward = endX >= startX
    const label = kindText(edge.kind) || edge.kindLabel || ''
    const chip = label ? measureChip(label) : null
    const lane = laneIndex.get(edge.id) || 0
    let midX
    if (forward && toColumn > fromColumn) {
      const gutterStart = columnX.get(fromColumn) + columnWidths.get(fromColumn) / 2
      const spread = Math.min(4, laneCount > 1 ? (laneSpan - 12) / (laneCount - 1) : 0)
      midX = gutterStart + 8 + lane * spread
    } else {
      midX = startX + 18 + Math.min(lane, 3) * 6
    }
    // The chip hugs the target box: its right edge is the inner boundary of the
    // gutter, so it can never be clipped, and the vertical run sits to its left.
    const labelX = chip ? endX - clearance - chip.width / 2 : (startX + endX) / 2
    return {
      ...edge,
      kindLabel: label,
      direction: forward ? 'forward' : 'backward',
      startX, startY: from.y, endX, endY: to.y, midX,
      chip,
      labelX: Number.isFinite(labelX) ? labelX : midX,
      labelY: to.y - LABEL.offset,
      labelWidth: chip?.width || 0,
      labelHeight: chip?.height || 0,
    }
  })

  // Chips from different sources can share a gutter; place them top-down and lift
  // any that would overlap, so no two labels ever sit on top of each other.
  const placedChips = []
  const chipsOverlap = (a, b) => Math.abs(a.labelX - b.labelX) < (a.labelWidth + b.labelWidth) / 2 + LABEL.gap
    && Math.abs(a.labelY - b.labelY) < (a.labelHeight + b.labelHeight) / 2 + LABEL.gap
  for (const edge of laidOutEdges) {
    if (!edge.chip) continue
    const candidate = { labelX: edge.labelX, labelY: edge.labelY, labelWidth: edge.labelWidth, labelHeight: edge.labelHeight }
    // A fixed six-step cap silently reintroduced overlaps in dense graphs. Jump
    // above the conflicting chip each time; the finite placed list bounds this.
    let collision = placedChips.find(other => chipsOverlap(candidate, other))
    while (collision) {
      candidate.labelY = collision.labelY - (candidate.labelHeight + collision.labelHeight) / 2 - LABEL.gap
      collision = placedChips.find(other => chipsOverlap(candidate, other))
    }
    edge.labelY = candidate.labelY
    placedChips.push(candidate)
  }

  // Lifted chips can extend above row zero. Translate ALL geometry together so
  // the browser's real viewBox cannot crop labels after collision avoidance.
  const margin = 16
  const minX = Math.min(...nodes.map(node => positions[node.id].x - boxes[node.id].width / 2),
    ...placedChips.map(chip => chip.labelX - chip.labelWidth / 2))
  const minY = Math.min(...nodes.map(node => positions[node.id].y - boxes[node.id].height / 2),
    ...placedChips.map(chip => chip.labelY - chip.labelHeight / 2))
  const shiftX = Math.max(0, margin - minX)
  const shiftY = Math.max(0, margin - minY)
  for (const position of Object.values(positions)) { position.x += shiftX; position.y += shiftY }
  for (const edge of laidOutEdges) {
    edge.startX += shiftX; edge.endX += shiftX; edge.midX += shiftX; edge.labelX += shiftX
    edge.startY += shiftY; edge.endY += shiftY; edge.labelY += shiftY
  }
  const width = Math.max(640,
    ...nodes.map(node => positions[node.id].x + boxes[node.id].width / 2 + margin),
    ...laidOutEdges.map(edge => Math.max(edge.midX, edge.labelX + edge.labelWidth / 2) + margin))
  const height = Math.max(240,
    ...nodes.map(node => positions[node.id].y + boxes[node.id].height / 2 + margin),
    ...laidOutEdges.map(edge => edge.labelY + edge.labelHeight / 2 + margin))
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
