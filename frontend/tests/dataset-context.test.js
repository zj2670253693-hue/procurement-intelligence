import test from 'node:test'
import assert from 'node:assert/strict'
import { createDatasetContext } from '../src/services/datasetContext.js'
test('test_late_result_after_dataset_switch_is_rejected', () => {
  const ctx = createDatasetContext()
  const oldTicket = ctx.ticket()
  ctx.select('up_' + 'a'.repeat(32))
  assert.equal(ctx.accept(oldTicket, { upload_dataset_id: 'official' }), false)
  assert.equal(ctx.accept(ctx.ticket(), { upload_dataset_id: 'up_' + 'b'.repeat(32) }), false)
  assert.equal(ctx.accept(ctx.ticket(), { upload_dataset_id: ctx.id }), true)
  assert.throws(() => ctx.select('../../private'))
})
