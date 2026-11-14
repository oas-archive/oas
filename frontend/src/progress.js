// 背景進度的全域狀態：伺服器補新收錄（/intake/status）＋手機讀有防護網站的佇列（pageReader.js）。
// 側欄底部、手機底部分頁列上方的進度列、一次收錄視窗、「整理 → 待整理」都讀這一份，關掉哪個視窗都不影響。
// 有工作在跑時每 2.5 秒問一次伺服器，沒有時 30 秒一次（收錄、重試後由 kickProgress 立刻問）。
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { pendingReads, readerState, subscribeReader } from './pageReader.js'
import { api } from './shared.jsx'
import { PROTECTED_LABEL } from './sites.js'

const BUSY_MS = 2500, IDLE_MS = 30000

// fill：loaded 是否問過伺服器、queued 還在排隊的件數、total 這一批的總件數、message 限流等提示、at 這次詢問開始的時間
let fill = { loaded: false, running: false, queued: 0, total: 0, message: '', at: 0 }
let snapshot = { fill, read: readerState(), pending: pendingReads() }
const listeners = new Set()
const update = () => {
  snapshot = { fill, read: readerState(), pending: pendingReads() }
  listeners.forEach(listener => listener())
}
subscribeReader(update)

let timer = null
let asking = false
/** 立刻問一次伺服器（收錄、重試之後呼叫）。 */
export function kickProgress() {
  clearTimeout(timer)
  timer = null
  if (asking) { timer = setTimeout(kickProgress, 300); return }
  asking = true
  const at = Date.now()
  api('/intake/status').then(state => {
    const queued = Math.max(0, Number(state.queued) || 0)
    fill = { loaded: true, running: queued > 0, queued, total: Math.max(Number(state.total) || 0, queued), message: queued ? String(state.message || '') : '', at }
    update()
  }).catch(() => {}).finally(() => {
    asking = false
    if (listeners.size && !timer) timer = setTimeout(kickProgress, fill.running ? BUSY_MS : IDLE_MS)
  })
}

const subscribe = listener => {
  listeners.add(listener)
  if (listeners.size === 1) kickProgress()
  return () => {
    listeners.delete(listener)
    if (!listeners.size) { clearTimeout(timer); timer = null }
  }
}

/** { fill, read, pending }：fill 是伺服器補資料，read 是手機讀頁這一輪的進度，pending 是還沒讀的作品。 */
export const useProgress = () => useSyncExternalStore(subscribe, () => snapshot)

/** 進度列要顯示的內容；沒有工作在跑時 active 為 false。 */
export function progressSummary({ fill, read }) {
  const parts = []
  let done = 0, total = 0
  if (fill.running) {
    const filled = Math.max(0, fill.total - fill.queued)
    parts.push(`補資料 ${filled}／${fill.total}`)
    done += filled; total += fill.total
  }
  if (read.running) {
    parts.push(`讀${PROTECTED_LABEL} ${read.done}／${read.total}`)
    done += read.done; total += read.total
  }
  return { active: parts.length > 0, text: parts.join('・'), ratio: total ? done / total : 0, message: fill.running ? fill.message : '' }
}

const FINISHED_MS = 4000

/** 主程式用一份：進度列的內容，做完後再顯示「補資料做完了」4 秒才消失（shown）；任何一種工作做完時呼叫 onFinish（重新載入書庫）。 */
export function useProgressLine(onFinish) {
  const progress = useProgress()
  const summary = progressSummary(progress)
  const [finished, setFinished] = useState(false)
  const was = useRef({ fill: false, read: false, any: false })
  const finish = useRef(onFinish)
  finish.current = onFinish
  const { fill, read } = progress
  useEffect(() => {
    const before = was.current
    was.current = { fill: fill.running, read: read.running, any: summary.active }
    if ((before.fill && !fill.running) || (before.read && !read.running)) finish.current?.()
    if (summary.active) { setFinished(false); return }
    if (!before.any) return
    setFinished(true)
    const timer = setTimeout(() => setFinished(false), FINISHED_MS)
    return () => clearTimeout(timer)
  }, [fill.running, read.running, summary.active])
  return { ...summary, shown: summary.active || finished, text: summary.active ? summary.text : '補資料做完了' }
}
