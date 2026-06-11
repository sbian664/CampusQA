<script setup>
import { nextTick, ref, watch } from 'vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'clear', 'upload'])

const inputText = ref('')
const textareaRef = ref(null)
const fileInputRef = ref(null)

function handleUpload() {
  fileInputRef.value?.click()
}

function onFileChange(e) {
  const file = e.target.files?.[0]
  if (!file) return
  emit('upload', file)
  e.target.value = ''
}

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 168) + 'px'
}

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
  inputText.value = ''
  nextTick(() => {
    if (textareaRef.value) textareaRef.value.style.height = 'auto'
  })
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

watch(inputText, autoResize)
</script>

<template>
  <footer class="shrink-0 border-t px-4 py-3 md:px-6 md:py-4" style="border-color: var(--border); background: color-mix(in oklch, var(--surface-raised), transparent 2%);">
    <div class="mx-auto w-full max-w-4xl">
      <div class="flex items-end gap-2 rounded-2xl border p-2" style="border-color: var(--border); background: var(--surface); box-shadow: 0 14px 30px oklch(25% 0.04 278 / 0.06);">
        <button
          @click="handleUpload"
          :disabled="disabled"
          class="icon-button shrink-0"
          title="上传文档"
          aria-label="上传文档"
        >
          <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
          </svg>
        </button>
        <input
          ref="fileInputRef"
          type="file"
          accept=".md,.txt,.pdf,.html"
          class="hidden"
          @change="onFileChange"
        />

        <textarea
          ref="textareaRef"
          v-model="inputText"
          :disabled="disabled"
          @keydown="handleKeydown"
          rows="1"
          placeholder="询问知识库，Enter 发送"
          class="max-h-[10.5rem] min-h-[2.375rem] min-w-0 flex-1 resize-none border-0 bg-transparent px-2 py-2 text-[15px] leading-6 outline-none placeholder:text-[color:var(--ink-soft)] disabled:cursor-not-allowed disabled:opacity-55"
          style="color: var(--ink);"
        ></textarea>

        <button
          @click="$emit('clear')"
          class="icon-button hidden shrink-0 sm:grid"
          title="清空对话"
          aria-label="清空对话"
        >
          <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4h6v3" />
          </svg>
        </button>

        <button
          @click="handleSend"
          :disabled="disabled || !inputText.trim()"
          class="primary-button h-[2.375rem] w-[2.375rem] shrink-0 p-0"
          title="发送"
          aria-label="发送消息"
        >
          <svg v-if="!disabled" class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M13 6l6 6-6 6" />
          </svg>
          <svg v-else class="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
          </svg>
        </button>
      </div>

      <div class="mt-2 flex flex-wrap items-center justify-between gap-2 px-1 text-xs" style="color: var(--ink-soft);">
        <span>支持 md、txt、pdf、html 上传</span>
        <span>Shift + Enter 换行</span>
      </div>
    </div>
  </footer>
</template>
