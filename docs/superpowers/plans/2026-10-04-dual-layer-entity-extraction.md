# 任务一双层实体提取实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将任务一拆分为采购包级基础结果和附件行级明细结果，在保持现有七字段接口兼容的同时提高基础评测精确率并保留附件覆盖能力。

**Architecture:** `ExtractionResult.entities` 继续承载每包一条的基础结果，新增 `detail_entities` 与 `detail_status` 承载附件明细及其可用状态。流水线分别构造 HTML-only 基础输入和 attachment-only 明细输入，独立检查点、独立输入指纹、失败隔离，最终通过同一个兼容 JSON、MySQL 仓库和 FastAPI 对外提供。

**Tech Stack:** Python 3.12、Pydantic、OpenAI-compatible DeepSeek/Qwen API、FastAPI、PyMySQL/MySQL 8、pytest。

**Spec:** `docs/superpowers/specs/2026-10-04-dual-layer-entity-extraction-design.md`

## Global Constraints

- 基础层每个采购包最多一条记录，仍然只使用官方七字段。
- 明细层不得进入基础准确率、精确率或召回率计算。
- “品牌（产品供应商）”在基础层优先使用明确对应的中标或成交供应商。
- “详见附件”等引用性文字不得直接成为基础字段值；附件缺失或不可读时按规格写“附件缺失”。
- 总价只使用明确中标或成交金额，禁止预算、最高限价、折扣率或下浮率推算。
- 旧 `raw_results.json` 必须可以读取，缺少新字段时不得报错。
- 默认并发固定为 `config.PROCESS_MAX_WORKERS=2`，适配 2 vCPU、4 GB、Ubuntu 24.04。
- 每 10 条写检查点；基础层和明细层失败互不清空对方的已验证结果。
- 私有金标、比赛原始数据、API Key、服务器密码和真实公告内容不得进入 Git。
- 所有自动化测试只使用虚构公告、供应商、金额、文件名和附件。

## Review Focus

1. HTML 明确写“详见附件”但同名 ZIP 不存在时，应标记 `missing`，基础引用字段为“附件缺失”，不能误标 `no_attachment`。
2. ZIP 同时包含一个可读附件和一个损坏附件时，应保留可读明细并标记 `available`，同时记录失败文件，不能把整包判为 `unreadable`。
3. 旧 JSON 缺少 `detail_status` 和输入指纹时应安全加载为待迁移状态，完成状态解析前不得通过正式 API 发布。
4. 同一采购包在正文重复出现且附件有 50 条设备时，基础层必须仍为一条，50 条明细必须保留在明细层。
5. 数据库保存基础结果成功、保存明细失败时，整篇公告事务必须回滚，不能留下新基础配旧明细。

---

### Task 1: 双层结果数据契约与兼容序列化

**Files:**
- Modify: `task1_entity_extraction/models/schemas.py`
- Create: `task1_entity_extraction/models/result_io.py`
- Modify: `task1_entity_extraction/output/writer.py`
- Modify: `task1_entity_extraction/pipeline.py`
- Modify: `task1_entity_extraction/import_to_db.py`
- Create: `task1_entity_extraction/tests/test_dual_layer_models.py`

**Interfaces:**
- Consumes: 现有 `ExtractedEntity`、`ExtractionResult` 和 `deduplicate_entities()`。
- Produces: `DetailStatus`；`DetailedEntity`；扩展后的 `ExtractionResult`；`parse_extraction_result(payload: Mapping[str, Any]) -> ExtractionResult`；`serialize_extraction_result(result: ExtractionResult) -> dict`；`deduplicate_detail_entities(items: list[DetailedEntity]) -> list[DetailedEntity]`。

- [ ] **Step 1: 写旧 JSON 兼容和新 JSON 往返的失败测试**

在 `tests/test_dual_layer_models.py` 增加：

