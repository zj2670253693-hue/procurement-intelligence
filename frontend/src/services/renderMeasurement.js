export function createRenderMeasurement({now = () => globalThis.performance?.now?.() ?? Date.now(),nextTick,nextFrame,isReady}) {
  let current
  return {
    start(generation,context) { current = {generation,context:{...context},started:now(),done:false} },
    cancel(generation) { if (current?.generation === generation) current = undefined },
    async finish(generation) {
      const sample = current
      if (!sample || sample.generation !== generation || sample.done) return null
      await nextTick(); await nextFrame(); await nextFrame()
      if (current !== sample || sample.done || !isReady(sample.context)) return null
      sample.done = true
      return {...sample.context,generation,elapsed_ms:Math.max(0,now()-sample.started)}
    },
  }
}
