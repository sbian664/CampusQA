<script setup>
import { ref, watch, nextTick } from 'vue'
import ChatBubble from './ChatBubble.vue'

const props = defineProps({
  messages: { type: Array, required: true },
  isLoading: { type: Boolean, default: false },
})

const emit = defineEmits(['copy', 'delete', 'edit', 'reroll'])

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
    class="chat-scroll min-h-0 flex-1 overflow-y-auto scroll-smooth px-4 py-5 md:px-6"
  >
    <div class="mx-auto flex min-h-full w-full max-w-4xl flex-col">
      <div
        v-if="messages.length === 0 && !isLoading"
        class="flex flex-1 items-center justify-center py-10"
      >
        <div class="w-full max-w-2xl">
          <div class="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-bold" style="border-color: var(--border); background: var(--surface-raised); color: var(--ink-muted);">
            <span class="status-dot"></span>
            知识库问答已就绪
          </div>
          <h2 class="mt-5 text-2xl font-[780] leading-tight md:text-3xl">问具体问题，保留可追溯的校园知识上下文。</h2>
          <p class="mt-3 max-w-[64ch] text-sm leading-7" style="color: var(--ink-muted);">
            上传资料、切换检索模式、继续历史会话都在同一个工作台中完成。回答支持 Markdown、代码块和表格，适合直接阅读和复核。
          </p>

          <div class="mt-6 grid gap-3 sm:grid-cols-3">
            <div class="rounded-2xl border p-4" style="border-color: var(--border); background: var(--surface-raised);">
              <p class="text-sm font-bold">政策查询</p>
              <p class="mt-1 text-xs leading-5" style="color: var(--ink-muted);">查找手册、制度、流程中的精确依据。</p>
            </div>
            <div class="rounded-2xl border p-4" style="border-color: var(--border); background: var(--surface-raised);">
              <p class="text-sm font-bold">文档问答</p>
              <p class="mt-1 text-xs leading-5" style="color: var(--ink-muted);">上传资料后围绕内容继续追问。</p>
            </div>
            <div class="rounded-2xl border p-4" style="border-color: var(--border); background: var(--surface-raised);">
              <p class="text-sm font-bold">会话复用</p>
              <p class="mt-1 text-xs leading-5" style="color: var(--ink-muted);">保留历史，回到上下文继续工作。</p>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="space-y-4 pb-2">
        <ChatBubble
          v-for="(msg, idx) in messages"
          :key="idx"
          :role="msg.role"
          :content="msg.content"
          :index="idx"
          :is-last-ai="msg.role === 'assistant' && idx === messages.length - 1"
          @copy="(text) => $emit('copy', text)"
          @delete="(i) => $emit('delete', i)"
          @edit="(payload) => $emit('edit', payload)"
          @reroll="$emit('reroll')"
        />

        <ChatBubble
          v-if="isLoading"
          role="assistant"
          :is-loading="true"
        />
      </div>
    </div>
  </div>
</template>