```python
def test_legacy_result_loads_with_unresolved_detail_status():
    result = parse_extraction_result({
        "announcement_id": "fictional-001",
        "success": True,
        "entities": [{"product_name": "虚构设备"}],
    })
    assert result.detail_entities == []
    assert result.detail_status is None
    assert result.schema_version == 1
    assert result.summary_input_hash == ""
    assert result.detail_input_hash == ""


def test_dual_layer_result_round_trips_without_mixing_layers():
    result = ExtractionResult(
        announcement_id="fictional-002",
        entities=[ExtractedEntity(product_name="采购包一")],
        detail_entities=[DetailedEntity(
            product_name="设备甲", source_file="明细.pdf", source_page=""
        )],
        detail_status="available",
        schema_version=2,
        summary_input_hash="summary-hash",
        detail_input_hash="detail-hash",
    )
    loaded = parse_extraction_result(serialize_extraction_result(result))
    assert [x.product_name for x in loaded.entities] == ["采购包一"]
    assert [x.product_name for x in loaded.detail_entities] == ["设备甲"]
    assert loaded.detail_entities[0].source_page == ""
    assert loaded.detail_status == "available"
```

再增加 `test_legacy_status_must_be_resolved_before_publish`，断言 `serialize_extraction_result(result, require_resolved=True)` 对 `detail_status=None` 抛出包含“待迁移”的 `ValueError`，覆盖 Review Focus 3。

- [ ] **Step 2: 运行测试并确认因新类型/函数不存在而失败**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_dual_layer_models.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task1-red
```

Expected: collection FAIL，缺少 `DetailedEntity` 或 `models.result_io`。

- [ ] **Step 3: 在 `models/schemas.py` 增加双层模型**

实现以下精确接口：

```python
DetailStatus = Literal[
    "available", "missing", "unreadable", "no_attachment", "extraction_failed"
]

class DetailedEntity(ExtractedEntity):
    package_name: str = ""
    source_file: str = ""
    source_page: str = ""

class ExtractionResult(BaseModel):
    # 保留原字段
    schema_version: int = 2
    detail_entities: List[DetailedEntity] = Field(default_factory=list)
    detail_status: Optional[DetailStatus] = None
    detail_error_message: str = ""
    summary_input_hash: str = ""
    detail_input_hash: str = ""
```

`deduplicate_detail_entities()` 在同一 `package_name` 内按七字段 NFKC、去空白后的完整内容稳定去重；来源字段不参与判重，保留首次来源。

- [ ] **Step 4: 在 `models/result_io.py` 集中实现读写**

实现：

```python
def parse_extraction_result(payload: Mapping[str, Any]) -> ExtractionResult
def serialize_extraction_result(
    result: ExtractionResult,
    *,
    require_resolved: bool = False,
) -> dict
```

旧载荷缺少 `schema_version` 时解析为 `1`，新结果固定为 `2`。序列化字典固定包含 `schema_version`、`announcement_id`、`success`、`error`、`entities`、`detail_entities`、`detail_status`、`detail_error_message`、两个输入指纹和 `extra_info`，不写 `raw_response`。`require_resolved=True` 且状态为 `None` 时拒绝输出。

- [ ] **Step 5: 让所有 JSON 入口统一使用新读写函数**

- `ResultWriter.write_raw_results()` 调用 `serialize_extraction_result()`；
- `EntityExtractionPipeline._load_previous_results()` 调用 `parse_extraction_result()`；
- `import_to_db.py` 调用 `parse_extraction_result()`；
- 保留旧 JSON 的 `error` 到 `error_message` 映射。

- [ ] **Step 6: 运行 Task 1 测试和旧框架测试**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_dual_layer_models.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task1-green
D:\project\.venv\Scripts\python.exe -X utf8 test_framework.py
```

Expected: 新测试全部 PASS；旧框架所有检查通过。

- [ ] **Step 7: 提交 Task 1**

```bash
git add task1_entity_extraction/models/schemas.py task1_entity_extraction/models/result_io.py task1_entity_extraction/output/writer.py task1_entity_extraction/pipeline.py task1_entity_extraction/import_to_db.py task1_entity_extraction/tests/test_dual_layer_models.py
git commit -m "feat: add dual-layer extraction result contract"
```

### Task 2: 附件解析状态与失败证据

**Files:**
- Modify: `task1_entity_extraction/data_loader/attachment_parser.py`
- Create: `task1_entity_extraction/tests/test_attachment_outcomes.py`

**Interfaces:**
- Consumes: Task 1 的 `DetailStatus`。
- Produces: `AttachmentParseOutcome`；`AttachmentParser.parse_zip_with_status(zip_path: str, announcement_id: str = "") -> AttachmentParseOutcome`；`classify_attachment_status(html_text: str, zip_exists: bool, outcome: AttachmentParseOutcome | None) -> DetailStatus`；旧 `parse_zip()` 二元组接口保持兼容。

