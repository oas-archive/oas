import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Card } from './Gallery.jsx'
import { SkeletonGrid } from './Extras.jsx'
import { markFirstScreen } from './Sidebar.jsx'
import { api, colourFor, coverRatio, coverSrc, CoverFace, errorNote, hideBroken, isFaceCover, shortTitle, STATUS_COLOURS, titleFor, TOUCH } from './shared.jsx'

// 深潛帶：每 6.5 秒換一件；封面載好才換，不會閃白。App 在背景、作品頁開著、游標停在上面時不換
export function DeepStrip({ seed, paused, onOpen }) {
  const [pick, setPick] = useState(seed)
  const hold = useRef(false)
  useEffect(() => {
    if (paused) return undefined
    const timer = setInterval(() => {
      if (document.hidden || hold.current) return
      api('/deep-dive').then(next => {
        if (!next) return
        if (!next.image_url) { setPick(next); return }
        const image = new Image()
        image.onload = image.onerror = () => setPick(next)
        image.src = coverSrc(next, 480)
      }).catch(() => {})
    }, 6500)
    return () => clearInterval(timer)
  }, [paused])
  return <button type="button" className="deep-strip" style={{ '--cover-colour': colourFor(pick) }} onClick={() => onOpen(pick)} onMouseEnter={() => { hold.current = true }} onMouseLeave={() => { hold.current = false }}>
    {pick.image_url && <img key={`cover-${pick.id}`} src={coverSrc(pick, 480)} srcSet={`${coverSrc(pick, 480)} 480w, ${coverSrc(pick)} 1000w`} sizes="(max-width: 780px) 100vw, 1000px" alt="" decoding="async" onError={hideBroken} />}
    <span>深潛區</span><b key={`title-${pick.id}`}>{shortTitle(pick)}</b>
  </button>
}

