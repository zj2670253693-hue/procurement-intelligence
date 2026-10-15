export function validDatasetId(id) { return id === 'official' || typeof id === 'string' && /^up_[a-f0-9]{32}$/.test(id) }
export function createDatasetContext(initial = 'official') {
  if (!validDatasetId(initial)) throw new Error('数据集编号无效')
  let id = initial, generation = 0
  return {
    get id() { return id }, get generation() { return generation },
    select(value) { if (!validDatasetId(value)) throw new Error('数据集编号无效'); id = value; generation++; return generation },
    ticket() { return Object.freeze({dataset_id:id,generation}) },
    accept(ticket,response) { return ticket?.dataset_id === id && ticket?.generation === generation && (!response?.upload_dataset_id || response.upload_dataset_id === id) },
  }
}
