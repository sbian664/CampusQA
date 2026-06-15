<script setup>
import { computed, ref, onMounted } from 'vue'
import ChatMessages from './components/ChatMessages.vue'
import ChatInput from './components/ChatInput.vue'
import ChatSidebar from './components/ChatSidebar.vue'
import ErrorToast from './components/ErrorToast.vue'
import { upsertSessionFromChatResponse } from './sessionList.js'
import { retryUntilResolved } from './retry.js'
import {
  buildKnowledgeUploadFormData,
  getKnowledgeUploadMessage,
  hasDraggedFiles,
  normalizeFileList,
} from './uploadBatch.js'

// ── 状态 ──
const messages = ref([])
const isLoading = ref(false)
const sessionId = ref(null)
const error = ref({ visible: false, message: '' })
const successMsg = ref({ visible: false, message: '' })
const showSidebar = ref(false)
const showSettings = ref(false)
const sessions = ref([])
const sessionTitle = ref('')
const agentMode = ref(true)
const cost = ref(null)
const isDraggingFiles = ref(false)
const isUploadingFiles = ref(false)
const BACKEND_RECOVERY_ATTEMPTS = 15
const BACKEND_RECOVERY_DELAY_MS = 2000

const currentSessionLabel = computed(() => {
  if (sessionTitle.value) return sessionTitle.value
  if (!sessionId.value) return '未保存的新会话'
  return `会话 ${sessionId.value.slice(0, 8)}`
})

const totalMessages = computed(() => messages.value.length)

// ── 从 localStorage 恢复 sessionId ──
onMounted(() => {
  const saved = localStorage.getItem('campusqa_session_id')
  if (saved) {
    sessionId.value = saved
  }
  recoverBackendState()
})

async function recoverBackendState() {
  try {
    await retryUntilResolved(
      async () => {
        await Promise.all([
          fetchMode({ throwOnFailure: true }),
          fetchSessions({ throwOnFailure: true }),
        ])
        if (sessionId.value) {
          await restoreSession({ throwOnFailure: true })
        }
      },
      {
        attempts: BACKEND_RECOVERY_ATTEMPTS,
        delayMs: BACKEND_RECOVERY_DELAY_MS,
      },
    )
  } catch {
    // 保持页面可用，用户发送消息或打开历史时仍会再次请求后端。
  }
}

async function restoreSession(options = {}) {
  const { throwOnFailure = false } = options
  try {
    const res = await fetch(`/api/session/${sessionId.value}`)
    if (res.ok) {
      const data = await res.json()
      messages.value = data.history || []
      sessionTitle.value = data.title || ''
      return true
    }
    throw new Error(`Failed to restore session (${res.status})`)
  } catch (err) {
    if (throwOnFailure) throw err
  }
  return false
}

// ── 发送消息 ──
async function sendMessage(text) {
  if (isLoading.value || !text.trim()) return

  messages.value.push({ role: 'user', content: text })
  isLoading.value = true
  error.value.visible = false

  try {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 60000)

    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        session_id: sessionId.value,
      }),
      signal: controller.signal,
    })

    clearTimeout(timeoutId)

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}))
      throw new Error(errData.detail || `请求失败 (${res.status})`)
    }

    const data = await res.json()

    if (data.session_id) {
      sessionId.value = data.session_id
      localStorage.setItem('campusqa_session_id', data.session_id)
      sessionTitle.value = data.session_title || sessionTitle.value
      sessions.value = upsertSessionFromChatResponse(sessions.value, data)
    }

    messages.value.push({ role: 'assistant', content: data.response })

  } catch (err) {
    if (err.name === 'AbortError') {
      showError('请求超时，请稍后重试')
    } else if (err.message.includes('Failed to fetch') || err.message.includes('NetworkError')) {
      showError('网络连接失败，请检查后端服务是否启动')
    } else {
      showError(err.message || '发生未知错误')
    }
  } finally {
    isLoading.value = false
  }
}

// ── 清空对话 ──
async function clearChat() {
  messages.value = []
  if (sessionId.value) {
    try {
      await fetch(`/api/session/${sessionId.value}`, { method: 'DELETE' })
    } catch { /* 静默 */ }
    sessionId.value = null
    localStorage.removeItem('campusqa_session_id')
  }
}

function showError(msg) {
  error.value = { visible: true, message: msg }
}

// ── 上传文件 ──
function showSuccess(msg, timeout = 4000) {
  successMsg.value = { visible: true, message: msg }
  setTimeout(() => { successMsg.value.visible = false }, timeout)
}

function onKnowledgeDragEnter(event) {
  if (!hasDraggedFiles(event)) return
  event.preventDefault()
  isDraggingFiles.value = true
}