// 首頁：主視覺（閱讀中與深潛作品，最多 5 件，手動切換）＋橫向書架列（繼續閱讀、探索書架、書單）
// 回到首頁時先顯示上次的內容（不用等 API、不閃骨架），同時在背景以同一個 seed 更新
let homeCache = null
let homeHeroIndex = 0
export default function HomeView({ onOpen, onHover, onQuick, onTag, onShowList, onCollection, collections, linkOf, onVisit, notify, version, changes, personal }) {
  const [seed, setSeed] = useState(() => homeCache?.seed ?? Math.floor(Math.random() * 100000))
  const [data, setDataState] = useState(() => homeCache)
  const setData = update => setDataState(current => { const next = typeof update === 'function' ? update(current) : update; homeCache = next; return next })
  const [heroIndex, setHeroIndexState] = useState(() => homeCache ? homeHeroIndex : 0)
  const setHeroIndex = value => { homeHeroIndex = value; setHeroIndexState(value) }
  const patchItems = (items, id, values) => items.map(entry => entry.id === id ? { ...entry, ...values } : entry)
  const patchLocal = (id, values) => setData(current => current && { ...current, hero: patchItems(current.hero, id, values), rows: current.rows.map(row => ({ ...row, items: patchItems(row.items, id, values) })) })
  const quickLocal = useCallback((item, patch) => {
    patchLocal(item.id, patch)
    return onQuick(item, patch).then(saved => { patchLocal(item.id, { rating: saved.rating, status: saved.status }); return saved })
      .catch(error => { patchLocal(item.id, { rating: item.rating, status: item.status }); throw error })
  }, [onQuick])
  const [loading, setLoading] = useState(false)  // 換一批時先讓舊書架變淡、按鈕顯示進行中，不讓人以為沒反應
  // 在作品頁改了評分、狀態或內容，回到探索時書架上也要是新的
  useEffect(() => {
    const listener = event => patchLocal(event.detail.id, event.detail.values)
    // 刪除的作品從主視覺與書架拿掉（首頁內容有快取，不拿掉會一直顯示到換一批）
    const removed = event => setData(current => current && { ...current, hero: current.hero.filter(entry => entry.id !== event.detail.id), rows: current.rows.map(row => ({ ...row, items: row.items.filter(entry => entry.id !== event.detail.id) })) })
    window.addEventListener('oas-item-changed', listener)
    window.addEventListener('oas-item-removed', removed)
    return () => { window.removeEventListener('oas-item-changed', listener); window.removeEventListener('oas-item-removed', removed) }
  }, [])
  useEffect(() => {
    const quiet = homeCache?.seed === seed  // 只是回到首頁：畫面照舊，背景更新
    if (!quiet) setLoading(true)
    let live = true  // 連續換一批時，較慢的舊回應不能蓋掉新的
    api(`/home?seed=${seed}${personal ? '&personal=true' : ''}`).then(result => {
      if (!live) return
      // 背景更新時主視覺停在原本那件（例如剛改成閱讀中的作品排到最前面，不要讓正在看的那張換掉）
      const showing = quiet && homeCache?.hero[homeHeroIndex]?.id
      const kept = showing ? result.hero.findIndex(entry => entry.id === showing) : -1
      if (kept >= 0 && kept !== homeHeroIndex) { heroJump.current = true; setHeroIndex(kept) }
      setData(result)
      markFirstScreen()  // 首頁到了：側欄、底部分頁的整理件數才開始算
    }).catch(error => live && notify(errorNote(error))).finally(() => live && setLoading(false))
    return () => { live = false }
  }, [seed, version, changes, personal])  // version：復原、還原備份後重新載入；changes：新增、修改作品後在背景更新；personal：手機直放橫放跨過寬度門檻
  const index = Math.min(heroIndex, Math.max(0, (data?.hero.length || 1) - 1))
  const hero = data?.hero[index]
  // 主視覺左右滑：跟著手指移動，放開後以 .55s 減速滑到定位；頭尾有阻力不繞圈
  const track = useRef(null)
  const drag = useRef(null)
  const place = (target, offset = 0, animate = true) => {
    const element = track.current
    if (!element) return
    element.style.transition = animate ? '' : 'none'
    element.style.transform = `translate3d(calc(${-target * 100}% + ${offset}px), 0, 0)`
    // 拖曳中：目前這張隨距離變淡、要進來的那張變亮；放開後交回 CSS 的淡入淡出
    const progress = Math.min(1, Math.abs(offset) / (element.clientWidth || 1))
    const incoming = element.children[target + (offset < 0 ? 1 : -1)]
    for (const [position, slide] of [...element.children].entries()) {
      const dragging = !animate && offset && (position === target || slide === incoming)
      slide.style.transition = dragging ? 'none' : ''
      slide.style.opacity = !dragging ? '' : position === target ? 1 - progress * 0.75 : 0.25 + progress * 0.75
    }
  }
  const heroJump = useRef(false)  // 背景更新改了主視覺的順序：直接放到定位，同一件作品不播滑動
  useLayoutEffect(() => { place(index, 0, !heroJump.current); heroJump.current = false }, [index, data])
  const dragStart = event => {
    if (data.hero.length < 2 || event.touches.length !== 1) return
    const touch = event.touches[0]
    drag.current = { x: touch.clientX, y: touch.clientY, at: performance.now(), axis: null, dx: 0 }
  }
  const dragMove = event => {
    const state = drag.current
    if (!state) return
    const touch = event.touches[0], dx = touch.clientX - state.x, dy = touch.clientY - state.y
    if (!state.axis) {
      if (Math.abs(dx) + Math.abs(dy) < 8) return
      state.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y'
    }
    if (state.axis !== 'x') return
    const atEdge = (index === 0 && dx > 0) || (index === data.hero.length - 1 && dx < 0)
    state.dx = atEdge ? dx * 0.3 : dx
    place(index, state.dx, false)
  }
  const dragEnd = () => {
    const state = drag.current
    drag.current = null
    if (!state || state.axis !== 'x') return
    const speed = Math.abs(state.dx) / Math.max(1, performance.now() - state.at)
    const flick = Math.abs(state.dx) > track.current.clientWidth * 0.18 || (speed > 0.35 && Math.abs(state.dx) > 24)
    const target = flick ? Math.min(data.hero.length - 1, Math.max(0, index + (state.dx < 0 ? 1 : -1))) : index
    place(target)
    setHeroIndex(target)
  }
  // 主視覺換作品時，環境光跟著換成它的封面主色
  useEffect(() => { if (hero) onHover(hero) }, [hero?.id])
  const reshuffle = () => { setHeroIndex(0); setSeed(value => value + 1) }  // 換一批從第一張重新開始，不停在原本滑到的位置
  const pullMark = usePullToShuffle(reshuffle, loading)
  // 主視覺換頁時整個首頁會重新執行：書架記住不重算，換頁動畫才不會被拖慢
  const shelves = useMemo(() => {
    if (!data) return null
    const more = row => row.key === 'reading' ? () => onShowList('reading') : row.key === 'unrated' ? () => onShowList('unrated') : row.key === 'opened' ? () => onShowList('recent') : row.collection ? () => onCollection(collections.find(collection => collection.id === row.collection)) : row.tag ? () => onTag(row.tag) : null
    return data.rows.map(row => <section key={row.key} className="shelf">
          <header><h3>{row.title}</h3>{row.subtitle && <p>{row.subtitle}</p>}
            {more(row) && <button type="button" className="shelf-more" onClick={more(row)}>看全部</button>}</header>
          {/* key 跟著這批內容的 seed：換一批拿到新內容時整排重掛，橫向位置與小圓點自然回到第一本（不用 key 的話，瀏覽器的捲動貼齊會跟著被搬動的同一張卡片，書架停在舊位置）；背景重抓同一個 seed 不變，不會把正在滑的書架拉回去 */}
          <ShelfRow key={data.seed} items={row.items} render={item => <Card key={item.id} item={item} onOpen={onOpen} list={row.items} onHover={onHover} onQuick={onQuick ? quickLocal : null} />} />
        </section>)
  }, [data, collections, quickLocal])
  if (!data) return <SkeletonGrid columns={4} count={8} />
  if (!hero) return <p className="explore-empty">典藏還太少，先收錄幾件作品吧。</p>
  return <section className={`home${loading ? ' is-loading' : ''}`} aria-label="首頁" aria-busy={loading}>
    {/* 換一批放在主視覺右上方（不寫標題，只留按鈕）：一眼看得到、不和切換條擠在一起，一次重抽主視覺與探索書架 */}
    {/* 手機沒有按鈕：在最上方往下拉就換一批 */}
    {TOUCH ? <div ref={pullMark} className={`pull-shuffle${loading ? ' is-loading' : ''}`} aria-hidden="true"><RefreshIcon /></div> : <header className="home-head">
      <button type="button" className="home-shuffle" disabled={loading} onClick={reshuffle} title="重新挑選主視覺的作品與下面的探索書架"><RefreshIcon />{loading ? '挑選中…' : '換一批'}</button>
    </header>}
    <section className="home-hero" style={{ '--cover-colour': colourFor(hero) }} aria-roledescription="輪播" aria-label="推薦作品">
      <div className="home-hero-track" ref={track} onTouchStart={dragStart} onTouchMove={dragMove} onTouchEnd={dragEnd} onTouchCancel={dragEnd}>
        {data.hero.map((item, position) => {
          const link = linkOf(item), current = position === index
          return <div key={item.id} className="home-hero-slide" style={{ '--cover-colour': colourFor(item) }} aria-hidden={!current} inert={!current} aria-roledescription="投影片" aria-label={`第 ${position + 1} 件，共 ${data.hero.length} 件`}>
            <div className="home-hero-copy">
              <p className="showcase-facts">{item.kind && <span>{item.kind}</span>}<span>{item.source_name}</span><span><i className="status-dot" style={{ background: STATUS_COLOURS[item.status] }} />{item.status}</span></p>
              <h1 title={titleFor(item)}>{shortTitle(item)}</h1>
              <p className="home-hero-meta"><span className={item.rating > 0 ? 'is-rated' : undefined}>{item.rating > 0 ? `★ ${Number(item.rating).toFixed(1)}` : '未評分'}</span>{item.tags.slice(0, 4).map(tag => <button key={tag} type="button" onClick={() => onTag(tag)}>{tag}</button>)}</p>
              <div className="home-hero-actions">
                {link ? <a className="primary" href={link} target="_blank" rel="noreferrer noopener" onClick={() => onVisit(item)}>前往閱讀 ↗</a> : <span className="primary is-disabled" title="此來源尚未設定網址規則">無來源連結</span>}
                <button type="button" className="ghost" onClick={() => onOpen(item, data.hero)}>詳細資料</button>
              </div>
            </div>
            <button type="button" className="home-hero-cover cover" data-cover-id={current ? item.id : undefined} onClick={() => onOpen(item, data.hero)} aria-label={`查看 ${titleFor(item)}`} style={{ '--ratio': isFaceCover(item) ? 1.15 : coverRatio(item) }}>
              {isFaceCover(item) ? <CoverFace item={item} /> : <img src={coverSrc(item, 480)} srcSet={`${coverSrc(item, 480)} 480w, ${coverSrc(item)} 1000w`} sizes="(max-width: 780px) 86vw, 420px" alt="" decoding="async" fetchPriority={current ? 'high' : undefined} onError={hideBroken} />}
            </button>
          </div>
        })}
      </div>
      {data.hero.length > 1 && <div className="home-hero-dots" role="tablist" aria-label="切換推薦作品">{data.hero.map((item, position) => <button key={item.id} type="button" role="tab" aria-selected={position === index} aria-label={titleFor(item)} onClick={() => setHeroIndex(position)} />)}</div>}
    </section>
    <h2 className="sr-only">書架</h2>
    {shelves}
  </section>
}

