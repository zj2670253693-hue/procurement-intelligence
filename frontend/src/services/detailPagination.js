/** Keep each detail page tied to the announcement and dataset that requested it. */
export function createDetailPagination({ fetch, state, accepts = () => true }) {
  let scope, generation = 0
  const reset = () => Object.assign(state, {
    items: [], total: 0, page: 1, pageSize: 100, loading: false, error: '',
    detail_status: '', detail_error_message: '',
  })
  reset()
  const current = (token, selected, value) => token === generation && scope === selected && accepts(selected, value)
  async function load(page = state.page) {
    if (!scope || !Number.isInteger(page) || page < 1) return
    const selected = scope, token = ++generation
    Object.assign(state, { page, items: [], loading: true, error: '' })
    try {
      const value = await fetch(selected.announcementId, selected.datasetId, { page, page_size: state.pageSize })
      if (!current(token, selected, value)) return
      Object.assign(state, { items: value.items || [], total: value.total,
        detail_status: value.detail_status || '', detail_error_message: value.detail_error_message || '' })
    } catch (error) {
      if (current(token, selected)) state.error = '附件明细加载失败：' + error.message
    } finally {
      if (current(token, selected)) state.loading = false
    }
  }
  return {
    select(value) { generation++; scope = { ...value }; reset() },
    cancel() { generation++; scope = undefined; reset() },
    load,
    retry: () => load(state.page),
  }
}
