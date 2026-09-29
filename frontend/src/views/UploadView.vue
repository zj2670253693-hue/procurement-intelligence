<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <span>数据集上传与自动处理</span>
        <span class="page-hint">支持 .html 公告与同名 .zip 附件，也可直接上传数据集整包 zip</span>
      </template>

      <el-upload
        v-model:file-list="fileList"
        drag
        multiple
        :auto-upload="false"
        accept=".html,.htm,.zip"
        class="dropzone"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择文件</em></div>
        <template #tip>
          <div class="el-upload__tip">
            一篇公告 = 一个 .html + 一个同名 .zip（无附件可只传 .html）
          </div>
        </template>
      </el-upload>

      <div class="actions">
        <el-checkbox v-model="runExtraction">
          调用大模型提取标的物
          <span class="page-hint">（关闭则仅解析，不消耗模型额度）</span>
        </el-checkbox>

        <div class="actions-btns">
          <el-button type="primary" :icon="Upload" :loading="uploading" :disabled="!fileList.length" @click="submit">
            开始处理
          </el-button>
          <el-button :icon="Delete" :disabled="uploading || !fileList.length" @click="fileList = []">
            清空
          </el-button>
        </div>
      </div>
    </el-card>

    <el-card v-if="task" shadow="never" class="mt-16">
      <template #header>
        <div class="task-header">
          <span>
            当前任务
            <span class="mono">{{ task.task_id }}</span>
          </span>
          <el-tag :type="statusType(task.status)" effect="light" round>
            {{ statusText(task.status) }}
          </el-tag>
        </div>
      </template>

      <el-progress
        :percentage="percent"
        :status="task.status === 'failed' ? 'exception' : task.status === 'done' ? 'success' : undefined"
        :stroke-width="18"
        text-inside
        :duration="3"
      />

      <el-row :gutter="12" class="mt-16">
        <el-col v-for="m in taskMetrics" :key="m.label" :xs="12" :sm="6">
          <div class="metric">
            <div class="metric-label">{{ m.label }}</div>
            <div class="metric-value">{{ m.value }}</div>
          </div>
        </el-col>
      </el-row>

      <el-alert
        class="mt-16"
        :title="task.message"
        :type="task.status === 'failed' ? 'error' : task.status === 'done' ? 'success' : 'info'"
        show-icon
        :closable="false"
      />

      <div v-if="task.status === 'done' && task.entities > 0" class="mt-16">
        <el-button type="primary" :icon="Search" @click="$router.push('/task1/entities')">
          前往「标的物检索」查看提取结果
        </el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="mt-16">
      <template #header>
        <span>处理历史</span>
        <el-button link type="primary" :icon="Refresh" @click="loadTasks">刷新</el-button>
      </template>

      <el-table :data="tasks" stripe v-if="tasks.length">
        <el-table-column prop="task_id" label="任务ID" width="130">
          <template #default="{ row }">
            <span class="mono">{{ row.task_id }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="状态" width="106">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" effect="light" size="small" round>
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="进度" width="150">
          <template #default="{ row }">
            <div class="mini-progress">
              <el-progress
                :percentage="row.total ? Math.round((row.done / row.total) * 100) : 0"
                :stroke-width="6"
                :show-text="false"
                :color="row.status === 'failed' ? '#ef4444' : undefined"
              />
              <span class="mini-text">{{ row.done }}/{{ row.total }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="entities" label="标的物" width="90" align="right">
          <template #default="{ row }">
            <span class="cell-empty" v-if="!row.entities">—</span>
            <span v-else>{{ row.entities }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="message" label="说明" min-width="260" show-overflow-tooltip />

        <el-table-column prop="created_at" label="创建时间" width="170">
          <template #default="{ row }">
            <span class="cell-empty">{{ row.created_at?.replace('T', ' ') }}</span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-else description="暂无处理记录，上传数据集后将在此显示" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Refresh, Search, Upload, UploadFilled } from '@element-plus/icons-vue'

import { getTask, listTasks, uploadDataset } from '../api/task3'

const fileList = ref([])
const runExtraction = ref(true)
const uploading = ref(false)
const task = ref(null)
const tasks = ref([])

let timer = null

const percent = computed(() => {
  if (!task.value || !task.value.total) return 0
  return Math.min(100, Math.round((task.value.done / task.value.total) * 100))
})

const taskMetrics = computed(() => [
  { label: '公告总数', value: task.value?.total ?? 0 },
  { label: '已处理', value: task.value?.done ?? 0 },
  { label: '成功', value: task.value?.success ?? 0 },
  { label: '提取标的物', value: task.value?.entities ?? 0 },
])

function statusText(s) {
  return { pending: '等待中', processing: '处理中', done: '已完成', failed: '失败' }[s] || s
}

function statusType(s) {
  return { pending: 'info', processing: 'warning', done: 'success', failed: 'danger' }[s] || 'info'
}

async function submit() {
  const fd = new FormData()
  fileList.value.forEach((f) => {
    if (f.raw) fd.append('files', f.raw)
  })
  fd.append('run_extraction', String(runExtraction.value))

  uploading.value = true
  try {
    const created = await uploadDataset(fd)
    task.value = created
    ElMessage.success(`已提交，共 ${created.total} 篇公告`)
    startPolling(created.task_id)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    uploading.value = false
  }
}

function startPolling(taskId) {
  stopPolling()
  timer = setInterval(async () => {
    try {
      const t = await getTask(taskId)
      task.value = t
      if (t.status === 'done' || t.status === 'failed') {
        stopPolling()
        loadTasks()
        if (t.status === 'done') ElMessage.success('处理完成')
        else ElMessage.error(t.message)
      }
    } catch (e) {
      stopPolling()
      ElMessage.error(e.message)
    }
  }, 3000)
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function loadTasks() {
  try {
    const data = await listTasks()
    tasks.value = data.items
  } catch {
    tasks.value = []
  }
}

onMounted(loadTasks)
onUnmounted(stopPolling)
</script>

<style scoped>
.dropzone :deep(.el-upload-dragger) {
  padding: 34px 20px;
  border: 1.5px dashed var(--c-border-strong);
  border-radius: var(--radius);
  background: var(--c-surface-2);
  transition: all 0.2s ease;
}

.dropzone :deep(.el-upload-dragger:hover) {
  border-color: var(--c-primary);
  background: var(--c-primary-soft);
}

.dropzone :deep(.el-icon--upload) {
  font-size: 46px;
  color: var(--c-primary);
  margin-bottom: 8px;
}

.actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 18px;
}

.actions-btns {
  display: flex;
  gap: 8px;
}

.task-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.mono {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12.5px;
  color: var(--c-text-2);
  margin-left: 4px;
}

/* 任务指标 */
.metric {
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
}

.metric-label {
  font-size: 12px;
  color: var(--c-text-3);
}

.metric-value {
  font-size: 20px;
  font-weight: 600;
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}

/* 历史表内迷你进度 */
.mini-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mini-progress :deep(.el-progress) {
  flex: 1;
}

.mini-text {
  font-size: 12px;
  color: var(--c-text-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
</style>