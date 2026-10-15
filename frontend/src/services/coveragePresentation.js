export function coverageMessage(value) {
  if (value?.availability !== 'partial') return ''
  const count = number => Number.isSafeInteger(number) && number >= 0 ? String(number) : '未知'
  return `本批次仅部分成功：已保存公告 ${count(value.processed_announcements)}/${count(value.total_announcements)}。仍有失败或不完整诊断，统计与关系只覆盖已保存部分；查询为空不代表实际不存在。`
}
