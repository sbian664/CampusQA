# Invisible Knowledge Base Batch Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal drag-only multi-file upload flow that indexes dropped files into the CampusQA knowledge base.

**Architecture:** Extend FastAPI upload handling to normalize one or many uploaded files into a batch processor with per-file results. Move frontend upload ownership from `ChatInput.vue` to `App.vue`, where the non-composer chat workspace owns drag state, drop handling, and batch upload messaging.

**Tech Stack:** FastAPI, pytest, Vue 3, Vite, Node `node:test`.

---

### Task 1: Backend Batch Upload API

**Files:**
- Create: `test_upload_batch.py`
- Modify: `server.py`

- [ ] **Step 1: Write failing tests**

Create `test_upload_batch.py` with tests that monkeypatch `server.get_kb`, `server.DOCUMENTS_DIR`, and `server.SUPPORTED_FORMATS`. Use `fastapi.testclient.TestClient` to post two files under `files`, then assert that both are reported in `uploaded`. Add a second test with one supported and one unsupported file, asserting HTTP 200, one success, and one failed item.

- [ ] **Step 2: Verify red**

Run: `python -m pytest test_upload_batch.py -q`

Expected: tests fail because `/api/upload` only accepts the single `file` form field.

- [ ] **Step 3: Implement batch upload**

In `server.py`, change `/api/upload` to accept optional `files: List[UploadFile] = File(None)` and optional `file: UploadFile = File(None)`. Normalize inputs into a list, reject requests with no files, process each file independently, save successful files to `DOCUMENTS_DIR`, update the knowledge base, and return `uploaded`, `failed`, `uploaded_count`, and `failed_count`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest test_upload_batch.py -q`

Expected: both tests pass.

### Task 2: Frontend Upload Request Logic

**Files:**
- Create: `frontend/src/uploadBatch.js`
- Create: `frontend/src/uploadBatch.test.js`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Write failing tests**

Create `uploadBatch.js` as the small testable boundary and `uploadBatch.test.js` with `node:test`. Assert that `buildKnowledgeUploadFormData([fileA, fileB])` appends both files under the `files` key and `getKnowledgeUploadMessage()` returns correct summaries for full success, partial failure, and full failure.

- [ ] **Step 2: Verify red**

Run: `node frontend/src/uploadBatch.test.js`

Expected: fail because `frontend/src/uploadBatch.js` does not exist.

- [ ] **Step 3: Implement helper**

Implement `buildKnowledgeUploadFormData(files)`, `normalizeFileList(value)`, `hasDraggedFiles(event)`, and `getKnowledgeUploadMessage(data)`.

- [ ] **Step 4: Verify green**

Run: `node frontend/src/uploadBatch.test.js`

Expected: tests pass.

### Task 3: Frontend Drag-Only UI

**Files:**
- Modify: `frontend/src/components/ChatInput.vue`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Remove composer upload affordance**

Delete the upload button, hidden file input, upload emit, and upload copy from `ChatInput.vue`. Keep send and clear controls stable.

- [ ] **Step 2: Add workspace drop zone**

In `App.vue`, add drag state and handlers around the `ChatMessages` region. Show an absolute overlay only when dragged data contains files. Do not attach drop handling to `ChatInput`.

- [ ] **Step 3: Wire batch upload**

Use `buildKnowledgeUploadFormData()` to post dropped files to `/api/upload`. Use `getKnowledgeUploadMessage()` for success and partial-failure toasts. Use existing `showError()` for all-failed and network failures.

- [ ] **Step 4: Verify build**

Run: `npm --prefix frontend run build`

Expected: Vite build completes.

### Task 4: Final Verification

**Files:**
- Verify changed files only.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest test_upload_batch.py -q
node frontend/src/uploadBatch.test.js
npm --prefix frontend run build
```

- [ ] **Step 2: Inspect diff**

Run: `git diff -- server.py frontend/src/App.vue frontend/src/components/ChatInput.vue frontend/src/uploadBatch.js frontend/src/uploadBatch.test.js test_upload_batch.py`

Confirm the diff matches the design: no persistent upload button, no folder upload, no conversation attachment behavior.
