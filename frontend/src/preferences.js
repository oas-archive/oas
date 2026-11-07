// 公開版第一次啟動選的設定。其他版本由伺服器回傳全部開啟，行為和原本相同
import { api } from './shared.jsx'

let current = { edition: 'personal', setup_done: true, adult_confirmed: true, save_covers: true, background_reader: true }

export const preferences = () => current
export const isPublic = () => current.edition === 'public'
export const loadPreferences = () => api('/preferences').then(value => { current = value; return value })
export const savePreferences = changes => api('/preferences', { method: 'PATCH', body: JSON.stringify(changes) }).then(value => { current = value; return value })
