// 配對頁（pair.html）：區網模式下還沒配對的裝置只會拿到這一頁（後端 frontend.py）。
// 不用 React、不載主程式：輸入電腦上顯示的 8 位數配對碼與裝置名稱，POST /api/pair 拿到 cookie 後進主畫面。
import { cleanCode, codeReady, formatCode, guessDeviceName, pairMessage, retrySeconds, waitText } from './pairLogic.js'

const form = document.getElementById('pair-form')
const codeInput = document.getElementById('pair-code')
const nameInput = document.getElementById('pair-name')
const button = document.getElementById('pair-submit')
const note = document.getElementById('pair-note')

nameInput.value = guessDeviceName(navigator.userAgent, navigator.maxTouchPoints || 0)
let waitUntil = 0
let busy = false

const show = (text, kind = 'error') => {
  note.textContent = text || ''
  note.dataset.kind = kind
}

const refresh = () => {
  const waiting = waitUntil > Date.now()
  button.disabled = busy || waiting || !codeReady(cleanCode(codeInput.value))
  button.textContent = busy ? '配對中…' : waiting ? `${waitText(Math.ceil((waitUntil - Date.now()) / 1000))}後可再試` : '配對並進入典藏館'
}

// 輸入或貼上時整理成「1234 5678」：去掉空白、-、全形數字與其他字
codeInput.addEventListener('input', () => {
  const code = cleanCode(codeInput.value)
  const shown = formatCode(code)
  if (codeInput.value !== shown) codeInput.value = shown
  if (note.dataset.kind === 'error' && waitUntil <= Date.now()) show('')
  refresh()
})

const countdown = () => {
  refresh()
  if (waitUntil > Date.now()) setTimeout(countdown, 1000)
  else show('')
}

form.addEventListener('submit', async event => {
  event.preventDefault()
  const code = cleanCode(codeInput.value)
  if (!codeReady(code) || busy || waitUntil > Date.now()) return
  busy = true
  show('')
  refresh()
  let response = null
  try {
    response = await fetch('/api/pair', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      body: JSON.stringify({ code, name: nameInput.value.trim().slice(0, 60) }),
    })
  } catch {
    show('連不上電腦上的典藏館。請確認電腦開著典藏館視窗、這台裝置和電腦連在同一個 Wi-Fi。')
  }
  busy = false
  if (response?.ok) {
    show('配對完成，正在進入典藏館…', 'ok')
    window.location.replace('/')
    return
  }
  if (response) {
    const seconds = retrySeconds(response.headers.get('Retry-After'))
    if (response.status === 429) { waitUntil = Date.now() + seconds * 1000; setTimeout(countdown, 1000) }
    show(pairMessage(response.status, seconds))
    if (response.status === 403) { codeInput.select() }
  }
  refresh()
})

refresh()
