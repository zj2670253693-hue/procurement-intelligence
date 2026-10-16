<template>
  <div>
    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" />
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

    <el-collapse><el-collapse-item title="更多筛选条件（金额单位：万元）">
      <el-row :gutter="12"><el-col v-for="column in columns" :key="column.prop" :span="8">
        <el-input v-model="fieldText[column.prop]" :placeholder="column.label + '包含'" clearable />
        <el-select v-model="fieldMissing[column.prop]" clearable placeholder="缺失状态不限"><el-option label="未公示/空值" value="missing"/><el-option label="有明确内容" value="present"/></el-select>
      </el-col></el-row>
      <el-row :gutter="12"><el-col v-for="field in ['unit_price','quantity','total_price']" :key="field" :span="8">
        <el-input v-model="rangeLow[field]" :placeholder="`${columns.find(c=>c.prop===field)?.label}下限`"/><el-input v-model="rangeHigh[field]" placeholder="上限"/>
      </el-col></el-row>
      <el-select v-model="quantityUnit" clearable placeholder="数量范围需明确单位"><el-option v-for="unit in quantityUnits" :key="unit" :label="unit" :value="unit"/></el-select>
      <p>单位不明、预算、折扣和未知值只保留原文，不参与数值区间计算。</p>
    </el-collapse-item></el-collapse>

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
          label="公告编号"
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
        <el-tab-pane label="汇总结果（金额：万元）" name="standard">
          <el-alert v-if="standardItems.error" :title="standardItems.error" type="error" :closable="false" />
          <el-button v-if="standardItems.error" @click="openAnnouncement(drawer.id)">重试加载</el-button>
          <el-alert title="结果按采购包汇总，并统一金额单位。未公示的信息保留空缺，不使用预算替代成交金额。" type="info" :closable="false" />
          <el-table :data="standardItems.items" stripe max-height="600"><el-table-column v-for="h in standardItems.headers" :key="h" :prop="h" :label="h" min-width="160" /></el-table>
          <details><summary>分类备注与导出详情</summary><pre class="doc-text">{{ officialNotes }}</pre></details>
        </el-tab-pane>
        <el-tab-pane label="附件明细（待核实）" name="details">
          <el-alert title="这些附件记录可能是采购计划或预算，尚未逐条确认为实际中标，不能当作已发生的交易。" type="warning" :closable="false" />
          <el-alert v-if="attachmentDetails.error" :title="attachmentDetails.error" type="error" :closable="false" />
          <el-button v-if="attachmentDetails.error" @click="detailsPager.retry()">重试本页</el-button>
          <p>解析状态：{{ attachmentDetails.detail_status || '未知' }}；{{ attachmentDetails.detail_error_message }}</p>
          <el-table v-loading="attachmentDetails.loading" :data="attachmentDetails.items" stripe max-height="600"><el-table-column v-for="c in detailColumns" :key="c.prop" :prop="c.prop" :label="c.label" min-width="160" /></el-table>
          <p aria-live="polite">共 {{ attachmentDetails.total }} 条；当前第 {{ attachmentDetails.page }} 页，每页 {{ attachmentDetails.pageSize }} 条</p>
          <el-pagination layout="total, prev, pager, next" :total="attachmentDetails.total" :page-size="attachmentDetails.pageSize"
            :current-page="attachmentDetails.page" @update:current-page="detailsPager.load" />
        </el-tab-pane>
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
          <div v-loading="drawer.sourceLoading" class="doc-wrap">
            <el-alert v-if="drawer.sourceError" :title="drawer.sourceError" type="error" :closable="false" />
            <el-button v-if="drawer.sourceError" @click="retrySource">重新读取原文</el-button>
            <pre v-if="announcement?.html_text" class="doc-text">{{ announcement.html_text }}</pre>
            <el-empty v-else-if="!drawer.sourceLoading && !drawer.sourceError" description="原文内容为空" />
          </div>
        </el-tab-pane>

        <!-- 页签 3：附件 -->
        <el-tab-pane :label="`附件 (${announcement ? announcement.attachment_files.length : '待核验'})`" name="attachments">
          <div v-loading="drawer.attLoading" class="doc-wrap">
            <el-alert v-if="drawer.sourceError" :title="drawer.sourceError" type="error" :closable="false" />
            <el-button v-if="drawer.sourceError" @click="retrySource">重新读取附件清单</el-button>
            <el-button v-if="announcement?.attachment_files.length" type="primary" :loading="drawer.downloading" @click="downloadArchive">下载原始附件 ZIP</el-button>
            <el-alert v-if="drawer.downloadError" :title="drawer.downloadError" type="error" :closable="false" />
            <el-alert v-if="drawer.attError" :title="drawer.attError" type="error" :closable="false" />
            <el-button v-if="drawer.attError" @click="loadAttachments">重新读取附件目录</el-button>
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
              v-else-if="drawer.attLoaded && !drawer.attLoading && !drawer.attMessage && !drawer.attError && !drawer.sourceError"
              :description="announcement?.attachment_files.length ? '附件暂无可预览文本，请下载原始附件 ZIP 查看' : '该公告没有附件包'"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-drawer>
  </div>