function onKnowledgeDragOver(event) {
  if (!hasDraggedFiles(event)) return
  event.preventDefault()
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'copy'
  }
  isDraggingFiles.value = true
}

function onKnowledgeDragLeave(event) {
  if (event.currentTarget?.contains(event.relatedTarget)) return
  isDraggingFiles.value = false
}

async function onKnowledgeDrop(event) {
  if (!hasDraggedFiles(event)) return
  event.preventDefault()
  isDraggingFiles.value = false

  const files = normalizeFileList(event.dataTransfer?.files)
  if (!files.length || isUploadingFiles.value) return

  await uploadKnowledgeFiles(files)
}

async function uploadKnowledgeFiles(files) {
  isUploadingFiles.value = true
  error.value.visible = false

  try {
    const res = await fetch('/api/upload', {
      method: 'POST',
      body: buildKnowledgeUploadFormData(files),
    })

    const data = await res.json().catch(() => ({}))
    const payload = res.ok ? data : data.detail || data
    const result = getKnowledgeUploadMessage(payload)

    if (!res.ok || result.type === 'error') {
      showError(result.message)
      return
    }

    showSuccess(result.message)
  } catch (err) {
    showError(err.message || '上传失败')
  } finally {
    isUploadingFiles.value = false
  }
}

async function uploadFile(file) {
  const formData = new FormData()
  formData.append('files', file)

  try {
    const res = await fetch('/api/upload', {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}))
      throw new Error(errData.detail || `上传失败 (${res.status})`)
    }
    const data = await res.json()
    successMsg.value = { visible: true, message: data.message || `${file.name} 上传成功` }
    setTimeout(() => { successMsg.value.visible = false }, 4000)
  } catch (err) {
    showError(err.message || '上传失败')
  }
}

// ── 会话管理 ──
async function fetchSessions(options = {}) {
  const { throwOnFailure = false } = options
  try {
    const res = await fetch('/api/sessions')
    if (res.ok) {
      const data = await res.json()
      sessions.value = data.sessions || []
      return true
    }
    throw new Error(`Failed to fetch sessions (${res.status})`)
  } catch (err) {
    if (throwOnFailure) throw err
  }
  return false
}

async function loadSession(id) {
  try {
    const res = await fetch('/api/session/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id }),
    })
    if (res.ok) {
      const data = await res.json()
      sessionId.value = data.session_id
      messages.value = data.history || []
      sessionTitle.value = data.title || ''
      localStorage.setItem('campusqa_session_id', data.session_id)
    }
  } catch (err) {
    showError('加载会话失败')
  }
}

function newSession() {
  messages.value = []
  sessionId.value = null
  sessionTitle.value = ''
  cost.value = null
  localStorage.removeItem('campusqa_session_id')
}

function openSidebar() {
  showSidebar.value = true
  fetchSessions()
}

// ── 模式切换 ──
async function fetchMode(options = {}) {
  const { throwOnFailure = false } = options
  try {
    const res = await fetch('/api/mode')
    if (res.ok) {
      const data = await res.json()
      agentMode.value = data.agent_mode
      return true
    }
    throw new Error(`Failed to fetch mode (${res.status})`)
  } catch (err) {
    if (throwOnFailure) throw err
  }
  return false
}

async function toggleMode() {
  try {
    const res = await fetch('/api/mode/toggle', { method: 'POST' })
    if (res.ok) {
      const data = await res.json()
      agentMode.value = data.agent_mode
    }
  } catch (err) {
    showError('切换模式失败')
  }
}

// ── 设置操作 ──
async function fetchCost() {
  if (!sessionId.value) return
  try {
    const res = await fetch(`/api/cost/${sessionId.value}`)
    if (res.ok) {
      cost.value = await res.json()
    }
  } catch { /* 静默 */ }
  showSettings.value = true
}

async function kbScan() {
  try {
    const res = await fetch('/api/kb/scan', { method: 'POST' })
    if (res.ok) {
      const data = await res.json()
      successMsg.value = { visible: true, message: `扫描完成，更新 ${data.updated_count} 个文档` }
      setTimeout(() => { successMsg.value.visible = false }, 4000)
    }
  } catch (err) {
    showError('扫描失败')
  }
  showSettings.value = false
}

async function kbRebuild() {
  try {
    const res = await fetch('/api/kb/rebuild', { method: 'POST' })
    if (res.ok) {
      successMsg.value = { visible: true, message: '索引重建完成' }
      setTimeout(() => { successMsg.value.visible = false }, 4000)
    }
  } catch (err) {
    showError('重建索引失败')
  }
  showSettings.value = false
}

