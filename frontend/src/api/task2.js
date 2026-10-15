import client from './client'

const scope = dataset => dataset==='official'?'/task2':`/platform/datasets/${dataset}/task2`
export const task2Status = (dataset='official') => client.get(scope(dataset)+'/summary')
export const organizationChoices = (params,dataset='official') => client.get(scope(dataset)+'/organizations', { params })
export const relationshipQuery = (payload,dataset='official') => client.post(scope(dataset)+'/query', payload)
export const sourceEvidence = (id,dataset_id,dataset='official') => client.get(`${scope(dataset)}/evidence/${encodeURIComponent(id)}`, { params: { dataset_id } })

export async function downloadStandard(dataset='official',name='standard.xlsx') {
  const blob = await client.get(dataset==='official'?'/task1/export/standard':`/platform/datasets/${dataset}/artifacts/${encodeURIComponent(name)}`, { responseType: 'blob' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name==='standard.xlsx'?'任务一-标准三表.xlsx':name
  link.click()
  URL.revokeObjectURL(url)
}
