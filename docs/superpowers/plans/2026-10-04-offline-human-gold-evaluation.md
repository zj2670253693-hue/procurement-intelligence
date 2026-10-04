# Offline Human-Gold Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Task 1 evaluator consume the 40-case human-review JSON directly and produce trustworthy aggregate metrics plus a field-level error CSV without exposing the private review data.

**Architecture:** Move ground-truth loading into a format-aware module, keep metric calculation in `Evaluator`, and add a separate audit module that reuses the exact same entity alignment and field matching methods. The CLI selects JSON or Excel by suffix, writes human-gold-specific outputs, and leaves the old self-derived report untouched.

**Tech Stack:** Python 3.10+, Pydantic 2, pandas, openpyxl, pytest, standard-library `argparse`, `json`, and `csv`.

**Spec:** `docs/superpowers/specs/2026-10-04-offline-human-gold-evaluation-design.md`

## Global Constraints

- Human-review JSON, reviewer names, absolute local paths, and generated error details must never be committed.
- The seven field names remain exactly `product_name`, `category`, `brand`, `spec_model`, `unit_price`, `quantity`, and `total_price`.
- JSON input must contain exactly 40 unique announcements by default; an explicit `--expected-cases` option may change that for tests or later validation sets.
- Deleted items are omitted, added items are retained, all-empty entities are omitted, and real empty strings remain empty.
- Exact field value `未公示` is rejected as an unclean placeholder instead of silently converted.
- Metrics and error rows must use one shared entity-alignment and field-matching implementation.
- The existing Excel evaluation path and `--allow-unreviewed` behavior remain compatible.
- Human-gold outputs use new filenames and never overwrite `output/evaluation_report.json`.
- Tests use fictional announcements and suppliers only.

## Review Focus

- JSON has 40 cases but a duplicate `announcement_id`: reject it before metrics are written; covered in Task 1 tests.
- An item is marked deleted while retaining non-empty data: omit it completely; covered in Task 1 tests.
- Prediction and gold entities have different ordering: align them by the shared matcher and avoid false errors; covered in Task 2 tests.
- A wrong non-empty value contributes one FP and one FN even though it appears as one `wrong_value` CSV row; covered in Task 2 tests through contribution columns.
- Evaluation fails after a previous successful run: do not partially overwrite either official output; covered in Task 3 tests by writing to temporary files only after validation succeeds.

---

## File Structure

- Create `task1_entity_extraction/evaluation/ground_truth.py`: format detection, Excel loader, review-JSON loader, and structural validation.
- Modify `task1_entity_extraction/evaluation/metrics.py`: expose stable public alignment and field-match methods used by both metrics and audit.
- Create `task1_entity_extraction/evaluation/audit.py`: produce field-level error rows and FP/FN contribution totals.
- Modify `task1_entity_extraction/run_evaluation.py`: argument parsing, loader selection, evaluation orchestration, and safe output writing.
- Create `task1_entity_extraction/tests/conftest.py`: fictional prediction and 40-case review-JSON builders.
- Create `task1_entity_extraction/tests/test_ground_truth.py`: loader and validation tests.
- Create `task1_entity_extraction/tests/test_evaluation_audit.py`: alignment, error-type, and metric-consistency tests.
- Create `task1_entity_extraction/tests/test_run_evaluation.py`: CLI and output integration tests.
- Create `task1_entity_extraction/requirements-dev.txt`: test-only pytest dependency.
- Modify `docs/任务一-业务流程说明.md`: reproducible offline evaluation commands and interpretation.
- Modify `.gitignore`: explicit protection for review-batch exports and private-validation directories.

## Task 1: Format-Aware Ground-Truth Loading

**Files:**
- Create: `task1_entity_extraction/evaluation/ground_truth.py`
- Create: `task1_entity_extraction/tests/conftest.py`
- Create: `task1_entity_extraction/tests/test_ground_truth.py`
- Create: `task1_entity_extraction/requirements-dev.txt`
- Modify: `task1_entity_extraction/run_evaluation.py`

