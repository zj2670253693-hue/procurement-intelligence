// Reservations are limits, not a bill. Never coerce missing provider usage to zero.
const number=value=>Number.isSafeInteger(value)&&value>=0?String(value):'未知'

export function budgetNotice(task){
  const diagnostic=task?.diagnostics?.find(item=>item.code==='inference_budget_exhausted')
  if(!diagnostic)return null
  const details=diagnostic.details
  const common='系统已在下一次发送前停止，不会自动重发、重置预算或提高费用上限。'
  if(!details)return {title:'本批次达到预算上限',description:common+'旧任务未保存详细用量，请查看调用记录，不能据此认为未产生费用。'}
  const budget=details.budget||{},required=details.required_total||{},usage=details.observed_usage||{}
  return {title:'本批次达到预算上限',description:
    `允许上限：${number(budget.max_calls)} 次调用、输入 ${number(budget.max_input_tokens)} token、输出 ${number(budget.max_output_tokens)} token。`+
    `若再调用，累计预留将为 ${number(required.calls)} 次、输入 ${number(required.input_tokens)} token、输出 ${number(required.output_tokens)} token。`+
    `预留上限不等于实际用量；已发送 ${number(usage.sent_calls)} 次，供应商报告实际输入 ${number(usage.input_tokens)} token、输出 ${number(usage.output_tokens)} token`+
    (usage.usage_unknown?'（有用量未知的请求，以供应商账单核对为准）。':'。')+common}
}

export function parseOnlyDescription(paidReady){
  return (paidReady
    ?'当前已开放智能提取，获得授权的账号可以使用，可能计费。'
    :'当前未开放智能提取，请联系管理员开通后再使用。')+
    '仅读取文件不会提取产品或供应商。若需要这些内容，请勾选智能提取后另建批次；系统不会自动转成付费处理。当前仍可下载结果表格和失败报告。'
}
