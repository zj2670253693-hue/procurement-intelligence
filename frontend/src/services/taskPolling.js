export const terminalStatuses = new Set(['validated_only','completed','partial','failed','cancelled','needs_confirmation'])
export function createTaskPolling({fetch,schedule = setTimeout,cancelSchedule = clearTimeout,onResult = () => {},onError = () => {},interval = 3000}) {
  let active = false, generation = 0, taskId, timer, inFlight = false
  const queue = (delay = interval) => { if (active) timer = schedule(tick,delay) }
  async function tick() {
    timer = undefined
    if (!active || inFlight) return
    const token = generation, id = taskId; inFlight = true
    try {
      const result = await fetch(id)
      if (!active || token !== generation) return
      onResult(result)
      if (terminalStatuses.has(result.status)) active = false
    } catch(error) {
      if (active && token === generation) { active = false; onError(error) }
    } finally {
      inFlight = false
      if (active) queue(token === generation ? interval : 0)
    }
  }
  return {
    start(id) { this.stop(); active = true; taskId = id; tick() },
    stop() { active = false; generation++; if (timer !== undefined) cancelSchedule(timer); timer = undefined },
  }
}
