// 手機 App 才有：在看不到的瀏覽器打開作品頁，把頁面 HTML 交回來，再交給伺服器依網站規則解析。
// 禁漫、MissAV 這類有防護的網站，伺服器自己連線會被擋，但手機上的瀏覽器看得到；解析規則仍只有伺服器那一份（metadata.py）。
import { preferences } from './preferences.js'

const waiting = {}
window.__oasPageDone = (id, html) => {
  const done = waiting[id]
  if (done) { delete waiting[id]; done(html || '') }
}

export const canReadPages = () => Boolean(window.OASNative?.readPage) && preferences().background_reader === true

/** 依序讀多件作品頁，交給伺服器補上空白欄位與標籤（/attention/fill-page）。rows：[{ id, url }]；onProgress(第幾件, 總數)。回傳補到幾件。 */
export async function fillFromPages(api, rows, onProgress = () => {}) {
  let filled = 0
  for (const [index, row] of rows.entries()) {
    onProgress(index + 1, rows.length)
    const html = await readPage(row.url)
    if (html) {
      const result = await api('/attention/fill-page', { method: 'POST', body: JSON.stringify({ id: row.id, html }) }).catch(() => null)
      if (result?.filled) filled += 1
    }
    if (index < rows.length - 1) await new Promise(done => setTimeout(done, 1500))  // 同一個網站不要連續狂讀
  }
  return filled
}

/** 讀一頁：回傳頁面 HTML；讀不到（不是手機 App、防護驗證沒過、逾時）回傳空字串。一次讀一頁，請依序呼叫。 */
export function readPage(url, timeout = 40000) {
  if (!canReadPages() || !/^https?:\/\//i.test(url || '')) return Promise.resolve('')
  return new Promise(resolve => {
    const id = Math.random().toString(36).slice(2)
    waiting[id] = resolve
    window.OASNative.readPage(id, url)
    setTimeout(() => window.__oasPageDone(id, ''), timeout)
  })
}
