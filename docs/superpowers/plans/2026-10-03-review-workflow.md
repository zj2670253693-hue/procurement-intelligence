# Human Review Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a database-backed initial review, second review, and arbitration workflow for the 40-announcement validation set, with a final export accepted by the existing evaluator.

**Architecture:** Preserve `extracted_entities` as immutable source data and snapshot its seven fields into a review batch. A focused DAO owns persistence, a service owns state transitions and dispute detection, FastAPI exposes stage-specific endpoints, and three Vue workbenches share source-viewing and entity-editing components.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic 2, PyMySQL/MySQL 8, openpyxl, Vue 3, Vue Router, Element Plus, Axios, pytest, Vitest, Vue Test Utils.

**Spec:** `docs/superpowers/specs/2026-10-03-review-workflow-design.md`

## Global Constraints

- Keep `extracted_entities` unchanged; human decisions live only in review tables.
- Use the exact case states `pending_initial`, `pending_review`, `pending_arbitration`, and `completed`.
- Store all seven entity fields in immutable model snapshots and stage snapshots.
- Require evidence for modified, deleted, added, or disputed data.
- Use optimistic locking through `review_cases.version`; stale writes return HTTP 409.
- Do not add a full account system; every write records a non-empty reviewer name.
- Export only completed batches and preserve the evaluator's existing Chinese column labels.
- Budget, ceiling price, and inferred discount calculations must not be treated as explicit transaction amounts.

## Review Focus

- A stale browser tab submits an old version: reject with 409 and leave persisted data unchanged (Task 2 and Task 3 tests).
- Source HTML or attachments are missing: return stored `raw_text` and a clear source-unavailable message (Task 3 test).
- A case contains added, deleted, and zero-entity outcomes: preserve the correct final entity set and export shape (Task 2 and Task 4 tests).
- Chinese text, quotes, long specification values, and empty strings pass through JSON snapshots without corruption (Task 1 test).
- A user skips required evidence or submits out of stage: reject the request without advancing status (Task 2 and Task 3 tests).

---

## File Structure

Backend:

- `database/schema.py`: review table DDL.
- `database/review_dao.py`: review batch, case, and item persistence only.
- `database/dao.py`: attach `ReviewDAO` to `DataRepository`.
- `models/review.py`: review constants and typed domain payloads.
- `api/services/review_service.py`: validation, state transitions, snapshots, disputes, and return logic.
- `api/schemas/reviews.py`: HTTP request and response models.
- `api/routers/reviews.py`: `/api/reviews` endpoints.
- `review/exporter.py`: completed-batch Excel and JSON generation.
- `tests/`: focused backend tests and fakes.
- `requirements-dev.txt`: pytest and test-only dependencies.

Frontend:

- `frontend/src/api/reviews.js`: review API client.
- `frontend/src/components/review/ReviewerIdentity.vue`: reviewer-name selection.
- `frontend/src/components/review/ReviewSourcePanel.vue`: HTML and attachment viewer.
- `frontend/src/components/review/ReviewEntityEditor.vue`: seven-field editor for initial review.
- `frontend/src/components/review/ReviewComparison.vue`: model/initial/review comparison.
- `frontend/src/views/ReviewDashboard.vue`: batch progress.
- `frontend/src/views/InitialReviewView.vue`: initial review workbench.
- `frontend/src/views/SecondReviewView.vue`: second review workbench.
- `frontend/src/views/ArbitrationView.vue`: dispute-only arbitration workbench.
- `frontend/src/**/*.spec.js`: Vitest component and API tests.

## Task 1: Persistence Schema and Review DAO

**Files:**
- Modify: `task1_entity_extraction/database/schema.py`
- Create: `task1_entity_extraction/database/review_dao.py`
- Modify: `task1_entity_extraction/database/dao.py`
- Create: `task1_entity_extraction/tests/fakes.py`
- Create: `task1_entity_extraction/tests/test_review_dao.py`
- Create: `task1_entity_extraction/requirements-dev.txt`

**Interfaces:**
- Produces: `ReviewDAO.create_batch(name: str, description: str, created_by: str, announcement_ids: list[str]) -> int`
- Produces: `ReviewDAO.get_batch(batch_id: int) -> dict | None`
- Produces: `ReviewDAO.summary(batch_id: int) -> dict`
- Produces: `ReviewDAO.list_cases(batch_id: int, status: str | None, limit: int, offset: int) -> tuple[int, list[dict]]`
- Produces: `ReviewDAO.get_case(case_id: int) -> dict | None`
- Produces: `ReviewDAO.replace_stage_items(case_id: int, stage: str, items: list[dict]) -> None`
- Produces: `ReviewDAO.update_case_state(case_id: int, expected_version: int, values: dict) -> bool`