**Interfaces:**
- Produces: `GroundTruthFormatError(ValueError)`.
- Produces: `load_ground_truth(path: Path, *, expected_cases: int = 40, require_reviewed: bool = True) -> list[dict]`.
- Produces: `load_review_json(path: Path, *, expected_cases: int = 40) -> list[dict]`.
- Produces: `load_review_excel(path: Path, *, require_reviewed: bool = True) -> list[dict]`.
- Preserves: `run_evaluation.load_ground_truth(xlsx_path, require_reviewed=True)` as a compatibility import or forwarding wrapper until all internal callers move to the new module.

- [ ] **Step 1: Add the test dependency** by creating `requirements-dev.txt` with `pytest>=8.0,<9` and no production-only packages.

- [ ] **Step 2: Write fictional JSON fixture builders** in `tests/conftest.py`: `make_review_case(case_no: int, items: list[dict] | None = None) -> dict` and `write_review_json(tmp_path: Path, cases: list[dict]) -> Path`.

- [ ] **Step 3: Write failing loader tests** in `tests/test_ground_truth.py` for a valid 40-case file, deletion omission, addition inclusion, all-empty entity omission, missing seven-field keys defaulting to empty, exact `未公示` rejection, duplicate/blank announcement IDs, malformed `cases/items/data`, wrong case count, unsupported suffix, and continued Excel loading.

- [ ] **Step 4: Run the focused tests and confirm they fail because the module is missing.**

Run: `D:\project\.venv\Scripts\python.exe -m pytest task1_entity_extraction/tests/test_ground_truth.py -v`

Expected: import failure for `evaluation.ground_truth`.

- [ ] **Step 5: Implement `GroundTruthFormatError` and `load_review_json`.** Decode with `utf-8-sig`, require a top-level object and `cases` list, validate unique non-empty IDs and expected count, skip deleted/all-empty items, preserve added items, stringify non-`None` values, and reject exact placeholder `未公示` with case/item/field context.

- [ ] **Step 6: Move the existing Excel logic into `load_review_excel`** without changing reviewed-verdict behavior, and implement `load_ground_truth` using exact lowercase suffix dispatch for `.json` and `.xlsx`.

- [ ] **Step 7: Keep a forwarding import/wrapper in `run_evaluation.py`** so earlier imports of `load_ground_truth` continue to work while the CLI is refactored in Task 3.

- [ ] **Step 8: Run the focused tests.**

Run: `D:\project\.venv\Scripts\python.exe -m pytest task1_entity_extraction/tests/test_ground_truth.py -v`

Expected: all loader tests pass.

- [ ] **Step 9: Commit.**

```bash
git add task1_entity_extraction/evaluation/ground_truth.py task1_entity_extraction/tests/conftest.py task1_entity_extraction/tests/test_ground_truth.py task1_entity_extraction/requirements-dev.txt task1_entity_extraction/run_evaluation.py
git commit -m "feat: load human review JSON as ground truth"
```

## Task 2: Shared Matching and Field-Level Error Audit

**Files:**
- Modify: `task1_entity_extraction/evaluation/metrics.py`
- Create: `task1_entity_extraction/evaluation/audit.py`
- Create: `task1_entity_extraction/tests/test_evaluation_audit.py`

**Interfaces:**
- Produces: `Evaluator.align_entities(pred_entities: list[dict], gt_entities: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]`.
- Produces: `Evaluator.field_match(pred: str, gt: str, field: str = "") -> bool`.
- Preserves: private `_align` and `_field_match` as forwarding aliases during this change to avoid breaking existing callers.
- Produces: `build_error_rows(predictions: list[ExtractionResult], ground_truths: list[dict], evaluator: Evaluator | None = None) -> list[dict]`.
- Each row includes: `announcement_id`, `prediction_entity_index`, `gold_entity_index`, `product_name_context`, `field`, `prediction_value`, `gold_value`, `error_type`, `fp_contribution`, and `fn_contribution`.

- [ ] **Step 1: Write failing audit tests** for `wrong_value`, `false_positive`, `false_negative`, `extra_entity`, and `missing_entity`; assert one wrong non-empty value has `fp_contribution=1` and `fn_contribution=1`.

