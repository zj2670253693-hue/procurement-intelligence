// Presentation only: do not infer an outcome or merge graph fact identities.
export const RELATION_LABELS = Object.freeze({
  procured_by: '采购单位', package_of: '所属项目', individual_subject: '对应组织',
  consortium_member: '联合体成员', awarded_to: '已中标', product_supplier: '明示产品供应商',
  award_only_won: '仅有中标证据', bid_participation_won: '投标·已中标',
  bid_participation_lost: '投标·明确未中标', bid_participation_unknown: '投标·结果未明确',
})

export const NODE_LABELS = Object.freeze({
  project: '采购项目', event: '采购包', bid_subject: '投标主体', organization: '组织名称',
})
export const NODE_COLORS = Object.freeze({
  project: '#d9eafa', event: '#dff2e6', bid_subject: '#ffebcc', organization: '#e9e1f4',
})
export const relationLabel = kind => RELATION_LABELS[kind] || '其他关系（' + kind + '）'
export const relationTone = kind => {
  if (['awarded_to', 'award_only_won', 'bid_participation_won'].includes(kind)) return 'won'
  if (kind === 'bid_participation_lost') return 'lost'
  if (kind === 'bid_participation_unknown') return 'unknown'
  return 'structure'
}
