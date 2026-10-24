import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, loadTagCategories, TAG_CATEGORIES } from './shared.jsx'

// 篩選時最常用一般標籤，所以排最前面；作者、社團數量多，排在後面。
const PICKER_ORDER = ['tag', 'parody', 'character', 'artist', 'group', 'category', 'language']

// 標籤多選：清單中的件數只計算「目前篩選結果」裡的作品，所以每多選一個標籤，就能看出還能再縮小到哪些標籤。
export default function TagPicker({ selected, mode, onChange, onModeChange, filterParams }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState(null)
  const [position, setPosition] = useState(null)
  const [active, setActive] = useState(0)
  const [categories, setCategories] = useState({})
  const root = useRef(null)
  const panel = useRef(null)

  useEffect(() => { if (open) loadTagCategories().then(setCategories) }, [open])
  useEffect(() => {
    if (!open) return
    let live = true
    setRows(null)
    api(`/tags/stats?limit=2000&${filterParams}`).then(result => live && setRows(result)).catch(() => live && setRows([]))
    return () => { live = false }
  }, [open, filterParams])

  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const rect = root.current.getBoundingClientRect()
      const width = Math.min(340, window.innerWidth - 24)
      setPosition({ top: rect.bottom + 8, left: Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12)), width })
    }
    place()
    const close = event => { if (!root.current?.contains(event.target) && !panel.current?.contains(event.target)) setOpen(false) }
    document.addEventListener('pointerdown', close)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, { passive: true, capture: true })
    return () => { document.removeEventListener('pointerdown', close); window.removeEventListener('resize', place); window.removeEventListener('scroll', place, { capture: true }) }
  }, [open])

  const visible = useMemo(() => {
    const term = query.trim().toLowerCase()
    const list = (rows || []).filter(row => !term || row.name.toLowerCase().includes(term))
    // 已選的標籤即使不在目前結果中（例如「任一符合」時）也保留在最上方，方便取消。
    const chosen = selected.map(name => list.find(row => row.name === name) || { name, count: null }).filter(row => !term || row.name.toLowerCase().includes(term))
    // 未選的依分類排序（作者、社團、原作、角色、標籤…），同分類內依件數。
    const order = Object.fromEntries(PICKER_ORDER.map((key, index) => [key, index]))
    const rest = list.filter(row => !selected.includes(row.name)).map(row => ({ ...row, category: categories[row.name] || 'tag' }))
    rest.sort((a, b) => order[a.category] - order[b.category] || b.count - a.count)
    return [...chosen.map(row => ({ ...row, category: 'selected' })), ...rest].slice(0, 800)
  }, [rows, query, selected, categories])
  useEffect(() => { setActive(0) }, [query, open])

  const toggle = name => onChange(selected.includes(name) ? selected.filter(value => value !== name) : [...selected, name])
  const onKeyDown = event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setOpen(false) }
    else if (event.key === 'ArrowDown') { event.preventDefault(); setActive(index => Math.min(visible.length - 1, index + 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(index => Math.max(0, index - 1)) }
    else if (event.key === 'Enter' && visible[active]) { event.preventDefault(); toggle(visible[active].name) }
  }

  return <div ref={root} className={`tag-picker${open ? ' is-open' : ''}`}>
    <button type="button" className="dropdown-trigger" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span># 標籤{selected.length ? `（${selected.length}）` : ''}</span><em aria-hidden="true">⌄</em>
    </button>
    {open && position && createPortal(<div ref={panel} className="tag-panel" style={position} role="dialog" aria-label="選擇標籤" onKeyDown={onKeyDown}>
      <label className="tag-panel-search"><span>#</span><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋標籤…" /></label>
      <div className="tag-panel-mode" role="radiogroup" aria-label="多個標籤的比對方式">
        {[['all', '全部符合'], ['any', '任一符合']].map(([value, label]) => <button key={value} type="button" role="radio" aria-checked={mode === value} className={mode === value ? 'is-active' : ''} onClick={() => onModeChange(value)}>{label}</button>)}
      </div>
      <ul className="tag-panel-list" role="listbox" aria-multiselectable="true">
        {rows === null ? <li className="tag-panel-empty">讀取中…</li> : visible.length === 0 ? <li className="tag-panel-empty">沒有符合的標籤</li> :
          visible.map((row, index) => {
            const checked = selected.includes(row.name)
            const heading = index === 0 || visible[index - 1].category !== row.category
              ? <li key={`h-${row.category}`} className="tag-panel-heading" aria-hidden="true">{row.category === 'selected' ? '已選' : TAG_CATEGORIES.find(([key]) => key === row.category)?.[1]}</li> : null
            return [heading, <li key={row.name} role="option" aria-selected={checked} className={`${checked ? 'is-checked' : ''}${index === active ? ' is-active' : ''}`} onMouseEnter={() => setActive(index)} onClick={() => toggle(row.name)}>
              <i aria-hidden="true">{checked ? '✓' : ''}</i><span>{row.name}</span>{row.count !== null && <small>{row.count}</small>}
            </li>]
          })}
      </ul>
      <footer><span>{selected.length ? `已選 ${selected.length} 個` : '可多選；數字為目前結果中的件數'}</span>{selected.length > 0 && <button type="button" onClick={() => onChange([])}>清除</button>}</footer>
    </div>, document.body)}
  </div>
}
