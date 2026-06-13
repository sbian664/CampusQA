<script setup>
defineProps({
  visible: { type: Boolean, default: false },
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: null },
})

const emit = defineEmits(['close', 'select', 'new', 'delete'])

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function shortId(id) {
  if (!id) return '新会话'
  return id.length > 12 ? `${id.slice(0, 8)}...${id.slice(-4)}` : id
}

function sessionLabel(session) {
  return session.title || shortId(session.session_id)
}
</script>

<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-40 bg-black/35 md:hidden"
    @click="$emit('close')"
  />

  <aside
    class="sidebar-shell"
    :class="visible ? 'is-open' : ''"
    style="border-color: var(--border); background: color-mix(in oklch, var(--surface), transparent 4%);"
  >
    <div class="shrink-0 flex items-center justify-between gap-3 px-1 pb-3">
      <div class="flex min-w-0 items-center gap-3">
        <div class="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-sm font-black text-white" style="background: var(--brand);">
          C
        </div>
        <div class="min-w-0">
          <h2 class="truncate text-sm font-[750]">CampusQA</h2>
          <p class="truncate text-xs" style="color: var(--ink-soft);">历史与知识库会话</p>
        </div>
      </div>
      <button
        @click="$emit('close')"
        class="icon-button md:hidden"
        title="关闭历史会话"
        aria-label="关闭历史会话"
      >
        <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <button
      @click="$emit('new'); $emit('close')"
      class="primary-button shrink-0 w-full px-3"
    >
      <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 5v14M5 12h14" />
      </svg>
      <span>新建对话</span>
    </button>

    <div class="mt-4 shrink-0 flex items-center justify-between px-1">
      <p class="text-xs font-bold" style="color: var(--ink-muted);">历史会话</p>
      <span class="rounded-full px-2 py-0.5 text-xs font-semibold" style="background: var(--surface-muted); color: var(--ink-muted);">
        {{ sessions.length }}
      </span>
    </div>

    <div class="sidebar-scroll mt-2 min-h-0 flex-1 overflow-y-auto pr-1" aria-label="历史会话列表">
      <div
        v-if="sessions.length === 0"
        class="rounded-2xl border px-4 py-6 text-center"
        style="border-color: var(--border); background: var(--surface-raised);"
      >
        <div class="mx-auto grid h-10 w-10 place-items-center rounded-xl" style="background: var(--accent-soft); color: var(--accent);">
          <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h8M8 14h5M5 5h14v14H5z" />
          </svg>
        </div>
        <p class="mt-3 text-sm font-semibold">暂无历史会话</p>
        <p class="mt-1 text-xs leading-5" style="color: var(--ink-muted);">开始提问后，会话会在这里保留。</p>
      </div>

      <div
        v-for="s in sessions"
        :key="s.session_id"
        class="group mb-1.5 flex items-center gap-2 rounded-xl border p-2 transition"
        :style="s.session_id === currentSessionId
          ? 'border-color: var(--brand); background: var(--brand-soft);'
          : 'border-color: transparent; background: transparent;'"
      >
        <button
          class="min-w-0 flex-1 rounded-lg px-2 py-1.5 text-left transition hover:bg-[var(--surface-muted)]"
          @click="$emit('select', s.session_id); $emit('close')"
        >
          <div class="truncate text-sm font-semibold">{{ sessionLabel(s) }}</div>
          <div class="mt-1 flex items-center gap-2 text-xs" style="color: var(--ink-soft);">
            <span>{{ s.message_count }} 条消息</span>
            <span aria-hidden="true">·</span>
            <span class="truncate">{{ formatTime(s.updated_at) }}</span>
          </div>
        </button>
        <button
          @click.stop="$emit('delete', s.session_id)"
          class="icon-button h-8 w-8 opacity-100 md:opacity-0 md:group-hover:opacity-100"
          title="删除会话"
          aria-label="删除会话"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4h6v3" />
          </svg>
        </button>
      </div>
    </div>
  </aside>
</template>