- [ ] **Step 1: 写附件状态的失败测试**

测试必须覆盖：

```python
def test_missing_zip_referenced_by_html_is_missing():
    assert classify_attachment_status("规格型号详见附件", False, None) == "missing"


def test_absent_zip_without_reference_is_no_attachment():
    assert classify_attachment_status("本项目无附件", False, None) == "no_attachment"


def test_mixed_zip_keeps_readable_file_and_reports_failed_file(tmp_path, monkeypatch):
    outcome = AttachmentParser().parse_zip_with_status(str(make_mixed_zip(tmp_path)))
    assert outcome.status == "available"
    assert outcome.parsed_files == ["可读.txt"]
    assert outcome.failed_files == ["损坏.pdf"]
```

最后一个测试覆盖 Review Focus 2；另加损坏 ZIP 全包 `unreadable` 测试。

- [ ] **Step 2: 运行测试并确认新接口缺失**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_attachment_outcomes.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task2-red
```

Expected: FAIL，缺少 `AttachmentParseOutcome` 或 `parse_zip_with_status`。

- [ ] **Step 3: 实现 `AttachmentParseOutcome` 和状态解析**

使用冻结 dataclass：

```python
@dataclass(frozen=True)
class AttachmentParseOutcome:
    texts: dict[str, str]
    tables: dict[str, list]
    archive_files: list[str]
    parsed_files: list[str]
    failed_files: list[str]
    status: DetailStatus
    error_message: str = ""
```

`available` 的判定优先于局部失败：至少一个支持文件解析出文本或表格即为 `available`。ZIP 损坏、支持文件全部失败或全部无有效内容为 `unreadable`。路径不存在由 `classify_attachment_status()` 结合 HTML 决定 `missing` 或 `no_attachment`。

- [ ] **Step 4: 保持旧调用方兼容**

`parse_zip()` 内部调用 `parse_zip_with_status()`，仍返回 `(outcome.texts, outcome.tables)`；现有 API 与旧框架无需立即改签名。

- [ ] **Step 5: 运行附件测试与旧框架附件测试**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_attachment_outcomes.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task2-green
D:\project\.venv\Scripts\python.exe -X utf8 test_framework.py
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交 Task 2**

```bash
git add task1_entity_extraction/data_loader/attachment_parser.py task1_entity_extraction/tests/test_attachment_outcomes.py
git commit -m "feat: expose attachment parsing status"
```

### Task 3: HTML-only 采购包基础提取与确定性校验

**Files:**
- Modify: `task1_entity_extraction/extractor/prompts.py`
- Modify: `task1_entity_extraction/extractor/llm_extractor.py`
- Create: `task1_entity_extraction/extractor/summary_rules.py`
- Create: `task1_entity_extraction/tests/test_summary_extraction.py`

**Interfaces:**
- Consumes: Task 1 的 `DetailStatus`、`ExtractedEntity`、`ExtractionResult`。
- Produces: `build_summary_source(html_text: str, html_tables: list) -> str`；`build_summary_prompt(text: str, has_tables: bool, detail_status: DetailStatus) -> tuple[str, str]`；`sanitize_summary_entities(entities: list[ExtractedEntity], *, detail_status: DetailStatus, source_text: str) -> list[ExtractedEntity]`；`LLMExtractor.extract_summary(...) -> ExtractionResult`。

- [ ] **Step 1: 写基础输入隔离、占位词和金额来源的失败测试**

增加以下行为测试：

```python
def test_summary_source_contains_html_tables_but_not_attachment_text():
    source = build_summary_source("正文", [[['采购包', '供应商']]])
    assert "正文" in source
    assert "采购包 | 供应商" in source
    assert "附件设备甲" not in source


@pytest.mark.parametrize("status, expected", [
    ("missing", "附件缺失"),
    ("unreadable", "附件缺失"),
    ("available", ""),
])
def test_reference_placeholder_follows_attachment_status(status, expected):
    result = sanitize_summary_entities(
        [ExtractedEntity(product_name="采购包一", spec_model="详见附件")],
        detail_status=status,
        source_text="规格型号详见附件",
    )
    assert result[0].spec_model == expected


def test_budget_times_discount_is_not_accepted_as_transaction_total():
    result = sanitize_summary_entities(
        [ExtractedEntity(product_name="采购包一", total_price="4.68万元")],
        detail_status="no_attachment",
        source_text="项目预算6万元，成交方式为市场指导价的78%",
    )
    assert result[0].total_price == ""
