<template>
  <div class="relationship-graph">
    <div class="graph-legend" aria-label="关系图图例">
      <span v-for="(label, kind) in NODE_LABELS" :key="kind" class="legend-item">
        <span class="node-swatch" :style="{background: NODE_COLORS[kind]}" />{{ label }}
      </span>
      <span class="legend-divider" />
      <span class="legend-item"><i class="line-swatch won" />已中标</span>
      <span class="legend-item"><i class="line-swatch lost" />明确未中标</span>
      <span class="legend-item"><i class="line-swatch unknown" />参与投标但原文未明确结果</span>
    </div>
    <p class="graph-note">结果未明确不代表未中标。每条线均标注关系；箭头指向关系对象，点击线或标签可查看原文证据。组织名称不代表已核验法人身份。</p>
    <div class="graph-scroll">
      <svg v-if="nodes.length" :viewBox="`0 0 ${layout.width} ${layout.height}`"
        :style="{width: layout.width + 'px', height: layout.height + 'px'}" role="img" aria-label="已验证关系图">
        <!-- Separate paint layers: no later edge line may draw over earlier labels. -->
        <g v-for="edge in layout.edges" :key="'line-'+edge.id" class="relation-line" :class="relationTone(edge.kind)">
          <path :d="edgePath(edge)" class="edge-line" />
          <path :d="arrowPath(edge)" class="edge-tip" />
          <path v-if="edge.chip" :d="`M ${edge.labelX} ${edge.labelY+edge.chip.height/2} L ${edge.labelX} ${edge.endY}`" class="label-leader" />
          <path :d="edgePath(edge)" class="edge-hit" @click="inspect(edge)" />
        </g>
        <g v-for="edge in layout.edges" :key="edge.id" :data-edge-id="edge.id" :class="relationTone(edge.kind)"
          tabindex="0" role="button" :aria-label="edgeDescription(edge)" @click="inspect(edge)"
          @keydown.enter="inspect(edge)" @keydown.space.prevent="inspect(edge)">
          <title>{{ edgeDescription(edge) }}</title>
          <template v-if="edge.chip">
            <rect :x="edge.labelX-edge.chip.width/2" :y="edge.labelY-edge.chip.height/2"
              :width="edge.chip.width" :height="edge.chip.height" :rx="edge.chip.height/2" class="edge-chip" />
            <text :x="edge.labelX" :y="edge.labelY+4" text-anchor="middle" class="edge-label">{{ edge.kindLabel }}</text>
          </template>
        </g>
        <g v-for="node in nodes" :key="node.id" :data-node-id="node.id"
          :transform="`translate(${layout.positions[node.id].x},${layout.positions[node.id].y})`"
          tabindex="0" role="button" :aria-label="`${NODE_LABELS[node.kind] || node.kind}：${node.label}`"
          @click="$emit('evidence', node.evidence_ids)" @keydown.enter="$emit('evidence', node.evidence_ids)"
          @keydown.space.prevent="$emit('evidence', node.evidence_ids)">
          <rect :x="-layout.boxes[node.id].width/2" y="-20" :width="layout.boxes[node.id].width"
            height="40" rx="9" :fill="NODE_COLORS[node.kind] || '#eef1f5'" stroke="#c3cedd" />
          <text text-anchor="middle" y="4.5" class="node-label">{{ node.label }}</text>
          <title>{{ NODE_LABELS[node.kind] }}：{{ node.label }}；{{ node.identity_status }}</title>
        </g>
      </svg>
      <el-empty v-else description="没有可显示的已验证关系" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { buildGraphLayout, coalesceGraph, edgePath } from '../services/graphLayout'
import { RELATION_LABELS, NODE_LABELS, NODE_COLORS, relationLabel, relationTone } from '../services/graphLabels'

const props = defineProps({graph: {type: Object, default: () => ({nodes: [], edges: []})}})
const emit = defineEmits(['evidence'])
const graph = computed(() => coalesceGraph(props.graph))
const nodes = computed(() => graph.value.nodes)
const layout = computed(() => buildGraphLayout({...graph.value,
  kindLabels: {...Object.fromEntries(graph.value.edges.map(edge => [edge.kind, relationLabel(edge.kind)])), ...RELATION_LABELS}}))
const names = computed(() => new Map(nodes.value.map(node => [node.id, node.label])))
const edgeDescription = edge => `${names.value.get(edge.from)} → ${names.value.get(edge.to)}：${relationLabel(edge.kind)}`
const inspect = edge => emit('evidence', edge.evidence_ids)
const arrowPath = edge => {
  const direction = edge.endX >= edge.midX ? 1 : -1
  return `M ${edge.endX} ${edge.endY} L ${edge.endX-direction*6} ${edge.endY-3.5} L ${edge.endX-direction*6} ${edge.endY+3.5} Z`
}
</script>

<style scoped>
.graph-legend{display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;font-size:13px;color:#344054}
.legend-item{display:inline-flex;align-items:center;gap:7px}.node-swatch{width:14px;height:14px;border:1px solid #c3cedd;border-radius:4px}.legend-divider{height:18px;border-left:1px solid #d0d5dd}
.line-swatch{width:24px;border-top:2px solid currentColor}.line-swatch.unknown{border-top-style:dashed}
.graph-note{font-size:13px;line-height:1.7;color:#526078;margin:12px 0}.graph-scroll{overflow:auto;max-height:680px;border:1px solid #e3e8f0;border-radius:10px;background:linear-gradient(180deg,#fbfcfe,#f6f8fc)}
svg{display:block}g[role=button],.edge-hit{cursor:pointer}g[role=button]:focus{outline:2px solid #2879ce;outline-offset:2px}
.structure{color:#526b8c}.won{color:#16804c}.lost{color:#b54708}.unknown{color:#667085}
.edge-line{fill:none;stroke:currentColor;stroke-width:1.6}.unknown .edge-line{stroke-dasharray:6 4}.edge-tip{fill:currentColor}.edge-hit{fill:none;stroke:transparent;stroke-width:14}
.edge-chip{fill:#fff;stroke:currentColor;stroke-opacity:.4}.edge-label{font-size:11px;fill:currentColor;pointer-events:none}.label-leader{fill:none;stroke:currentColor;stroke-opacity:.25;stroke-width:1;stroke-dasharray:2 3;pointer-events:none}
.node-label{font-size:12px;fill:#22304a}g[role=button]:hover .edge-chip{stroke-width:2}
</style>
