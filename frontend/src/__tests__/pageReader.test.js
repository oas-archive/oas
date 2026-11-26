import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 全部資料都是虛構的編號與網址。pageReader 在載入時就讀 localStorage、掛 window，所以每個測試重新載入模組。
const api = vi.fn()
vi.mock('../shared.jsx', () => ({ api: (...args) => api(...args) }))

let backgroundReader = true
vi.mock('../preferences.js', () => ({ preferences: () => ({ background_reader: backgroundReader }) }))

const KEY = 'oas-read-queue'
const fakeStorage = (initial = {}) => {
  const data = { ...initial }
  return {
    data,
    getItem: key => (key in data ? data[key] : null),
    setItem: (key, value) => { data[key] = String(value) },
    removeItem: key => { delete data[key] },
  }
}

let storage, win, pages, reader
/** pages：網址 → 要回傳的 HTML（沒列的網址讀不到，回傳空字串）。原生 readPage 先記下呼叫，再過一小段時間回覆。 */
const setup = async (stored, pageMap = {}) => {
  storage = fakeStorage(stored === undefined ? {} : { [KEY]: stored })
  pages = pageMap
  const calls = []
  win = { OASNative: { readPage: vi.fn((id, url) => { calls.push(url); setTimeout(() => win.__oasPageDone(id, pages[url] || ''), 10) }) } }
  win.calls = calls
  vi.stubGlobal('window', win)
  vi.stubGlobal('localStorage', storage)
  vi.resetModules()
  reader = await import('../pageReader.js')
}
const saved = () => (KEY in storage.data ? JSON.parse(storage.data[KEY]) : null)
const settle = (ms = 200000) => vi.advanceTimersByTimeAsync(ms)

