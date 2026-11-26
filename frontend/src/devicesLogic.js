// 「整理 → 其他裝置」的純邏輯：配對網址、倒數、QR 的 SVG 路徑、事件與狀態的中文說明。畫面在 DevicesPanel.jsx

// QR 內容：其他裝置要開的網址（還沒配對的裝置開首頁就會拿到配對頁）。IPv6 要加方括號
export const pairUrl = (address, port) => {
  if (!address) return ''
  const host = address.includes(':') ? `[${address}]` : address
  return `http://${host}${port && Number(port) !== 80 ? `:${port}` : ''}/`
}

export const secondsLeft = (expiresAt, now = Date.now()) => {
  const end = Date.parse(expiresAt)
  return Number.isFinite(end) ? Math.max(0, Math.ceil((end - now) / 1000)) : 0
}

export const countdownText = seconds => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

// uqr 的 encode() 結果（data: boolean[][]）→ 一條 SVG 路徑，每個深色格子一個 1×1 方塊；margin 是四周留白的格數
export const qrPath = (data, margin = 0) => {
  let path = ''
  data.forEach((row, y) => row.forEach((dark, x) => { if (dark) path += `M${x + margin} ${y + margin}h1v1h-1z` }))
  return path
}

export const LEVELS = [['standard', '標準'], ['readonly', '唯讀']]
export const LEVEL_HINTS = { standard: '可以瀏覽、收錄、編輯、評分、整理', readonly: '只能瀏覽、搜尋，不能修改任何東西' }
export const levelLabel = level => LEVELS.find(([value]) => value === level)?.[1] || level

export const STATUS_LABELS = { active: '有效', revoked: '已撤銷', expired: '已過期（90 天沒用）' }

export const EVENT_LABELS = {
  code_created: '產生配對碼', code_cancelled: '取消配對碼', code_voided: '配對碼猜錯太多次，已作廢',
  paired: '配對成功', pair_failed: '配對碼錯誤', pair_blocked: '嘗試太多次，暫停配對',
  unauthorized: '沒配對的裝置來要資料', revoked: '撤銷裝置', revoked_all: '全部撤銷',
  level_changed: '改權限', remote_on: '開啟區網模式', remote_off: '關閉區網模式',
}
export const eventLabel = kind => EVENT_LABELS[kind] || kind
// 事件的細節：裝置名稱、位址、其他說明（級別換成中文；配對成功的 detail 就是裝置名稱，不重複列）
export const eventDetail = event => [...new Set([event.device_name, event.ip, LEVELS.some(([value]) => value === event.detail) ? levelLabel(event.detail) : event.detail].filter(Boolean))].join('、')
// 需要注意的事件（畫面上用琥珀色）
export const eventWarns = kind => ['pair_failed', 'pair_blocked', 'code_voided', 'unauthorized'].includes(kind)

// 最近一次配對碼的下場是「猜錯 5 次作廢」時，回傳 { time, ips }（ips：這組碼產生後猜錯的來源位址），否則 null。
// events 是新的在前（GET /api/devices/events）；擁有者看到就知道有裝置在猜碼，確認後再重新產生
const CODE_EVENTS = ['code_created', 'code_cancelled', 'code_voided', 'paired']
export const voidedAlert = (events = []) => {
  const latest = events.findIndex(event => CODE_EVENTS.includes(event.kind))
  if (latest < 0 || events[latest].kind !== 'code_voided') return null
  const created = events.findIndex((event, index) => index > latest && event.kind === 'code_created')
  const window = events.slice(0, created < 0 ? events.length : created)
  const ips = [...new Set(window.filter(event => ['code_voided', 'pair_failed', 'pair_blocked'].includes(event.kind)).map(event => event.ip).filter(Boolean))]
  return { time: events[latest].time, ips }
}

const pad = value => String(value).padStart(2, '0')
// 2026-11-26T08:30:00+00:00 → 「11/26 16:30」（本機時區）；今年以外加上年份
export const shortTime = (iso, now = new Date()) => {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const day = `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
  return date.getFullYear() === now.getFullYear() ? day : `${date.getFullYear()}/${day}`
}

// 有效的在前（伺服器已排好）；畫面上已撤銷、過期的收在後面
export const splitDevices = items => ({ active: items.filter(item => item.status === 'active'), inactive: items.filter(item => item.status !== 'active') })

// ---------- 新版頁面（樣本 A，2026-11-26）：頁首狀態、相對時間、裝置圖示、開啟後的檢查清單 ----------

// 頁首的一句狀態。tone：on 開啟中、wait 等重開、off 關閉
export const remoteStatus = (remote, activeCount = 0) => {
  if (!remote) return { tone: 'off', title: '讀取中…', sub: '' }
  const { enabled: now, enabled_next: next } = remote
  if (next && now) return { tone: 'on', title: '家裡的其他裝置可以連進來', sub: `區網模式開啟中，${activeCount} 台裝置可用` }
  if (next) return { tone: 'wait', title: '已設定開啟，重開典藏館後生效', sub: '關掉典藏館視窗再打開，其他裝置才連得到' }
  if (now) return { tone: 'wait', title: '已設定關閉，重開典藏館後生效', sub: '重開之前，已配對的裝置仍然連得進來' }
  return { tone: 'off', title: '只有這台電腦能用典藏館', sub: '開啟區網模式後，家裡的手機、平板也能用瀏覽器連進來' }
}

// 「新增裝置」為什麼不能按（可以按時回傳空字串）
export const addBlockedReason = (remote, checks) => {
  if (!remote) return '讀取中…'
  if (!remote.enabled_next) return remote.enabled ? '區網模式已設定關閉，不能再新增裝置。' : '開啟區網模式後才能新增裝置。'
  if (!remote.enabled) return '重開典藏館後才能新增裝置。'
  if (!checks) return '正在檢查這台電腦的設定…'
  if (!checksPass(checks)) return '上面的檢查都通過後才能新增裝置。'
  return ''
}

// 最後連線：5 分鐘內算「正在使用」，其他用「3 分鐘前」「2 小時前」「4 天前」；超過 30 天改顯示日期
export const relativeTime = (iso, now = Date.now()) => {
  if (!iso) return '還沒連線'
  const time = Date.parse(iso)
  if (!Number.isFinite(time)) return '—'
  const minutes = Math.max(0, Math.floor((now - time) / 60000))
  if (minutes < 1) return '剛剛'
  if (minutes < 60) return `${minutes} 分鐘前`
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)} 小時前`
  if (minutes < 60 * 24 * 30) return `${Math.floor(minutes / 1440)} 天前`
  return shortTime(iso, new Date(now))
}
export const inUse = (iso, now = Date.now()) => Boolean(iso) && now - Date.parse(iso) < 5 * 60000