- [ ] **Step 1: Add failing DAO tests** for batch snapshot creation, unique batch/announcement membership, ordered item retrieval, UTF-8/long JSON round trips, and conditional version updates.
- [ ] **Step 2: Run `python -m pytest tests/test_review_dao.py -v`** and verify failures identify missing tables and `ReviewDAO`.
- [ ] **Step 3: Add `CREATE_TABLE_REVIEW_BATCHES`, `CREATE_TABLE_REVIEW_CASES`, and `CREATE_TABLE_REVIEW_ITEMS`** with the exact states and fields from the spec, then append them to `ALL_TABLES`.
- [ ] **Step 4: Implement `ReviewDAO`** with parameterized SQL only; snapshot seven model fields at batch creation and never update `model_data` later.
- [ ] **Step 5: Attach `self.reviews = ReviewDAO(db)` to `DataRepository`** without changing existing DAO interfaces.
- [ ] **Step 6: Run `python -m pytest tests/test_review_dao.py -v`** and verify all persistence tests pass.
- [ ] **Step 7: Commit** with `git commit -m "feat: add review workflow persistence"`.

## Task 2: Domain Models and Workflow Service

**Files:**
- Create: `task1_entity_extraction/models/review.py`
- Create: `task1_entity_extraction/api/services/review_service.py`
- Create: `task1_entity_extraction/tests/test_review_service.py`

**Interfaces:**
- Consumes: Task 1 `ReviewDAO` methods.
- Produces: `ReviewService.create_batch(name: str, description: str, created_by: str, announcement_ids: list[str]) -> int`
- Produces: `ReviewService.save_initial(case_id: int, reviewer: str, version: int, items: list[ReviewItemInput], submit: bool) -> dict`
- Produces: `ReviewService.save_second_review(case_id: int, reviewer: str, version: int, items: list[ReviewItemInput], submit: bool) -> dict`
- Produces: `ReviewService.save_arbitration(case_id: int, reviewer: str, version: int, items: list[ReviewItemInput], submit: bool) -> dict`
- Produces: `ReviewService.return_case(case_id: int, reviewer: str, version: int, target_stage: str, reason: str) -> dict`
- Produces: `WorkflowConflict`, `WorkflowValidationError`, and `WorkflowNotFound` exceptions.

- [ ] **Step 1: Write failing service tests** for legal transitions, out-of-stage submission, missing reviewer, missing evidence, stale version, added/deleted items, zero-entity cases, automatic no-dispute completion, dispute routing, and return-to-stage behavior.
- [ ] **Step 2: Run `python -m pytest tests/test_review_service.py -v`** and verify the missing service causes failure.
- [ ] **Step 3: Define review constants and Pydantic domain models** including the seven exact field names and verdict enums.
- [ ] **Step 4: Implement initial-review validation and submission** so draft saves remain in stage and final submission advances to `pending_review`.
- [ ] **Step 5: Implement second-review comparison** using normalized seven-field values plus add/delete verdicts; set `has_dispute` per item and route the case to `completed` or `pending_arbitration`.
- [ ] **Step 6: Implement arbitration and return logic** with final snapshots, required return reason, version checks, and downstream-stage clearing.
- [ ] **Step 7: Run `python -m pytest tests/test_review_service.py -v`** and verify all transition tests pass.
- [ ] **Step 8: Commit** with `git commit -m "feat: implement review workflow state machine"`.

## Task 3: FastAPI Review Endpoints

**Files:**
- Create: `task1_entity_extraction/api/schemas/reviews.py`
- Create: `task1_entity_extraction/api/routers/reviews.py`
- Modify: `task1_entity_extraction/api/main.py`
- Create: `task1_entity_extraction/tests/test_review_api.py`

**Interfaces:**
- Consumes: Task 2 `ReviewService`.
- Produces: `/api/reviews/summary`, `/batches`, `/cases`, case detail, stage save/submit, and return endpoints defined in the spec.

