import { createPortal } from 'react-dom'
import { useLayoutEffect, useRef, useState } from 'react'

export const PAGE_SIZE = 120
export const STATES = ['未讀', '閱讀中', '已完食', '暫時擱置', '棄坑']
export const PALETTES = ['#8c82ff', '#d17c9f', '#55abb2', '#d79a60', '#8f79bd', '#c96f6d']
export const EMPTY = { code: '', source_name: '其他網站', title: '', note: '', image_url: '', rating: 0, status: '未讀', tags: [] }

// 資料服務連不上（Docker 還在啟動、剛被關閉）時通知畫面顯示橫幅；502–504 是網頁伺服器找不到資料服務。
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
export const coverSrc = (item, width) => item?.id && item.image_url ? `/api/covers/${item.id}?v=${encodeURIComponent(item.updated_at || '')}${width ? `&w=${width}` : ''}` : item?.image_url
// 環境光：直接寫入 CSS 變數，不經 React 狀態，避免懸停時整頁重新渲染；捲動中不切換，避免捲動時連續重繪。
let lastScrollAt = 0
if (typeof window !== 'undefined') window.addEventListener('scroll', () => { lastScrollAt = performance.now() }, { passive: true })
let ambientFrame = 0
export const setAmbientColour = (colour, force = false) => {
  if (!force && performance.now() - lastScrollAt < 180) return
  cancelAnimationFrame(ambientFrame)
  ambientFrame = requestAnimationFrame(() => document.documentElement.style.setProperty('--ambient', colour))
}
let hoveredItem = null
export const getHoveredItem = () => hoveredItem
export const hoverAmbient = item => { hoveredItem = item; setAmbientColour(colourFor(item)) }
export const STATUS_COLOURS = { 未讀: '#8c93b8', 閱讀中: '#55abb2', 已完食: '#8fcf8a', 暫時擱置: '#d7a35f', 棄坑: '#c96f6d' }
export const STATUS_OPTIONS = STATES.map(value => ({ value, label: value, colour: STATUS_COLOURS[value] }))
export const coverRatio = item => item?.cover_ratio && item.cover_ratio > 0.3 && item.cover_ratio < 3 ? item.cover_ratio : 4 / 3
export const hideBroken = event => { event.currentTarget.style.visibility = 'hidden' }
export const formatBytes = bytes => bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`
export const isEditing = () => ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable
export const linkFor = (item, sources) => {
  if (!item?.code) return ''
  if (/^https?:\/\//i.test(item.code)) return item.code
  const template = sources.find(source => source.name === item.source_name)?.template || ''
  return template.includes('{}') ? template.replace('{}', encodeURIComponent(item.code)) : ''
}

export function Stars({ value = 0, onChange, allowHalf = false, small = false, id }) {
  const [preview, setPreview] = useState(null)
  const step = allowHalf ? 0.5 : 1
  const shown = preview ?? value
  const pick = (index, event) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const half = allowHalf && event.clientX - rect.left < rect.width / 2
    return index + 1 - (half ? 0.5 : 0)
  }
  const commit = next => onChange(Math.abs(next - value) < 0.01 ? 0 : next)  // 再點一次同一分數即清除
  const onKeyDown = event => {
    const keys = { ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step }
    if (keys[event.key] !== undefined) { event.preventDefault(); event.stopPropagation(); onChange(Math.min(5, Math.max(0, value + keys[event.key]))) }
    else if (/^[0-5]$/.test(event.key)) { event.preventDefault(); event.stopPropagation(); onChange(Number(event.key)) }
  }
  return <div id={id} className={`stars${small ? ' is-small' : ''}`} role="slider" tabIndex={0} aria-label="評分" aria-valuemin={0} aria-valuemax={5} aria-valuenow={value} aria-valuetext={value ? `${value} 顆星` : '未評分'} onKeyDown={onKeyDown} onMouseLeave={() => setPreview(null)}>
    {[0, 1, 2, 3, 4].map(index => {
      const fill = Math.max(0, Math.min(1, shown - index)) * 100
      return <button key={index} type="button" tabIndex={-1} aria-hidden="true" onMouseMove={event => setPreview(pick(index, event))} onClick={event => { event.stopPropagation(); commit(pick(index, event)) }}>
        <span className="star-base">★</span><span className="star-fill" style={{ width: `${fill}%` }}>★</span>
      </button>
    })}
  </div>
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
    <button type="button" className="dropdown-trigger" aria-haspopup="listbox" aria-expanded={open} aria-label={ariaLabel} onClick={() => { setOpen(!open); setActive(Math.max(0, options.indexOf(current))) }}>
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
export const KINDS = ['漫畫', '動畫', '影片', '創作者', '其他']

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
