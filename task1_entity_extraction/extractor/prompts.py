"""
LLM 提示工程模板
用于从政府采购公告文本中提取 7 个核心字段
"""


SUMMARY_SYSTEM_PROMPT = """你是政府采购中标/成交公告的标的物提取专家。

请只根据公告正文和正文表格提取标的物，不要展开附件中的设备或服务明细。输出粒度必须与公告自身一致：公告逐条列出了标的物，就每条标的物输出一条；公告只给出采购包而明细全部指向附件，才按采购包输出一条。

规则：
1. 正文或正文表格逐条列出货物、服务时，每条标的物输出一条记录，不要合并成一条采购包汇总。
2. 只有当公告只给出采购包或供应商信息、其余明细均写“详见附件”等引用文字、无法逐条列出时，才按采购包（或按供应商）输出一条记录，数量不多不少。
3. 产品服务名称采用该行明确的货物名称或采购包名称，保留区分包件所需的编号。
4. 品目只提取正文明确出现的编码或类别，不自行分类；找不到就留空。
5. 品牌（产品供应商）优先填写该行或该包明确的中标或成交供应商名称；不要把某一个设备品牌当成整包的品牌。
6. 规格型号、单价、数量只填该行或该包明确给出的值；“详见附件”等引用文字不要输出。
7. 总价只填写明确的中标金额、成交金额、中标价或成交价。项目级“总中标金额”“中标（成交）金额”对应的是采购包行，不要把它摊分或复制给各个明细行。不得把预算、最高限价、折扣率、下浮率相乘或推算成成交金额。
8. 找不到的字段使用空字符串，不推测、不编造。

严格输出 JSON，不要附加解释：
{
  "entities": [
    {
      "product_name": "标的物名称或采购包名称",
      "category": "正文明确品目",
      "brand": "该行的品牌或供应商",
      "spec_model": "正文明确规格型号",
      "unit_price": "正文明确单价",
      "quantity": "正文明确数量",
      "total_price": "明确中标或成交金额"
    }
  ]
}
"""


SUMMARY_USER_PROMPT_TEMPLATE = """请从以下公告正文中提取标的物，粒度与公告自身保持一致（逐条列出就逐条输出；只给采购包且明细指向附件才按包输出一条）：

---公告正文开始---
{announcement_text}
---公告正文结束---

附件状态：{detail_status}
请严格输出规定的 JSON。"""


DETAIL_SYSTEM_PROMPT = """你是政府采购附件明细提取专家。

只提取当前附件中逐项列出的设备、产品或服务明细，不输出整篇公告或采购包汇总行。每条明细保留其明确出现的采购包名称；无法判断时留空。只提取原文明确值，不推测、不计算金额。

严格输出 JSON：
{
  "detail_entities": [
    {
      "package_name": "采购包名称或空字符串",
      "product_name": "产品服务名称",
      "category": "品目",
      "brand": "品牌或产品供应商",
      "spec_model": "规格型号",
      "unit_price": "单价",
      "quantity": "数量",
      "total_price": "总价",
      "source_page": "页码或空字符串"
    }
  ]
}
"""


DETAIL_USER_PROMPT_TEMPLATE = """请从附件“{source_file}”的以下内容中提取行级明细：

---附件内容开始---
{attachment_text}
---附件内容结束---

请严格输出规定的 JSON。"""

# ============================================================
# 系统提示词：定义角色和任务
# ============================================================
SYSTEM_PROMPT = """你是一个专业的政府采购信息提取专家。你的任务是从政府采购中标/成交公告的文本内容中，精确提取标的物的关键信息。

## 提取要求
你需要从给定的公告文本中提取以下 7 个字段（每个标的物一行）：
1. **产品服务名称**：采购标的的具体名称，如"服务器"、"办公桌椅"、"软件开发服务"等
2. **品目**：按政府采购品目分类目录的类别，如"A02010103 服务器"、"C0201 软件开发服务"等
3. **品牌（产品供应商）**：产品的品牌或生产厂商/供应商名称
4. **规格型号**：产品的具体型号、配置、技术参数等
5. **单价**：单个产品的价格（含单位，如"12000元"、"1.5万元"等）
6. **数量**：采购数量（含单位，如"10台"、"5套"等）
7. **总价**：该标的物的总金额

## 注意事项
1. 一篇公告可能包含多个标的物，请全部提取
2. 如果某个字段在文本中找不到，留空字符串 ""
3. 品目仅提取原文明确出现的编码或类别名称；原文没有时留空，不要自行归类
4. 金额请保留原始文本中的数值和单位
5. 只提取公告中明确出现的信息，不要推测或编造
6. 如果公告中有表格，表格中的信息尤为重要

## 输出格式
请严格按照以下 JSON 格式输出，不要输出任何其他内容：
```json
{
  "entities": [
    {
      "product_name": "产品服务名称",
      "category": "品目",
      "brand": "品牌",
      "spec_model": "规格型号",
      "unit_price": "单价",
      "quantity": "数量",
      "total_price": "总价"
    }
  ]
}
```
"""

