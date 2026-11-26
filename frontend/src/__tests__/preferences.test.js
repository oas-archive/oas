import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.fn()
const setCoverEpoch = vi.fn()
vi.mock('../shared.jsx', () => ({ api: (...args) => api(...args), setCoverEpoch: (...args) => setCoverEpoch(...args) }))

let prefs
beforeEach(async () => {
  api.mockReset()
  setCoverEpoch.mockReset()
  vi.unstubAllGlobals()
  vi.resetModules()
  prefs = await import('../preferences.js')
})

describe('尚未載入時', () => {
  it('一律當作未載入、全部關閉（讀取失敗不能退回個人版）', () => {
    expect(prefs.preferences()).toEqual({ edition: '', setup_done: false, adult_confirmed: false, save_covers: false, background_reader: false })
    expect(prefs.isPublic()).toBe(false)
    expect(prefs.isDesktop()).toBe(false)
  })
})

describe('載入與儲存', () => {
  it('loadPreferences 採用伺服器回傳並更新封面版本', async () => {
    api.mockResolvedValue({ edition: 'public', cover_epoch: 7, background_reader: true })
    const value = await prefs.loadPreferences()
    expect(api).toHaveBeenCalledWith('/preferences')
    expect(value.edition).toBe('public')
    expect(prefs.isPublic()).toBe(true)
    expect(prefs.preferences().background_reader).toBe(true)
    expect(setCoverEpoch).toHaveBeenCalledWith(7)
  })

  it('載入失敗時不改變目前設定', async () => {
    api.mockRejectedValue(new Error('down'))
    await expect(prefs.loadPreferences()).rejects.toThrow('down')
    expect(prefs.preferences().edition).toBe('')
  })

  it('savePreferences 以 PATCH 送出變更並採用回傳值', async () => {
    api.mockResolvedValue({ edition: 'personal', save_covers: true, cover_epoch: 2 })
    await prefs.savePreferences({ save_covers: true })
    expect(api).toHaveBeenCalledWith('/preferences', { method: 'PATCH', body: JSON.stringify({ save_covers: true }) })
    expect(prefs.preferences().save_covers).toBe(true)
    expect(prefs.isPublic()).toBe(false)
  })
})

describe('asks（要問哪些題目）', () => {
  const load = async value => { api.mockResolvedValue(value); await prefs.loadPreferences() }

  it('舊回傳沒有 platform／questions：當成手機，三題都問', async () => {
    await load({ edition: 'public' })
    expect(['adult_confirmed', 'save_covers', 'background_reader'].map(prefs.asks)).toEqual([true, true, true])
  })

  it('platform 為 desktop：不問背景讀取', async () => {
    await load({ edition: 'public', platform: 'desktop' })
    expect(prefs.isDesktop()).toBe(true)
    expect(prefs.asks('adult_confirmed')).toBe(true)
    expect(prefs.asks('save_covers')).toBe(true)
    expect(prefs.asks('background_reader')).toBe(false)
  })

  it('伺服器給了 questions 就以它為準（優先於平台預設）', async () => {
    await load({ edition: 'public', platform: 'android', questions: ['save_covers'] })
    expect(prefs.asks('save_covers')).toBe(true)
    expect(prefs.asks('adult_confirmed')).toBe(false)
  })

  it('questions 是空陣列：什麼都不問', async () => {
    await load({ edition: 'public', questions: [] })
    expect(prefs.asks('adult_confirmed')).toBe(false)
  })

  it('questions 不是陣列：退回平台預設', async () => {
    await load({ edition: 'public', platform: 'desktop', questions: 'save_covers' })
    expect(prefs.asks('background_reader')).toBe(false)
    expect(prefs.asks('adult_confirmed')).toBe(true)
  })

  it('未知題目名稱回傳 false', async () => {
    await load({ edition: 'public' })
    expect(prefs.asks('nope')).toBe(false)
  })
})

describe('本機封面', () => {
  it('storedCoverCount 取 covers 張數，非數字當 0', async () => {
    api.mockResolvedValueOnce({ covers: 12, files: 24 })
    expect(await prefs.storedCoverCount()).toBe(12)
    api.mockResolvedValueOnce({ covers: 'abc' })
    expect(await prefs.storedCoverCount()).toBe(0)
  })

  it('/covers/stored 失敗時退回 /covers/status 的 cached', async () => {
    api.mockRejectedValueOnce(new Error('404'))
    api.mockResolvedValueOnce({ cached: 5 })
    expect(await prefs.storedCoverCount()).toBe(5)
    expect(api).toHaveBeenLastCalledWith('/covers/status')
  })

  it('兩支都失敗時錯誤會往外丟（不吞掉）', async () => {
    api.mockRejectedValue(new Error('x'))
    await expect(prefs.storedCoverCount()).rejects.toThrow('x')
  })

  it('clearStoredCovers 回傳刪除數、更新封面版本、清手機暫存', async () => {
    const clearWebCache = vi.fn()
    vi.stubGlobal('window', { OASNative: { clearWebCache } })
    api.mockResolvedValue({ removed: 9, cover_epoch: 3 })
    expect(await prefs.clearStoredCovers()).toBe(9)
    expect(api).toHaveBeenCalledWith('/covers/clear', { method: 'POST' })
    expect(setCoverEpoch).toHaveBeenCalledWith(3)
    expect(clearWebCache).toHaveBeenCalledOnce()
  })

  it('電腦版沒有 OASNative 也能清', async () => {
    vi.stubGlobal('window', {})
    api.mockResolvedValue({ removed: 0, cover_epoch: 1 })
    expect(await prefs.clearStoredCovers()).toBe(0)
  })
})
