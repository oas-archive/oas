import { describe, expect, it, vi } from 'vitest'

// progress.js 會載入 pageReader（碰 window）與 shared.jsx（畫面共用），這裡只測純函式，兩者都換成假的
vi.mock('../pageReader.js', () => ({ pendingReads: () => [], readerState: () => ({}), subscribeReader: () => () => {} }))
vi.mock('../shared.jsx', () => ({ api: vi.fn() }))

import { progressSummary } from '../progress.js'
import { PROTECTED_LABEL } from '../sites.js'

const fill = (extra = {}) => ({ loaded: true, running: false, queued: 0, total: 0, message: '', at: 0, ...extra })
const read = (extra = {}) => ({ running: false, done: 0, total: 0, filled: 0, dead: 0, paused: false, ...extra })

describe('progressSummary', () => {
  it('兩邊都沒在跑：不顯示、比例 0', () => {
    expect(progressSummary({ fill: fill(), read: read() })).toEqual({ active: false, text: '', ratio: 0, message: '' })
  })

  it('只有伺服器補資料：已補 = 總數 − 排隊中', () => {
    const summary = progressSummary({ fill: fill({ running: true, queued: 3, total: 10, message: '限流中' }), read: read() })
    expect(summary.active).toBe(true)
    expect(summary.text).toBe('補資料 7／10')
    expect(summary.ratio).toBeCloseTo(0.7)
    expect(summary.message).toBe('限流中')
  })

  it('排隊數大於總數時已補件數不會是負的', () => {
    const summary = progressSummary({ fill: fill({ running: true, queued: 5, total: 2 }), read: read() })
    expect(summary.text).toBe('補資料 0／2')
    expect(summary.ratio).toBe(0)
  })

  it('只有手機讀頁：文字用防護網站標籤，提示訊息只來自伺服器補資料', () => {
    const summary = progressSummary({ fill: fill({ message: '殘留' }), read: read({ running: true, done: 1, total: 4 }) })
    expect(summary.text).toBe(`讀${PROTECTED_LABEL} 1／4`)
    expect(summary.ratio).toBe(0.25)
    expect(summary.message).toBe('')
  })

  it('兩種都在跑：用「・」串起來，比例合併計算', () => {
    const summary = progressSummary({ fill: fill({ running: true, queued: 1, total: 2 }), read: read({ running: true, done: 1, total: 2 }) })
    expect(summary.text).toBe(`補資料 1／2・讀${PROTECTED_LABEL} 1／2`)
    expect(summary.ratio).toBe(0.5)
  })

  it('總數為 0 但標示在跑：比例 0，不是 NaN', () => {
    const summary = progressSummary({ fill: fill(), read: read({ running: true, done: 0, total: 0 }) })
    expect(summary.active).toBe(true)
    expect(summary.ratio).toBe(0)
  })
})
