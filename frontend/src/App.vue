<script setup>
import { ref, onMounted } from 'vue'
import ChatMessages from './components/ChatMessages.vue'
import ChatInput from './components/ChatInput.vue'
import ErrorToast from './components/ErrorToast.vue'

// ── 状态 ──
const messages = ref([])
const isLoading = ref(false)
const sessionId = ref(null)
const error = ref({ visible: false, message: '' })

// ── 从 localStorage 恢复 sessionId ──
onMounted(() => {
  const saved = localStorage.getItem('campusqa_session_id')
  if (saved) {
    sessionId.value = saved
    restoreSession()
  }
})

async function restoreSession() {
  try {
    const res = await fetch(`/api/session/${sessionId.value}`)
    if (res.ok) {
      const data = await res.json()
      messages.value = data.history || []
    }
  } catch {
    // 静默失败
  }
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
</script>

<template>
  <div class="h-full flex flex-col max-w-3xl mx-auto">
    <!-- 标题栏 -->
    <header
      class="shrink-0 px-4 py-3 border-b border-gray-200 dark:border-gray-700
             bg-white/80 dark:bg-[#40414f]/80 backdrop-blur-sm"
    >
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 rounded-lg bg-blue-500 flex items-center justify-center">
            <span class="text-white font-bold text-sm">C</span>
          </div>
          <div>
            <h1 class="font-semibold text-gray-800 dark:text-gray-100">CampusQA</h1>
            <p class="text-xs text-gray-400 dark:text-gray-500">知识库智能问答助手</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span
            v-if="sessionId"
            class="text-xs text-gray-400 dark:text-gray-500 px-2 py-1 rounded bg-gray-100 dark:bg-gray-700"
          >
            {{ sessionId }}
          </span>
        </div>
      </div>
    </header>

    <!-- 消息列表 -->
    <ChatMessages :messages="messages" :is-loading="isLoading" />

    <!-- 输入区 -->
    <ChatInput :disabled="isLoading" @send="sendMessage" @clear="clearChat" />

    <!-- 错误提示 -->
    <ErrorToast
      :visible="error.visible"
      :message="error.message"
      @dismiss="error.visible = false"
    />
  </div>
</template>

