export function normalizeFileList(value) {
  if (!value || typeof value.length !== 'number' || value.length === 0) {
    return []
  }
  return Array.from(value).filter(Boolean)
}

export function buildKnowledgeUploadFormData(files) {
  const formData = new FormData()
  normalizeFileList(files).forEach((file) => {
    formData.append('files', file)
  })
  return formData
}

export function hasDraggedFiles(event) {
  const dataTransfer = event?.dataTransfer
  if (!dataTransfer) return false

  if (dataTransfer.items && Array.from(dataTransfer.items).some((item) => item.kind === 'file')) {
    return true
  }

  return Array.from(dataTransfer.types || []).includes('Files')
}

export function getKnowledgeUploadMessage(data) {
  const uploadedCount = Number(data?.uploaded_count || 0)
  const failedCount = Number(data?.failed_count || 0)

  if (uploadedCount > 0 && failedCount === 0) {
    return {
      type: 'success',
      message: `已上传 ${uploadedCount} 个文件到知识库`,
    }
  }

  if (uploadedCount > 0 && failedCount > 0) {
    return {
      type: 'success',
      message: `已上传 ${uploadedCount} 个文件，${failedCount} 个失败`,
    }
  }

  const firstFailure = data?.failed?.[0]
  const failureDetail = firstFailure
    ? `${firstFailure.filename}: ${firstFailure.error}`
    : '没有文件被上传'

  return {
    type: 'error',
    message: `上传失败：${failureDetail}`,
  }
}
