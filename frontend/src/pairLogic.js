// 配對頁（pair.html）的純邏輯：整理配對碼、猜裝置名稱、等待秒數的說明。
// 只給配對頁用：主程式不要 import 這個檔，否則打包時會被拆成共用程式塊放進 assets/，還沒配對的裝置就載不到（見 vite.config.js）。

export const CODE_LENGTH = 8

// 貼上的內容可能帶空白、-、全形數字或「配對碼：」之類的字：全形轉半形，只留數字，最多 8 位
export const cleanCode = text => String(text ?? '')
  .replace(/[０-９]/g, digit => String.fromCharCode(digit.charCodeAt(0) - 0xfee0))
  .replace(/\D/g, '')
  .slice(0, CODE_LENGTH)

export const codeReady = code => new RegExp(`^\\d{${CODE_LENGTH}}$`).test(code)

// 顯示用：1234 5678
export const formatCode = code => (code.length > 4 ? `${code.slice(0, 4)} ${code.slice(4)}` : code)

// 依瀏覽器判斷預設的裝置名稱（使用者可以改）：例如「手機 Chrome」「iPad Safari」「電腦 Edge」
export function guessDeviceName(userAgent = '', maxTouchPoints = 0) {
  const ua = String(userAgent)
  let kind = '裝置'
  if (/iPhone|iPod/.test(ua)) kind = 'iPhone'
  else if (/iPad/.test(ua) || (/Macintosh/.test(ua) && maxTouchPoints > 1)) kind = 'iPad'  // iPadOS 的 Safari 預設回報成 Mac
  else if (/Android/.test(ua)) kind = /Mobile/.test(ua) ? '手機' : '平板'
  else if (/Windows|Macintosh|CrOS|Linux|X11/.test(ua)) kind = '電腦'
  let browser = ''
  if (/EdgA?\/|EdgiOS\//.test(ua)) browser = 'Edge'
  else if (/SamsungBrowser\//.test(ua)) browser = 'Samsung 瀏覽器'
  else if (/OPR\/|OPiOS\//.test(ua)) browser = 'Opera'
  else if (/Firefox\/|FxiOS\//.test(ua)) browser = 'Firefox'
  else if (/Chrome\/|CriOS\//.test(ua)) browser = 'Chrome'
  else if (/Safari\//.test(ua)) browser = 'Safari'
  return browser ? `${kind} ${browser}` : kind === '裝置' ? '未命名裝置' : kind
}

// 429 的 Retry-After（秒）；讀不懂時當 60 秒
export const retrySeconds = header => {
  const seconds = Number.parseInt(header, 10)
  return Number.isFinite(seconds) && seconds > 0 ? seconds : 60
}

export const waitText = seconds => (seconds >= 120 ? `約 ${Math.ceil(seconds / 60)} 分鐘` : `${seconds} 秒`)

// POST /api/pair 的結果 → 畫面要顯示的訊息（成功回 null）
export function pairMessage(status, retryAfter) {
  if (status === 200) return null
  if (status === 403) return '配對碼錯誤或已失效。請看電腦上目前顯示的碼，或在電腦上按「新增裝置」重新產生。'
  if (status === 429) return `嘗試太多次，請等 ${waitText(retryAfter)}後再試。`
  if (status === 404) return '電腦上的典藏館沒有開啟區網模式。請在電腦的「整理 → 其他裝置」開啟並重開典藏館。'
  if (status === 422) return '配對碼要是 8 位數字。'
  return '配對沒有完成，請稍後再試。'
}