```

再增加“明确成交金额 4.68 万元应保留”“相同采购包摘要稳定去重”“基础 Prompt 明确品牌使用对应成交供应商”的测试。

- [ ] **Step 2: 运行测试并确认新 Prompt/规则不存在**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_summary_extraction.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task3-red
```

Expected: FAIL，缺少 `summary_rules` 或 `build_summary_prompt`。

- [ ] **Step 3: 实现 HTML-only Prompt**

`SUMMARY_SYSTEM_PROMPT` 必须明确：一包一条、品牌优先对应成交供应商、不得输出附件行项目、不得计算金额、不得直接输出引用性占位词。`build_summary_source()` 只接受 HTML 文本与 HTML 表格，不提供附件参数。

- [ ] **Step 4: 实现确定性清理规则**

`sanitize_summary_entities()` 完成：

- 引用性占位词清理；
- 缺失/不可读状态的“附件缺失”转换；
- NFKC 七字段完全相同记录的稳定去重；
- 总价金额必须能在“中标金额、成交金额、中标价、成交价”等明确交易语境中找到；只在预算、限价、比例语境中出现则清空。

金额语境匹配只用于拒绝可疑值，不推算或补写金额。

- [ ] **Step 5: 在 `LLMExtractor` 增加基础提取入口**

实现：

```python
def extract_summary(
    self,
    text: str,
    *,
    has_tables: bool,
    detail_status: DetailStatus,
    announcement_id: str = "",
) -> ExtractionResult
```

调用现有 `_call_llm()` 与 `_parse_entities()`，随后调用 `sanitize_summary_entities()`。不改变旧 `extract()`，直到 Task 5 完成流水线切换。

- [ ] **Step 6: 运行 Task 3 测试和全部现有 pytest**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_summary_extraction.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task3-green
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task3-all
```

Expected: 全部 PASS。

- [ ] **Step 7: 提交 Task 3**

```bash
git add task1_entity_extraction/extractor/prompts.py task1_entity_extraction/extractor/llm_extractor.py task1_entity_extraction/extractor/summary_rules.py task1_entity_extraction/tests/test_summary_extraction.py
git commit -m "feat: extract package-level summary entities"
```

### Task 4: 附件明细提取、分块和旧结果迁移

**Files:**
- Modify: `task1_entity_extraction/extractor/prompts.py`
- Modify: `task1_entity_extraction/extractor/llm_extractor.py`
- Create: `task1_entity_extraction/extractor/detail_processing.py`
- Create: `task1_entity_extraction/tests/test_detail_extraction.py`

**Interfaces:**
- Consumes: Task 1 的 `DetailedEntity`、`deduplicate_detail_entities()`；Task 2 的 `AttachmentParseOutcome`。
- Produces: `chunk_attachment_text(text: str, max_length: int, overlap: int) -> list[str]`；`migrate_legacy_entities(result: ExtractionResult, *, detail_status: DetailStatus) -> ExtractionResult`；`LLMExtractor.extract_details(...) -> tuple[list[DetailedEntity], DetailStatus, str]`。

- [ ] **Step 1: 写迁移、分块、来源和去重的失败测试**

测试至少包含：

```python
def test_legacy_entities_move_to_details_without_becoming_summary():
    old = ExtractionResult(
        announcement_id="fictional-001",
        entities=[ExtractedEntity(product_name="设备甲")],
        detail_status=None,
    )
    migrated = migrate_legacy_entities(old, detail_status="available")
    assert migrated.entities == []
    assert [x.product_name for x in migrated.detail_entities] == ["设备甲"]
    assert migrated.detail_status == "available"


def test_legacy_entities_without_readable_attachment_are_not_detail_candidates():
    old = ExtractionResult(
        announcement_id="fictional-002",
        entities=[ExtractedEntity(product_name="原基础结果")],
        detail_status=None,
        schema_version=1,
    )
    migrated = migrate_legacy_entities(old, detail_status="no_attachment")
    assert migrated.entities == []
    assert migrated.detail_entities == []


def test_duplicate_detail_across_chunks_keeps_first_source():
    items = deduplicate_detail_entities([
        DetailedEntity(package_name="包一", product_name="设备甲", source_file="A.pdf"),
        DetailedEntity(package_name="包一", product_name="设备甲", source_file="B.pdf"),
    ])
    assert len(items) == 1
    assert items[0].source_file == "A.pdf"
