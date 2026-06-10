<script setup>
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({
  role: { type: String, required: true },       // 'user' | 'assistant'
  content: { type: String, default: '' },
  isLoading: { type: Boolean, default: false },
})

function renderMarkdown(text) {
  if (!text) return ''
  const raw = marked.parse(text)
  return DOMPurify.sanitize(raw)
}
</script>

<template>
  <!-- 用户消息：右对齐 -->
  <div v-if="role === 'user'" class="flex justify-end mb-4 px-4">
    <div class="max-w-[75%] rounded-2xl rounded-br-md px-4 py-3 bg-blue-500 text-white shadow-sm">
      <p class="whitespace-pre-wrap break-words text-[15px] leading-relaxed">{{ content }}</p>
    </div>
  </div>

  <!-- AI 消息：左对齐 -->
  <div v-else class="flex justify-start mb-4 px-4">
    <div
      class="max-w-[85%] rounded-2xl rounded-bl-md px-4 py-3 shadow-sm
             bg-white text-gray-800
             dark:bg-[#444654] dark:text-gray-100"
    >
      <!-- 加载态 -->
      <div v-if="isLoading" class="flex items-center gap-2 py-1">
        <span class="text-sm text-gray-400 dark:text-gray-500">思考中</span>
        <span class="flex gap-1">
          <span class="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style="animation-delay: 0ms"></span>
          <span class="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style="animation-delay: 150ms"></span>
          <span class="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style="animation-delay: 300ms"></span>
        </span>
      </div>

      <!-- Markdown 渲染内容 -->
      <div
        v-else
        class="markdown-body text-[15px] leading-relaxed"
        v-html="renderMarkdown(content)"
      ></div>
    </div>
  </div>
</template>
