import client from './client'

/**
 * 任务三：数据集上传与自动化处理
 */

/**
 * 上传数据集并启动后台处理
 * @param {FormData} formData  含 files（多个 HTML/zip）与 run_extraction
 * @param {Function} [onUploadProgress] 上传进度回调
 */
export function uploadDataset(formData, onUploadProgress) {
  return client.post('/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0, // 数据集可能较大，不限时
    onUploadProgress,
  })
}

/** 查询单个任务的进度 */
export function getTask(taskId) {
  return client.get(`/tasks/${taskId}`)
}

/** 任务列表 */
export function listTasks() {
  return client.get('/tasks')
}