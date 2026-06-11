<script setup>
import { ref, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({
  role: { type: String, required: true },
  content: { type: String, default: '' },
  isLoading: { type: Boolean, default: false },
  index: { type: Number, default: -1 },
  isLastAi: { type: Boolean, default: false },
})

const emit = defineEmits(['copy', 'delete', 'edit', 'reroll'])

const isEditing = ref(false)
const editText = ref('')
const editInputRef = ref(null)

function startEdit() {
  editText.value = props.content
  isEditing.value = true
  nextTick(() => editInputRef.value?.focus())
}

function confirmEdit() {
  const text = editText.value.trim()
  if (!text || text === props.content) { isEditing.value = false; return }
  emit('edit', { index: props.index, newText: text })
  isEditing.value = false
}

function cancelEdit() { isEditing.value = false }

function onEditKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); confirmEdit() }
  if (e.key === 'Escape') cancelEdit()
}

function copyText() {
  navigator.clipboard.writeText(props.content).then(() => emit('copy', props.content))
}

function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text))
}
</script>

<template>
  <!-- 用户消息 -->
  <div v-if="role === 'user'" class="group flex justify-end mb-2 px-4">
    <div class="relative max-w-[75%]">
      <div v-if="isEditing" class="flex flex-col gap-2">
        <textarea ref="editInputRef" v-model="editText" @keydown="onEditKeydown" rows="3"
          class="w-full resize-none rounded-xl border border-blue-300 dark:border-blue-600
                 bg-white dark:bg-[#40414f] text-gray-800 dark:text-gray-100
                 px-3 py-2 text-[15px] leading-relaxed
                 focus:outline-none focus:ring-2 focus:ring-blue-500"></textarea>
        <div class="flex justify-end gap-1.5">
          <button @click="cancelEdit"
            class="px-2.5 py-1 text-xs rounded-lg bg-gray-100 dark:bg-gray-700
                   text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button @click="confirmEdit"
            class="px-2.5 py-1 text-xs rounded-lg bg-blue-500 text-white hover:bg-blue-600">保存并重新生成</button>
        </div>
      </div>
      <div v-else class="rounded-2xl rounded-br-md px-4 py-3 bg-blue-500 text-white shadow-sm">
        <p class="whitespace-pre-wrap break-words text-[15px] leading-relaxed">{{ content }}</p>
      </div>
      <div class="absolute -bottom-1 right-0 translate-y-full flex gap-0.5
                  opacity-0 group-hover:opacity-100 transition-opacity z-20">
        <button @click="copyText" class="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700" title="复制">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
        </button>
        <button @click="startEdit" class="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700" title="编辑">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
        </button>
        <button @click="$emit('delete', index)" class="p-1 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-900/20" title="删除">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
        </button>
      </div>
    </div>
  </div>

  <!-- AI 消息 -->
  <div v-else class="group flex justify-start mb-2 px-4">
    <div class="relative max-w-[85%]">
      <div class="rounded-2xl rounded-bl-md px-4 py-3 shadow-sm bg-white text-gray-800 dark:bg-[#444654] dark:text-gray-100">
        <div v-if="isLoading" class="flex items-center gap-2 py-1">
          <span class="text-sm text-gray-400 dark:text-gray-500">思考中</span>
          <span class="flex gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style="animation-delay:0ms"></span>
            <span class="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style="animation-delay:150ms"></span>
            <span class="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style="animation-delay:300ms"></span>
          </span>
        </div>
        <div v-else class="markdown-body text-[15px] leading-relaxed" v-html="renderMarkdown(content)"></div>
      </div>
      <div v-if="!isLoading" class="absolute -bottom-1 left-0 translate-y-full flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity z-20">
        <button @click="copyText" class="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700" title="复制">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
        </button>
        <button @click="$emit('delete', index)" class="p-1 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-900/20" title="删除">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
        </button>
        <button v-if="isLastAi" @click="$emit('reroll')" class="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700" title="重新生成">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        </button>
      </div>
    </div>
  </div>
</template>
