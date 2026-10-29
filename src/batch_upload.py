"""Per-file outcomes for the legacy multi-file admin upload endpoint."""

import os

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.errors import FileLimitError, RepairRequired, IndexWriteConflict
from src.file_processing import run_file_work


async def upload_batch(files, upload_one):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    uploaded, failed, items = [], [], []
    repair_error = None
    for index, upload in enumerate(files):
        filename = os.path.basename(upload.filename or "")
        item = {"index": index, "filename": filename}
        try:
            await run_file_work(upload_one, filename, upload.file)
            item["status"] = "uploaded"
            uploaded.append({"filename": filename, "status": "ok"})
        except FileLimitError as error:
            item.update(status="oversize", error=str(error))
            failed.append({"filename": filename, "error": str(error)})
        except RepairRequired as error:
            item.update(status="repair_required", error=str(error))
            failed.append({"filename": filename, "error": str(error)})
            repair_error = str(error)
        except IndexWriteConflict:
            raise
        except Exception as error:
            item.update(status="error", error=str(error))
            failed.append({"filename": filename, "error": str(error)})
        items.append(item)
        if repair_error is not None:
            items.extend({"index": remaining, "filename": os.path.basename(files[remaining].filename or ""),
                          "status": "not_processed"} for remaining in range(index + 1, len(files)))
            break
    result = {
        "status": "ok" if uploaded and repair_error is None else "error",
        "uploaded": uploaded, "failed": failed, "items": items,
        "uploaded_count": len(uploaded), "failed_count": len(failed),
        "message": f"Uploaded {len(uploaded)} file(s), {len(failed)} failed",
    }
    headers = {"X-Upload-Result": "partial" if uploaded else "none"}
    if repair_error is not None:
        return JSONResponse(status_code=500, content={"detail": repair_error,
            "repair_required": True, "result": result}, headers=headers)
    if not uploaded:
        code = 413 if all(item["status"] == "oversize" for item in items) else 400
        return JSONResponse(status_code=code, content={"detail": result}, headers=headers)
    return result
