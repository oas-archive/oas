import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, CATEGORY_NAMES, colourFor, CoverFace, coverRatio, coverSrc, groupTags, hideBroken, isFaceCover, loadTagCategories, noteText, STATUS_COLOURS, shortTitle } from './shared.jsx'

// ---------- 搜尋：即時建議與最近搜尋 ----------

const RECENT_KEY = 'oas-recent-search'
const readRecent = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') } catch { return [] } }
const writeRecent = list => { try { localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 8))) } catch { /* 只影響這次 */ } }

// 打字時列出符合的作品與標籤；輸入框空白時列出最近搜過的字。方向鍵選擇、Enter 開啟、Esc 收起。
// 搜尋語法（後端 search.py）：建議面板與零結果時都顯示
export const SEARCH_TIPS = '空白分開多個詞（都要符合）・-詞 排除・"整句" 不拆開・簡繁、全半形、同義詞都找得到'

export function SearchBox({ value, onChange, onOpenItem, onTag }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [recent, setRecent] = useState(readRecent)
  const [active, setActive] = useState(-1)
  const root = useRef(null)
  const term = value.trim()

  useEffect(() => {
    if (!term) { setData(null); return }
    let live = true
    const timer = setTimeout(() => api(`/search/suggest?q=${encodeURIComponent(term)}`).then(result => live && setData(result)).catch(() => {}), 150)
    return () => { live = false; clearTimeout(timer) }
  }, [term])
  useEffect(() => { setActive(-1) }, [term, open])
  useEffect(() => {
    if (!open) return
    const close = event => { if (!root.current?.contains(event.target)) setOpen(false) }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])

  const remember = text => {
    const word = text.trim()
    if (word.length < 2) return
    const next = [word, ...recent.filter(entry => entry !== word)]
    setRecent(next); writeRecent(next)
  }
  const forget = word => { const next = recent.filter(entry => entry !== word); setRecent(next); writeRecent(next) }
  const options = term
    ? [...(data?.items || []).map(item => ({ kind: 'item', key: `i${item.id}`, item })), ...(data?.tags || []).map(tag => ({ kind: 'tag', key: `t${tag.name}`, tag }))]
    : recent.map(word => ({ kind: 'recent', key: `r${word}`, word }))
  const choose = option => {
    setOpen(false)
    if (option.kind === 'item') { remember(term); onOpenItem(option.item) }
    else if (option.kind === 'tag') { remember(term); onChange(''); onTag(option.tag.name) }
    else onChange(option.word)
  }
  const onKeyDown = event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!options.length) return
      event.preventDefault(); setOpen(true)
      // -1 代表回到輸入框本身；上下循環
      setActive(index => event.key === 'ArrowDown' ? (index + 1 >= options.length ? -1 : index + 1) : (index - 1 < -1 ? options.length - 1 : index - 1))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      if (open && options[active]) choose(options[active])
      else { remember(term); setOpen(false); event.currentTarget.blur() }
    } else if (event.key === 'Escape') {
      if (open) { event.preventDefault(); event.stopPropagation(); setOpen(false) } else event.currentTarget.blur()
    }
  }
  const items = options.filter(option => option.kind === 'item'), tags = options.filter(option => option.kind === 'tag')
  const row = (option, content) => {
    const index = options.indexOf(option)
    return <li key={option.key} role="option" aria-selected={index === active} className={index === active ? 'is-active' : ''} onMouseEnter={() => setActive(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(option)}>{content}</li>
  }
  const showPanel = open && (term ? data && options.length > 0 : recent.length > 0)
  return <div className="search-box" ref={root}>
    <label className="search"><span>⌕</span>
      <input id="oas-search" type="search" role="combobox" aria-expanded={showPanel} aria-controls="oas-search-panel" aria-autocomplete="list" aria-label="搜尋典藏" autoComplete="off" autoCapitalize="none" autoCorrect="off" enterKeyHint="search" spellCheck={false}
        value={value} onChange={event => { onChange(event.target.value); setOpen(true) }} onFocus={() => setOpen(true)} onBlur={() => remember(term)} onKeyDown={onKeyDown} placeholder="搜尋標題、標籤或代碼" />
      <kbd>/</kbd></label>
    {showPanel && <div className="search-panel" id="oas-search-panel" tabIndex={0}>
      {term ? <>
        {items.length > 0 && <><p className="search-group">作品</p><ul role="listbox" aria-label="作品">{items.map(option => row(option, <>
          <i className="search-thumb" style={{ '--cover-colour': colourFor(option.item) }}>{!isFaceCover(option.item) && <img src={coverSrc(option.item, 240)} alt="" onError={hideBroken} />}</i>
          <span className="search-title">{shortTitle(option.item)}</span><small>{option.item.source_name}</small>
        </>))}</ul></>}
        {tags.length > 0 && <><p className="search-group">標籤</p><ul role="listbox" aria-label="標籤">{tags.map(option => row(option, <>
          <span className="search-title">{option.tag.via && <span className="search-via">{option.tag.via} →</span>}{option.tag.category === 'tag' ? '#' : ''}{option.tag.name}</span>
          {option.tag.category !== 'tag' && <small className="search-category">{CATEGORY_NAMES[option.tag.category]}</small>}<small>{option.tag.count} 件</small>
        </>))}</ul></>}
        <p className="search-foot">Enter 篩選書庫；選作品直接開啟，選作者等標籤開資料頁<br />{SEARCH_TIPS}</p>
      </> : <><p className="search-group">最近搜尋</p><ul role="listbox" aria-label="最近搜尋">{options.map(option => row(option, <>
        <span className="search-title">{option.word}</span>
        <button type="button" className="search-forget" onMouseDown={event => event.preventDefault()} onClick={event => { event.stopPropagation(); forget(option.word) }} aria-label={`從最近搜尋移除 ${option.word}`}>×</button>
      </>))}</ul><p className="search-foot">{SEARCH_TIPS}</p></>}
    </div>}
  </div>
}

