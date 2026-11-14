// 手機 App 才有：在看不到的瀏覽器打開作品頁，把頁面 HTML 交回來，再交給伺服器依網站規則解析。
// 有些網站伺服器自己連線讀不到，手機上的瀏覽器看得到；解析規則仍只有伺服器那一份（metadata.py）。使用者要在設定裡開啟才會用。
//
// 一次讀很多件（一次收錄、待整理的「試著讀取」）走這裡的全域佇列，不跟著哪個視窗：
// - 還沒讀的作品存在 localStorage（oas-read-queue），App 重新載入（例如分享進來）或被系統關掉，再開時接著讀；
// - 「背景讀取」關掉時（公開版第一次啟動可以不開）不讀也不排；公開版在設定裡關掉時連排隊的網址一起清掉（clearReads）；
// - 分享、抓取資訊這類使用者正在等的單頁讀取優先，佇列讀完手上這頁就讓它先讀。
import { preferences } from './preferences.js'
import { api } from './shared.jsx'

const QUEUE_KEY = 'oas-read-queue'
const GAP_MS = 1500  // 同一個網站不要連續狂讀

const waiting = {}
window.__oasPageDone = (id, html) => {
  const done = waiting[id]
  if (done) { delete waiting[id]; done(html || '') }
}

export const canReadPages = () => Boolean(window.OASNative?.readPage) && preferences().background_reader === true

// 一次只讓一頁在讀（App 外殼也是一次一頁，排在後面的會被這邊的逾時算成沒讀到）；使用者在等的排前面
let busy = false
const urgent = [], background = []
const acquire = later => new Promise(resolve => {
  if (!busy) { busy = true; resolve() } else (later ? background : urgent).push(resolve)
})
const release = () => {
  const next = urgent.shift() || background.shift()
  if (next) next(); else busy = false
}

/** 讀一頁：回傳頁面 HTML；讀不到（不是手機 App、防護驗證沒過、逾時）回傳空字串。later：佇列用，讓使用者在等的先讀。 */
export async function readPage(url, timeout = 40000, later = false) {
  if (!canReadPages() || !/^https?:\/\//i.test(url || '')) return ''
  await acquire(later)
  try {
    return await new Promise(resolve => {
      const id = Math.random().toString(36).slice(2)
      waiting[id] = resolve
      window.OASNative.readPage(id, url)
      setTimeout(() => window.__oasPageDone(id, ''), timeout)
    })
  } finally { release() }
}

// ---------- 全域佇列 ----------

const valid = row => row && Number.isInteger(row.id) && /^https?:\/\//i.test(row.url || '')
const loadQueue = () => {
  try {
    const rows = JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]')
    return Array.isArray(rows) ? rows.filter(valid).map(row => ({ id: row.id, url: row.url })) : []
  } catch { return [] }
}
const saveQueue = rows => {
  try { if (rows.length) localStorage.setItem(QUEUE_KEY, JSON.stringify(rows)); else localStorage.removeItem(QUEUE_KEY) } catch { /* 存不了只影響重開後接著讀 */ }
}

let queue = loadQueue()
// 這一輪的進度：done／total 是這次開始讀之後的件數（重開 App 接著讀時從 0 算起），filled 是補到資料的件數
let reader = { running: false, done: 0, total: 0, filled: 0 }
let worker = null
let generation = 0  // clearReads 清掉佇列後加一，讀到一半的那件回來時就不再記進度
const listeners = new Set()
const emit = () => { listeners.forEach(listener => listener()) }

export const subscribeReader = listener => { listeners.add(listener); return () => { listeners.delete(listener) } }
/** 目前這一輪的進度（同一個物件代表沒變，給 useSyncExternalStore 用）。 */
export const readerState = () => reader
/** 還沒讀的作品 [{ id, url }]。 */
export const pendingReads = () => queue

const pause = ms => new Promise(done => setTimeout(done, ms))

function work() {
  if (worker) return
  worker = (async () => {
    const round = generation
    while (queue.length && canReadPages() && round === generation) {
      const row = queue[0]
      const html = await readPage(row.url, 40000, true)
      if (!canReadPages() || round !== generation) break  // 讀到一半關掉背景讀取：不送出這頁
      const result = html ? await api('/attention/fill-page', { method: 'POST', body: JSON.stringify({ id: row.id, html }) }).catch(() => null) : null
      if (round !== generation) break
      queue = queue.filter(item => item.id !== row.id)
      saveQueue(queue)
      reader = { ...reader, done: reader.done + 1, filled: reader.filled + (result?.filled ? 1 : 0) }
      emit()
      if (queue.length) await pause(GAP_MS)
    }
    if (round === generation) reader = { ...reader, running: false }
    worker = null
    emit()
    if (round !== generation && queue.length) work()  // 清掉後又排了新的
  })()
}

/** 把作品排進背景讀取（[{ id, url }]，已在佇列的略過）。回傳新排入的件數；不能讀（不是手機 App、背景讀取關閉）時回傳 0。 */
export function queueReads(rows) {
  if (!canReadPages()) return 0
  const known = new Set(queue.map(row => row.id))
  const fresh = []
  for (const row of rows) {
    if (!valid(row) || known.has(row.id)) continue
    known.add(row.id)
    fresh.push({ id: row.id, url: row.url })
  }
  if (!fresh.length) return 0
  queue = [...queue, ...fresh]
  saveQueue(queue)
  reader = reader.running ? { ...reader, total: reader.total + fresh.length } : { running: true, done: 0, total: queue.length, filled: 0 }
  emit()
  work()
  return fresh.length
}

/** App 開啟時呼叫：上次還沒讀完的接著讀（背景讀取關閉時不讀）。 */
export function resumeReads() {
  if (reader.running || !queue.length || !canReadPages()) return
  reader = { running: true, done: 0, total: queue.length, filled: 0 }
  emit()
  work()
}

/** 公開版在設定裡關掉背景讀取時呼叫：清掉還沒讀的作品網址（含 localStorage 裡存的），進度歸零。 */
export function clearReads() {
  generation++
  queue = []
  saveQueue(queue)
  reader = { running: false, done: 0, total: 0, filled: 0 }
  emit()
}
