export const INTELLIGENT_SEARCH_STORAGE_KEY = 'campusqa_intelligent_search'

export function loadIntelligentSearch(storage = localStorage) {
  return storage.getItem(INTELLIGENT_SEARCH_STORAGE_KEY) !== 'false'
}

export function saveIntelligentSearch(storage = localStorage, enabled) {
  storage.setItem(INTELLIGENT_SEARCH_STORAGE_KEY, String(Boolean(enabled)))
}
