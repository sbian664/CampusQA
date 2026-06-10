<script setup>
import { ref, watch, nextTick } from 'vue'
import ChatBubble from './ChatBubble.vue'

const props = defineProps({
  messages: { type: Array, required: true },
  isLoading: { type: Boolean, default: false },
})

const containerRef = ref(null)

function scrollToBottom() {
  nextTick(() => {
    if (containerRef.value) {
      containerRef.value.scrollTop = containerRef.value.scrollHeight
    }
  })
}

watch(() => props.messages.length, scrollToBottom)
watch(() => props.isLoading, (val) => { if (val) scrollToBottom() })
</script>

<template>
  <div
    ref="containerRef"
    class="flex-1 overflow-y-auto py-4 scroll-smooth"
  >
    <!-- 空状态 -->
    <div
      v-if="messages.length === 0 && !isLoading"
      class="flex flex-col items-center justify-center h-full text-gray-400 dark:text-gray-500"
    >
      <svg class="w-16 h-16 mb-4 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
              d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
      <p class="text-lg font-medium">CampusQA</p>
      <p class="text-sm mt-1">知识库智能问答助手</p>
    </div>

    <!-- 消息列表 -->
    <ChatBubble
      v-for="(msg, idx) in messages"
      :key="idx"
      :role="msg.role"
      :content="msg.content"
    />

    <!-- 加载态气泡 -->
    <ChatBubble
      v-if="isLoading"
      role="assistant"
      :is-loading="true"
    />
  </div>
</template>
