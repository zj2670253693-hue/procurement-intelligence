import test from 'node:test'
import assert from 'node:assert/strict'
import {coverageMessage} from '../src/services/coveragePresentation.js'
test('partial warning persists even when query rows are empty',()=>{
  const message=coverageMessage({availability:'partial',processed_announcements:1,total_announcements:2})
  assert.match(message,/部分/);assert.match(message,/1\/2/);assert.match(message,/不代表/)
  assert.equal(coverageMessage({availability:'completed',processed_announcements:2,total_announcements:2}),'')
})
