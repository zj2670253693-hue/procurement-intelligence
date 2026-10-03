<template>
  <div>
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <div class="toolbar-fields">
          <el-input
            v-model="query.keyword"
            placeholder="搜索 产品名称 / 品牌 / 规格型号 / 品目"
            clearable
            class="w-280"
            :prefix-icon="Search"
            @keyup.enter="onSearch"
          />

          <el-select v-model="query.field" placeholder="字段筛选：不限" clearable class="w-190">
            <el-option
              v-for="opt in fieldOptions"
              :key="opt.value"
              :label="`仅看有${opt.label}的`"
              :value="opt.value"
            />
          </el-select>

          <el-select v-model="query.page_size" class="w-110">
            <el-option v-for="n in [10, 20, 50, 100]" :key="n" :label="`每页 ${n} 条`" :value="n" />
          </el-select>
        </div>

        <div class="toolbar-actions">
          <el-button type="primary" :icon="Search" @click="onSearch">检索</el-button>
          <el-button :icon="RefreshLeft" @click="onReset">重置</el-button>
        </div>
      </div>
    </el-card>

    <el-card shadow="never" class="mt-16">
      <template #header>
        <div class="table-header">
          <span>检索结果</span>
          <span class="count-chip">
            共 <b>{{ total.toLocaleString() }}</b> 条标的物
          </span>
        </div>
      </template>

      <el-table v-loading="loading" :data="rows" stripe border height="560">
        <el-table-column type="index" label="#" width="56" align="center" fixed />

        <el-table-column
          prop="announcement_id"
          label="公告ID"
          width="170"
          fixed
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span class="mono">{{ row.announcement_id }}</span>
          </template>
        </el-table-column>

        <el-table-column
          v-for="col in columns"
          :key="col.prop"
          :prop="col.prop"
          :label="col.label"
          :width="col.width"
          :min-width="col.minWidth"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span :class="{ 'cell-empty': !row[col.prop] }">
              {{ row[col.prop] || '—' }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="104" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" :icon="View" @click="openAnnouncement(row.announcement_id)">
              公告
            </el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty description="没有匹配的标的物，试试换个关键词" />
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          background
          layout="total, prev, pager, next, jumper"
          :total="total"
          :current-page="query.page"
          :page-size="query.page_size"
          @current-change="onPageChange"
        />
      </div>
    </el-card>

    <el-drawer v-model="drawer.visible" size="76%" :with-header="false">
      <div class="drawer-head">
        <div>
          <div class="drawer-title">公告 {{ drawer.id }}</div>
          <div class="drawer-sub">
            <template v-if="announcement">
              原文 {{ announcement.html_length.toLocaleString() }} 字 ·
              附件 {{ announcement.attachment_files.length }} 个
              <span v-if="!announcement.source_exists" class="warn">（源文件已不可用，展示库内文本）</span>
            </template>
            <template v-else>提取出的标的物与公告原文</template>
          </div>
        </div>
        <el-button text :icon="Close" @click="drawer.visible = false" />
      </div>

      <el-alert
        v-if="drawer.error"
        :title="drawer.error"
        type="error"
        show-icon
        :closable="false"
        class="mb-16"
      />

      <el-tabs v-model="drawer.tab" @tab-change="onTabChange">
        <!-- 页签 1：提取结果 -->
        <el-tab-pane :label="`提取结果 (${drawer.rows.length})`" name="entities">
          <el-table v-loading="drawer.loading" :data="drawer.rows" stripe border max-height="600">
            <el-table-column type="index" label="#" width="56" align="center" />
            <el-table-column
              v-for="col in columns"
              :key="col.prop"
              :prop="col.prop"
              :label="col.label"
              :min-width="col.minWidth || 140"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                <span :class="{ 'cell-empty': !row[col.prop] }">
                  {{ row[col.prop] || '—' }}
                </span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- 页签 2：公告原文 -->
        <el-tab-pane label="公告原文" name="html">
          <div v-loading="drawer.loading" class="doc-wrap">
            <pre v-if="announcement?.html_text" class="doc-text">{{ announcement.html_text }}</pre>
            <el-empty v-else description="未获取到公告原文" />
          </div>
        </el-tab-pane>

        <!-- 页签 3：附件 -->
        <el-tab-pane :label="`附件 (${announcement?.attachment_files.length || 0})`" name="attachments">
          <div v-loading="drawer.attLoading" class="doc-wrap">
            <el-alert
              v-if="drawer.attMessage"
              :title="drawer.attMessage"
              type="warning"
              show-icon
              :closable="false"
              class="mb-16"
            />

            <el-collapse v-if="attachmentItems.length">
              <el-collapse-item v-for="(a, i) in attachmentItems" :key="i" :name="i">
                <template #title>
                  <span class="att-name">{{ a.name }}</span>
                  <span class="att-len">{{ a.length.toLocaleString() }} 字</span>
                </template>
                <pre class="doc-text">{{ a.text }}</pre>
              </el-collapse-item>
            </el-collapse>

            <el-empty
              v-else-if="!drawer.attLoading && !drawer.attMessage"
              description="该公告没有附件包"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Close, RefreshLeft, Search, View } from '@element-plus/icons-vue'

