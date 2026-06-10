<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  message: { type: String, default: '' },
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['dismiss'])

let timer = null

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
      class="fixed bottom-20 left-1/2 -translate-x-1/2 z-50
             bg-red-500 text-white px-5 py-3 rounded-xl shadow-lg
             flex items-center gap-3 max-w-md w-[90%]"
    >
      <svg class="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span class="text-sm flex-1">{{ message }}</span>
      <button @click="$emit('dismiss')" class="shrink-0 hover:opacity-80">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  </Transition>
</template>

<style scoped>
.toast-enter-active { transition: all 0.3s ease-out; }
.toast-leave-active { transition: all 0.2s ease-in; }
.toast-enter-from { opacity: 0; transform: translate(-50%, 10px); }
.toast-leave-to { opacity: 0; transform: translate(-50%, -10px); }
</style>
