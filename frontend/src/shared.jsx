import { createPortal } from 'react-dom'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

export const PAGE_SIZE = 120
// 觸控螢幕（手機、平板）：沒有實體鍵盤與滑鼠，快捷鍵與按鍵提示用不到
export const TOUCH = Boolean(window.matchMedia?.('(hover: none) and (pointer: coarse)').matches)
// 手指從這個元素開始左右滑時，是否該交給元素自己（可左右捲動的區塊、評分星星、輸入框、畫布），不當成整頁的滑動手勢
// 把錯誤交給提示訊息：setMessage(errorNote(error)) 會一直顯示到手動關閉，不會幾秒就消失
export const errorNote = error => ({ text: error.message, error: true })

// 對話框的鍵盤焦點：打開時移進來、Tab 只在裡面循環、關閉時回到原本的位置（觸控螢幕不還原，免得跳出鍵盤）。可以疊好幾層，只有最上層生效
const focusTraps = []
const FOCUSABLE = 'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex]:not([tabindex="-1"])'
export function useFocusTrap(active = true, existingRef = null) {
  const ownRef = useRef(null)
  const ref = existingRef || ownRef
  useEffect(() => {
    const box = ref.current
    if (!active || !box) return undefined
    const previous = document.activeElement
    focusTraps.push(box)
    if (!box.contains(document.activeElement)) {
      if (!box.hasAttribute('tabindex')) box.setAttribute('tabindex', '-1')
      box.focus({ preventScroll: true })
    }
    const onKey = event => {
      if (event.key !== 'Tab' || focusTraps[focusTraps.length - 1] !== box) return
      const items = [...box.querySelectorAll(FOCUSABLE)].filter(element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden')
      if (!items.length) { event.preventDefault(); return }
      const first = items[0], last = items[items.length - 1], current = document.activeElement
      if (!box.contains(current) || current === box) { event.preventDefault(); (event.shiftKey ? last : first).focus() }
      else if (event.shiftKey && current === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && current === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      focusTraps.splice(focusTraps.indexOf(box), 1)
      if (!TOUCH && previous?.isConnected && previous !== document.body) previous.focus({ preventScroll: true })
    }
  }, [active])
  return ref
}

// App 自己的確認框（取代瀏覽器的 confirm）：await ask('訊息', { confirm: '刪除', danger: true })，回傳 true／false
let askHost = null
export const ask = (message, { confirm = '確定', cancel = '取消', danger = false } = {}) => new Promise(resolve => {
  if (!askHost) { resolve(window.confirm(message)); return }
  askHost({ message, confirm, cancel, danger, resolve })
})
export const confirmLeave = () => ask('內容還沒儲存，確定離開嗎？剛才輸入的內容會消失。', { confirm: '放棄內容', cancel: '繼續編輯', danger: true })
function AskDialog({ request, done }) {
  const ref = useFocusTrap()
  useEffect(() => {
    const onKey = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); done(false) } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])
  return createPortal(<div className="oas-modal ask-layer" onClick={event => event.target === event.currentTarget && done(false)}>
    <section ref={ref} className="oas-modal-card ask-card" role="alertdialog" aria-modal="true" aria-label="請確認">
      <p>{request.message}</p>
      <div className="smart-dialog-actions">
        <button type="button" className="ghost" onClick={() => done(false)}>{request.cancel}</button>
        <button type="button" autoFocus className={request.danger ? 'ghost danger is-strong-danger' : 'save'} onClick={() => done(true)}>{request.confirm}</button>
      </div>
    </section>
  </div>, document.body)
}
export function AskHost() {
  const [request, setRequest] = useState(null)
  useEffect(() => { askHost = setRequest; return () => { askHost = null } }, [])
  if (!request) return null
  return <AskDialog request={request} done={answer => { setRequest(null); request.resolve(answer) }} />
}

