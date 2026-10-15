import test from 'node:test'
import assert from 'node:assert/strict'
import {budgetNotice, parseOnlyDescription} from '../src/services/taskDiagnostics.js'

test('budget refusal separates reservation from measured tokens, without suggesting automatic retry',()=>{
  const notice=budgetNotice({diagnostics:[{code:'inference_budget_exhausted',details:{
    budget:{max_calls:8,max_input_tokens:64000,max_output_tokens:32768},
    required_total:{calls:3,input_tokens:75000,output_tokens:12288},
    next_request_bound:{input_tokens:25000,output_tokens:4096},
    observed_usage:{sent_calls:2,input_tokens:721,output_tokens:96,usage_unknown:false}
  }}]})
  assert.match(notice.title,/预算/)
  assert.match(notice.description,/64000/)
  assert.match(notice.description,/75000/)
  assert.match(notice.description,/721/)
  assert.match(notice.description,/96/)
  assert.match(notice.description,/预留.*不等于.*实际/)
  assert.match(notice.description,/不会自动/)
})

test('legacy or unknown-usage diagnostics do not invent actual zero usage',()=>{
  assert.equal(budgetNotice({diagnostics:[]}),null)
  const unknown=budgetNotice({diagnostics:[{code:'inference_budget_exhausted',details:{
    observed_usage:{sent_calls:2,input_tokens:null,output_tokens:null,usage_unknown:true}
  }}]})
  assert.match(unknown.description,/未知/)
  assert.doesNotMatch(unknown.description,/实际.*0/)
  assert.match(budgetNotice({diagnostics:[{code:'inference_budget_exhausted'}]}).description,/详细用量/)
})

test('parse-only guidance follows the actual server switch, never falsely claims it is off',()=>{
  assert.match(parseOnlyDescription(false),/未开放/)
  assert.doesNotMatch(parseOnlyDescription(true),/关闭|不可用|未开放/)
  assert.match(parseOnlyDescription(true),/可能计费/)
})
