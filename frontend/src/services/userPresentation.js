// Friendly labels are presentation only. Full IDs remain the API/query keys.
export const sourceSection = evidence => String(evidence?.locator?.section || evidence?.header_context || '')
  .split('|')[0].trim()
export const sourceOptionLabel = (evidence, ordinal) => {
  const section = sourceSection(evidence)
  return `来源 ${ordinal}` + (section ? ` · ${section.length > 36 ? section.slice(0,36)+'…' : section}` : ' · 公告原文')
}
export const modeLabel = mode => ({extract:'智能提取',parse_only:'仅读取文件'}[mode] || '模式待确认')
export const availabilityLabel = value => ({completed:'已完成',partial:'部分成功',failed:'处理失败',
  receiving:'上传中',queued:'等待处理',running:'处理中',validating:'检查中',
  validated_only:'文件读取完成',cancelled:'已取消',needs_confirmation:'需人工确认',staging:'准备中'}[value] || '状态待确认')
export const datasetChoiceLabel = (item,ordinal=1) => item.upload_dataset_id === 'official'
  ? '官方公告数据（只读）' : `我的上传 ${ordinal} · ${modeLabel(item.mode)} · ${availabilityLabel(item.availability)}`
export const downloadLabel = name => ({'standard.xlsx':'下载结果表格','raw_results.json':'下载完整结果',
  'errors.json':'下载失败明细（JSON）','errors.csv':'下载失败清单（表格）'}[name] || '下载文件')