// 瀏覽器有空時才做（最多等 timeout 毫秒）；回傳取消函式
export const whenIdle = (callback, timeout = 800) => {
  if (!window.requestIdleCallback) { const timer = setTimeout(callback, 200); return () => clearTimeout(timer) }
  const handle = window.requestIdleCallback(callback, { timeout })
  return () => window.cancelIdleCallback(handle)
}
export const ownsSideSwipe = element => {
  for (let node = element; node && node !== document.body; node = node.parentElement) {
    if (['CANVAS', 'INPUT', 'TEXTAREA', 'SELECT'].includes(node.tagName) || node.getAttribute?.('role') === 'slider') return true
    if (node.scrollWidth > node.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(node).overflowX)) return true
  }
  return false
}
// 作品的評分、狀態、內容改了：通知各頁面（例如探索書架）更新自己保存的那份
export const announceItem = (id, values) => window.dispatchEvent(new CustomEvent('oas-item-changed', { detail: { id, values } }))
// 刪除作品後通知各自保存一份清單的頁面（首頁、待整理）拿掉它，不必等重新載入
export const announceRemoved = id => window.dispatchEvent(new CustomEvent('oas-item-removed', { detail: { id } }))
export const STATES = ['未讀', '閱讀中', '已完食', '暫時擱置', '棄坑']
export const PALETTES = ['#8c82ff', '#d17c9f', '#55abb2', '#d79a60', '#8f79bd', '#c96f6d']
export const EMPTY = { code: '', source_name: '其他網站', title: '', note: '', image_url: '', rating: 0, status: '未讀', tags: [] }

// 資料服務連不上（還在啟動、剛被關閉）時通知畫面顯示橫幅；502–504 是開發模式的 Vite 轉發找不到資料服務。
const reportOffline = () => window.dispatchEvent(new Event('oas-offline'))
export const api = (path, options = {}) => fetch(`/api${path}`, { headers: { 'Content-Type': 'application/json' }, ...options })
  .catch(() => { reportOffline(); throw new Error('連不上典藏館的資料服務') })
  .then(async response => {
    if (response.status >= 502 && response.status <= 504) { reportOffline(); throw new Error('典藏館的資料服務沒有回應') }
    if (!response.ok) {
      // 欄位驗證失敗（422）時 detail 是陣列，取出每一條的說明
      const detail = (await response.json().catch(() => ({}))).detail
      throw new Error((Array.isArray(detail) ? detail.map(entry => entry?.msg).filter(Boolean).join('；') : detail) || '操作未完成')
    }
    return response.status === 204 ? null : response.json()
  })