- [ ] **Step 1: Write failing TestClient tests** covering response shapes, 404 mapping, 422 evidence validation, 409 state/version conflicts, pagination, and missing-source fallback.
- [ ] **Step 2: Run `python -m pytest tests/test_review_api.py -v`** and verify routes are absent.
- [ ] **Step 3: Define request/response schemas** with `reviewer`, `version`, typed items, evidence, verdicts, and constrained target stages.
- [ ] **Step 4: Implement `reviews.py` endpoints** and map service exceptions to 404, 409, or 422 without exposing internal errors.
- [ ] **Step 5: Reuse the existing announcement and attachment loaders** for source content; return database `raw_text` with `source_exists=false` when files are missing.
- [ ] **Step 6: Register the router in `api/main.py`** under `/api/reviews`.
- [ ] **Step 7: Run `python -m pytest tests/test_review_api.py -v`** and verify all endpoint tests pass.
- [ ] **Step 8: Commit** with `git commit -m "feat: expose review workflow api"`.

## Task 4: Final Answer Export

**Files:**
- Create: `task1_entity_extraction/review/__init__.py`
- Create: `task1_entity_extraction/review/exporter.py`
- Modify: `task1_entity_extraction/api/routers/reviews.py`
- Create: `task1_entity_extraction/tests/test_review_exporter.py`

**Interfaces:**
- Consumes: completed batch and case/item rows from `ReviewDAO`.
- Produces: `export_review_json(batch_id: int, review_dao: ReviewDAO) -> bytes`
- Produces: `export_review_xlsx(batch_id: int, review_dao: ReviewDAO) -> bytes`

- [ ] **Step 1: Write failing exporter tests** for incomplete-batch rejection, Chinese headers, deletion omission, addition inclusion, empty values, ordering, and successful loading by `run_evaluation.load_ground_truth(..., require_reviewed=True)`.
- [ ] **Step 2: Run `python -m pytest tests/test_review_exporter.py -v`** and verify exporter functions are missing.
- [ ] **Step 3: Implement JSON export** grouped by announcement with the seven exact English field names.
- [ ] **Step 4: Implement in-memory XLSX export** with sheet `标注(待核验)`, current Chinese field labels, non-empty `核验结论`, and no temporary server file.
- [ ] **Step 5: Add `/batches/{batch_id}/export.json` and `/export.xlsx` endpoints** with correct media types and filenames.
- [ ] **Step 6: Run `python -m pytest tests/test_review_exporter.py -v`** and verify evaluator compatibility.
- [ ] **Step 7: Commit** with `git commit -m "feat: export completed review batches"`.

## Task 5: Frontend Review Foundation and Dashboard