</template>

<script setup>
import { watch, reactive, ref, onUnmounted } from 'vue'
import { useDataset } from '../services/useDataset'
import { createDetailPagination } from '../services/detailPagination'
const {datasetId,context}=useDataset()
import { ElMessage } from 'element-plus'
import { Close, RefreshLeft, Search, View } from '@element-plus/icons-vue'

import {
  getAnnouncement,
  getAnnouncementAttachments,
  getAnnouncementArchive,
  getEntitiesByAnnouncement,
  searchEntities,
  getStandardItems, getOfficialNotes, getAttachmentDetails,
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
const loadError=ref(''),fieldText=reactive({}),fieldMissing=reactive({}),rangeLow=reactive({}),rangeHigh=reactive({}),quantityUnit=ref('')
const quantityUnits=['套','批','台','件','个','项','人天','吨','米','平方米','本','辆','张','份']
let loadGeneration=0
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
  attError: '',
  sourceLoading: false,
  sourceError: '',
  downloading: false,
  downloadError: '',
})

const announcement = ref(null)
const attachmentItems = ref([])
const standardItems = ref({headers: [], items: []})
const officialNotes = ref('')
const attachmentDetails = reactive({})
const detailsPager = createDetailPagination({ fetch: getAttachmentDetails, state: attachmentDetails,
  accepts: (scope, value) => drawer.visible && drawer.id === scope.announcementId && datasetId.value === scope.datasetId && context.accept(scope.ticket, value) })
const detailColumns = [{prop:'package_name',label:'采购包'}, ...columns, {prop:'source_file',label:'来源附件'},{prop:'source_page',label:'页号'}]
let drawerGeneration = 0, sourceGeneration = 0

async function load() {
  const ticket=context.ticket(),token=++loadGeneration
  loading.value = true
  loadError.value=''
  try {
    const filters={text:{},missing:{},ranges:{}}
    for(const column of columns){if(fieldText[column.prop])filters.text[column.prop]=fieldText[column.prop];if(fieldMissing[column.prop])filters.missing[column.prop]=fieldMissing[column.prop]==='missing'}
    if(query.field)filters.missing[query.field]=false
    if(query.keyword)filters.text.product_name=query.keyword
    for(const name of ['unit_price','quantity','total_price'])if(rangeLow[name] || rangeHigh[name])filters.ranges[name]=[rangeLow[name] || null,rangeHigh[name] || null]
    if(quantityUnit.value)filters.quantity_unit=quantityUnit.value
    const advanced=Object.keys(fieldText).some(key=>fieldText[key]) || Object.keys(fieldMissing).some(key=>fieldMissing[key]) || Object.keys(filters.ranges).length
    const data = await searchEntities({
      keyword: query.keyword,
      field: query.field,
      page: query.page,
      page_size: query.page_size,
      filters:datasetId.value!=='official' || advanced?filters:undefined,
    },ticket.dataset_id)
    if(token!==loadGeneration || !context.accept(ticket,data))return
    rows.value = data.items
    total.value = data.total
  } catch (e) {
    if(token===loadGeneration && context.accept(ticket))loadError.value='刷新失败；下方可能是上次结果：'+e.message
  } finally {
    if(token===loadGeneration && context.accept(ticket))loading.value=false
  }
}

function onSearch() {
  query.page = 1
  load()
}

