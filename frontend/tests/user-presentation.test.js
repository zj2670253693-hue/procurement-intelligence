import test from 'node:test'
import assert from 'node:assert/strict'
import {sourceSection,sourceOptionLabel,datasetChoiceLabel,modeLabel,availabilityLabel,downloadLabel} from '../src/services/userPresentation.js'

test('user-facing source choices use readable numbered names, never evidence hashes',()=>{
  const source={evidence_id:'evidence_'+'a'.repeat(64),header_context:'八、其他补充事宜 | 合同包1 | 供应商'}
  assert.equal(sourceSection(source),'八、其他补充事宜')
  assert.equal(sourceOptionLabel(source,2),'来源 2 · 八、其他补充事宜')
  assert.equal(sourceOptionLabel(null,3),'来源 3 · 公告原文')
  assert.ok(sourceOptionLabel({locator:{section:'某'.repeat(200)}},1).length<50)
})
test('upload names keep partial outcomes explicit without exposing internal identifiers',()=>{
  assert.equal(datasetChoiceLabel({upload_dataset_id:'official'}),'官方公告数据（只读）')
  const item={upload_dataset_id:'up_'+'a'.repeat(32),mode:'extract',availability:'partial'}
  assert.equal(datasetChoiceLabel(item,2),'我的上传 2 · 智能提取 · 部分成功')
  assert.doesNotMatch(datasetChoiceLabel(item,2),/up_|extract|partial/)
  assert.equal(modeLabel('parse_only'),'仅读取文件')
  assert.equal(availabilityLabel('unexpected_status'),'状态待确认')
  assert.equal(downloadLabel('standard.xlsx'),'下载结果表格')
})