```

再通过 monkeypatch `_call_llm()` 模拟两个分块共返回 50 条明细和一条跨块重复项，断言最终保留 50 条且均带 `source_file`。

- [ ] **Step 2: 运行测试并确认新明细接口缺失**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_detail_extraction.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task4-red
```

Expected: FAIL，缺少 `detail_processing` 或 `extract_details`。

- [ ] **Step 3: 实现文本分块和旧结果迁移**

`chunk_attachment_text()` 使用 `config.MAX_TEXT_LENGTH_PER_REQUEST=8000` 和 `TEXT_CHUNK_OVERLAP=500`，优先在换行处截断，保证前进量大于零。`migrate_legacy_entities()` 只用于明确的迁移命令：当附件状态为 `available` 时，把旧 `entities` 转成无来源明细候选；其他状态不把旧基础行伪装成附件明细。所有迁移场景都清空旧基础层，等待新的 summary pass 填充，并把 `schema_version` 更新为 `2`。

- [ ] **Step 4: 增加明细 Prompt 与解析入口**

明细 Prompt 输出 `package_name` 加七字段；不输出采购包汇总行。实现：

```python
def extract_details(
    self,
    *,
    texts: dict[str, str],
    tables: dict[str, list],
    announcement_id: str = "",
) -> tuple[list[DetailedEntity], DetailStatus, str]
```

每个附件独立分块，注入 `source_file`；全部分块失败返回 `([], "extraction_failed", error)`，至少一个成功则返回去重明细和 `available`，局部失败文本写入诊断信息但不丢弃成功明细。

- [ ] **Step 5: 运行 Task 4 测试和模型解析测试**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_detail_extraction.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task4-green
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task4-all
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交 Task 4**

```bash
git add task1_entity_extraction/extractor/prompts.py task1_entity_extraction/extractor/llm_extractor.py task1_entity_extraction/extractor/detail_processing.py task1_entity_extraction/tests/test_detail_extraction.py
git commit -m "feat: preserve attachment-level detail entities"
```

### Task 5: 双层流水线、独立指纹和检查点 CLI

**Files:**
- Modify: `task1_entity_extraction/pipeline.py`
- Modify: `task1_entity_extraction/main.py`
- Modify: `task1_entity_extraction/api/routers/task3.py`
- Modify: `task1_entity_extraction/output/writer.py`
- Create: `task1_entity_extraction/tests/test_dual_layer_pipeline.py`

**Interfaces:**
- Consumes: Tasks 1–4 的模型、附件结果、基础提取和明细提取入口。
- Produces: `RunMode = Literal["summary", "details", "both"]`；扩展的 `process_single_file()`、`process_directory()`、`run()`；CLI 参数 `--mode`、`--previous-results`、`--raw-output`、`--include-ids-file`。

- [ ] **Step 1: 写端到端隔离和 Review Focus 1/4 的失败测试**

使用虚构 HTML、虚构 ZIP 和 mock extractor：

```python
def test_summary_never_receives_attachment_text(tmp_path, fake_extractor):
    result = pipeline.process_single_file(str(make_html_and_zip(tmp_path)))
    assert "附件设备甲" not in fake_extractor.summary_calls[0].text
    assert "附件设备甲" in fake_extractor.detail_calls[0].texts["明细.txt"]


def test_one_repeated_package_and_fifty_details_stay_in_separate_layers(...):
    result = pipeline.process_single_file(...)
    assert len(result.entities) == 1
    assert len(result.detail_entities) == 50


def test_referenced_but_absent_zip_marks_missing_and_uses_attachment_missing(...):
    result = pipeline.process_single_file(...html_with_reference...)
    assert result.detail_status == "missing"
    assert result.entities[0].spec_model == "附件缺失"
```

第三个测试覆盖 Review Focus 1，第二个覆盖 Review Focus 4。

- [ ] **Step 2: 写分层续跑与输入指纹的失败测试**

断言：

- summary hash 未变化且已有成功 summary 时，`--resume --mode summary` 不调用模型；
- 附件 hash 变化时只重跑 details；
- summary 失败保留上一版 `entities`；
- details 失败保留基础层并写 `extraction_failed`；
- 自定义 raw output 使用独立 `<stem>.<mode>.checkpoint.json`，不覆盖默认 `raw_results.json`。

