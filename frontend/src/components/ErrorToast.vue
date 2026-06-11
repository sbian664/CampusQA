<script setup>
import { computed, watch } from 'vue'

const props = defineProps({
  message: { type: String, default: '' },
  visible: { type: Boolean, default: false },
  type: { type: String, default: 'error' },
})

const emit = defineEmits(['dismiss'])

let timer = null

const isSuccess = computed(() => props.type === 'success')

watch(() => props.visible, (val) => {
  if (val) {
    clearTimeout(timer)
    timer = setTimeout(() => emit('dismiss'), 5000)
  }
})
</script>

<template>
  <Transition name="toast">
    <div
      v-if="visible"
      class="fixed bottom-5 left-1/2 z-50 flex w-[min(92vw,28rem)] -translate-x-1/2 items-start gap-3 rounded-2xl border px-4 py-3 shadow-lg"
      :style="isSuccess
        ? 'border-color: var(--success); background: var(--success-soft); color: var(--ink);'
        : 'border-color: var(--danger); background: var(--danger-soft); color: var(--ink);'"
      role="status"
    >
      <div
        class="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg text-white"
        :style="{ background: isSuccess ? 'var(--success)' : 'var(--danger)' }"
      >
        <svg v-if="isSuccess" class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
        </svg>
        <svg v-else class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M5 5l14 14" />
        </svg>
      </div>
      <span class="min-w-0 flex-1 text-sm font-semibold leading-6">{{ message }}</span>
      <button @click="$emit('dismiss')" class="icon-button h-7 w-7 shrink-0" title="关闭" aria-label="关闭提示">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  </Transition>
</template>

<style scoped>
.toast-enter-active { transition: opacity 180ms ease, transform 180ms ease; }
.toast-leave-active { transition: opacity 140ms ease, transform 140ms ease; }
.toast-enter-from { opacity: 0; transform: translate(-50%, 10px); }
.toast-leave-to { opacity: 0; transform: translate(-50%, 8px); }
</style>