// 環境光取自封面主色（後端分析快取封面得出）；尚無主色時退回固定色盤。
export const colourFor = item => item?.cover_colour || PALETTES[(item?.id || 0) % PALETTES.length]
export const titleFor = item => item?.title || item?.code || '未命名典藏'
// 顯示用短標題（後端 titles.short_title 算好放在 short_title）：卡片、書架、側欄、搜尋建議用；
// 完整標題在作品頁、滑鼠提示、確認對話框與重複作品比對
export const shortTitle = item => item?.short_title || titleFor(item)
// 封面網址帶圖片網址的雜湊當版本：只有換封面才重抓（瀏覽器會快取七天）；改標題、標籤不必重新下載，換封面也不會看到舊圖
const coverVersion = url => { let hash = 0; for (let index = 0; index < url.length; index++) hash = (hash * 31 + url.charCodeAt(index)) | 0; return (hash >>> 0).toString(36) }
export const coverSrc = (item, width) => item?.id && item.image_url ? `/api/covers/${item.id}?v=${coverVersion(item.image_url)}${width ? `&w=${width}` : ''}` : item?.image_url
// 環境光：直接寫入 CSS 變數，不經 React 狀態，避免懸停時整頁重新渲染；捲動中不切換，避免捲動時連續重繪。
// 懸停只換背景光（.oas-ambient 自己的 --glow，漸變過去）；改 :root 的 --ambient 會讓整頁所有元素重算樣式，只在開啟作品、儲存時（force）才換。
let lastScrollAt = 0
if (typeof window !== 'undefined') window.addEventListener('scroll', () => { lastScrollAt = performance.now() }, { passive: true })
let ambientFrame = 0
let lastGlow = ''
export const setAmbientColour = (colour, force = false) => {
  if (!force && performance.now() - lastScrollAt < 180) return
  cancelAnimationFrame(ambientFrame)
  ambientFrame = requestAnimationFrame(() => {
    // 兩層交叉淡入：每次只畫一次新顏色，淡入只動 opacity。舊做法逐格改漸層顏色，整個畫面要重繪 0.6 秒，滑鼠移過卡片就一直發生
    const layers = document.querySelectorAll('.oas-ambient i')
    if (layers.length === 2 && colour !== lastGlow) {
      lastGlow = colour
      const next = layers[0].classList.contains('is-on') ? layers[1] : layers[0]
      next.style.setProperty('--glow', colour)
      next.classList.add('is-on')
      ;(next === layers[0] ? layers[1] : layers[0]).classList.remove('is-on')
    }
    if (force) document.documentElement.style.setProperty('--ambient', colour)
  })
}
// 開啟、切換作品時用：展示頁本身的顏色走 --cover-colour，環境光可以等封面動畫結束、底下的頁面收起來後再換，
// 重算樣式才不會擠在動畫中間；連續切換只換最後一件。
const AMBIENT_DELAY = 660
let ambientTimer = 0, ambientPending = null
export const flushAmbientColour = () => {
  clearTimeout(ambientTimer)
  if (ambientPending === null) return
  const colour = ambientPending
  ambientPending = null
  setAmbientColour(colour, true)
}
export const setAmbientColourSoon = colour => {
  ambientPending = colour
  clearTimeout(ambientTimer)
  ambientTimer = setTimeout(flushAmbientColour, AMBIENT_DELAY)
}
let hoveredItem = null
export const getHoveredItem = () => hoveredItem
let hoverTimer = 0
export const hoverAmbient = item => { hoveredItem = item; clearTimeout(hoverTimer); hoverTimer = setTimeout(() => setAmbientColour(colourFor(item)), 140) }
export const STATUS_COLOURS = { 未讀: '#8c93b8', 閱讀中: '#55abb2', 已完食: '#8fcf8a', 暫時擱置: '#d7a35f', 棄坑: '#c96f6d' }
// 平常選得到的狀態；「暫時擱置」「棄坑」收起來，只有作品本來就是這兩種時才列出（舊資料、其他人的備份可能有）
export const MAIN_STATES = ['未讀', '閱讀中', '已完食']
const statusOption = value => ({ value, label: value, colour: STATUS_COLOURS[value] })
export const STATUS_OPTIONS = MAIN_STATES.map(statusOption)
export const statusOptions = current => !current || MAIN_STATES.includes(current) ? STATUS_OPTIONS : [...STATUS_OPTIONS, statusOption(current)]
export const coverRatio = item => item?.cover_ratio && item.cover_ratio > 0.3 && item.cover_ratio < 3 ? item.cover_ratio : 4 / 3
export const hideBroken = event => { event.currentTarget.style.visibility = 'hidden' }
export const formatBytes = bytes => bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`
export const isEditing = () => ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable
export const linkFor = (item, sources) => {
  if (!item?.code) return ''
  if (/^https?:\/\//i.test(item.code)) return item.code
  const template = sources.find(source => source.name === item.source_name)?.template || ''
  // 只接受 http／https 的範本（備份可能帶來別的）；代碼可以含斜線（MissAV 的 actresses/名字），斜線不編碼，與後端 page_url 組出同一個網址
  if (!/^https?:\/\//i.test(template) || !template.includes('{}')) return ''
  return template.replace('{}', () => item.code.split('/').map(encodeURIComponent).join('/'))
}

// 評分星星：點一下或按住左右滑動，放開時存；有 allowHalf 時以半顆對齊。滑動中顯示即時分數，觸控螢幕每換一格輕震一下。
// 清除評分用旁邊的「清除」按鈕（小尺寸的卡片上不顯示），不再「點同一個分數就清除」，手機上連點才不會誤清。
export function Stars({ value = 0, onChange, allowHalf = false, small = false, id }) {
  const [preview, setPreview] = useState(null)
  const [dragging, setDragging] = useState(false)
  const row = useRef(null)
  const step = allowHalf ? 0.5 : 1
  const shown = preview ?? value
  const at = event => {  // 手指或滑鼠的位置換成分數（最少一個 step，最多 5）
    const rect = row.current.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
    return Math.max(step, Math.ceil(ratio * 5 / step - 1e-9) * step)
  }
  const show = next => setPreview(current => {
    if (current !== next && dragging && navigator.vibrate) navigator.vibrate(8)
    return next
  })
  const onPointerDown = event => {
    if (event.button > 0) return
    event.stopPropagation()
    row.current.setPointerCapture?.(event.pointerId)
    setDragging(true)
    setPreview(at(event))
  }
  const onPointerMove = event => {
    if (dragging) show(at(event))
    else if (event.pointerType === 'mouse') setPreview(at(event))  // 滑鼠移過時預覽；手指沒有「移過」，不預覽
  }
  const onPointerUp = event => {
    if (!dragging) return
    event.stopPropagation()
    setDragging(false)
    const next = at(event)
    setPreview(event.pointerType === 'mouse' ? next : null)
    if (Math.abs(next - value) > 0.01) onChange(next)
  }
  const onPointerCancel = () => { setDragging(false); setPreview(null) }  // 改成上下捲動頁面時取消
  const onKeyDown = event => {
    const keys = { ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step }
    if (keys[event.key] !== undefined) { event.preventDefault(); event.stopPropagation(); onChange(Math.min(5, Math.max(0, value + keys[event.key]))) }
    else if (/^[0-5]$/.test(event.key)) { event.preventDefault(); event.stopPropagation(); onChange(Number(event.key)) }
  }
  return <span className={`stars-wrap${small ? ' is-small' : ''}`}>
    <span id={id} ref={row} className={`stars${small ? ' is-small' : ''}${dragging ? ' is-dragging' : ''}`} role="slider" tabIndex={0} aria-label="評分" aria-valuemin={0} aria-valuemax={5} aria-valuenow={value} aria-valuetext={value ? `${value} 顆星` : '未評分'}
      onKeyDown={onKeyDown} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerCancel}
      onPointerLeave={event => { if (!dragging && event.pointerType === 'mouse') setPreview(null) }} onClick={event => event.stopPropagation()}>
      {[0, 1, 2, 3, 4].map(index => <span key={index} className="star" aria-hidden="true">
        <span className="star-base">★</span><span className="star-fill" style={{ clipPath: `inset(0 ${100 - Math.max(0, Math.min(1, shown - index)) * 100}% 0 0)` }}>★</span>
      </span>)}
      {dragging && <output className="stars-live">{shown.toFixed(allowHalf ? 1 : 0)}</output>}
    </span>
    {!small && value > 0 && <button type="button" className="stars-clear" onClick={event => { event.stopPropagation(); onChange(0) }}>清除</button>}
  </span>
}

export function Dropdown({ value, options, onChange, ariaLabel, small = false }) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [position, setPosition] = useState(null)
  const root = useRef(null)
  const menu = useRef(null)
  const current = options.find(option => option.value === value) || options[0]
  useLayoutEffect(() => {
    if (!open) return
    // 選單以 portal 固定定位，不會被卡片封面的 overflow 裁切；空間不足時改向上展開。
    const place = () => {
      const rect = root.current.getBoundingClientRect()
      const height = Math.min(300, options.length * 36 + 12)
      const below = window.innerHeight - rect.bottom > height + 12
      setPosition({ left: Math.min(rect.left, window.innerWidth - 200), top: below ? rect.bottom + 6 : rect.top - height - 6, minWidth: Math.max(rect.width, 150) })
    }
    place()
    const close = event => { if (!root.current?.contains(event.target) && !menu.current?.contains(event.target)) setOpen(false) }
    const dismiss = () => setOpen(false)
    document.addEventListener('pointerdown', close)
    window.addEventListener('scroll', dismiss, { passive: true, capture: true })
    window.addEventListener('resize', dismiss)
    return () => { document.removeEventListener('pointerdown', close); window.removeEventListener('scroll', dismiss, { capture: true }); window.removeEventListener('resize', dismiss) }
  }, [open, options.length])
  const choose = option => { setOpen(false); if (option.value !== value) onChange(option.value) }
  const onKeyDown = event => {
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); return }
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault(); event.stopPropagation()
      if (!open) { setOpen(true); setActive(Math.max(0, options.indexOf(current))); return }
      setActive(index => (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length)
    }
    if ((event.key === 'Enter' || event.key === ' ') && open) { event.preventDefault(); event.stopPropagation(); choose(options[active]) }
  }
  return <div ref={root} className={`dropdown${small ? ' is-small' : ''}${open ? ' is-open' : ''}`} onKeyDown={onKeyDown} onClick={event => event.stopPropagation()}>
    <button type="button" className="dropdown-trigger" aria-haspopup="listbox" aria-expanded={open} aria-label={current?.label ? `${ariaLabel}：${current.label}` : ariaLabel} onClick={() => { setOpen(!open); setActive(Math.max(0, options.indexOf(current))) }}>
      {current?.colour && <i className="status-dot" style={{ background: current.colour }} />}<span>{current?.label}</span><em aria-hidden="true">⌄</em>
    </button>
    {open && position && createPortal(<ul ref={menu} className="dropdown-menu" role="listbox" aria-label={ariaLabel} style={position} onClick={event => event.stopPropagation()}>
      {options.map((option, index) => <li key={option.value || 'all'} role="option" aria-selected={option.value === value} className={`${index === active ? 'is-active' : ''}${option.value === value ? ' is-current' : ''}`} onMouseEnter={() => setActive(index)} onClick={() => choose(option)}>
        {option.colour && <i className="status-dot" style={{ background: option.colour }} />}{option.label}{option.value === value && <b aria-hidden="true">✓</b>}
      </li>)}
    </ul>, document.body)}
  </div>
}

// 標籤分類（來自 nhentai 等來源）：顯示順序與中文名稱。分類對照只取一次並快取。
export const TAG_CATEGORIES = [['artist', '作者'], ['group', '社團'], ['parody', '原作'], ['character', '角色'], ['tag', '標籤'], ['category', '類別'], ['language', '語言']]
export const CATEGORY_NAMES = Object.fromEntries(TAG_CATEGORIES)
// 使用者自己寫的筆記（去掉系統自動加的「原連結」「合併自」紀錄）；空字串代表沒有筆記
export const noteText = item => (item?.note || '').split('\n').filter(line => line.trim() && !/^(原連結|合併自)：/.test(line.trim())).join(' ')
let categoryCache = null
export const loadTagCategories = (refresh = false) => {
  if (!categoryCache || refresh) categoryCache = api('/tags/categories').catch(() => ({}))
  return categoryCache
}
export const groupTags = (names, categories) => TAG_CATEGORIES
  .map(([key, label]) => ({ key, label, names: names.filter(name => (categories[name] || 'tag') === key) }))
  .filter(group => group.names.length)
export const KINDS = ['漫畫', '動畫', '影片', '創作者', '網站', '其他']

// 沒有封面或封面只是頁面圖示時的顯示：創作者用圓形頭像、網站用置中圖示，都沒有時顯示依類型的佔位圖與網站名稱。
const KIND_GLYPHS = { 漫畫: '❏', 動畫: '✦', 影片: '▶', 創作者: '◉', 其他: '⌂' }
export const siteName = item => {
  if (item?.source_name && item.source_name !== '其他網站') return item.source_name
  try { return new URL(item.code).hostname.replace(/^www[0-9]*\./, '') } catch { return item?.source_name || '' }
}
// 創作者頁標題常是「名字的色情片 | 網站」，頭像下方只顯示名字。
const creatorName = item => titleFor(item).split(/[|｜]/)[0].trim().replace(/(的?色情片.*|'s Porn Videos.*| Porn Videos.*)$/i, '').trim().slice(0, 40)
export const isFaceCover = item => !item?.image_url || item.image_kind === 'avatar' || item.image_kind === 'icon'
export function CoverFace({ item, width = 360 }) {
  const name = siteName(item)
  const [failed, setFailed] = useState('')  // 圖片載入失敗時退回佔位圖
  const fail = () => setFailed(item.image_url)
  if (failed === item.image_url) return <div className="cover-face is-empty"><i aria-hidden="true">{KIND_GLYPHS[item.kind] || '⌂'}</i><b>{item.image_kind === 'avatar' ? creatorName(item) : name}</b><small>{item.kind || '尚無封面'}</small></div>
  if (item.image_url && item.image_kind === 'avatar') return <div className="cover-face is-avatar"><img src={coverSrc(item, width)} alt="" loading="lazy" onError={fail} /><b>{creatorName(item)}</b><small>{name}</small></div>
  if (item.image_url && item.image_kind === 'icon') return <div className="cover-face is-icon"><img src={coverSrc(item)} alt="" loading="lazy" onError={fail} onLoad={event => event.currentTarget.naturalWidth < 2 && fail()} /><b>{name}</b><small>{item.kind || '網站'}</small></div>
  return <div className="cover-face is-empty"><i aria-hidden="true">{KIND_GLYPHS[item.kind] || '⌂'}</i><b>{name}</b><small>{item.kind || '尚無封面'}</small></div>
}