- [ ] **Step 3: 运行测试并确认现有单层流水线失败**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_dual_layer_pipeline.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task5-red
```

Expected: FAIL，现有流水线把 `content.full_text` 发送给单一 `extract()`，也没有新 CLI 参数。

- [ ] **Step 4: 重构单文件处理为独立层**

将签名改为：

```python
def process_single_file(
    self,
    html_file_path: str,
    *,
    run_mode: RunMode = "both",
    previous_result: ExtractionResult | None = None,
    resume: bool = False,
) -> ExtractionResult
```

- summary 使用 `build_summary_source(content.html_text, content.html_tables)`；
- details 只使用 `AttachmentParseOutcome.texts/tables`；
- 对两层输入分别计算 SHA-256；
- 未运行的一层从 `previous_result` 原样复制；
- `schema_version=1` 的旧结果先结合本轮附件状态调用 `migrate_legacy_entities()`；
- 任一层失败不得清空另一层；summary 失败时只有 `schema_version>=2` 的已验证基础结果可以回退，旧单层结果不得重新发布为基础层；
- `result.success` 只表示基础层是否成功；明细失败通过 `detail_status="extraction_failed"` 和 `detail_error_message` 表达，不能把成功基础层标成失败；
- `extra_info` 继续使用 `content.full_text`，但 `--no-extra` 在 40 条试跑中关闭。

- [ ] **Step 5: 扩展目录处理、检查点和输出**

`process_directory()` 接收 `run_mode`、`previous_results: dict[str, ExtractionResult]`、`include_ids: set[str] | None`、`resume`。检查点按 raw output stem 与 mode 隔离，每 10 条原子写入。`ResultWriter.write_raw_results()` 新增临时写入加 `os.replace()`，正式 JSON 不出现半写文件。

- [ ] **Step 6: 扩展 CLI 与上传任务**

`main.py` 新增：

- `--mode {summary,details,both}`，默认 `both`；
- `--previous-results PATH`；
- `--raw-output NAME.json`；
- `--include-ids-file PATH`，每行一个公告 ID，忽略空行并拒绝重复 ID。

上传任务继续默认 `both`，统计同时显示基础实体数和明细实体数。

- [ ] **Step 7: 运行流水线测试、全部 pytest 和旧框架**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_dual_layer_pipeline.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task5-green
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task5-all
D:\project\.venv\Scripts\python.exe -X utf8 test_framework.py
```

Expected: 全部 PASS。

- [ ] **Step 8: 提交 Task 5**

```bash
git add task1_entity_extraction/pipeline.py task1_entity_extraction/main.py task1_entity_extraction/api/routers/task3.py task1_entity_extraction/output/writer.py task1_entity_extraction/tests/test_dual_layer_pipeline.py
git commit -m "feat: orchestrate dual-layer extraction pipeline"
```

### Task 6: MySQL 原子持久化与附件明细 API

**Files:**
- Modify: `task1_entity_extraction/database/schema.py`
- Modify: `task1_entity_extraction/database/connection.py`
- Modify: `task1_entity_extraction/database/dao.py`
- Modify: `task1_entity_extraction/api/routers/task1.py`
- Create: `task1_entity_extraction/tests/test_dual_layer_persistence.py`
- Create: `task1_entity_extraction/tests/test_dual_layer_api.py`

**Interfaces:**
- Consumes: Task 1 的 `DetailedEntity`、`ExtractionResult`、`DetailStatus`。
- Produces: `CREATE_TABLE_ENTITY_DETAILS`；幂等 announcement 列迁移；`DatabaseManager.transaction()`；`DetailEntityDAO`；`DataRepository.details`；`GET /api/entities/{announcement_id}/details`；`GET /api/detail-entities`；扩展 stats/health。

- [ ] **Step 1: 写 DDL、DAO 和事务回滚的失败测试**

使用记录 SQL 的 fake database，断言：

- 初始化包含 `extracted_entity_details` 表；
- `announcements` 已存在但缺列时只添加 `detail_status`、`detail_error_message`；再次初始化不重复添加；
- `DetailEntityDAO.save_details()` 对公告幂等替换并稳定去重；
- `DataRepository.save_extraction_result()` 保存基础成功但明细插入抛错时调用 rollback，且不 commit，覆盖 Review Focus 5。

