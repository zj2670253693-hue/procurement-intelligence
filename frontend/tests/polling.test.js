import test from 'node:test'
import assert from 'node:assert/strict'
import { createTaskPolling } from '../src/services/taskPolling.js'
const flush = () => new Promise(resolve => setImmediate(resolve))
test('test_polling_reconnect_and_unmount', async () => {
  const timers = new Map(); let serial = 0, calls = 0, release
  const results = [], errors = []
  const poll = createTaskPolling({fetch: async () => { calls++; if (calls === 1) return new Promise(resolve => {release = resolve}); if (calls === 2) throw new Error('offline'); return {status:'completed'} }, schedule: fn => {timers.set(++serial,fn); return serial}, cancelSchedule: id => timers.delete(id), onResult: value => results.push(value), onError: error => errors.push(error)})
  poll.start('first'); await flush(); poll.start('second'); await flush()
  assert.equal(calls,1)
  release({status:'running'}); await flush()
  assert.equal(results.length,0)
  const firstTimer=[...timers.values()][0]; timers.clear(); firstTimer(); await flush()
  assert.equal(errors.length,1); assert.equal(timers.size,0)
  poll.start('second'); await flush(); assert.equal(results[0].status,'completed')
  poll.stop(); assert.equal(timers.size,0)
})
