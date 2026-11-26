// 配對頁的純邏輯：整理配對碼、猜裝置名稱、等待秒數
import { describe, expect, it } from 'vitest'
import { cleanCode, codeReady, formatCode, guessDeviceName, pairMessage, retrySeconds, waitText } from '../pairLogic.js'

const UA = {
  androidChrome: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36',
  androidTablet: 'Mozilla/5.0 (Linux; Android 13; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  samsung: 'Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile Safari/537.36',
  iphoneSafari: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1',
  iphoneChrome: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1',
  ipadDesktop: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15',
  windowsEdge: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
  windowsFirefox: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0',
}

describe('cleanCode', () => {
  it('去掉空白、-、全形數字與其他字，最多 8 位', () => {
    expect(cleanCode('1234 5678')).toBe('12345678')
    expect(cleanCode(' 1234-5678 ')).toBe('12345678')
    expect(cleanCode('１２３４－５６７８')).toBe('12345678')
    expect(cleanCode('配對碼：1234 5678')).toBe('12345678')
    expect(cleanCode('123456789')).toBe('12345678')
    expect(cleanCode(null)).toBe('')
  })
  it('8 位數字才算完整', () => {
    expect(codeReady('12345678')).toBe(true)
    expect(codeReady('1234567')).toBe(false)
    expect(codeReady('1234567a')).toBe(false)
  })
  it('顯示成四位一組', () => {
    expect(formatCode('12345678')).toBe('1234 5678')
    expect(formatCode('123')).toBe('123')
  })
})

describe('guessDeviceName', () => {
  it('依瀏覽器判斷裝置與瀏覽器', () => {
    expect(guessDeviceName(UA.androidChrome)).toBe('手機 Chrome')
    expect(guessDeviceName(UA.androidTablet)).toBe('平板 Chrome')
    expect(guessDeviceName(UA.samsung)).toBe('手機 Samsung 瀏覽器')
    expect(guessDeviceName(UA.iphoneSafari)).toBe('iPhone Safari')
    expect(guessDeviceName(UA.iphoneChrome)).toBe('iPhone Chrome')
    expect(guessDeviceName(UA.windowsEdge)).toBe('電腦 Edge')
    expect(guessDeviceName(UA.windowsFirefox)).toBe('電腦 Firefox')
  })
  it('iPadOS 的 Safari 回報成 Mac：有觸控就當 iPad', () => {
    expect(guessDeviceName(UA.ipadDesktop, 5)).toBe('iPad Safari')
    expect(guessDeviceName(UA.ipadDesktop, 0)).toBe('電腦 Safari')
  })
  it('認不得時給一個能改的名稱', () => {
    expect(guessDeviceName('')).toBe('未命名裝置')
    expect(guessDeviceName('SomeTV/1.0')).toBe('未命名裝置')
  })
})

describe('配對結果的訊息', () => {
  it('Retry-After 讀不懂時當 60 秒', () => {
    expect(retrySeconds('42')).toBe(42)
    expect(retrySeconds(null)).toBe(60)
    expect(retrySeconds('soon')).toBe(60)
  })
  it('429 顯示要等多久', () => {
    expect(waitText(30)).toBe('30 秒')
    expect(waitText(600)).toBe('約 10 分鐘')
    expect(pairMessage(429, 42)).toContain('42 秒')
  })
  it('403 不分原因、成功沒有訊息', () => {
    expect(pairMessage(403)).toContain('錯誤或已失效')
    expect(pairMessage(200)).toBe(null)
    expect(pairMessage(404)).toContain('區網模式')
  })
})
