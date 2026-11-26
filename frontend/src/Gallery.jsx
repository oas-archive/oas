import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { cancelPreview, schedulePreview, SEARCH_TIPS } from './Extras.jsx'
import { colourFor, coverRatio, coverSrc, CoverFace, Dropdown, hideBroken, isFaceCover, noteText, shortTitle, Stars, STATUS_COLOURS, statusOptions, titleFor } from './shared.jsx'

// 筆記裡只有系統自動寫的「原連結」「合併自」時，不算有筆記
const hasNote = item => Boolean(noteText(item))

// list：開啟時一起帶上的清單（書架、資料頁）。不用每次新建的 onOpen 函式傳，memo 才擋得住開關作品時整頁卡片重新渲染
export const Card = memo(function Card({ item, onOpen, onHover, onQuick, picked, list }) {
  const stop = event => event.stopPropagation()
  const keyOpen = event => { if ((event.key === 'Enter' || event.key === ' ') && event.target === event.currentTarget) { event.preventDefault(); onOpen(item, list ?? event) } }
  const enter = event => { onHover(item); if (picked === undefined) schedulePreview(item, event.currentTarget.querySelector('.cover')) }
  return <article tabIndex={0} aria-label={titleFor(item)} onClick={event => { cancelPreview(); onOpen(item, list ?? event) }} onKeyDown={keyOpen} onMouseEnter={enter} onMouseLeave={cancelPreview} onFocus={() => onHover(item)} style={{ '--cover-colour': colourFor(item) }} className={picked === undefined ? undefined : `is-pickable${picked ? ' is-picked' : ''}`} aria-pressed={picked}>
    {picked !== undefined && <i className="pick-mark" aria-hidden="true">{picked ? '✓' : ''}</i>}
    {item.link_dead && <i className="dead-badge" title="來源已失效">失效</i>}
    <div className="cover" data-cover-id={item.id} style={{ '--cover-colour': colourFor(item), aspectRatio: `1 / ${isFaceCover(item) ? 1.15 : coverRatio(item)}` }}>{isFaceCover(item) ? <CoverFace item={item} /> : <img src={coverSrc(item, 360)} srcSet={`${coverSrc(item, 360)} 360w, ${coverSrc(item, 480)} 480w, ${coverSrc(item)} 1000w`} sizes="(max-width: 780px) 100vw, 260px" alt="" loading="lazy" decoding="async" onError={hideBroken} />}<div className="cover-glow" />
      {onQuick && item.id && <div className="quick" onClick={stop} onKeyDown={stop}><Stars value={item.rating} allowHalf onChange={rating => onQuick(item, { rating }).catch(() => {})} small /><Dropdown small ariaLabel="閱讀狀態" value={item.status} options={statusOptions(item.status)} onChange={status => onQuick(item, { status }).catch(() => {})} /></div>}
    </div>
    <div className="card-copy"><h3 title={titleFor(item)}>{shortTitle(item)}</h3><p className="card-meta"><span><i className="status-dot" style={{ background: STATUS_COLOURS[item.status] }} />{item.status}{item.status === '閱讀中' && item.progress ? ` ${item.progress}` : ''}</span><span className={item.rating > 0 ? 'is-rated' : undefined}>{item.rating > 0 ? `★ ${Number(item.rating).toFixed(1)}` : '未評分'}</span><span>{item.source_name}</span>{hasNote(item) && <span className="note-mark" title="有筆記" aria-label="有筆記">✎</span>}</p><div>{item.tags.slice(0, 3).map(tag => <span key={tag}>{tag}</span>)}</div></div></article>
})

// 瀑布流：依封面比例把卡片依序放進目前最短的欄。追加項目只會接在欄底，既有卡片位置不變，因此「再載入」與圖片載入都不會跳動。
export function Masonry({ items, renderItem, minColumn = 220, gap = 14, className = '' }) {
  const ref = useRef(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    observer.observe(element)
    setWidth(element.clientWidth)
    return () => observer.disconnect()
  }, [])
  const count = Math.max(1, Math.floor((width + gap) / (minColumn + gap)))
  const columns = useMemo(() => {
    const columnWidth = Math.max(120, (width - gap * (count - 1)) / count)
    const lists = Array.from({ length: count }, () => [])
    const heights = new Array(count).fill(0)
    for (const item of items) {
      let target = 0
      for (let index = 1; index < count; index++) if (heights[index] < heights[target] - 1) target = index
      lists[target].push(item)
      heights[target] += columnWidth * (isFaceCover(item) ? 1.15 : coverRatio(item)) + (minColumn < 200 ? 64 : 96) + gap
    }
    return lists
  }, [items, count, width, gap])
  return <section ref={ref} className={`oas-gallery ${className}`} aria-label="典藏瀑布流" style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))`, gap }}>
    {columns.map((list, index) => <div key={index} className="masonry-column" style={{ gap }}>{list.map(renderItem)}</div>)}
  </section>
}

// 典藏館零結果：說清楚找了什麼，給能直接按的下一步（拼字更正、拿掉其他篩選），再提示搜尋語法
export function EmptyResults({ query, didYouMean, hasFilters, otherFilters, onSearch, onClearOthers }) {
  if (!query) return <p className="gallery-empty">沒有符合條件的作品。{hasFilters ? '試著拿掉一些篩選條件。' : '按右上角「收錄典藏」開始收藏。'}</p>
  return <div className="gallery-empty" role="status">
    <p>找不到符合「<b>{query}</b>」的作品{otherFilters ? '（同時套用了其他篩選）' : ''}。</p>
    {(didYouMean || otherFilters) && <div className="gallery-empty-actions">
      {didYouMean && <button type="button" className="primary" onClick={() => onSearch(didYouMean)}>改搜「{didYouMean}」</button>}
      {otherFilters && <button type="button" className="ghost" onClick={onClearOthers}>拿掉其他篩選，只用搜尋詞</button>}
    </div>}
    <small>{SEARCH_TIPS}</small>
  </div>
}
