// 這台裝置能做什麼（access.js）：電腦本機、標準、唯讀；401 什麼時候要回配對頁
import { afterEach, describe, expect, it } from 'vitest'
import { accessFrom, canWrite, currentAccess, isLocalMachine, needsPairing, setAccess } from '../access.js'

afterEach(() => setAccess({ remote: false, access: 'readonly', device: null }))

describe('accessFrom', () => {
  it('照伺服器回傳的級別', () => {
    expect(accessFrom(200, { remote: true, access: 'standard', device: { id: 1 } })).toEqual({ remote: true, access: 'standard', device: { id: 1 } })
    expect(accessFrom(200, { remote: false, access: 'local', device: null }).access).toBe('local')
  })
  it('手機 App 與舊版沒有這支 API（404）：當本機，和沒有這功能時一樣', () => {
    expect(accessFrom(404, null)).toEqual({ remote: false, access: 'local', device: null })
  })
  it('認不得的級別當唯讀（最嚴）', () => {
    expect(accessFrom(200, { access: 'none' }).access).toBe('readonly')
    expect(accessFrom(200, {}).access).toBe('readonly')
  })
})

describe('權限判斷', () => {
  it('本機全部可用；標準可寫但不是本機；唯讀都不行', () => {
    const local = { access: 'local' }, standard = { access: 'standard' }, readonly = { access: 'readonly' }
    expect([canWrite(local), isLocalMachine(local)]).toEqual([true, true])
    expect([canWrite(standard), isLocalMachine(standard)]).toEqual([true, false])
    expect([canWrite(readonly), isLocalMachine(readonly)]).toEqual([false, false])
  })
  it('還沒讀到之前當最嚴', () => {
    expect(currentAccess().access).toBe('readonly')
    expect(canWrite()).toBe(false)
    setAccess({ remote: true, access: 'standard', device: null })
    expect(canWrite()).toBe(true)
    expect(isLocalMachine()).toBe(false)
  })
})

describe('needsPairing', () => {
  it('只有 401 且 pair: true 才回配對頁', () => {
    expect(needsPairing(401, { pair: true })).toBe(true)
    expect(needsPairing(401, { detail: '缺少典藏館的通行碼' })).toBe(false)  // 手機 App 的通行碼，不是區網配對
    expect(needsPairing(403, { pair: true })).toBe(false)
    expect(needsPairing(401, null)).toBe(false)
  })
})
