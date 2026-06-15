# Invisible Knowledge Base Batch Upload Design

## Goal

Add a minimal knowledge-base upload flow for CampusQA:

- Users can upload multiple files in one operation.
- Upload is triggered by dragging files into the page.
- The upload target is explicitly the knowledge base, not the chat composer.
- The normal UI remains clean, with no persistent upload button or upload panel.

## Scope

In scope:

- Multi-file upload for supported document formats: `.md`, `.txt`, `.pdf`, `.html`.
- A drag overlay that appears only while files are being dragged over the app.
- Drop handling on the non-composer chat workspace area.
- Backend processing that saves each file into `data/documents` and updates the knowledge base index.
- Per-file success and failure reporting.
- A summary toast after upload completes.

Out of scope:

- Folder drag-and-drop or recursive directory upload.
- Uploading files as current conversation attachments.
- Persistent upload buttons, upload cards, or knowledge-base management pages.
- Progress bars for individual files.

## Product Behavior

The app has two upload meanings:

- Dropping files outside the composer uploads them to the knowledge base.
- The composer remains dedicated to conversation input and does not accept knowledge-base drops.

This preserves a future path where composer-level uploads can mean "attach this file to the current conversation" without conflicting with knowledge-base ingestion.

When a user drags files into the page:

- The app displays a lightweight overlay over the chat workspace, excluding the composer.
- The overlay says the files will be uploaded to the knowledge base.
- If the user drops on the workspace, all dropped files are submitted together.
- If the user drops over the composer or leaves the page, no upload is started.

## Backend Design

Extend `POST /api/upload` to accept multiple files with the `files` form field. To preserve compatibility, the endpoint should also continue accepting the existing single `file` field where practical.

For each uploaded file:

1. Validate the extension against `SUPPORTED_FORMATS`.
2. Save the file into `DOCUMENTS_DIR`.
3. Call the existing knowledge-base update path for that saved file.
4. Record either a success result or a failure result.

The endpoint returns a batch response:

```json
{
  "status": "ok",
  "uploaded": [
    { "filename": "policy.md", "status": "ok" }
  ],
  "failed": [
    { "filename": "image.png", "error": "Unsupported file format: .png" }
  ],
  "uploaded_count": 1,
  "failed_count": 1,
  "message": "Uploaded 1 file, 1 failed"
}
```

If at least one file succeeds, the response should be HTTP 200. If all files fail validation or processing, return an error status with the same failure detail shape.

## Frontend Design

`ChatInput.vue` no longer exposes an upload button, hidden file input, or `upload` event. It remains focused on text input, clear, and send.

`App.vue` owns the knowledge-base drag state:

- Track whether dragged data contains files.
- Show the overlay only while file drag is active.
- Attach drop handling to the chat workspace area above the composer.
- Ignore drops on the composer.
- Convert dropped `DataTransfer.files` into an array and send them in one multipart request.
- Display a success/error toast using the existing toast mechanism.

The upload request sends all files under the `files` field:

```js
const formData = new FormData()
files.forEach((file) => formData.append('files', file))
await fetch('/api/upload', { method: 'POST', body: formData })
```

## Error Handling

Backend errors are per file wherever possible. One bad file should not prevent other valid files from being indexed.

Frontend messages:

- All succeeded: "Uploaded N files to the knowledge base"
- Partial failure: "Uploaded N files, M failed"
- All failed: show an error toast with the first failure reason and count.

Network or server-level failures use the existing error toast path.

## Testing

Backend tests:

- Multiple supported files are accepted and processed independently.
- Unsupported files are reported in `failed`.
- Partial failure returns success counts and failure counts.
- Existing single-file upload behavior remains compatible if supported by the endpoint implementation.

Frontend tests or focused logic tests:

- Batch upload appends every file with the `files` form key.
- Composer upload emit/button behavior is removed.
- Drop handling ignores empty/non-file drags.

Manual verification:

- Drag multiple supported files into the chat workspace and confirm they appear in `data/documents`.
- Ask a question that should retrieve content from an uploaded file.
- Drag over the composer and confirm it does not start a knowledge-base upload.
