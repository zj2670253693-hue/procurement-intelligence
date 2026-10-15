import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { createDatasetContext } from './datasetContext'
export function useDataset() {
  const route=useRoute()
  const datasetId=computed(()=>String(route.query.dataset_id || 'official'))
  const context=createDatasetContext(datasetId.value)
  return {datasetId,context}
}