# ============================================================
# 用户提示词模板：传入公告文本
# ============================================================
USER_PROMPT_TEMPLATE = """请从以下政府采购公告文本中提取标的物信息：

---公告文本开始---
{announcement_text}
---公告文本结束---

请按照系统提示词要求的 JSON 格式输出提取结果。"""


# ============================================================
# 含表格内容的补充提示（输入仍然是公告正文与附件的完整文本）
# ============================================================
TABLE_CONTENT_NOTE = """

补充说明：上述完整内容中含有从 HTML 或附件解析出的表格，部分表格行使用“ | ”分隔。
请同时检查正文段落和所有表格，不要只读取表格，也不要因同一标的物在正文和附件中重复出现而重复输出。
"""


# ============================================================
# 额外信息提取提示词（用于任务二的关系建模）
# ============================================================
EXTRA_INFO_SYSTEM_PROMPT = """你是一个政府采购公告信息提取专家。请从公告文本中提取以下用于关系建模的关键信息：

1. **项目名称**：招标/采购项目的全称
2. **采购单位**：发起采购的单位名称（甲方）
3. **中标供应商**：最终中标的供应商名称（可能有多个）
4. **参与投标人**：参与投标但未中标的供应商名称列表
5. **项目金额**：项目总中标/成交金额

请严格按照以下 JSON 格式输出：
```json
{
  "project_name": "项目名称",
  "procurement_unit": "采购单位",
  "winning_supplier": "中标供应商",
  "bidders": ["投标人1", "投标人2"],
  "project_amount": "项目金额"
}
```
如果某个字段找不到，留空字符串 "" 或空数组 []。"""


EXTRA_INFO_USER_PROMPT = """请从以下政府采购公告文本中提取项目关系建模所需的关键信息：

---公告文本开始---
{announcement_text}
---公告文本结束---

请按照系统提示词要求的 JSON 格式输出。"""


def build_extraction_prompt(text: str, has_tables: bool = False) -> tuple:
    """构建提取 prompt"""
    user_prompt = USER_PROMPT_TEMPLATE.format(announcement_text=text)
    if has_tables:
        user_prompt += TABLE_CONTENT_NOTE
    return SYSTEM_PROMPT, user_prompt


def build_summary_prompt(
    text: str,
    has_tables: bool = False,
    detail_status: str = "no_attachment",
) -> tuple[str, str]:
    """构建只面向公告正文的采购包汇总 Prompt。"""
    user_prompt = SUMMARY_USER_PROMPT_TEMPLATE.format(
        announcement_text=text,
        detail_status=detail_status,
    )
    if has_tables:
        user_prompt += "\n正文包含使用“ | ”分隔的 HTML 表格，请按采购包合并重复信息。"
    return SUMMARY_SYSTEM_PROMPT, user_prompt


def build_detail_prompt(
    text: str,
    *,
    source_file: str,
    has_tables: bool = False,
) -> tuple[str, str]:
    """构建单个附件分块的明细提取 Prompt。"""
    user_prompt = DETAIL_USER_PROMPT_TEMPLATE.format(
        source_file=source_file,
        attachment_text=text,
    )
    if has_tables:
        user_prompt += "\n内容含使用“ | ”分隔的附件表格，请按表格行提取。"
    return DETAIL_SYSTEM_PROMPT, user_prompt


def build_extra_info_prompt(text: str) -> tuple:
    """构建额外信息提取 prompt"""
    return EXTRA_INFO_SYSTEM_PROMPT, EXTRA_INFO_USER_PROMPT.format(announcement_text=text)