import {
  getAnnouncement,
  getAnnouncementAttachments,
  getEntitiesByAnnouncement,
  searchEntities,
} from '../api/task1'

const columns = [
  { prop: 'product_name', label: '产品服务名称', minWidth: 200 },
  { prop: 'category', label: '品目', minWidth: 150 },
  { prop: 'brand', label: '品牌（产品供应商）', minWidth: 160 },
  { prop: 'spec_model', label: '规格型号', minWidth: 190 },
  { prop: 'unit_price', label: '单价', width: 130 },
  { prop: 'quantity', label: '数量', width: 96 },
  { prop: 'total_price', label: '总价', width: 130 },
]

const fieldOptions = [
  { label: '单价', value: 'unit_price' },
  { label: '数量', value: 'quantity' },
  { label: '总价', value: 'total_price' },
  { label: '规格型号', value: 'spec_model' },
  { label: '品目', value: 'category' },
  { label: '品牌', value: 'brand' },
]

const loading = ref(false)
const rows = ref([])
const total = ref(0)

const query = reactive({
  keyword: '',
  field: '',
  page: 1,
  page_size: 20,
})

const drawer = reactive({
  visible: false,
  tab: 'entities',
  loading: false,
  error: '',
  id: '',
  rows: [],
  attLoading: false,
  attLoaded: false,
  attMessage: '',
})

const announcement = ref(null)
const attachmentItems = ref([])

async function load() {
  loading.value = true
  try {
    const data = await searchEntities({
      keyword: query.keyword,
      field: query.field,
      page: query.page,
      page_size: query.page_size,
    })
    rows.value = data.items
    total.value = data.total
  } catch (e) {
    ElMessage.error(e.message)
    rows.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function onSearch() {
  query.page = 1
  load()
}

function onReset() {
  query.keyword = ''
  query.field = ''
  query.page = 1
  query.page_size = 20
  load()
}

function onPageChange(page) {
  query.page = page
  load()
}

async function openAnnouncement(id) {
  drawer.visible = true
  drawer.tab = 'entities'
  drawer.loading = true
  drawer.error = ''
  drawer.id = id
  drawer.rows = []
  drawer.attLoading = false
  drawer.attLoaded = false
  drawer.attMessage = ''
  announcement.value = null
  attachmentItems.value = []

  try {
    // 标的物与公告原文并行取，原文失败不影响表格展示
    const [ents, ann] = await Promise.all([
      getEntitiesByAnnouncement(id),
      getAnnouncement(id).catch(() => null),
    ])
    drawer.rows = ents.items
    announcement.value = ann
  } catch (e) {
    drawer.error = e.message
  } finally {
    drawer.loading = false
  }
}

async function onTabChange(name) {
  // 附件解析较慢，首次切到该页签时才加载
  if (name !== 'attachments' || drawer.attLoaded) return
  drawer.attLoaded = true
  drawer.attLoading = true
  try {
    const data = await getAnnouncementAttachments(drawer.id)
    attachmentItems.value = data.items || []
    drawer.attMessage = data.message || ''
  } catch (e) {
    drawer.attMessage = e.message
  } finally {
    drawer.attLoading = false
  }
}

onMounted(load)
</script>

<style scoped>
.mb-16 {
  margin-bottom: 16px;
}

.mt-16 {
  margin-top: 16px;
}

/* 工具栏 */
.toolbar-card :deep(.el-card__body) {
  padding: 16px 18px !important;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.toolbar-fields {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.w-110 {
  width: 110px;
}

.w-190 {
  width: 190px;
}

.w-280 {
  width: 280px;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
}

/* 表头 */
.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.count-chip {
  font-size: 12px;
  color: var(--c-text-3);
  font-weight: 400;
}

.count-chip b {
  color: var(--c-primary);
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  margin: 0 3px;
}

.mono {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  color: var(--c-text-2);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

/* 抽屉头部 */
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 14px;
  margin-bottom: 10px;
  border-bottom: 1px solid var(--c-border);
}

.drawer-title {
  font-size: 16px;
  font-weight: 600;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
}

.drawer-sub {
  font-size: 12px;
  color: var(--c-text-3);
  margin-top: 3px;
}

.warn {
  color: var(--c-warning);
}

/* 正文/附件展示 */
.doc-wrap {
  min-height: 120px;
}

.doc-text {
  margin: 0;
  padding: 14px 16px;
  max-height: 560px;
  overflow: auto;
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13px;
  line-height: 1.85;
  color: var(--c-text);
  white-space: pre-wrap;
  word-break: break-word;
}

.att-name {
  font-size: 13px;
  font-weight: 500;
}

.att-len {
  margin-left: 10px;
  font-size: 12px;
  color: var(--c-text-3);
}
</style>