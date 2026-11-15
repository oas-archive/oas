// 公開版第一次啟動選的設定。其他版本由伺服器回傳全部開啟，行為和原本相同
import { api, setCoverEpoch } from './shared.jsx'

// 還沒從伺服器讀到之前一律當作「未載入（edition 空白）、全部關閉」：讀取失敗時不能退回成個人版（設定完成、背景讀取開啟），main.jsx 會改顯示錯誤與重試
let current = { edition: '', setup_done: false, adult_confirmed: false, save_covers: false, background_reader: false }

export const preferences = () => current
export const isPublic = () => current.edition === 'public'
const adopt = value => { current = value; setCoverEpoch(value.cover_epoch); return value }
export const loadPreferences = () => api('/preferences').then(adopt)
export const savePreferences = changes => api('/preferences', { method: 'PATCH', body: JSON.stringify(changes) }).then(adopt)

// 平台與要問的題目只在這裡讀：伺服器回傳 platform（"desktop" 或 "android"）與 questions（這台要問的題目名稱）。
// 舊的回傳沒有這兩個欄位：當成手機、三題都問，和原本一樣
const QUESTIONS = { android: ['adult_confirmed', 'save_covers', 'background_reader'], desktop: ['adult_confirmed', 'save_covers'] }
export const isDesktop = () => current.platform === 'desktop'
export const asks = name => (Array.isArray(current.questions) ? current.questions : QUESTIONS[isDesktop() ? 'desktop' : 'android']).includes(name)

// 已經存在本機的封面張數（選「不存」時問要不要刪）。/covers/stored 回 { covers: 張數, files: 檔案數（含縮圖）, bytes }；
// 伺服器沒有這支 API 時，退回維護頁用的封面統計
export const storedCoverCount = () => api('/covers/stored').then(result => Number(result.covers) || 0)
  .catch(() => api('/covers/status').then(result => Number(result.cached) || 0))
// 刪掉存在本機的封面（要先存成「不存」），回傳刪掉的檔案數。手機的瀏覽器也暫存過封面，要一起清掉
export const clearStoredCovers = () => api('/covers/clear', { method: 'POST' }).then(({ removed, cover_epoch }) => { setCoverEpoch(cover_epoch); window.OASNative?.clearWebCache?.(); return removed })