function onReset() {
  for(const value of [fieldText,fieldMissing,rangeLow,rangeHigh])for(const key of Object.keys(value))delete value[key]
  quantityUnit.value=''
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
  const ticket=context.ticket()
  const token=++drawerGeneration
  drawer.visible = true
  drawer.tab = 'entities'
  drawer.loading = true
  drawer.error = ''
  drawer.id = id
  drawer.rows = []
  drawer.attLoading = false
  drawer.attLoaded = false
  drawer.attMessage = ''
  drawer.attError = ''
  drawer.sourceError = ''
  drawer.sourceLoading = true
  drawer.downloading = false
  drawer.downloadError = ''
  announcement.value = null
  attachmentItems.value = []
  standardItems.value={headers: [], items: []}
  officialNotes.value=''
  detailsPager.select({announcementId:id,datasetId:ticket.dataset_id,ticket})
  detailsPager.load(1)

  try {
    // 标的物与公告原文并行取，原文失败不影响表格展示
    const source = readSource(id,ticket,token)
    const [ents, , standard, notes] = await Promise.all([
      ticket.dataset_id === 'official' ? getEntitiesByAnnouncement(id,ticket.dataset_id) : source.then(value=>({items:value.result.entities})),
      source.catch(() => null),
      getStandardItems(id,ticket.dataset_id).catch(e => ({headers:[],items:[],error:e.message})),
      (ticket.dataset_id === 'official' ? getOfficialNotes(id,ticket.dataset_id) : source.then(value=>({notes:value.result.extra_info,note:'预算/需求/意向不能混成已发生交易。'}))).catch(e => ({error:e.message})),
    ])
    if(token!==drawerGeneration || !context.accept(ticket))return
    if(ticket.dataset_id === 'official')drawer.rows = ents.items
    standardItems.value=standard
    if(ticket.dataset_id === 'official')officialNotes.value=JSON.stringify(notes, null, 2)
  } catch (e) {
    if(token===drawerGeneration)drawer.error = e.message
  } finally {
    if(token===drawerGeneration)drawer.loading = false
  }
}

async function onTabChange(name) {
  // 附件解析较慢，首次切到该页签时才加载
  if (name !== 'attachments' || drawer.attLoaded) return
  await loadAttachments()
}

async function loadAttachments() {
  const ticket=context.ticket()
  drawer.attLoaded = false
  drawer.attLoading = true
  drawer.attError = ''
  const token=drawerGeneration
  try {
    const data = await getAnnouncementAttachments(drawer.id,ticket.dataset_id)
    if(token!==drawerGeneration || !context.accept(ticket,data))return
    attachmentItems.value = data.items || []
    drawer.attMessage = data.message || ''
    drawer.attLoaded = true
  } catch (e) {
    if(token===drawerGeneration && context.accept(ticket))drawer.attError = '附件目录读取失败：'+e.message
  } finally {
    if(token===drawerGeneration)drawer.attLoading = false
  }
}

async function readSource(id,ticket,token) {
  const request=++sourceGeneration
  const current=value=>request===sourceGeneration && token===drawerGeneration && drawer.visible && drawer.id===id && datasetId.value===ticket.dataset_id && context.accept(ticket,value)
  drawer.sourceError='';drawer.sourceLoading=true
  try {
    const value=await getAnnouncement(id,ticket.dataset_id)
    if(current(value)) {
      announcement.value=value
      if(ticket.dataset_id!=='official') {
        drawer.rows=value.result.entities
        officialNotes.value=JSON.stringify({notes:value.result.extra_info,note:'预算/需求/意向不能混成已发生交易。'},null,2)
        drawer.error=''
      }
    }
    return value
  } catch(e) {
    if(current())drawer.sourceError='原文及附件清单读取失败：'+e.message
    throw e
  } finally {
    if(current())drawer.sourceLoading=false
  }
}

async function retrySource() {
  try { await readSource(drawer.id,context.ticket(),drawerGeneration) } catch { /* readSource exposes the failure next to the retry button. */ }
}

async function downloadArchive() {
  const ticket=context.ticket(),token=drawerGeneration,id=drawer.id
  drawer.downloading=true;drawer.downloadError=''
  try {
    const blob=await getAnnouncementArchive(id,ticket.dataset_id)
    if(token!==drawerGeneration || !drawer.visible || !context.accept(ticket))return
    const url=URL.createObjectURL(blob),link=document.createElement('a')
    link.href=url;link.download=id+'.zip';link.click()
    setTimeout(()=>URL.revokeObjectURL(url),0)
  } catch(e) {
    if(token===drawerGeneration && context.accept(ticket))drawer.downloadError='附件下载失败：'+e.message
  } finally {
    if(token===drawerGeneration)drawer.downloading=false
  }
}

watch(()=>drawer.visible,visible=>{if(!visible){drawerGeneration++;detailsPager.cancel();drawer.loading=false;drawer.sourceLoading=false;drawer.attLoading=false;drawer.downloading=false}},{flush:'sync'})
watch(datasetId,id=>{context.select(id);loadGeneration++;drawerGeneration++;detailsPager.cancel();drawer.visible=false;drawer.loading=false;drawer.attLoading=false;rows.value=[];total.value=0;announcement.value=null;attachmentItems.value=[];standardItems.value={headers:[],items:[]};officialNotes.value='';query.page=1;load()},{immediate:true})
onUnmounted(()=>{loadGeneration++;drawerGeneration++;detailsPager.cancel()})
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