// 裝置圖示：從名稱猜（配對頁預設的名稱像「手機 Chrome」「iPad Safari」「Windows Edge」）
export const deviceKind = (name = '') => {
  if (/平板|ipad|tablet|tab\b/i.test(name)) return 'tablet'
  if (/電腦|筆電|windows|mac|linux|pc\b|laptop|desktop|chromebook/i.test(name)) return 'laptop'
  return 'phone'
}

// 檢查清單（GET /api/devices/checks）：status 是 ok／fail／unknown；unknown（無法偵測）不擋，只有 fail 擋「新增裝置」
export const CHECK_KEYS = ['network', 'firewall', 'restart', 'proxy']
export const checksPass = checks => Boolean(checks) && CHECK_KEYS.every(key => checks[key]?.status !== 'fail')
export const checksSummary = checks => {
  const statuses = CHECK_KEYS.map(key => checks?.[key]?.status)
  const ok = statuses.filter(status => status === 'ok').length
  const fail = statuses.filter(status => status === 'fail').length
  if (fail) return `還有 ${fail} 項要處理`
  return ok === CHECK_KEYS.length ? '全部通過' : `${ok} 項通過，其他無法偵測`
}

const names = list => list.map(item => `「${item}」`).join('、')
// 每一項的標題與說明。action：這一項可以按的按鈕（network-settings 開 Windows 設定、firewall 跑防火牆設定）
export const checkItem = (key, checks) => {
  const item = checks?.[key] || { status: 'unknown' }
  const { status } = item
  if (key === 'network') {
    const publicOnes = (item.networks || []).filter(network => network.category === 'public').map(network => network.name || '未命名的網路')
    const privateOnes = (item.networks || []).filter(network => network.category === 'private').map(network => network.name || '未命名的網路')
    return {
      key, status, title: '網路是私人網路',
      detail: status === 'ok' ? `目前的網路${names(privateOnes)}是私人網路。`
        : status === 'fail' ? `${names(publicOnes)}是公用網路，防火牆規則在那裡不生效，別人也比較容易連進來。家裡的網路請改成「私人網路」：Windows 設定 → 網路和網際網路 → 內容。`
          : '無法偵測網路類型。請自己確認這台電腦連的是家裡的網路，而且設成「私人網路」。',
      action: status === 'ok' ? null : 'network-settings',
    }
  }
  if (key === 'firewall') {
    const port = item.port || 8080
    const extra = []
    if (item.public_python_rules?.length) extra.push(`另外有 ${item.public_python_rules.length} 條 Python 的舊規則允許「公用網路」連進來（Windows 第一次跳「允許存取」時建的）。建議到 Windows 設定 → 隱私權與安全性 → Windows 安全性 → 防火牆與網路保護 → 允許應用程式通過防火牆，找到 Python，把「公用」取消勾選。典藏館不會自動改。`)
    return {
      key, status, title: '防火牆已設定',
      detail: status === 'ok' ? `已有「OAS 區網」規則：只在私人網路、TCP ${port}、只接受同一個區網。`
        : status === 'fail' ? `${(item.problems || []).join('；')}。按「設定防火牆」，Windows 會詢問是否允許（選「是」），再照視窗的說明做。`
          : '無法偵測防火牆。請自己確認用系統管理員身分執行過 tools\enable-lan-firewall.ps1。',
      extra, action: status === 'ok' ? null : 'firewall',
    }
  }
  if (key === 'restart') {
    return {
      key, status, title: '已用區網模式重開',
      detail: status === 'ok' ? '這次啟動已經是區網模式，其他裝置連得進來。' : '關掉典藏館視窗再打開（關閉時會自動備份），其他裝置才連得到。',
      action: null,
    }
  }
  const entries = (item.entries || []).map(entry => `${entry.listen} → ${entry.connect}`)
  return {
    key: 'proxy', status, title: '沒有代理或埠轉送',
    detail: status === 'fail' ? `發現指到 ${item.port || 8080} 埠的埠轉送（${entries.join('、')}）：經過它的連線會被當成這台電腦本人，不用配對就能做所有事。請用系統管理員身分執行 netsh interface portproxy delete v4tov4 listenport=<埠> listenaddress=<位址> 刪掉。`
      : `${status === 'ok' ? '沒有發現埠轉送。' : '無法偵測埠轉送。'}代理軟體偵測不到：若有開「允許區網連線」，或用 ssh -L、WSL／Docker 轉送這個埠，請先關掉。`,
    action: null,
  }
}
