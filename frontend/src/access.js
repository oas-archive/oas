// 這台裝置能做什麼（多裝置登入）：電腦本機全部可用；區網的其他裝置分「標準」與「唯讀」。
// main.jsx 開畫面前讀一次 GET /api/devices/me；各畫面用 canWrite()、isLocalMachine() 決定要不要顯示按鈕，不要等按了才報錯。

// 還沒讀到之前當成最嚴（唯讀、不是本機）：讀取失敗時不能退回成「全部可用」
let current = { remote: false, access: 'readonly', device: null }

// 伺服器回傳 → 整理過的狀態。手機 App 與沒有這支 API 的舊版回 404：那就是本機（和沒有這功能時一樣）
export const accessFrom = (status, body) => {
  if (status === 404) return { remote: false, access: 'local', device: null }
  const level = body?.access
  return { remote: Boolean(body?.remote), access: ['local', 'standard', 'readonly'].includes(level) ? level : 'readonly', device: body?.device || null }
}

export const currentAccess = () => current
export const isLocalMachine = (state = current) => state.access === 'local'
export const canWrite = (state = current) => state.access === 'local' || state.access === 'standard'
// 只限電腦本機的功能（還原、備份下載、清封面、來源規則、匯入標籤包、裝置管理）：其他裝置一律不顯示入口
export const localOnly = isLocalMachine

export const setAccess = state => {
  current = state
  if (typeof document !== 'undefined') document.documentElement.dataset.access = state.access
  return state
}

// 不經 api()：404 要當成本機，不能變成錯誤；401（還沒配對）交給 api() 一樣轉去配對頁
export const loadAccess = () => fetch('/api/devices/me', { headers: { 'Content-Type': 'application/json' } })
  .then(async response => {
    if (response.status === 401) { const body = await response.json().catch(() => ({})); if (needsPairing(401, body)) goPair() }
    if (!response.ok && response.status !== 404) throw new Error('讀不到這台裝置的權限')
    return setAccess(accessFrom(response.status, response.ok ? await response.json() : null))
  })

// 主畫面任何 API 回 401 且 pair: true（區網模式下這台裝置沒配對或被撤銷）→ 回到首頁，伺服器會改送配對頁
export const needsPairing = (status, body) => status === 401 && body?.pair === true
let leaving = false
export const goPair = () => {
  if (leaving) return
  leaving = true
  window.location.replace('/')
}