beforeEach(() => {
  vi.useFakeTimers()
  api.mockReset()
  api.mockResolvedValue({ filled: true, result: 'alive' })
  backgroundReader = true
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('載入時讀回佇列', () => {
  it('讀回有效的列，只留 id／url／check 三個欄位', async () => {
    await setup(JSON.stringify([{ id: 1, url: 'https://example.test/a', extra: 'x' }, { id: 2, url: 'https://example.test/b', check: true }]))
    expect(reader.pendingReads()).toEqual([{ id: 1, url: 'https://example.test/a' }, { id: 2, url: 'https://example.test/b', check: true }])
  })

  it('壞掉的 JSON、不是陣列、沒有存都是空佇列', async () => {
    await setup('{壞掉')
    expect(reader.pendingReads()).toEqual([])
    await setup('{"id":1}')
    expect(reader.pendingReads()).toEqual([])
    await setup(undefined)
    expect(reader.pendingReads()).toEqual([])
  })

  it('濾掉編號不是整數、網址不是 http(s) 的列與 null', async () => {
    await setup(JSON.stringify([
      null, { id: '3', url: 'https://example.test/x' }, { id: 1.5, url: 'https://example.test/x' },
      { id: 4, url: 'javascript:alert(1)' }, { id: 5, url: 'ftp://example.test/x' }, { id: 6 },
      { id: 7, url: 'HTTP://example.test/ok' },
    ]))
    expect(reader.pendingReads().map(row => row.id)).toEqual([7])
  })
})

describe('canReadPages／readPage', () => {
  it('不是手機 App（沒有 OASNative.readPage）或背景讀取關閉時不能讀', async () => {
    await setup()
    expect(reader.canReadPages()).toBe(true)
    backgroundReader = false
    expect(reader.canReadPages()).toBe(false)
    backgroundReader = true
    delete win.OASNative.readPage
    expect(reader.canReadPages()).toBe(false)
  })

  it('背景讀取只有 === true 才算開（"true" 字串不算）', async () => {
    await setup()
    backgroundReader = 'true'
    expect(reader.canReadPages()).toBe(false)
  })

  it('網址不是 http(s) 或空的：直接回傳空字串，不呼叫原生', async () => {
    await setup()
    expect(await reader.readPage('file:///etc/passwd')).toBe('')
    expect(await reader.readPage('')).toBe('')
    expect(await reader.readPage(undefined)).toBe('')
    expect(win.OASNative.readPage).not.toHaveBeenCalled()
  })

  it('讀到就回傳 HTML；原生沒回覆時逾時回傳空字串', async () => {
    await setup(undefined, { 'https://example.test/ok': '<p>hi</p>' })
    const ok = reader.readPage('https://example.test/ok')
    await settle(50)
    expect(await ok).toBe('<p>hi</p>')

    win.OASNative.readPage.mockImplementation(() => {})  // 永遠不回覆
    const slow = reader.readPage('https://example.test/slow', 1000)
    await settle(1000)
    expect(await slow).toBe('')
  })

  it('晚到的回覆（已逾時或沒人等）被忽略，不會出錯', async () => {
    await setup()
    expect(() => win.__oasPageDone('不存在的編號', '<p>x</p>')).not.toThrow()
  })

  it('一次只讀一頁：使用者在等的先於佇列用的', async () => {
    await setup(undefined, { 'https://example.test/1': 'a', 'https://example.test/2': 'b', 'https://example.test/3': 'c' })
    const order = []
    const first = reader.readPage('https://example.test/1').then(() => order.push(1))
    const later = reader.readPage('https://example.test/2', 40000, true).then(() => order.push(2))
    const urgent = reader.readPage('https://example.test/3').then(() => order.push(3))
    await settle(200)
    await Promise.all([first, later, urgent])
    expect(order).toEqual([1, 3, 2])
  })
})

describe('queueReads', () => {
  it('不能讀時回傳 0，也不寫入 localStorage', async () => {
    await setup()
    backgroundReader = false
    expect(reader.queueReads([{ id: 1, url: 'https://example.test/a' }])).toBe(0)
    expect(saved()).toBeNull()
    expect(reader.readerState().running).toBe(false)
  })

  it('濾掉無效列、同一批與已在佇列的重複；補資料與檢查是兩件事', async () => {
    await setup()
    const n = reader.queueReads([
      { id: 1, url: 'https://example.test/a' },
      { id: 1, url: 'https://example.test/a' },            // 同批重複
      { id: 1, url: 'https://example.test/a', check: true },  // 同編號但是檢查：算另一件
      { id: 2, url: 'ftp://example.test/b' },              // 網址不合
      null, { id: 'x', url: 'https://example.test/c' },
    ])
    expect(n).toBe(2)
    expect(saved()).toEqual([{ id: 1, url: 'https://example.test/a' }, { id: 1, url: 'https://example.test/a', check: true }])
    expect(reader.queueReads([{ id: 1, url: 'https://example.test/a' }])).toBe(0)  // 已在佇列
  })

  it('全部無效時回傳 0 且不開始跑', async () => {
    await setup()
    expect(reader.queueReads([])).toBe(0)
    expect(reader.queueReads([{ id: 1, url: 'nope' }])).toBe(0)
    expect(reader.readerState().running).toBe(false)
    expect(win.OASNative.readPage).not.toHaveBeenCalled()
  })
})

describe('一件讀完才移除', () => {
  it('讀的過程中佇列（與 localStorage）仍留著，讀完那件才拿掉，全部讀完後清掉 key', async () => {
    await setup(undefined, { 'https://example.test/a': '<a>', 'https://example.test/b': '<b>' })
    reader.queueReads([{ id: 1, url: 'https://example.test/a' }, { id: 2, url: 'https://example.test/b' }])
    await settle(5)  // 第一件讀到一半
    expect(win.calls).toEqual(['https://example.test/a'])
    expect(saved().map(row => row.id)).toEqual([1, 2])

    await settle(30)  // 第一件讀完
    expect(saved().map(row => row.id)).toEqual([2])
    expect(api).toHaveBeenCalledWith('/attention/fill-page', { method: 'POST', body: JSON.stringify({ id: 1, html: '<a>' }) })

    await settle()  // 全部讀完
    expect(saved()).toBeNull()
    expect(reader.pendingReads()).toEqual([])
    expect(reader.readerState()).toMatchObject({ running: false, done: 2, total: 2, filled: 2 })
  })

  it('讀不到（空白）的作品不送伺服器，但一樣從佇列移除（不會永遠卡著）', async () => {
    await setup(undefined, {})
    reader.queueReads([{ id: 1, url: 'https://example.test/a' }])
    await settle()
    expect(api).not.toHaveBeenCalled()
    expect(reader.pendingReads()).toEqual([])
    expect(reader.readerState()).toMatchObject({ done: 1, filled: 0 })
  })

  it('伺服器回報沒補到資料、或呼叫失敗：不算 filled', async () => {
    await setup(undefined, { 'https://example.test/a': '<a>', 'https://example.test/b': '<b>' })
    api.mockResolvedValueOnce({ filled: false }).mockRejectedValueOnce(new Error('boom'))
    reader.queueReads([{ id: 1, url: 'https://example.test/a' }, { id: 2, url: 'https://example.test/b' }])
    await settle()
    expect(reader.readerState()).toMatchObject({ done: 2, filled: 0, running: false })
    expect(reader.pendingReads()).toEqual([])
  })

  it('同一輪讀到一半又排新的：total 累加，不重算', async () => {
    await setup(undefined, { 'https://example.test/a': '<a>', 'https://example.test/b': '<b>' })
    reader.queueReads([{ id: 1, url: 'https://example.test/a' }])
    await settle(5)
    expect(reader.queueReads([{ id: 2, url: 'https://example.test/b' }])).toBe(1)
    expect(reader.readerState().total).toBe(2)
    await settle()
    expect(reader.readerState()).toMatchObject({ done: 2, total: 2, running: false })
  })

  it('訂閱者在進度改變時被通知，取消訂閱後不再通知', async () => {
    await setup(undefined, { 'https://example.test/a': '<a>' })
    const listener = vi.fn()
    const off = reader.subscribeReader(listener)
    reader.queueReads([{ id: 1, url: 'https://example.test/a' }])
    expect(listener).toHaveBeenCalled()
    off()
    listener.mockClear()
    await settle()
    expect(listener).not.toHaveBeenCalled()
  })
})

describe('resumeReads（重開 App 接著讀）', () => {
  it('把上次沒讀完的接著讀，每件只讀一次；讀完 localStorage 清空', async () => {
    await setup(JSON.stringify([{ id: 1, url: 'https://example.test/a' }, { id: 2, url: 'https://example.test/b' }]),
      { 'https://example.test/a': '<a>', 'https://example.test/b': '<b>' })
    reader.resumeReads()
    reader.resumeReads()  // 重複呼叫（例如畫面重繪）不會開第二個讀取者
    await settle()
    expect(win.calls).toEqual(['https://example.test/a', 'https://example.test/b'])
    expect(api).toHaveBeenCalledTimes(2)
    expect(saved()).toBeNull()
    expect(reader.readerState()).toMatchObject({ running: false, done: 2, total: 2 })
  })

  it('接著讀的途中又 queueReads 同一件，不會重複排入或重複讀', async () => {
    await setup(JSON.stringify([{ id: 1, url: 'https://example.test/a' }]), { 'https://example.test/a': '<a>' })
    reader.resumeReads()
    expect(reader.queueReads([{ id: 1, url: 'https://example.test/a' }])).toBe(0)
    await settle()
    expect(win.calls).toEqual(['https://example.test/a'])
  })

  it('佇列是空的或背景讀取關閉時什麼都不做，佇列保留給下次', async () => {
    await setup(undefined)
    reader.resumeReads()
    expect(reader.readerState().running).toBe(false)

    await setup(JSON.stringify([{ id: 1, url: 'https://example.test/a' }]))
    backgroundReader = false
    reader.resumeReads()
    await settle()
    expect(win.OASNative.readPage).not.toHaveBeenCalled()
    expect(saved()).toEqual([{ id: 1, url: 'https://example.test/a' }])
  })

  it('讀到一半關掉背景讀取：那頁不送出，也不從佇列移除', async () => {
    await setup(JSON.stringify([{ id: 1, url: 'https://example.test/a' }]), { 'https://example.test/a': '<a>' })
    reader.resumeReads()
    await settle(5)
    backgroundReader = false
    await settle()
    expect(api).not.toHaveBeenCalled()
    expect(saved()).toEqual([{ id: 1, url: 'https://example.test/a' }])
    expect(reader.readerState().running).toBe(false)
  })
})

describe('clearReads', () => {
  it('清掉佇列與 localStorage、進度歸零；讀到一半的那件回來後不記進度', async () => {
    await setup(undefined, { 'https://example.test/a': '<a>', 'https://example.test/b': '<b>' })
    reader.queueReads([{ id: 1, url: 'https://example.test/a' }, { id: 2, url: 'https://example.test/b' }])
    await settle(5)
    reader.clearReads()
    expect(saved()).toBeNull()
    expect(reader.pendingReads()).toEqual([])
    expect(reader.readerState()).toMatchObject({ running: false, done: 0, total: 0 })
    await settle()
    expect(api).not.toHaveBeenCalled()
    expect(reader.readerState()).toMatchObject({ done: 0, total: 0 })
  })
})

describe('禁漫檢查（check:true）', () => {
  const check = id => ({ id, url: `https://18comic.test/album/${id}`, check: true })

  it('結果送 /linkcheck/jm/result，認定已不存在的計入 dead', async () => {
    await setup(undefined, { 'https://18comic.test/album/1': '<x>' })
    api.mockResolvedValue({ result: 'dead' })
    reader.queueReads([check(1)])
    await settle()
    expect(api).toHaveBeenCalledWith('/linkcheck/jm/result', { method: 'POST', body: JSON.stringify({ id: 1, html: '<x>' }) })
    expect(reader.readerState()).toMatchObject({ done: 1, dead: 1, paused: false })
    expect(reader.pendingReads()).toEqual([])
  })

  it('讀到空白（驗證頁沒過、逾時）：不送伺服器、不算失效，並把這一輪剩下的檢查全拿掉，補資料的留著', async () => {
    await setup(undefined, { 'https://example.test/fill': '<f>' })
    reader.queueReads([check(1), check(2), { id: 9, url: 'https://example.test/fill' }, check(3)])
    await settle(5)
    await settle(30)  // 第一件（空白）讀完
    expect(api).not.toHaveBeenCalledWith('/linkcheck/jm/result', expect.anything())
    expect(reader.pendingReads()).toEqual([{ id: 9, url: 'https://example.test/fill' }])
    expect(reader.readerState()).toMatchObject({ paused: true, dead: 0, done: 1, total: 2 })
    await settle()
    expect(api).toHaveBeenCalledWith('/attention/fill-page', expect.anything())
    expect(reader.readerState()).toMatchObject({ running: false, done: 2, total: 2, paused: true })
    expect(saved()).toBeNull()
  })

  it('伺服器判定 alive 或呼叫失敗都不算 dead，也不停止後面的檢查', async () => {
    await setup(undefined, { 'https://18comic.test/album/1': '<1>', 'https://18comic.test/album/2': '<2>' })
    api.mockResolvedValueOnce({ result: 'alive' }).mockRejectedValueOnce(new Error('x'))
    reader.queueReads([check(1), check(2)])
    await settle()
    expect(reader.readerState()).toMatchObject({ done: 2, dead: 0, paused: false })
  })
})

describe('startJmCheck', () => {
  it('向伺服器要清單並以 check 排入；每次載入最多一輪', async () => {
    await setup(undefined, {})
    api.mockImplementation(async path => (path === '/linkcheck/jm/targets' ? { targets: [{ id: 5, url: 'https://18comic.test/album/5' }, { id: 6, url: 'bad' }] } : {}))
    await reader.startJmCheck()
    expect(saved()).toEqual([{ id: 5, url: 'https://18comic.test/album/5', check: true }])
    api.mockClear()
    await reader.startJmCheck()
    expect(api).not.toHaveBeenCalledWith('/linkcheck/jm/targets')
  })

  it('不能讀時不問伺服器；問失敗時不丟錯', async () => {
    await setup()
    backgroundReader = false
    await reader.startJmCheck()
    expect(api).not.toHaveBeenCalled()

    backgroundReader = true
    api.mockRejectedValue(new Error('offline'))
    await expect(reader.startJmCheck()).resolves.toBeUndefined()
  })

  it('scheduleJmCheck 等 30 秒才開始，取消後不會開始', async () => {
    await setup()
    api.mockResolvedValue({ targets: [] })
    const cancel = reader.scheduleJmCheck()
    await settle(29000)
    expect(api).not.toHaveBeenCalled()
    await settle(1500)
    expect(api).toHaveBeenCalledWith('/linkcheck/jm/targets')

    await setup()
    api.mockClear()
    reader.scheduleJmCheck()()
    await settle(60000)
    expect(api).not.toHaveBeenCalled()
    cancel()
  })
})

describe('localStorage 存不了', () => {
  it('寫入丟例外時仍可繼續讀（只影響重開後接著讀）', async () => {
    await setup(undefined, { 'https://example.test/a': '<a>' })
    storage.setItem = () => { throw new Error('quota') }
    expect(reader.queueReads([{ id: 1, url: 'https://example.test/a' }])).toBe(1)
    await settle()
    expect(reader.readerState()).toMatchObject({ done: 1, running: false })
  })
})