// ── 重新生成 (reroll) ──
async function rerollLast() {
  if (messages.value.length < 2) return
  // 找到最后一个 user 消息
  let lastUserIdx = -1
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') { lastUserIdx = i; break }
  }
  if (lastUserIdx < 0) return
  const lastUserMsg = messages.value[lastUserIdx].content

  // 本地移除末条 assistant
  messages.value.pop()
  isLoading.value = true

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: lastUserMsg,
        session_id: sessionId.value,
        reroll: true,
      }),
    })
    if (!res.ok) throw new Error('请求失败')
    const data = await res.json()
    messages.value.push({ role: 'assistant', content: data.response })
    if (data.session_id) {
      sessionId.value = data.session_id
      sessionTitle.value = data.session_title || sessionTitle.value
      localStorage.setItem('campusqa_session_id', data.session_id)
      sessions.value = upsertSessionFromChatResponse(sessions.value, data)
    }
  } catch (err) {
    showError('重新生成失败')
  } finally {
    isLoading.value = false
  }
}

// ── 删除单条消息 ──
async function deleteMessage(index) {
  if (!sessionId.value) {
    messages.value.splice(index, 1)
    return
  }
  try {
    await fetch(`/api/session/${sessionId.value}/message/${index}`, { method: 'DELETE' })
  } catch { /* 静默 */ }
  messages.value.splice(index, 1)
}

// ── 编辑用户消息（分支）──
async function editMessage({ index, newText }) {
  if (isLoading.value) return
  // 本地截断到编辑位置
  messages.value = messages.value.slice(0, index)
  messages.value.push({ role: 'user', content: newText })
  isLoading.value = true

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: newText,
        session_id: sessionId.value,
        edit_index: index,
      }),
    })
    if (!res.ok) throw new Error('请求失败')
    const data = await res.json()
    messages.value.push({ role: 'assistant', content: data.response })
    if (data.session_id) {
      sessionId.value = data.session_id
      sessionTitle.value = data.session_title || sessionTitle.value
      localStorage.setItem('campusqa_session_id', data.session_id)
      sessions.value = upsertSessionFromChatResponse(sessions.value, data)
    }
  } catch (err) {
    showError('编辑失败')
  } finally {
    isLoading.value = false
  }
}

// ── 删除会话 ──
async function deleteSession(id) {
  if (!confirm(`确定要删除会话 ${id} 吗？此操作不可撤销。`)) return
  try {
    await fetch(`/api/sessions/${id}`, { method: 'DELETE' })
  } catch { /* 静默 */ }
  if (sessionId.value === id) newSession()
  fetchSessions()
}

// ── 复制通知 ──
function copyNotified() {
  successMsg.value = { visible: true, message: '已复制到剪贴板' }
  setTimeout(() => { successMsg.value.visible = false }, 2000)
}
</script>