- [ ] **Step 2: 运行持久化测试并确认新表/DAO 不存在**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_dual_layer_persistence.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task6-db-red
```

Expected: FAIL，缺少新 DDL、DAO 或事务接口。

- [ ] **Step 3: 实现幂等数据库初始化和事务**

- `ALL_TABLES` 加入明细表；
- `DatabaseManager.init_tables()` 使用 `SHOW COLUMNS FROM announcements LIKE %s` 后按需执行 `ALTER TABLE`，不依赖 MySQL 方言不一致的 `ADD COLUMN IF NOT EXISTS`；
- `DatabaseManager.transaction()` 在成功时 commit，异常时 rollback 并重新抛出；
- `DataRepository.save_extraction_result()` 在一个事务中保存公告、基础、明细和任务二关系。

- [ ] **Step 4: 实现 `DetailEntityDAO`**

提供：

```python
def save_details(self, announcement_id: str, items: list[DetailedEntity]) -> None
def list_by_announcement(self, announcement_id: str) -> list[dict]
def count_all(self) -> int
def search(self, keyword: str = "", limit: int = 100, offset: int = 0) -> list[dict]
def count_search(self, keyword: str = "") -> int
```

公告元数据 upsert 同时写 `detail_status` 和 `detail_error_message`；状态为 `None` 时拒绝正式保存。

- [ ] **Step 5: 写并运行 API 失败测试**

使用 fake repository 直接调用路由函数，断言：

- 明细存在时返回 `announcement_id`、`detail_status`、`total`、`items`；
- `missing` 且无明细时返回 200 和空数组，不返回 404；
- 未迁移 `detail_status=None` 返回 409；
- `/api/stats` 分别返回 `entities` 与 `detail_entities`；
- `/api/detail-entities?keyword=设备甲` 能按产品、品牌、型号和品目搜索明细并分页；
- 旧 `/api/entities/{id}` 响应保持不变。

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_dual_layer_api.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task6-api-red
```

Expected: FAIL，缺少明细路由和统计字段。

- [ ] **Step 6: 实现明细 API 和统计**

新增明确的明细路径：

```python
@router.get("/entities/{announcement_id}/details")
def entity_details_by_announcement(...)
```

`health()` 与 `stats()` 增加 `detail_entities`；基础 `entities` 含义不变。

新增独立的 `GET /api/detail-entities` 分页搜索接口，避免改变现有 `/api/entities` 的默认基础层语义。

- [ ] **Step 7: 运行 Task 6 测试和全部 pytest**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_dual_layer_persistence.py tests/test_dual_layer_api.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task6-green
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task6-all
```

Expected: 全部 PASS。

- [ ] **Step 8: 提交 Task 6**

```bash
git add task1_entity_extraction/database/schema.py task1_entity_extraction/database/connection.py task1_entity_extraction/database/dao.py task1_entity_extraction/api/routers/task1.py task1_entity_extraction/tests/test_dual_layer_persistence.py task1_entity_extraction/tests/test_dual_layer_api.py
git commit -m "feat: persist and serve attachment details"
```

### Task 7: 评测边界、运维文档与 40 条试跑

**Files:**
- Modify: `task1_entity_extraction/tests/test_run_evaluation.py`
- Modify: `task1_entity_extraction/run_evaluation.py`
- Modify: `task1_entity_extraction/.env.example`
- Modify: `docs/任务一-业务流程说明.md`
- Create: `task1_entity_extraction/tests/test_dual_layer_evaluation.py`

**Interfaces:**
- Consumes: Task 5 的双层 raw JSON 和 CLI；现有 `evaluate_files()`。
- Produces: 明确只评测 `entities` 的回归测试；40 条 summary-only 私有试跑命令；全量运行门禁。

- [ ] **Step 1: 写评测忽略明细层的失败测试**

构造同一条基础实体和 50 条错误明细，断言指标仍为 100%：

```python
def test_human_gold_metrics_ignore_detail_entities(tmp_path):
    predictions = [{
        "announcement_id": "fictional-001",
        "entities": [{"product_name": "采购包一"}],
        "detail_entities": [{"product_name": f"错误设备-{i}"} for i in range(50)],
        "detail_status": "available",
        "success": True,
    }]
    metrics = evaluate_fixture(predictions, gold=[{"product_name": "采购包一"}])
    assert metrics.accuracy == 1.0
    assert metrics.precision == 1.0
