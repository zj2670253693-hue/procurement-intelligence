const maximumChunk = 16 * 1024 * 1024
const aborted = signal => { if (signal?.aborted) throw new Error('上传已取消') }
export async function hashParts(file,chunkBytes = maximumChunk,{crypto = globalThis.crypto,signal,onProgress = () => {}} = {}) {
  if (!crypto?.subtle) throw new Error('分片校验需要HTTPS或受控本地安全连接；当前浏览器缺少安全哈希能力')
  if (!Number.isSafeInteger(chunkBytes) || chunkBytes <= 0 || chunkBytes > maximumChunk || !Number.isSafeInteger(file.size) || file.size <= 0) throw new Error('文件大小或分片大小无效')
  const hashes = []
  for (let start = 0; start < file.size; start += chunkBytes) {
    aborted(signal)
    const buffer = await file.slice(start,Math.min(start+chunkBytes,file.size)).arrayBuffer()
    const digest = await crypto.subtle.digest('SHA-256',buffer)
    aborted(signal)
    hashes.push([...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join(''))
    onProgress({hashed_bytes:Math.min(start+chunkBytes,file.size),total_bytes:file.size})
  }
  return {name:file.name,bytes:file.size,part_hashes:hashes}
}
export async function uploadParts(session,files,{onProgress = () => {},signal,sendPart,complete} = {}) {
  const chunkBytes = session.chunk_bytes || maximumChunk
  if (!Number.isSafeInteger(chunkBytes) || chunkBytes <= 0 || chunkBytes > maximumChunk) throw new Error('服务器分片大小无效')
  if (!sendPart || !complete) throw new Error('上传服务尚未配置')
  const total = session.files.reduce((sum,item)=>sum+item.bytes,0)
  let uploaded = 0
  for (const slot of session.files) {
    const matches = files.filter(file=>file.name === slot.name && file.size === slot.bytes)
    if (matches.length !== 1) throw new Error('请重新选择与服务器清单完全对应的原始文件')
    const file = matches[0], received = new Set(slot.received_parts)
    for (let start = 0,part = 0; start < file.size; start += chunkBytes,part++) {
      aborted(signal)
      const end = Math.min(start+chunkBytes,file.size)
      if (!received.has(part)) await sendPart(session.job.task_id,slot.file_id,part,file.slice(start,end),signal)
      uploaded += end-start; onProgress({uploaded_bytes:uploaded,total_bytes:total})
    }
  }
  aborted(signal)
  return complete(session.job.task_id,signal)
}
