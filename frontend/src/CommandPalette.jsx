import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, colourFor, coverSrc, hideBroken, STATUS_COLOURS, titleFor } from './shared.jsx'

const match = (text, query) => text.toLowerCase().includes(query.toLowerCase())

// 指令面板（Ctrl / ⌘ + K）：在同一個輸入框裡搜尋作品、標籤與動作，全程可用鍵盤完成。
export default function CommandPalette({ actions, onClose, onOpenItem, onTag }) {
  const [query, setQuery] = useState('')
  const [items, setItems] = useState([])
  const [tags, setTags] = useState([])
  const [active, setActive] = useState(0)
  const listRef = useRef(null)

  useEffect(() => { api('/tags').then(setTags).catch(() => {}) }, [])
  useEffect(() => {
    const term = query.trim()
    if (!term) { setItems([]); return }
    let live = true
    const timer = setTimeout(() => {
      api(`/resources?limit=8&q=${encodeURIComponent(term)}`).then(result => live && setItems(result.items)).catch(() => {})
    }, 120)
    return () => { live = false; clearTimeout(timer) }
  }, [query])

  const groups = useMemo(() => {
    const term = query.trim()
    const matchedActions = term ? actions.filter(action => match(`${action.label} ${action.keywords || ''}`, term)) : actions
    const matchedTags = term ? tags.filter(name => match(name, term)).slice(0, 6) : []
    return [
      { title: '作品', rows: items.map(item => ({ key: `item-${item.id}`, kind: 'item', item, run: () => onOpenItem(item) })) },
      { title: '標籤', rows: matchedTags.map(name => ({ key: `tag-${name}`, kind: 'tag', label: `#${name}`, hint: '在典藏館篩選', run: () => onTag(name) })) },
      { title: term ? '動作' : '快速動作', rows: matchedActions.map(action => ({ key: `action-${action.id}`, kind: 'action', ...action })) },
    ].filter(group => group.rows.length)
  }, [query, items, tags, actions])
  const flat = groups.flatMap(group => group.rows)

  useEffect(() => { setActive(0) }, [query, items.length])
  useEffect(() => { listRef.current?.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' }) }, [active])

  const run = row => { if (!row) return; onClose(); row.run() }
  const onKeyDown = event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose() }
    else if (event.key === 'ArrowDown') { event.preventDefault(); setActive(index => (index + 1) % Math.max(1, flat.length)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(index => (index - 1 + flat.length) % Math.max(1, flat.length)) }
    else if (event.key === 'Enter') { event.preventDefault(); run(flat[active]) }
  }

  let index = -1
  return createPortal(<div className="palette-layer" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <div className="palette" role="dialog" aria-modal="true" aria-label="指令面板" onKeyDown={onKeyDown}>
      <label className="palette-input"><span>⌕</span><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋作品、標籤，或輸入動作…" aria-label="指令面板搜尋" /><kbd>Esc</kbd></label>
      <div className="palette-list" ref={listRef} role="listbox">
        {groups.map(group => <section key={group.title}>
          <h3>{group.title}</h3>
          {group.rows.map(row => {
            index += 1
            const current = index
            return <button key={row.key} type="button" role="option" aria-selected={current === active} className={current === active ? 'is-active' : ''} onMouseMove={() => setActive(current)} onClick={() => run(row)}>
              {row.kind === 'item' ? <>
                <i className="palette-thumb" style={{ '--node': colourFor(row.item) }}>{row.item.image_url && <img src={coverSrc(row.item, 240)} alt="" onError={hideBroken} />}</i>
                <span className="palette-label">{titleFor(row.item)}<small><i className="status-dot" style={{ background: STATUS_COLOURS[row.item.status] }} />{row.item.status}{row.item.rating > 0 ? `　★ ${Number(row.item.rating).toFixed(1)}` : ''}　{row.item.source_name}</small></span>
              </> : <>
                <i className="palette-icon">{row.kind === 'tag' ? '#' : row.icon || '›'}</i>
                <span className="palette-label">{row.label}</span>
                {row.hint && <kbd>{row.hint}</kbd>}
              </>}
            </button>
          })}
        </section>)}
        {!flat.length && <p className="palette-empty">{query.trim() ? '找不到符合的作品、標籤或動作' : '輸入以開始搜尋'}</p>}
      </div>
      <footer><span><kbd>↑</kbd><kbd>↓</kbd> 選擇</span><span><kbd>Enter</kbd> 執行</span><span><kbd>Ctrl</kbd><kbd>K</kbd> 開關</span></footer>
    </div>
  </div>, document.body)
}