// ---------- 懸停預覽 ----------

// 卡片透過 previewBus 通知；預覽元件只有一個，由 App 放在畫面上。
export const previewBus = { show: () => {}, hide: () => {} }
const PREVIEW_DELAY = 325
let previewTimer = 0
export const schedulePreview = (item, element) => {
  clearTimeout(previewTimer)
  if (!item?.id || window.matchMedia?.('(hover: none)').matches) return
  previewTimer = setTimeout(() => previewBus.show(item, element), PREVIEW_DELAY)
}
export const cancelPreview = () => { clearTimeout(previewTimer); previewBus.hide() }

export function HoverPreview({ disabled }) {
  const [state, setState] = useState(null)
  const [categories, setCategories] = useState({})
  const [position, setPosition] = useState(null)
  const panel = useRef(null)
  useEffect(() => {
    previewBus.show = (item, element) => { if (!disabled && element?.isConnected) setState({ item, rect: element.getBoundingClientRect(), home: Boolean(element.closest('.home')) }) }
    previewBus.hide = () => setState(null)
    const hide = () => { clearTimeout(previewTimer); setState(null) }
    // 滾輪捲動時先收起，停下來後游標下有卡片就重新預覽：游標沒移動時瀏覽器不一定再發 mouseenter，要自己補一次
    let mouse = null, settle = 0
    const onMouse = event => { mouse = { x: event.clientX, y: event.clientY } }
    const onScroll = () => {
      hide()
      clearTimeout(settle)
      settle = setTimeout(() => {
        if (mouse) document.elementFromPoint(mouse.x, mouse.y)?.closest('article')?.querySelector('.cover')?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, clientX: mouse.x, clientY: mouse.y }))
      }, 120)
    }
    const onPress = () => { clearTimeout(settle); hide() }
    // capture：書架等區塊自己的左右捲動也要收起預覽
    window.addEventListener('scroll', onScroll, { passive: true, capture: true })
    window.addEventListener('mousemove', onMouse, { passive: true })
    window.addEventListener('pointerdown', onPress, true)
    return () => {
      clearTimeout(settle)
      previewBus.show = () => {}; previewBus.hide = () => {}
      window.removeEventListener('scroll', onScroll, { capture: true }); window.removeEventListener('mousemove', onMouse); window.removeEventListener('pointerdown', onPress, true)
    }
  }, [disabled])
  useEffect(() => { if (disabled) setState(null) }, [disabled])  // 開啟其他視窗時收掉，關閉後不會在舊位置冒出來
  useEffect(() => { if (state) loadTagCategories().then(setCategories) }, [state])
  useLayoutEffect(() => {
    if (!state || !panel.current) { setPosition(null); return }
    const { rect } = state
    const width = panel.current.offsetWidth, height = panel.current.offsetHeight
    const right = rect.right + 14 + width <= window.innerWidth - 12
    const left = right ? rect.right + 14 : Math.max(12, rect.left - 14 - width)
    const top = Math.min(Math.max(12, rect.top), window.innerHeight - height - 12)
    setPosition({ left, top })
  }, [state, categories])  // 分類載入後文字會變多，要重新定位，不然下緣跑出視窗
  if (!state || disabled) return null
  const { item, rect, home } = state
  // 預覽一律比卡片大一點（書庫 1.14 倍，小、中、大都一樣；首頁卡片較小，1.25 倍）；但比點開的作品頁小
  const large = rect.width > 260
  const width = Math.min(440, Math.max(200, Math.round(rect.width * (home ? 1.25 : 1.14))))
  const groups = groupTags(item.tags, categories)
  const note = noteText(item)
  return createPortal(<aside ref={panel} className={`hover-preview${position ? ' is-placed' : ''}${large ? ' is-large' : ''}`} style={{ ...(position || { left: -9999, top: 0 }), width, '--cover-colour': colourFor(item) }} aria-hidden="true">
    <div className="hover-cover" style={{ height: Math.min(window.innerHeight - 150, Math.round(width * Math.min(1.33, isFaceCover(item) ? 1.15 : coverRatio(item)))) }}>{isFaceCover(item) ? <CoverFace item={item} width={360} /> : <img src={coverSrc(item, 360)} alt="" onError={hideBroken} />}</div>
    <div className="hover-body">
      <h4>{shortTitle(item)}</h4>
      <p className="hover-meta"><span><i className="status-dot" style={{ background: STATUS_COLOURS[item.status] }} />{item.status}{item.progress ? ` ${item.progress}` : ''}</span>{item.rating > 0 && <span className="is-rated">★ {Number(item.rating).toFixed(1)}</span>}{item.kind && <span>{item.kind}</span>}<span>{item.source_name}</span></p>
      {groups.slice(0, large ? 3 : 2).map(group => <p key={group.key} className="hover-tags"><b>{group.label}</b>{group.names.slice(0, large ? 8 : 5).map(name => <span key={name}>{name}</span>)}</p>)}
      {note && large && <p className="hover-note">{note.slice(0, 60)}{note.length > 60 ? '…' : ''}</p>}
    </div>
  </aside>, document.body)
}

