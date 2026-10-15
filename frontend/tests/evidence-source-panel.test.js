import test from 'node:test'
import assert from 'node:assert/strict'
import {h} from 'vue'
import {renderVue} from './helpers/renderVue.js'

const stubs={
  'el-select':{setup(props,{slots}){return()=>h('div',slots.default?.())}},
  'el-option':{props:['label'],setup(props){return()=>h('span',props.label)}},
  'el-button':{setup(props,{slots}){return()=>h('button',slots.default?.())}},
}
test('actual source panel leads with original text and folds all machine identifiers by default',async()=>{
  const evidence={evidence_id:'evidence_'+ 'a'.repeat(64),dataset_id:'ds_'+'b'.repeat(64),
    html_sha256:'c'.repeat(64),announcement_id:'t20260803_27062627',
    raw_quote:'济南裕杨科技发展有限公司',header_context:'八、其他补充事宜 | 合同包1 | 供应商',
    locator:{section:'八、其他补充事宜',table:3,row:3,cell:0,start:23415,end:23427}}
  const html=await renderVue(new URL('../src/components/EvidenceSourcePanel.vue',import.meta.url),{
    evidence,ids:[evidence.evidence_id],selectedId:evidence.evidence_id,records:{[evidence.evidence_id]:evidence}},stubs)
  const details=html.match(/<details\b[^>]*>[\s\S]*?<\/details>/)?.[0]
  assert.ok(details)
  assert.doesNotMatch(details.match(/^<details[^>]*>/)[0],/\bopen\b/)
  for(const token of [evidence.evidence_id,evidence.dataset_id,evidence.html_sha256,'23415'])assert.ok(details.includes(token))
  const primary=html.replace(details,'').replace(/<[^>]+>/g,'')
  assert.ok(primary.includes(evidence.raw_quote))
  assert.ok(primary.includes('公告章节') && primary.includes('八、其他补充事宜'))
  assert.ok(primary.includes('来源 1 · 八、其他补充事宜'))
  assert.doesNotMatch(primary,/evidence_|ds_|SHA256|字符|单元格/)
})
test('untrusted original text remains escaped and reload failure is visible without losing the selector',async()=>{
  const url=new URL('../src/components/EvidenceSourcePanel.vue',import.meta.url)
  const html=await renderVue(url,{evidence:{evidence_id:'evidence_a',raw_quote:'<img src=x onerror=alert(1)>'},ids:['evidence_a']},stubs)
  assert.ok(html.includes('&lt;img'))
  assert.doesNotMatch(html,/<img\b/)
  const failure=await renderVue(url,{ids:['evidence_a'],error:'原文暂时无法加载'},stubs)
  assert.match(failure,/role="alert"/)
  assert.match(failure,/重新加载/)
  assert.match(failure,/选择来源/)
})
