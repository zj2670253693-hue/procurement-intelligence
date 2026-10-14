import client from './client'

export const task2Status = () => client.get('/task2/status')
export const organizationChoices = (params) => client.get('/task2/organizations', { params })
export const relationshipQuery = (payload) => client.post('/task2/query', payload)
export const sourceEvidence = (id, dataset_id) => client.get(`/task2/evidence/${encodeURIComponent(id)}`, { params: { dataset_id } })

export async function downloadStandard() {
  const blob = await client.get('/task1/export/standard', { responseType: 'blob' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = '任务一-标准三表.xlsx'
  link.click()
  URL.revokeObjectURL(url)
}
