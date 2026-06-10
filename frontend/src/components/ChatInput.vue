<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'clear'])

const inputText = ref('')
const textareaRef = ref(null)

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
  inputText.value = ''
  // 重置高度
  nextTick(() => {
    if (textareaRef.value) {
      textareaRef.value.style.height = 'auto'
    }
  })
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

import { nextTick } from 'vue'

watch(inputText, autoResize)
</script>

<template>
  <div class="border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-[#40414f] px-4 py-3">
    <div class="max-w-3xl mx-auto flex items-end gap-2">
      <!-- 清空按钮 -->
      <button
        @click="$emit('clear')"
        class="shrink-0 p-2 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100
               dark:hover:text-gray-300 dark:hover:bg-gray-700 transition-colors"
        title="清空对话"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>

      <!-- 输入框 -->
      <textarea
        ref="textareaRef"
        v-model="inputText"
        :disabled="disabled"
        @keydown="handleKeydown"
        rows="1"
        placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
        class="flex-1 resize-none rounded-xl border border-gray-300 dark:border-gray-600
               bg-white dark:bg-[#40414f] text-gray-800 dark:text-gray-100
               px-4 py-2.5 text-[15px] leading-relaxed
               placeholder-gray-400 dark:placeholder-gray-500
               focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
               disabled:opacity-50 disabled:cursor-not-allowed
               max-h-40 overflow-y-auto"
      ></textarea>

      <!-- 发送按钮 -->
      <button
        @click="handleSend"
        :disabled="disabled || !inputText.trim()"
        class="shrink-0 p-2.5 rounded-xl text-white transition-all
               bg-blue-500 hover:bg-blue-600
               disabled:bg-gray-300 dark:disabled:bg-gray-600
               disabled:cursor-not-allowed"
        title="发送"
      >
        <svg v-if="!disabled" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
        </svg>
        <!-- 加载中旋转图标 -->
        <svg v-else class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
        </svg>
      </button>
    </div>
  </div>
</template>