```

再断言评测报告不写入附件明细或私有绝对路径。

- [ ] **Step 2: 运行测试并确认兼容边界**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_dual_layer_evaluation.py -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-task7-red
```

Expected: 如果现有加载器忽略额外字段则测试可能直接 PASS；此时记录“评测边界已有实现”，不要制造无意义代码改动。报告隐私测试仍必须运行。

- [ ] **Step 3: 只做必要的评测兼容改动**

若 Step 2 暴露问题，只修改 `load_predictions()` 使其显式读取 `entities` 并忽略 `detail_entities`。不得把明细层转换成基础预测。

- [ ] **Step 4: 更新配置示例与业务流程文档**

文档写明：

- `--mode summary`、`--mode details`、`--mode both` 的用途；
- 40 条试跑使用 `--no-extra --workers 2 --raw-output raw_results_dual_layer_pilot.json`；
- 基础评测与附件覆盖率分开；
- 前端基础接口和明细接口；
- 2 vCPU、4 GB 推荐值；
- 不在命令行示例中出现任何真实密钥、私有文件名或真实公告 ID。

- [ ] **Step 5: 运行全部自动化验证**

Run:

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --basetemp=../.superpowers/sdd/2026-10-04-dual-layer-entity-extraction/pytest-final
D:\project\.venv\Scripts\python.exe -X utf8 test_framework.py
git diff --check
$secretPatterns = @(
  ('sk-' + '[A-Za-z0-9]{20,}'),
  ('xy' + '123456'),
  ('47\\.' + '237\\.'),
  ('t20' + '26'),
  ('procurement_' + 'initial_review')
)
rg -n -g '!output/**' -g '!.env' -e $secretPatterns[0] -e $secretPatterns[1] -e $secretPatterns[2] -e $secretPatterns[3] -e $secretPatterns[4] task1_entity_extraction docs
```

Expected: pytest 全部 PASS；旧框架全部通过；diff check 无错误；敏感信息扫描无输出。

- [ ] **Step 6: 提交代码与文档**

```bash
git add task1_entity_extraction/run_evaluation.py task1_entity_extraction/tests/test_run_evaluation.py task1_entity_extraction/tests/test_dual_layer_evaluation.py task1_entity_extraction/.env.example docs/任务一-业务流程说明.md
git commit -m "docs: document dual-layer evaluation workflow"
```

- [ ] **Step 7: 从私有金标生成本地 ID 清单（不提交）**

使用本地私有金标读取 40 个唯一 `announcement_id`，写入 Git 忽略的临时文件。校验恰好 40 行且没有重复；不要在终端或日志打印全部 ID。

- [ ] **Step 8: 运行 40 条 summary-only 试跑**

使用环境变量中的 DeepSeek Key，不在命令行传入密钥：

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 main.py --input <本地公告目录> --provider deepseek --mode summary --previous-results output/raw_results.json --include-ids-file <私有ID清单> --raw-output raw_results_dual_layer_pilot.json --no-extra --workers 2 --resume
```

Expected: 只处理 40 个公告；每 10 条写检查点；输出同时保留迁移后的 `detail_entities` 和新 `entities`；不覆盖旧 `raw_results.json`。

- [ ] **Step 9: 离线评测并执行门禁**

```powershell
D:\project\.venv\Scripts\python.exe -X utf8 run_evaluation.py <私有金标JSON> --predictions output/raw_results_dual_layer_pilot.json --report-out output/evaluation_report_dual_layer_pilot.json --errors-out output/evaluation_errors_dual_layer_pilot.csv
```

必须同时满足：

- 综合得分高于 `75.71%`；
- 精确率高于 `71.85%`；
- 基础层不含“详见附件”等引用性占位词；
- 旧附件详细候选没有因 summary pass 丢失；
- “少量采购包、大量附件设备”的虚构回归测试通过。

任何门槛未满足时，停止全量运行，保留试跑输出并根据错误 CSV 回到对应任务排查根因。

- [ ] **Step 10: 报告试跑结果并等待全量运行决定**

向用户报告新旧指标、API 调用条数、失败公告数、基础实体数、明细实体保留数和主要剩余错误。全量运行会产生额外 API 费用和正式数据变化，因此在用户看到试跑结果并明确同意后再执行；不得在本任务中自动开始。