- [ ] **Step 2: Add a failing entity-order test** proving swapped prediction order produces no audit rows when values otherwise match.

- [ ] **Step 3: Add a failing reconciliation test** asserting the sum of audit `fp_contribution`/`fn_contribution` equals the FP/FN implied by `Evaluator.field_stats` for every field.

- [ ] **Step 4: Run the focused tests and verify failure because `evaluation.audit` and public matching methods do not exist.**

Run: `D:\project\.venv\Scripts\python.exe -m pytest task1_entity_extraction/tests/test_evaluation_audit.py -v`

Expected: import or attribute failures.

- [ ] **Step 5: Expose the shared matching methods in `Evaluator`.** Move existing method bodies to `align_entities` and `field_match`, update `evaluate` to call them, and leave compatibility wrappers that delegate to the public methods.

- [ ] **Step 6: Implement `build_error_rows`.** Reuse `Evaluator.align_entities` and `Evaluator.field_match`; emit paired-field errors first, then unmatched prediction fields as `extra_entity`, then unmatched gold fields as `missing_entity`, preserving deterministic announcement/entity/field order.

- [ ] **Step 7: Run metric and audit tests.**

Run: `D:\project\.venv\Scripts\python.exe -m pytest task1_entity_extraction/tests/test_evaluation_audit.py task1_entity_extraction/test_framework.py -v`

Expected: audit tests pass and existing metric smoke checks remain green.

- [ ] **Step 8: Commit.**

```bash
git add task1_entity_extraction/evaluation/metrics.py task1_entity_extraction/evaluation/audit.py task1_entity_extraction/tests/test_evaluation_audit.py
git commit -m "feat: report field-level extraction errors"
```

## Task 3: CLI Integration and Safe Human-Gold Outputs

**Files:**
- Modify: `task1_entity_extraction/run_evaluation.py`
- Create: `task1_entity_extraction/tests/test_run_evaluation.py`

**Interfaces:**
- Produces: `parse_args(argv: list[str] | None = None) -> argparse.Namespace`.
- Produces: `evaluate_files(ground_truth_path: Path, predictions_path: Path, *, expected_cases: int = 40, require_reviewed: bool = True) -> tuple[EvaluationMetrics, list[dict], int]`, where the integer is gold entity count.
- Produces: `write_report(path: Path, metrics: EvaluationMetrics, *, source_name: str, gold_entity_count: int) -> None`.
- Produces: `write_error_csv(path: Path, rows: list[dict]) -> None` using `utf-8-sig`.
- CLI options: positional `ground_truth`, `--predictions`, `--expected-cases`, `--allow-unreviewed`, `--report-out`, and `--errors-out`.

- [ ] **Step 1: Write failing CLI tests** for suffix-based loading, default human-gold output names, custom output paths, source basename without absolute path, UTF-8 BOM CSV, 40/273 counts, and preservation of `output/evaluation_report.json`.

- [ ] **Step 2: Write a failing atomic-output test.** Seed existing report/error files, make input validation fail, run the orchestration function, and assert both old files are unchanged.

- [ ] **Step 3: Run the focused tests and verify failures identify the old positional parsing and missing writers.**

Run: `D:\project\.venv\Scripts\python.exe -m pytest task1_entity_extraction/tests/test_run_evaluation.py -v`

Expected: CLI/interface assertion failures.

- [ ] **Step 4: Replace manual `sys.argv` parsing with `argparse`.** Default predictions to `output/raw_results.json`; default JSON-ground-truth outputs to `output/evaluation_report_human_gold.json` and `output/evaluation_errors_human_gold.csv`; preserve the existing Excel path when no positional path is supplied.

- [ ] **Step 5: Implement `evaluate_files`.** Fully load and validate both inputs, run `Evaluator.evaluate`, build audit rows, and return in-memory results before opening either output file.

- [ ] **Step 6: Implement safe writers.** Write JSON/CSV to sibling temporary files, flush and close them, then replace final targets only after both serializations succeed; clean temporary files on failure.

- [ ] **Step 7: Add report metadata** containing `source_file` basename, `generated_at` ISO timestamp with timezone, `gold_entity_count`, metric fields, composite score, and per-field stats; exclude reviewer data and absolute paths.