// 手機首頁：頁面已在最上方時往下拉，超過門檻放開就換一批。只動標記的 transform，手指移動時不重新渲染首頁
const PULL_TRIGGER = 72
function usePullToShuffle(onTrigger, loading) {
  const mark = useRef(null)
  const trigger = useRef(onTrigger)
  const busy = useRef(loading)
  trigger.current = onTrigger
  busy.current = loading
  useEffect(() => {
    if (!TOUCH) return undefined
    let start = null
    const paint = pull => {
      const element = mark.current
      if (!element) return
      const ready = pull >= PULL_TRIGGER
      element.style.transition = 'none'
      element.style.opacity = String(Math.min(1, pull / PULL_TRIGGER))
      element.style.transform = `translateY(${Math.min(pull, 110) * 0.6}px)`
      element.firstChild.style.rotate = `${pull * 3}deg`
      element.classList.toggle('is-ready', ready)
    }
    const reset = () => {
      const element = mark.current
      if (!element) return
      element.style.transition = element.style.opacity = element.style.transform = ''
      element.firstChild.style.rotate = ''
      element.classList.remove('is-ready')
    }
    const onStart = event => {
      const inPage = event.target.closest?.('.oas-main') && !event.target.closest('.showcase, .filter-sheet, .oas-modal')
      start = event.touches.length === 1 && inPage && window.scrollY <= 0 && !busy.current ? { x: event.touches[0].clientX, y: event.touches[0].clientY, pull: 0, axis: null } : null
    }
    const onMove = event => {
      if (!start) return
      if (event.touches.length !== 1) { start = null; reset(); return }
      const dx = event.touches[0].clientX - start.x, dy = event.touches[0].clientY - start.y
      if (!start.axis) {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return
        start.axis = dy > Math.abs(dx) * 1.2 ? 'pull' : 'other'  // 左右滑輪播、往上捲頁面都不算
      }
      if (start.axis !== 'pull') return
      start.pull = Math.max(0, dy)
      paint(start.pull)
    }
    const onEnd = () => {
      if (!start) return
      const fire = start.axis === 'pull' && start.pull >= PULL_TRIGGER
      start = null
      reset()
      if (fire) trigger.current()
    }
    window.addEventListener('touchstart', onStart, { passive: true })
    window.addEventListener('touchmove', onMove, { passive: true })
    window.addEventListener('touchend', onEnd, { passive: true })
    window.addEventListener('touchcancel', onEnd, { passive: true })
    return () => {
      window.removeEventListener('touchstart', onStart); window.removeEventListener('touchmove', onMove)
      window.removeEventListener('touchend', onEnd); window.removeEventListener('touchcancel', onEnd)
    }
  }, [])
  return mark
}

// 換一批用「重新整理」的循環箭頭，和頂列抽抽的骰子分開，不會看成同一件事
const RefreshIcon = () => <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4" /></svg>

// 探索的一排書架；手機上一次一本，下方小圓點顯示目前是第幾本（點圓點也能跳過去）
function ShelfRow({ items, render }) {
  const row = useRef(null)
  const [index, setIndex] = useState(0)
  const onScroll = () => {
    const element = row.current
    const width = element.firstElementChild?.getBoundingClientRect().width || 1
    setIndex(Math.round(element.scrollLeft / (width + parseFloat(getComputedStyle(element).columnGap || 0))))
  }
  const jump = target => { const card = row.current.children[target]; card && row.current.scrollTo({ left: card.offsetLeft - row.current.offsetLeft, behavior: 'smooth' }) }
  return <>
    <div className="shelf-row" ref={row} onScroll={onScroll}>{items.map(render)}</div>
    {items.length > 1 && <div className="shelf-dots" aria-hidden="true">{items.map((item, position) => <button key={item.id} type="button" tabIndex={-1} className={position === index ? 'is-active' : undefined} onClick={() => jump(position)} />)}</div>}
  </>
}
