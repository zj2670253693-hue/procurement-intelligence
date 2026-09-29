<template>
  <div v-loading="loading">
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="mb-16"
    />

    <el-row :gutter="16">
      <el-col v-for="card in cards" :key="card.label" :xs="24" :sm="8">
        <div class="stat">
          <div class="stat-icon" :style="{ background: card.bg, color: card.color }">
            <el-icon><component :is="card.icon" /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">{{ card.label }}</div>
            <div class="stat-value">
              {{ card.value }}
              <span v-if="card.suffix" class="stat-suffix">{{ card.suffix }}</span>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>

    <el-card shadow="never" class="mt-16">
      <template #header>
        <span>核心字段填充率</span>
        <span class="page-hint">数据来源：GET /api/stats</span>
      </template>

      <el-table :data="fieldRows" stripe>
        <el-table-column prop="label" label="字段" width="200">
          <template #default="{ row }">
            <span class="field-name">{{ row.label }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="count" label="非空条数" width="130" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.count }}</span>
          </template>
        </el-table-column>

        <el-table-column label="填充率" min-width="320">
          <template #default="{ row }">
            <div class="rate-cell">
              <el-progress
                :percentage="row.rate"
                :stroke-width="14"
                :color="rateColor(row.rate)"
                :show-text="false"
              />
              <span class="rate-text" :style="{ color: rateColor(row.rate) }">
                {{ row.rate.toFixed(1) }}%
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="结论" width="130">
          <template #default="{ row }">
            <el-tag :type="rateTag(row.rate)" effect="light" size="small">
              {{ rateLabel(row.rate) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

import { getStats } from '../api/task1'

const loading = ref(false)
const error = ref('')
const stats = ref({})

const cards = computed(() => [
  {
    label: '公告总数',
    value: stats.value.announcements ?? '—',
    icon: 'Files',
    color: '#2563eb',
    bg: '#eff6ff',
  },
  {
    label: '标的物总数',
    value: stats.value.entities ?? '—',
    icon: 'Goods',
    color: '#0d9488',
    bg: '#effcf9',
  },
  {
    label: '核心字段',
    value: fieldRows.value.length,
    suffix: '个',
    icon: 'Grid',
    color: '#7c3aed',
    bg: '#f5f3ff',
  },
])

const fieldRows = computed(() =>
  Object.entries(stats.value.field_fill || {}).map(([label, v]) => ({
    label,
    count: v.count,
    rate: v.rate,
  })),
)

function rateColor(rate) {
  if (rate >= 95) return '#10b981'
  if (rate >= 85) return '#2563eb'
  if (rate >= 70) return '#f59e0b'
  return '#ef4444'
}

function rateTag(rate) {
  if (rate >= 95) return 'success'
  if (rate >= 85) return 'primary'
  if (rate >= 70) return 'warning'
  return 'danger'
}

function rateLabel(rate) {
  if (rate >= 95) return '优秀'
  if (rate >= 85) return '良好'
  if (rate >= 70) return '待优化'
  return '需重点优化'
}

onMounted(async () => {
  loading.value = true
  try {
    stats.value = await getStats()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.mb-16 {
  margin-bottom: 16px;
}

.mt-16 {
  margin-top: 16px;
}

/* 统计卡片 */
.stat {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.2s ease, transform 0.2s ease;
  margin-bottom: 12px;
}

.stat:hover {
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}

.stat-icon {
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 11px;
  font-size: 21px;
}

.stat-label {
  font-size: 12.5px;
  color: var(--c-text-3);
}

.stat-value {
  font-size: 26px;
  font-weight: 650;
  line-height: 1.25;
  color: var(--c-text);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.5px;
}

.stat-suffix {
  font-size: 13px;
  font-weight: 400;
  color: var(--c-text-3);
  margin-left: 3px;
}

/* 表格内元素 */
.field-name {
  font-weight: 500;
}

.num {
  font-variant-numeric: tabular-nums;
  color: var(--c-text-2);
}

.rate-cell {
  display: flex;
  align-items: center;
  gap: 12px;
}

.rate-cell :deep(.el-progress) {
  flex: 1;
}

.rate-text {
  width: 52px;
  text-align: right;
  font-weight: 600;
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}
</style>