**Files:**
- Create: `frontend/src/api/reviews.js`
- Create: `frontend/src/components/review/ReviewerIdentity.vue`
- Create: `frontend/src/views/ReviewDashboard.vue`
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/layouts/MainLayout.vue`
- Modify: `frontend/package.json`
- Create: `frontend/src/api/reviews.spec.js`
- Create: `frontend/src/views/ReviewDashboard.spec.js`
- Create: `frontend/vitest.config.js`

**Interfaces:**
- Consumes: Task 3 summary, batch, and case-list APIs.
- Produces: `getReviewerName() -> string`, `setReviewerName(name: string) -> void`, and typed-by-convention API helpers for later views.

- [ ] **Step 1: Add Vitest, Vue Test Utils, and jsdom dev dependencies** plus `npm run test`.
- [ ] **Step 2: Write failing API and dashboard tests** for endpoint parameters, reviewer-name requirement, four progress counts, and navigation to each stage.
- [ ] **Step 3: Run `npm run test -- --run`** and verify missing modules/components fail.
- [ ] **Step 4: Implement `reviews.js`** for every Task 3 and Task 4 endpoint.
- [ ] **Step 5: Implement reviewer identity selection** stored in localStorage, with no password or authorization claims.
- [ ] **Step 6: Implement dashboard cards and task progress** and add review routes/menu entries.
- [ ] **Step 7: Run `npm run test -- --run` and `npm run build`** and verify both pass.
- [ ] **Step 8: Commit** with `git commit -m "feat: add review dashboard"`.

## Task 6: Initial Review Workbench

**Files:**
- Create: `frontend/src/components/review/ReviewSourcePanel.vue`
- Create: `frontend/src/components/review/ReviewEntityEditor.vue`
- Create: `frontend/src/views/InitialReviewView.vue`
- Create: `frontend/src/views/InitialReviewView.spec.js`
- Modify: `frontend/src/router/index.js`

**Interfaces:**
- Consumes: Task 5 API helpers and reviewer identity.
- Produces: reusable source panel and initial-stage payload matching `InitialSaveRequest`.

- [ ] **Step 1: Write failing component tests** for task selection, HTML/attachment tabs, seven editable fields, correct/modify/delete/add/no-entity verdicts, mandatory evidence, draft save, submit confirmation, and 409 refresh message.
- [ ] **Step 2: Run `npm run test -- --run InitialReviewView`** and verify the view is absent.
- [ ] **Step 3: Implement `ReviewSourcePanel.vue`** with lazy attachment loading and missing-source warning.
- [ ] **Step 4: Implement `ReviewEntityEditor.vue`** with stable local item IDs, seven fields, verdicts, evidence, additions, and soft deletion.
- [ ] **Step 5: Implement `InitialReviewView.vue`** with case list, progress, unsaved-change protection, draft save, and stage submission.
- [ ] **Step 6: Run the focused test and `npm run build`** and verify both pass.
- [ ] **Step 7: Commit** with `git commit -m "feat: add initial review workbench"`.

## Task 7: Second Review and Arbitration Workbenches

**Files:**
- Create: `frontend/src/components/review/ReviewComparison.vue`
- Create: `frontend/src/views/SecondReviewView.vue`
- Create: `frontend/src/views/ArbitrationView.vue`
- Create: `frontend/src/views/SecondReviewView.spec.js`
- Create: `frontend/src/views/ArbitrationView.spec.js`
- Modify: `frontend/src/router/index.js`

**Interfaces:**
- Consumes: Tasks 5-6 API helpers, identity, and source panel.
- Produces: second-review and arbitration payloads matching backend schemas.

- [ ] **Step 1: Write failing tests** for model/initial comparison, changed-field highlighting, agree/disagree choices, required disagreement evidence, dispute-only arbitration, choosing initial/review/custom values, return reasons, and final submission.
- [ ] **Step 2: Run focused Vitest files** and verify components are missing.
- [ ] **Step 3: Implement `ReviewComparison.vue`** with normalized visual equality and explicit blank-value display.
- [ ] **Step 4: Implement `SecondReviewView.vue`** with independent decisions, suggested values, evidence validation, draft save, and submit.
- [ ] **Step 5: Implement `ArbitrationView.vue`** filtered to disputes, per-field final-value choice, return actions, and final lock.
- [ ] **Step 6: Run focused tests and `npm run build`** and verify both pass.
- [ ] **Step 7: Commit** with `git commit -m "feat: add review and arbitration workbenches"`.

## Task 8: Batch Initialization and End-to-End Verification

**Files:**
- Create: `task1_entity_extraction/init_review_batch.py`
- Modify: `task1_entity_extraction/test_framework.py`
- Modify: `docs/任务一-业务流程说明.md`
- Create: `task1_entity_extraction/tests/test_review_e2e.py`

**Interfaces:**
- Consumes: all prior task interfaces.
- Produces: CLI `python init_review_batch.py <ground_truth.xlsx> --name <name> --created-by <reviewer>`.

- [ ] **Step 1: Write a failing end-to-end test** that initializes one sample case, performs a modification, deletion, addition, second-review disagreement, arbitration, export, and evaluator load while asserting `extracted_entities` is unchanged.
- [ ] **Step 2: Run `python -m pytest tests/test_review_e2e.py -v`** and verify the initializer is missing.
- [ ] **Step 3: Implement the initializer CLI** to read unique announcement IDs from the existing validation workbook and create a model snapshot batch; reject missing or duplicate-only input.
- [ ] **Step 4: Extend `test_framework.py`** with a lightweight review route/import smoke test that does not require live LLM calls.
- [ ] **Step 5: Document reviewer workflow, initializer command, state meanings, export command, and recovery from 409 conflicts.**
- [ ] **Step 6: Run backend suite** with `python -m pytest tests -v` and `python test_framework.py`.
- [ ] **Step 7: Run frontend suite** with `npm run test -- --run` and `npm run build`.
- [ ] **Step 8: Run `git diff --check`** and verify no whitespace errors.
- [ ] **Step 9: Commit** with `git commit -m "test: verify review workflow end to end"`.

## Final Verification

- [ ] Initialize the real 40-announcement batch from `output/validation_set/ground_truth.xlsx` in a disposable/test database.
- [ ] Walk one real announcement through all three stages in the UI.
- [ ] Verify model snapshots and `extracted_entities` remain unchanged.
- [ ] Export Excel and run `python run_evaluation.py <exported-file>` successfully.
- [ ] Confirm the dashboard counts equal the sum of all four states.
- [ ] Confirm backend tests, frontend tests, frontend production build, and `git diff --check` pass.
- [ ] Review the final branch diff for secrets, generated files, accidental dataset changes, and scope creep.
