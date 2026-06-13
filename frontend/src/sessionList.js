export function upsertSessionFromChatResponse(sessions, chatResponse, now = new Date()) {
  if (!chatResponse?.session_id) return sessions

  const updatedAt = now.toISOString()
  const existing = sessions.find((session) => session.session_id === chatResponse.session_id)
  const nextSession = {
    session_id: chatResponse.session_id,
    title: chatResponse.session_title ?? existing?.title ?? null,
    message_count: (existing?.message_count ?? 0) + 2,
    created_at: existing?.created_at ?? updatedAt,
    updated_at: updatedAt,
  }

  return [
    nextSession,
    ...sessions.filter((session) => session.session_id !== chatResponse.session_id),
  ]
}
