import test from 'node:test'
import assert from 'node:assert/strict'
import { createRenderMeasurement } from '../src/services/renderMeasurement.js'
test('test_measurement_waits_table_and_svg_once', async () => {
  let time=10, ready=false; const phases=[]
  const meter=createRenderMeasurement({now:()=>time,nextTick:async()=>{phases.push('tick')},nextFrame:async()=>{phases.push('frame');time+=20;ready=phases.length>=3},isReady:()=>ready})
  meter.start(1,{dataset_id:'official',query_type:'buyer_suppliers'})
  const sample=await meter.finish(1)
  assert.deepEqual(phases,['tick','frame','frame'])
  assert.equal(sample.elapsed_ms,40)
  assert.equal(await meter.finish(1),null)
  meter.start(2,{dataset_id:'official'}); meter.cancel(2)
  assert.equal(await meter.finish(2),null)
  meter.start(3,{dataset_id:'official'}); meter.start(4,{dataset_id:'official'})
  assert.equal(await meter.finish(3),null)
})
