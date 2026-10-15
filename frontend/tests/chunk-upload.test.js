import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto, createHash } from 'node:crypto'
import { hashParts, uploadParts } from '../src/services/chunkUpload.js'
test('test_hash_parts_bound_and_resume', async () => {
  const bytes=Buffer.from('abcdefghij'); const slices=[]
  const file={name:'synthetic.zip',size:bytes.length,slice(start,end){slices.push(end-start);return {arrayBuffer:async()=>bytes.subarray(start,end)}}}
  const hashes=await hashParts(file,4,{crypto:webcrypto})
  assert.deepEqual(hashes.part_hashes,[0,4,8].map(i=>createHash('sha256').update(bytes.subarray(i,i+4)).digest('hex')))
  assert.ok(Math.max(...slices)<=4)
  const sent=[],progress=[]
  const session={job:{task_id:'synthetic'},chunk_bytes:4,files:[{name:file.name,bytes:file.size,file_id:'file',received_parts:[0]}]}
  const receipt=await uploadParts(session,[file],{sendPart:async(task,id,part,blob)=>{sent.push(part)},complete:async()=>({status:'queued'}),onProgress:value=>progress.push(value)})
  assert.deepEqual(sent,[1,2]); assert.equal(receipt.status,'queued'); assert.equal(progress.at(-1).uploaded_bytes,10)
  await assert.rejects(hashParts(file,16*1024*1024+1,{crypto:webcrypto}))
  await assert.rejects(hashParts(file,4,{crypto:{}}),/安全/)
  const signal=new AbortController();signal.abort()
  await assert.rejects(uploadParts(session,[file],{signal:signal.signal,sendPart:async()=>{},complete:async()=>{throw new Error('should not finalize')}}))
})