- [ ] **Step 8: Run focused and complete backend tests.**

Run: `D:\project\.venv\Scripts\python.exe -m pytest task1_entity_extraction/tests -v`

Expected: all new tests pass.

Run: `D:\project\.venv\Scripts\python.exe task1_entity_extraction/test_framework.py`

Expected: existing framework checks pass.

- [ ] **Step 9: Commit.**

```bash
git add task1_entity_extraction/run_evaluation.py task1_entity_extraction/tests/test_run_evaluation.py
git commit -m "feat: run reproducible human gold evaluation"
```

## Task 4: Documentation, Data Protection, and Real-Data Verification

**Files:**
- Modify: `.gitignore`
- Modify: `docs/任务一-业务流程说明.md`

**Interfaces:**
- Documents command: `python run_evaluation.py <审核JSON路径>` from `task1_entity_extraction/`.
- Protects: `procurement_initial_review_batch_*.json` and `task1_entity_extraction/private_validation/` from accidental Git inclusion.

- [ ] **Step 1: Add minimal ignore rules** for review-batch JSON exports and a future private-validation directory; retain existing broad ignores for generated output JSON/CSV/XLSX.

- [ ] **Step 2: Document the offline workflow**: use the cleaned arbitration-like JSON, keep blanks empty, run the command, interpret the five metrics, inspect the error CSV, and never commit the input/output data.

- [ ] **Step 3: Install only the test dependency if pytest is missing.**

Run: `D:\project\.venv\Scripts\python.exe -m pip install -r task1_entity_extraction/requirements-dev.txt`

Expected: pytest is available; production requirements remain unchanged.

- [ ] **Step 4: Run the complete automated suite.**

Run: `D:\project\.venv\Scripts\python.exe -m pytest task1_entity_extraction/tests -v`

Expected: all tests pass.

Run: `D:\project\.venv\Scripts\python.exe task1_entity_extraction/test_framework.py`

Expected: framework checks pass.

- [ ] **Step 5: Run the real 40-case evaluation** using the cleaned local JSON without copying it into the repository.

Run from `D:\project\task1_entity_extraction`:

```powershell
..\.venv\Scripts\python.exe run_evaluation.py "<本地审核JSON绝对路径>"
```

Expected: 40 announcements, 273 gold entities, and human-gold report/error files are generated; metrics match the confirmed scoring口径, with the trial baseline near accuracy 67.11%, precision 71.85%, recall 91.05%, F1 80.32%, and composite 75.71%.

- [ ] **Step 6: Inspect the real outputs.** Confirm `source_file` contains only the basename, the CSV opens as Chinese UTF-8, case 9 has supplier-as-brand with undisclosed fields empty, and audit FP/FN contributions reconcile with the report.

- [ ] **Step 7: Verify repository hygiene.**

Run: `git status --short --ignored`

Expected: human JSON and generated reports are ignored or outside the repository; only code, tests, docs, and `.gitignore` are candidates for commit.

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git grep -n -E "sk-[A-Za-z0-9]{16,}|真实审核人姓名|procurement_initial_review_batch_[0-9]+"`

Expected: no API keys, reviewer names, or fixed private input filename in tracked implementation files.

- [ ] **Step 8: Commit documentation and ignore rules.**

```bash
git add .gitignore docs/任务一-业务流程说明.md
git commit -m "docs: document private human gold evaluation"
```

## Final Verification

- [ ] `git log -5 --oneline` shows four focused implementation commits after the design/plan commits.
- [ ] `python -m pytest task1_entity_extraction/tests -v` passes.
- [ ] `python task1_entity_extraction/test_framework.py` passes.
- [ ] Real input loads exactly 40 announcements and 273 gold entities.
- [ ] Official human-gold report and error CSV are generated under `task1_entity_extraction/output/`.
- [ ] Old `evaluation_report.json` remains unchanged.
- [ ] Trial and formal metrics agree under the same input and code revision.
- [ ] No private JSON, generated report, reviewer name, API key, or absolute user path is staged.
- [ ] Review the full branch diff for accidental dataset content and scope creep.