// ---------- 斷線橫幅 ----------

// 資料服務連不上時出現，每 4 秒試一次；恢復後顯示「已重新連上」並重新載入資料。
export function OfflineBanner({ onBack }) {
  const [state, setState] = useState('ok')  // ok／offline／back
  useEffect(() => {
    const onOffline = () => setState(current => current === 'offline' ? current : 'offline')
    window.addEventListener('oas-offline', onOffline)
    return () => window.removeEventListener('oas-offline', onOffline)
  }, [])
  useEffect(() => {
    if (state === 'back') { const timer = setTimeout(() => setState('ok'), 3000); return () => clearTimeout(timer) }
    if (state !== 'offline') return
    const timer = setInterval(() => fetch('/api/health').then(response => { if (response.ok) { setState('back'); onBack() } }).catch(() => {}), 4000)
    return () => clearInterval(timer)
  }, [state])
  if (state === 'ok') return null
  return <div className={`offline-banner${state === 'back' ? ' is-back' : ''}`} role="status">
    {state === 'back' ? <b>已重新連上典藏館，資料已重新載入。</b> : <>
      <b>典藏館的資料服務沒有回應，每 4 秒自動重試。</b>
      <span>通常是典藏館的服務還在啟動或剛被關閉，等一下就好。超過一分鐘還沒恢復，請關掉典藏館視窗，再雙擊「OAS 典藏館」捷徑重新開啟。</span>
    </>}
  </div>
}

// ---------- 載入中的卡片輪廓 ----------

const SKELETON_RATIOS = [1.42, 1.3, 1.5, 1.38, 1.45, 1.33, 1.4, 1.48]
export function SkeletonGrid({ columns = 4, count = 12 }) {
  const lists = Array.from({ length: columns }, () => [])
  for (let index = 0; index < count; index++) lists[index % columns].push(index)
  return <section className="oas-gallery skeleton-grid" aria-label="載入中" aria-busy="true" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: 14 }}>
    {lists.map((list, column) => <div key={column} className="masonry-column" style={{ gap: 14 }}>{list.map(index => <div key={index} className="skeleton-card">
      <i style={{ aspectRatio: `1 / ${SKELETON_RATIOS[index % SKELETON_RATIOS.length]}` }} /><b /><b className="is-short" />
    </div>)}</div>)}
  </section>
}