<template>
  <div class="app-shell">
    <ChatSidebar
      :visible="showSidebar"
      :sessions="sessions"
      :current-session-id="sessionId"
      @close="showSidebar = false"
      @select="loadSession"
      @new="newSession"
      @delete="deleteSession"
    />

    <main class="min-w-0 h-full min-h-0 flex flex-col overflow-hidden px-3 py-3 md:px-5 md:py-5">
      <section class="surface-panel min-h-0 flex flex-1 flex-col overflow-hidden rounded-[22px]">
        <header class="relative z-20 shrink-0 border-b px-4 py-3 md:px-5" style="border-color: var(--border); background: color-mix(in oklch, var(--surface-raised), transparent 4%);">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex min-w-0 items-center gap-3">
              <button
                @click="openSidebar"
                class="icon-button md:hidden"
                title="历史会话"
                aria-label="打开历史会话"
              >
                <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7h16M4 12h16M4 17h16" />
                </svg>
              </button>

              <div class="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-sm font-black text-white" style="background: linear-gradient(135deg, var(--brand), var(--accent));">
                C
              </div>
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <h1 class="truncate text-base font-[750] leading-tight">CampusQA</h1>
                  <span class="status-dot shrink-0" title="前端已连接"></span>
                </div>
                <p class="truncate text-xs" style="color: var(--ink-soft);">{{ currentSessionLabel }} · {{ totalMessages }} 条消息</p>
              </div>
            </div>

            <div class="flex items-center gap-2">
              <button
                @click="toggleMode"
                class="inline-flex h-10 items-center gap-2 rounded-xl border px-3 text-sm font-semibold transition"
                style="border-color: var(--border); background: var(--surface-muted); color: var(--ink);"
                :title="agentMode ? 'Agent 自主检索，点击切换到一步式 RAG' : '一步式 RAG，点击切换到 Agent 自主检索'"
              >
                <span class="h-2 w-2 rounded-full" :style="{ background: agentMode ? 'var(--brand)' : 'var(--accent)' }"></span>
                <span>{{ agentMode ? 'Agent 检索' : '一步式 RAG' }}</span>
              </button>

              <div class="relative">
                <button
                  @click="showSettings = !showSettings; if (showSettings) fetchCost()"
                  class="icon-button border"
                  style="border-color: var(--border); background: var(--surface-raised);"
                  title="知识库与设置"
                  aria-label="打开知识库与设置"
                >
                  <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7h16M6 12h12M8 17h8" />
                  </svg>
                </button>

                <div
                  v-if="showSettings"
                  class="absolute right-0 top-full mt-2 w-[19rem] overflow-hidden rounded-2xl border py-2"
                  style="z-index: 40; border-color: var(--border); background: var(--surface-raised); box-shadow: var(--shadow-menu);"
                >
                  <div class="px-4 pb-3 pt-2">
                    <p class="text-sm font-bold">运行状态</p>
                    <p class="mt-1 text-xs leading-5" style="color: var(--ink-muted);">
                      {{ agentMode ? 'Agent 会自主选择检索步骤并调用工具。' : 'RAG 模式会执行单轮知识库召回。' }}
                    </p>
                  </div>

                  <div class="mx-2 rounded-xl px-3 py-2" style="background: var(--surface-muted);">
                    <p class="text-xs font-semibold" style="color: var(--ink-soft);">Token 消耗</p>
                    <p v-if="cost" class="mt-1 text-sm font-semibold">
                      {{ cost.total_tokens?.toLocaleString() || 0 }} tokens · {{ cost.tool_calls || 0 }} 次工具调用
                    </p>
                    <p v-else-if="sessionId" class="mt-1 text-sm" style="color: var(--ink-muted);">正在读取当前会话...</p>
                    <p v-else class="mt-1 text-sm" style="color: var(--ink-muted);">新会话暂无统计</p>
                  </div>

                  <div class="mt-2 border-t pt-2" style="border-color: var(--border);">
                    <button
                      @click="toggleMode(); showSettings = false"
                      class="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-semibold transition hover:bg-[var(--surface-muted)]"
                    >
                      <span>{{ agentMode ? '切换到一步式 RAG' : '切换到 Agent 自主检索' }}</span>
                      <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                    <button
                      @click="kbScan()"
                      class="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-semibold transition hover:bg-[var(--surface-muted)]"
                    >
                      <span>扫描目录加载文档</span>
                      <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v16h16M8 12h8M8 8h8M8 16h5" />
                      </svg>
                    </button>
                    <button
                      @click="kbRebuild()"
                      class="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-semibold transition hover:bg-[var(--surface-muted)]"
                    >
                      <span>重建知识库索引</span>
                      <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v6h6M20 20v-6h-6M20 9A8 8 0 006.7 5M4 15a8 8 0 0013.3 4" />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </header>

        <div class="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div
            class="relative min-h-0 flex flex-1 flex-col overflow-hidden"
            @dragenter="onKnowledgeDragEnter"
            @dragover="onKnowledgeDragOver"
            @dragleave="onKnowledgeDragLeave"
            @drop="onKnowledgeDrop"
          >
            <ChatMessages :messages="messages" :is-loading="isLoading"
              @copy="copyNotified" @delete="deleteMessage" @edit="editMessage" @reroll="rerollLast" />

            <div
              v-if="isDraggingFiles"
              class="absolute inset-3 z-30 grid place-items-center rounded-2xl border-2 border-dashed px-6 text-center"
              style="border-color: var(--brand); background: color-mix(in oklch, var(--surface-raised), transparent 8%); box-shadow: var(--shadow-menu);"
            >
              <div>
                <p class="text-base font-bold" style="color: var(--ink);">松开后上传到知识库</p>
                <p class="mt-1 text-sm" style="color: var(--ink-muted);">支持一次拖入多个 md / txt / pdf / html 文件</p>
              </div>
            </div>
          </div>

          <ChatInput :disabled="isLoading || isUploadingFiles" @send="sendMessage" @clear="clearChat" />
        </div>
      </section>
    </main>

    <ErrorToast
      v-if="successMsg.visible"
      :visible="successMsg.visible"
      :message="successMsg.message"
      type="success"
      @dismiss="successMsg.visible = false"
    />
    <ErrorToast
      :visible="error.visible"
      :message="error.message"
      type="error"
      @dismiss="error.visible = false"
    />
  </div>
</template>
