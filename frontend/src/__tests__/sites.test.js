import { describe, expect, it } from 'vitest'
import { JM_SEARCH, JM_SOURCE, PROTECTED, PROTECTED_LABEL } from '../sites.js'

describe('PROTECTED（要靠手機瀏覽器讀的網站）', () => {
  it.each([
    'https://18comic.vip/album/100001',
    'https://www.jmcomic.me/album/100001',
    'https://missav.ws/dm1/abc',
    'https://MISSAV.example/ABC',   // 大小寫不分
    'HTTPS://18COMIC.EXAMPLE/x',
  ])('認得有防護的網址 %s', url => {
    expect(PROTECTED.test(url)).toBe(true)
  })

  it.each([
    'https://example.com/g/100001/',
    'https://nhentai.example/g/1/',
    '',
    'not a url',
  ])('不認得一般網址 "%s"', url => {
    expect(PROTECTED.test(url)).toBe(false)
  })

  it('沒有 g 旗標：連續呼叫 test 結果穩定（不會因 lastIndex 交錯）', () => {
    const url = 'https://18comic.vip/album/1'
    expect([PROTECTED.test(url), PROTECTED.test(url), PROTECTED.test(url)]).toEqual([true, true, true])
  })
})

describe('禁漫常數', () => {
  it('搜尋網址以 search_query= 結尾，可直接接關鍵字，且屬於有防護網站', () => {
    expect(JM_SEARCH.endsWith('search_query=')).toBe(true)
    expect(PROTECTED.test(JM_SEARCH)).toBe(true)
  })
  it('來源名稱與進度列標籤是非空字串', () => {
    expect(JM_SOURCE).toBeTruthy()
    expect(PROTECTED_LABEL).toBeTruthy()
  })
})
