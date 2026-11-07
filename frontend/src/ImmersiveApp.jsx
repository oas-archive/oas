import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import CommandPalette from './CommandPalette.jsx'
import { cancelPreview, HoverPreview, OfflineBanner, schedulePreview, SEARCH_TIPS, SearchBox, SkeletonGrid } from './Extras.jsx'
import ImportDialog from './ImportDialog.jsx'
import InsightsView, { INSIGHT_TABS, migrateKeptPairs, shownTab, tabPage } from './InsightsView.jsx'
import { canReadPages, readPage } from './pageReader.js'
import { isPublic } from './preferences.js'
import Showcase, { playShowcaseClose } from './Showcase.jsx'
import Sidebar, { BottomTabs, PAGES, useTidyCounts } from './Sidebar.jsx'
import StatsView from './StatsView.jsx'
import FilterSheet, { ListTypeDialog, SmartDialog } from './FilterSheet.jsx'
import {
  api, colourFor, coverRatio, coverSrc, Dropdown, EMPTY, getHoveredItem, hideBroken, hoverAmbient, isEditing, KINDS, loadTagCategories, CoverFace, isFaceCover,
  CATEGORY_NAMES, whenIdle, ask, AskHost, confirmLeave, errorNote, formatBytes, useFocusTrap, linkFor, noteText, PAGE_SIZE, setAmbientColour, setAmbientColourSoon, Stars, MAIN_STATES, STATES, STATUS_COLOURS, STATUS_OPTIONS, statusOptions, TAG_CATEGORIES, titleFor, shortTitle, TOUCH, announceItem, announceRemoved,
} from './shared.jsx'

const SORTS = [{ value: 'relevance', label: '最相符' }, { value: 'updated', label: '最近更新' }, { value: 'created', label: '收錄時間' }, { value: 'opened', label: '最近開啟' }, { value: 'rating', label: '評分高到低' }, { value: 'title', label: '標題' }]
const PAGE_TITLES = { home: ['首頁', ''], gallery: ['書庫', '今晚想走進哪個世界？'], taste: ['口味', '看出口味，找下一本。'], tidy: ['整理', '補資料、清重複，收藏保持乾淨。'], stats: ['統計', '你的收藏，用數字看。'], creator: ['資料頁', ''] }
// 這些分類的標籤點下去開資料頁，一般標籤則在書庫篩選
const PROFILE_CATEGORIES = ['artist', 'group', 'parody', 'character']
// 從外部網站回來時，離開超過這麼久才詢問（太短多半只是切出去看一眼）
const RETURN_PROMPT_AFTER = 15000
// 卡片大小：瀑布流每欄的最小寬度
const DENSITIES = [['s', '小', 160], ['m', '中', 220], ['l', '大', 300]]
const PHONE_DENSITIES = [['m', '一欄'], ['s', '兩欄']]  // 手機寬度只分得出這兩種
const SHORTCUTS = [
  ['任何地方', [['/', '搜尋'], ['Ctrl K', '指令面板'], ['N', '收錄新典藏'], ['?', '顯示這份快捷鍵'], ['Home', '回到頂端'], ['Esc', '關閉視窗或離開批次選取']]],
  ['典藏卡片（懸停或用 Tab 聚焦時）', [['1–5', '評分，0 清除'], ['Enter', '開啟']]],
  ['展示頁', [['← →', '上一件、下一件'], ['1–5', '評分'], ['E', '編輯'], ['Esc', '關閉']]],
  ['批次選取', [['Shift ＋ 點擊', '一次選取一段範圍']]],
]
// 筆記裡只有系統自動寫的「原連結」「合併自」時，不算有筆記
const hasNote = item => Boolean(noteText(item))

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
// 封面放大／縮回的動畫在 Showcase.jsx（FLIP，只動封面本身）；這裡負責找出卡片封面的位置。
// 關閉動畫的安全上限：背景分頁動畫可能不走，時間到一定卸載，不會卡住
const CLOSE_LIMIT = 800
// 展示頁開啟後多久才把底下的頁面收起來（content-visibility）：要等封面放大結束，動畫中不動底下的版面
const COVER_MAIN_AFTER = 640
const WARM_AFTER = 1500  // 開 App 多久後預先排一次展示頁
const WARM_LIMIT = 1500  // 最多等字型這麼久
const inViewport = element => {
  if (!element) return null
  const rect = element.getBoundingClientRect()
  return rect.bottom > 0 && rect.top < window.innerHeight && rect.width > 0 ? element : null
}
// preferHover：開啟時優先取游標下的卡片（同一作品可能同時出現在多個書架）；關閉時游標下可能已是別張卡片，所以不採用。
const cardCover = (id, preferHover = true) => (preferHover && inViewport(document.querySelector(`article:hover [data-cover-id="${id}"]`))) || inViewport(document.querySelector(`.oas-main [data-cover-id="${id}"]`))

export default function ImmersiveApp() {
  const urlState = useRef(new URLSearchParams(window.location.search)).current
  const [items, setItems] = useState([])
  const [dashboard, setDashboard] = useState(null)
  useEffect(migrateKeptPairs, [])
  const [query, setQuery] = useState(() => urlState.get('q') || '')
  const [state, setState] = useState(() => urlState.get('status') || '')
  const [source, setSource] = useState(() => urlState.get('source') || '')
  const [selectedTags, setSelectedTags] = useState(() => urlState.getAll('tag'))
  const [excludedTags, setExcludedTags] = useState(() => urlState.getAll('not_tag'))
  const [tagMode, setTagMode] = useState(() => urlState.get('match') === 'any' ? 'any' : 'all')
  const [selected, setSelected] = useState(null)
  const [context, setContext] = useState(null)
  const [direction, setDirection] = useState(0)
  const [page, setPage] = useState(() => PAGES.some(([id]) => id === urlState.get('page')) || (urlState.get('page') === 'creator' && urlState.get('name')) ? urlState.get('page') : 'home')
  const [creator, setCreator] = useState(() => urlState.get('name') || '')
  // 提示訊息：{ text, undo }；undo 是後端復原紀錄的 id，有值時提示旁邊顯示「復原」並停留較久
  const [message, setMessageState] = useState(null)
  const setMessage = useCallback((text, undoId = null) => setMessageState(text ? (text.error ? { text: text.text, error: true } : { text, undo: undoId }) : null), [])
  const [dataVersion, setDataVersion] = useState(0)  // 復原後讓關聯頁、資料頁重新載入
  const [unratedOnly, setUnratedOnly] = useState(() => urlState.get('rated') === '0')
  const [returning, setReturning] = useState(null)  // 從外部網站回來時詢問的作品
  const pendingVisit = useRef(null)
  const [total, setTotal] = useState(0)
  const [sourceList, setSourceList] = useState([])
  const [panel, setPanel] = useState(null)
  const [insightTab, setInsightTab] = useState(() => INSIGHT_TABS.some(([id]) => id === urlState.get('view')) ? urlState.get('view') : 'taste')
  const tab = shownTab(page, insightTab)
  const [density, setDensity] = useState(() => { try { return localStorage.getItem('oas-density') || 'm' } catch { return 'm' } })
  const changeDensity = value => { setDensity(value); try { localStorage.setItem('oas-density', value) } catch { /* 只影響這次 */ } }
  const [activeSmart, setActiveSmart] = useState(null)
  const [smartDialog, setSmartDialog] = useState(null)  // 智慧書單的取名視窗：new 新增、rename 改名
  const [filterOpen, setFilterOpen] = useState(false)
  const [smartDraft, setSmartDraft] = useState(false)  // 從側欄「＋」新增智慧書單：面板底部變成「下一步：取名」
  const [paletteOpen, setPaletteOpen] = useState(false)
  const itemsRef = useRef(items)
  itemsRef.current = items

  const [loadingMore, setLoadingMore] = useState(false)
  const [loadingList, setLoadingList] = useState(true)  // 換篩選時重新載入中：沒有卡片時顯示輪廓，有卡片時淡化
  const [kind, setKind] = useState(() => urlState.get('kind') || '')
  const [sort, setSort] = useState(() => SORTS.some(option => option.value === urlState.get('sort')) ? urlState.get('sort') : 'updated')
  const [deadOnly, setDeadOnly] = useState(() => urlState.get('dead') === '1')
  const [didYouMean, setDidYouMean] = useState(null)  // 零結果時後端猜的拼字更正
  // 開始搜尋時排序自動換成「最相符」，清掉搜尋詞再換回「最近更新」；手動選過別的排序就不動
  const hadQuery = useRef(Boolean(query.trim()))
  useEffect(() => {
    const has = Boolean(query.trim())
    if (has === hadQuery.current) return
    hadQuery.current = has
    setSort(current => has && current === 'updated' ? 'relevance' : !has && current === 'relevance' ? 'updated' : current)
  }, [query])
  const [collections, setCollections] = useState([])
  const [activeCollection, setActiveCollection] = useState(() => Number(urlState.get('list')) ? { id: Number(urlState.get('list')), name: '' } : null)
  const [sidebarKey, setSidebarKey] = useState(0)
  const bumpSidebar = () => setSidebarKey(value => value + 1)
  const tidyCounts = useTidyCounts(sidebarKey)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const [shortSide, setShortSide] = useState(() => Math.min(window.innerWidth, window.innerHeight))
  const [navPreference, setNavPreference] = useState(() => { try { return localStorage.getItem('oas-nav') } catch { return null } })
  useEffect(() => {
    const onResize = () => { setViewport(window.innerWidth); setShortSide(Math.min(window.innerWidth, window.innerHeight)) }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  // 手機（含橫放：看短邊）用底部分頁列；其他寬度依使用者選擇，沒選過時窄視窗（< 1100px）預設迷你
  const navVariant = viewport < 780 || (TOUCH && shortSide < 600) ? 'phone' : (navPreference || (viewport < 1100 ? 'mini' : 'full'))
  const toggleNav = () => {
    const next = navVariant === 'mini' ? 'full' : 'mini'
    setNavPreference(next)
    try { localStorage.setItem('oas-nav', next) } catch { /* 無法保存時只影響這次 */ }
  }
  const [batch, setBatch] = useState(null)  // 批次編輯：null 為一般模式，否則為已選作品 id 的 Set
  const filterParams = `sort=${sort}${unratedOnly ? '&rated=false' : ''}${deadOnly ? '&dead=true' : ''}${activeCollection ? `&collection=${activeCollection.id}` : ''}${kind ? `&kind=${encodeURIComponent(kind)}` : ''}&q=${encodeURIComponent(query)}${state ? `&status=${encodeURIComponent(state)}` : ''}${source ? `&source=${encodeURIComponent(source)}` : ''}${selectedTags.map(tag => `&tags=${encodeURIComponent(tag)}`).join('')}${selectedTags.length > 1 ? `&tag_mode=${tagMode}` : ''}${excludedTags.map(tag => `&not_tags=${encodeURIComponent(tag)}`).join('')}`
  // 永遠用最新的篩選條件：load 也會從舊的事件處理函式呼叫（指令面板、復原、斷線重連）
  const filterRef = useRef(filterParams)
  filterRef.current = filterParams
  const filterQuery = () => filterRef.current
  const dashboardRef = useRef(dashboard)
  dashboardRef.current = dashboard
  // 每次載入有編號：較慢的舊回應不能蓋掉新條件的結果，也不能接到新清單後面
  const loadSeq = useRef(0)
  // reset：只是換篩選，資料沒變，不重算首頁數字和側欄（重複作品、待整理、標籤建議）；其他時候是資料可能變了
  const load = async (reset = false) => {
    const seq = ++loadSeq.current
    setLoadingList(true)
    try {
      // 重新載入時保留已展開的數量，避免儲存後畫面跳回第一頁
      const limit = reset ? PAGE_SIZE : Math.min(Math.max(PAGE_SIZE, Math.ceil(itemsRef.current.length / PAGE_SIZE) * PAGE_SIZE), 5000)
      const [resources, stats] = await Promise.all([api(`/resources?limit=${limit}&${filterQuery()}`), reset && dashboardRef.current ? null : api('/dashboard')])
      if (seq !== loadSeq.current) return
      setItems(resources.items); setTotal(resources.total ?? resources.items.length); setDidYouMean(resources.did_you_mean || null)
      if (stats) setDashboard(stats)
      if (!reset) bumpSidebar()
    } catch (error) { if (seq === loadSeq.current) setMessage(errorNote(error)) } finally { if (seq === loadSeq.current) setLoadingList(false) }
  }
  const loadMore = async () => {
    const seq = loadSeq.current
    setLoadingMore(true)
    try {
      const resources = await api(`/resources?limit=${PAGE_SIZE}&offset=${items.length}&${filterQuery()}`)
      if (seq !== loadSeq.current) return
      setItems(current => [...current, ...resources.items.filter(item => !current.some(existing => existing.id === item.id))])
      setTotal(resources.total ?? total)
    } catch (error) { setMessage(errorNote(error)) } finally { setLoadingMore(false) }
  }
  useEffect(() => { const timer = setTimeout(() => load(true), 180); return () => clearTimeout(timer) }, [filterParams])
  useEffect(() => { api('/sources').then(setSourceList).catch(() => {}) }, [])
  // 「收錄到 OAS」書籤或手機分享帶來的資料（?add=JSON），並清掉網址參數：
  // 開啟預先填好的收錄表單。已在典藏中的，表單會提示並提供「開啟它」「補上它的空白欄位」：網址可能是別的網站組出來的，不能不問就改既有作品。
  // 分享會整頁重新載入，所以先回到分享前所在的頁面（例如「待整理」），關掉作品頁時才不會回到首頁。
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get('add')
    if (!raw) return
    let back = null
    try { back = JSON.parse(sessionStorage.getItem('oas-last-view') || 'null') } catch { /* 沒有就留在首頁 */ }
    window.history.replaceState(null, '', window.location.pathname)
    let data
    try { data = JSON.parse(raw) } catch { setMessage('書籤資料格式不正確'); return }
    if (back && back.page !== 'home' && PAGES.some(([id]) => id === back.page)) {
      if (INSIGHT_TABS.some(([id]) => id === back.view)) setInsightTab(back.view)
      setPage(back.page)
    }
    const draft = { ...EMPTY, title: String(data.title || ''), image_url: String(data.image_url || ''), tags: Array.isArray(data.tags) ? data.tags.map(String) : [], source_url: String(data.url || '') }
    api('/metadata', { method: 'POST', body: JSON.stringify({ url: draft.source_url, fetch: false, title: draft.title }) })
      .then(async info => {
        // 手機 App：在看不到的瀏覽器打開作品頁，交給伺服器照網站規則解析（禁漫、MissAV 等伺服器讀不到的網站也讀得到標籤）
        if (canReadPages()) {
          setMessage('讀取作品頁中…')
          const html = await readPage(info.url || draft.source_url)
          const read = html ? await api('/metadata', { method: 'POST', body: JSON.stringify({ url: draft.source_url, html, title: draft.title }) }).catch(() => null) : null
          if (read?.title) {
            info = read
            Object.assign(draft, { title: read.title, image_url: draft.image_url || read.image_url || '', tags: [...new Set([...draft.tags, ...read.tags])], tag_categories: read.categories || {}, similar: read.similar || [] })
          }
          setMessage('')
        }
        // 標題去掉網站名稱；手機分享沒有封面時，用依網址推得的封面（例如禁漫、MissAV）
        const filled = { ...draft, title: info.title || draft.title, image_url: draft.image_url || info.image_url || '' }
        return open({ ...filled, source_name: info.source_name, code: info.code, duplicate: info.duplicate || null })
      })
      .catch(() => open({ ...draft, code: draft.source_url }))
  }, [])
  // 記住目前在哪一頁，給上面的分享回到原頁面用
  useEffect(() => { try { sessionStorage.setItem('oas-last-view', JSON.stringify({ page, view: insightTab })) } catch { /* 只影響分享後回到哪一頁 */ } }, [page, insightTab])
  useEffect(() => { if (!message || message.error) return; const timer = setTimeout(() => setMessage(''), message.undo ? 10000 : 3800); return () => clearTimeout(timer) }, [message])

  const quick = useCallback(async (item, patch) => {
    const apply = values => {
      setItems(current => current.map(entry => entry.id === item.id ? { ...entry, ...values } : entry))
      setSelected(current => current?.id === item.id ? { ...current, ...values } : current)
      announceItem(item.id, values)  // 探索書架等自己保存一份作品的頁面也跟著更新
    }
    apply(patch)
    try {
      const saved = await api(`/resources/${item.id}/quick`, { method: 'PATCH', body: JSON.stringify(patch) })
      apply({ rating: saved.rating, status: saved.status, link_dead: saved.link_dead, progress: saved.progress, finished_at: saved.finished_at })
      setReturning(current => current?.id === item.id ? { ...current, ...saved } : current)
      api('/dashboard').then(setDashboard).catch(() => {})
      return saved
    } catch (error) {
      const before = { rating: item.rating, status: item.status, progress: item.progress, link_dead: item.link_dead }
      apply(before)
      setReturning(current => current?.id === item.id ? { ...current, ...before } : current)
      setMessage(errorNote(error))
      throw error
    }
  }, [])

  // 正在播關閉動畫時放這裡「立刻結束」的函式；動畫中再次關閉或開啟別的作品都先呼叫它，不會卡住或留下殘影
  const closing = useRef(null)
  // 卡片封面的位置：開啟時記下，展示頁掛載時取走
  const flipFrom = useRef(null)
  // 卡片上已經載入的封面圖：展示頁直接沿用，放大動畫期間不必等新的圖
  const coverHint = useRef(null)
  // 開 App 後趁空檔在看不見的地方排一次展示頁：載入字型、跑過程式，第一次點開作品才不會卡。使用者已經打開作品就不做
  const [warmItem, setWarmItem] = useState(null)
  useEffect(() => {
    let live = true
    let cancelIdle = () => {}
    const timer = setTimeout(() => {
      cancelIdle = whenIdle(() => {
        if (selectedRef.current) return
        api('/resources?limit=1&works=true').then(result => { if (live && !selectedRef.current && result.items[0]) setWarmItem(result.items[0]) }).catch(() => {})
      }, 4000)
    }, WARM_AFTER)
    return () => { live = false; clearTimeout(timer); cancelIdle() }
  }, [])
  useEffect(() => {
    if (!warmItem) return
    let live = true
    const done = () => { if (live) setWarmItem(null) }
    const frame = requestAnimationFrame(() => {
      Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, WARM_LIMIT))]).then(() => requestAnimationFrame(done))
    })
    return () => { live = false; cancelAnimationFrame(frame) }
  }, [warmItem])
  // 開啟展示頁：list 是目前所在的清單（典藏館或某個書架），供 ← / → 切換。
  const open = useCallback((item, list = null) => {
    closing.current?.()
    // 展示頁已開著時底下的頁面被收起來，卡片位置不可靠，不從卡片放大
    const card = !selectedRef.current && item?.id ? cardCover(item.id) : null
    flipFrom.current = card ? card.getBoundingClientRect() : null
    const picture = card?.querySelector('img')
    coverHint.current = picture?.complete && picture.naturalWidth ? { id: item.id, image_url: item.image_url, src: picture.currentSrc } : null
    setAmbientColourSoon(colourFor(item))
    if (!window.history.state?.oasItem) window.history.pushState({ ...window.history.state, oasItem: item?.id ?? 'new' }, '')
    setDirection(0); setSelected(item); setContext(list)
  }, [])
  // 展示頁內切換：由展示頁自己做輕量的方向動畫。關閉動畫中不再切換。
  const navigate = useCallback((item, step = 0) => {
    if (closing.current) return
    setAmbientColourSoon(colourFor(item))
    const latest = itemsRef.current.find(entry => entry.id === item.id) || item
    setDirection(step); setSelected(latest)
  }, [])
  // 展示頁開著時，底下的頁面不必繪製：用 content-visibility 跳過它，切換作品、換環境光時不會連帶重算幾百張卡片。
  // 先記下目前尺寸當佔位，捲動位置才不會跑掉。
  const coverMain = on => {
    const main = document.querySelector('.oas-main')
    if (!main || on === main.classList.contains('is-covered')) return
    main.classList.toggle('is-covered', on)
  }
  const showing = Boolean(selected)
  useEffect(() => {
    if (!showing) { coverMain(false); return }
    // 隱藏底下的頁面要重算所有卡片的樣式：等封面放大結束、瀏覽器有空才做，不和原圖、相似作品擠在同一格；已在關閉就不收
    let cancelIdle = () => {}
    const timer = setTimeout(() => { cancelIdle = whenIdle(() => { if (!closing.current) coverMain(true) }) }, COVER_MAIN_AFTER)
    return () => { clearTimeout(timer); cancelIdle() }
  }, [showing])
  // 關閉時封面縮回原卡片（若卡片仍在畫面內，否則淡出），動畫完才卸載。
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const closeNow = () => {
    const id = selectedRef.current?.id
    // 重工作都放在動畫開始前的這一格：還沒換的環境光、恢復底下的頁面（縮回動畫才找得到原卡片）、量位置
    coverMain(false)
    const running = playShowcaseClose(id ? cardCover(id, false)?.getBoundingClientRect() : null)
    if (!running) { setSelected(null); setContext(null); return }
    let timer = 0
    const finish = () => {
      if (closing.current !== finish) return
      closing.current = null
      clearTimeout(timer)
      // 同步卸載：動畫留在元素上的結束狀態跟著移除，接著開啟的作品是全新的展示頁
      flushSync(() => { setSelected(null); setContext(null) })
    }
    closing.current = finish
    timer = setTimeout(finish, CLOSE_LIMIT)
    running.then(finish, finish)
  }
  // 關閉展示頁一律走「返回」，讓瀏覽紀錄和畫面一致；popstate 會呼叫 closeNow
  // 關閉動畫中再按一次：直接結束動畫。返回還沒生效前的連按不理會，否則會多退一頁
  const backAt = useRef(-1000)
  // 表單有還沒儲存的內容：關閉、返回鍵、重新整理都先問
  const formDirty = useRef(false)
  useEffect(() => {
    const warn = event => { if (formDirty.current) event.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])
  const closeShowcase = () => {
    if (closing.current) { closing.current(); return }
    if (formDirty.current) { confirmLeave().then(leave => { if (leave) { formDirty.current = false; closeShowcase() } }); return }
    if (!window.history.state?.oasItem) { closeNow(); return }
    if (performance.now() - backAt.current < 300) return
    backAt.current = performance.now()
    window.history.back()
  }
  // 其他方式離開展示頁（篩選標籤…）時，把這一筆紀錄改成一般頁面，之後按返回不會重新打開
  const forgetShowcase = () => { if (window.history.state?.oasItem) window.history.replaceState({ ...window.history.state, oasItem: undefined }, '') }
  useEffect(() => {
    // 第一筆紀錄記下開啟時的頁面，從其他頁按返回才回得來（例如一開始就在統計頁）
    // 重新整理時舊的「展示頁」紀錄要清掉，否則按返回會整頁重新載入，而不是關閉展示頁
    window.history.replaceState({ ...window.history.state, oasPage: window.history.state?.oasPage || page, oasCreator: window.history.state?.oasCreator || creator || undefined, oasItem: undefined }, '')
    const onPop = event => {
      const state = event.state || {}
      // 返回鍵已經退了一筆紀錄：先補回去再問，決定離開才真的退
      if (selectedRef.current && !state.oasItem && formDirty.current) {
        window.history.pushState({ ...state, oasItem: selectedRef.current.id ?? 'new' }, '')
        confirmLeave().then(leave => { if (leave) { formDirty.current = false; window.history.back() } })
        return
      }
      if (closing.current) closing.current()  // 關閉動畫中又按返回：結束動畫，照一般換頁往下走
      else if (selectedRef.current && !state.oasItem) { closeNow(); return }
      setPage(current => {
        const target = state.oasPage || 'home'
        if (target !== current) { window.scrollTo({ top: 0 }); if (state.oasCreator) setCreator(state.oasCreator) }
        return target
      })
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  // 圖譜節點只帶摘要欄位；開啟前取回完整資料，避免儲存時把筆記等欄位寫成空白。
  const openById = useCallback(async id => {
    try { open(await api(`/resources/${id}`)) } catch (error) { setMessage(errorNote(error)) }
  }, [open])
  const openInGallery = useCallback(item => open(item, itemsRef.current), [open])
  const openDuplicate = id => api(`/resources/${id}`).then(item => { closing.current?.(); setDirection(0); setSelected(item); setContext(null) }).catch(error => setMessage(errorNote(error)))
  // 換頁：記一筆瀏覽紀錄，滑鼠返回鍵或 Alt + ← 可以回到上一頁
  const pushPage = (target, name = '') => {
    if (target === page && (target !== 'creator' || name === creator)) {
      // 還是同一頁（例如在作品頁點標籤，留在書庫）：作品頁那筆紀錄用不到了，退回去，之後才不會多按一次返回
      if (window.history.state?.oasItem) window.history.back()
      return
    }
    forgetShowcase()
    window.history.pushState({ oasPage: target, oasCreator: name || undefined }, '')
  }
  const filterTags = (tags, mode = 'all', status = '') => { pushPage('gallery'); setActiveSmart(null); setActiveCollection(null); setDeadOnly(false); setUnratedOnly(false); setKind(''); setTagMode(mode); setExcludedTags([]); setSelected(null); setPage('gallery'); setState(status); setSource(''); setQuery(''); setSelectedTags(tags); window.scrollTo({ top: 0 }) }
  const filterTag = tag => filterTags([tag])
  const openProfile = name => { pushPage('creator', name); setSelected(null); setCreator(name); setPage('creator'); window.scrollTo({ top: 0 }) }
  // 展示頁、統計頁的標籤：作者、社團、原作、角色開資料頁，其餘在書庫篩選
  const openTag = async tag => { const categories = await loadTagCategories(); PROFILE_CATEGORIES.includes(categories[tag]) ? openProfile(tag) : filterTag(tag) }
  const toggleTag = tag => setSelectedTags(current => current.includes(tag) ? current.filter(value => value !== tag) : [...current, tag])
  const goTo = target => { pushPage(target); setPage(target); window.scrollTo({ top: 0 }) }
  // 復原：提示訊息的「復原」按鈕、Ctrl + Z、指令面板都走這裡
  const undoAction = async id => {
    try {
      const result = await api(`/undo/${id}`, { method: 'POST' })
      setMessage(`已復原：${result.label}${result.kept ? `（${result.kept} 件收錄後已被編輯，保留下來）` : ''}`)
      setDataVersion(value => value + 1); loadTagCategories(true); loadCollections(); load()
    } catch (error) { setMessage(errorNote(error)) }
  }
  const undoLatest = async () => {
    try { const latest = await api('/undo/latest'); latest ? undoAction(latest.id) : setMessage('一小時內沒有可以復原的動作') } catch (error) { setMessage(errorNote(error)) }
  }
  // 從外部網站看完回來：離開超過 RETURN_PROMPT_AFTER 才詢問；太快回來就繼續等下一次回來
  useEffect(() => {
    const onBack = () => {
      if (document.visibilityState === 'hidden') return
      const pending = pendingVisit.current
      if (!pending || Date.now() - pending.at < RETURN_PROMPT_AFTER) return
      pendingVisit.current = null
      api(`/resources/${pending.id}`).then(item => setReturning(item)).catch(() => {})
    }
    window.addEventListener('focus', onBack)
    document.addEventListener('visibilitychange', onBack)
    return () => { window.removeEventListener('focus', onBack); document.removeEventListener('visibilitychange', onBack) }
  }, [])

  useEffect(() => {
    const listener = event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPaletteOpen(value => !value); return }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !isEditing() && message?.undo) { event.preventDefault(); const id = message.undo; setMessage(''); undoAction(id); return }
      if (paletteOpen) return
      if (event.key === 'Escape') {
        if (document.querySelector('.dropdown.is-open')) return
        if (batch && !selected) { setBatch(null); return }
        if (panel) {
          if (panel === 'import' && formDirty.current) { confirmLeave().then(leave => { if (leave) { formDirty.current = false; setPanel(null) } }); return }
          setPanel(null); return
        }
        if (selected) { event.preventDefault(); closeShowcase(); return }
        if (returning) setReturning(null)
        return
      }
      if (isEditing() || event.ctrlKey || event.metaKey || event.altKey || selected) return
      if (event.key === '/') { event.preventDefault(); document.querySelector('#oas-search')?.focus() }
      if (event.key === '?') { event.preventDefault(); setPanel('keys'); return }
      if (event.key.toLowerCase() === 'n') { event.preventDefault(); open(EMPTY) }
      const hovered = getHoveredItem()
      if (/^[0-5]$/.test(event.key) && hovered?.id && document.querySelector('article:hover, article:focus')) { event.preventDefault(); quick(hovered, { rating: Number(event.key) }).catch(() => {}) }
    }
    window.addEventListener('keydown', listener); return () => window.removeEventListener('keydown', listener)
  }, [selected, panel, paletteOpen, quick, open, batch, message, returning])

  const sources = useMemo(() => [...new Set([...sourceList.map(item => item.name), ...items.map(item => item.source_name)])].sort(), [items, sourceList])
  const save = async payload => {
    try {
      const saved = selected?.id ? await api(`/resources/${selected.id}`, { method: 'PATCH', body: JSON.stringify(payload) }) : await api('/resources', { method: 'POST', body: JSON.stringify(payload) })
      if (Object.keys(payload.tag_categories || {}).length) await loadTagCategories(true)  // 讀作品頁帶來的分類，存完才看得到
      setMessage('典藏已儲存'); setAmbientColour(colourFor(saved), true); setSelected(saved); announceItem(saved.id, saved); load()
      return true
    } catch (error) { setMessage(errorNote(error)); return false }
  }
  const loadCollections = () => api('/collections').then(list => {
    setCollections(list)
    // 從網址還原的書單只有 id，載入後補上名稱；書單已不存在就離開篩選
    setActiveCollection(current => current && !current.name ? (list.find(entry => entry.id === current.id) || null) : current)
  }).catch(() => {})
  // 篩選條件同步到網址（預設值不寫，網址保持乾淨）
  useEffect(() => {
    const params = new URLSearchParams()
    if (page !== 'home') params.set('page', page)
    if (query) params.set('q', query)
    if (state) params.set('status', state)
    if (source) params.set('source', source)
    if (kind) params.set('kind', kind)
    selectedTags.forEach(tag => params.append('tag', tag))
    if (selectedTags.length > 1 && tagMode === 'any') params.set('match', 'any')
    excludedTags.forEach(tag => params.append('not_tag', tag))
    if (sort !== 'updated') params.set('sort', sort)
    if (activeCollection) params.set('list', String(activeCollection.id))
    if (deadOnly) params.set('dead', '1')
    if (unratedOnly) params.set('rated', '0')
    if ((page === 'taste' || page === 'tidy') && tab !== shownTab(page, '')) params.set('view', tab)
    if (page === 'creator' && creator) params.set('name', creator)
    if (selected?.id) params.set('item', String(selected.id))
    const next = params.toString()
    // 保留 history.state（返回鍵用的紀錄），只改網址
    if (next !== window.location.search.replace(/^\?/, '')) window.history.replaceState(window.history.state, '', next ? `?${next}` : window.location.pathname)
  }, [page, query, state, source, kind, selectedTags, excludedTags, tagMode, sort, activeCollection, deadOnly, unratedOnly, tab, creator, selected?.id, filterOpen])  // filterOpen：關閉篩選面板走「返回」，網址會退回開啟前，要重新寫回目前的條件
  // 網址帶 item 時（重新整理、書籤）直接開啟那件作品
  useEffect(() => { const id = Number(urlState.get('item')); if (id) openById(id) }, [])
  useEffect(() => { loadCollections() }, [])
  const createCollection = async (name, query = '') => {
    try { const made = await api('/collections', { method: 'POST', body: JSON.stringify({ name, query }) }); await loadCollections(); setMessage(query ? `已建立智慧書單「${made.name}」（${made.count} 件，會依條件自動更新）` : `已建立書單「${made.name}」`); return made } catch (error) { setMessage(errorNote(error)); return null }
  }
  // 智慧書單：目前的篩選條件（與網址相同的格式）
  const filterSearch = () => {
    const params = new URLSearchParams()
    if (query) params.set('q', query)
    if (state) params.set('status', state)
    if (source) params.set('source', source)
    if (kind) params.set('kind', kind)
    selectedTags.forEach(tag => params.append('tag', tag))
    if (selectedTags.length > 1 && tagMode === 'any') params.set('match', 'any')
    excludedTags.forEach(tag => params.append('not_tag', tag))
    if (deadOnly) params.set('dead', '1')
    if (unratedOnly) params.set('rated', '0')
    if (sort !== 'updated') params.set('sort', sort)
    return params.toString()
  }
  const hasFilters = Boolean(query || state || source || kind || selectedTags.length || excludedTags.length || deadOnly || unratedOnly)
  const showDeepStrip = navVariant !== 'phone' && dashboard?.deep_dive && !query && !source && !kind && !selectedTags.length && !excludedTags.length && !deadOnly && !unratedOnly && !batch && !activeCollection && !activeSmart
  const clearFilters = () => { setQuery(''); setState(''); setSource(''); setKind(''); setSelectedTags([]); setExcludedTags([]); setDeadOnly(false); setUnratedOnly(false) }
  // 已套用的條件：全部列在同一處，每一項都能直接移除
  const applied = [
    query && { key: 'q', label: `「${query}」`, remove: () => setQuery('') },
    unratedOnly ? { key: 'unrated', label: '待評分', remove: () => { setUnratedOnly(false); setState('') } } : state && { key: 'status', label: `狀態：${state}`, remove: () => setState('') },
    deadOnly && { key: 'dead', label: '來源失效', remove: () => setDeadOnly(false) },
    kind && { key: 'kind', label: `類型：${kind}`, remove: () => setKind('') },
    source && { key: 'source', label: `來源：${source}`, remove: () => setSource('') },
    ...selectedTags.map(tag => ({ key: `tag-${tag}`, label: `#${tag}`, remove: () => toggleTag(tag) })),
    ...excludedTags.map(tag => ({ key: `not-${tag}`, label: `不要 #${tag}`, excluded: true, remove: () => setExcludedTags(current => current.filter(value => value !== tag)) })),
  ].filter(Boolean)
  const statusTabs = ['', ...STATES.filter(value => MAIN_STATES.includes(value) || value === state || dashboard?.status_counts?.[value])]
  const filterCount = selectedTags.length + excludedTags.length + (kind ? 1 : 0) + (source ? 1 : 0)
  const smartDirty = Boolean(activeSmart) && filterSearch() !== activeSmart.query
  const applySearch = search => {
    const params = new URLSearchParams(search)
    setQuery(params.get('q') || ''); setState(params.get('status') || ''); setSource(params.get('source') || ''); setKind(params.get('kind') || '')
    setSelectedTags(params.getAll('tag')); setExcludedTags(params.getAll('not_tag')); setTagMode(params.get('match') === 'any' ? 'any' : 'all'); setDeadOnly(params.get('dead') === '1'); setUnratedOnly(params.get('rated') === '0')
    setSort(SORTS.some(option => option.value === params.get('sort')) ? params.get('sort') : 'updated')
  }
  const leaveSmart = () => { setActiveSmart(null); applySearch('') }
  const saveSmart = async name => {
    const made = await createCollection(name, filterSearch())
    if (made) { setActiveSmart({ ...made }); setSmartDialog(null) }
  }
  // 預設名稱取自條件（去掉前綴），改不改都可以
  const smartName = () => applied.map(item => item.label.replace(/^(狀態|類型|來源)：/, '').replace('#', '')).join('、').slice(0, 30)
  const renameSmart = async name => {
    const id = (activeSmart || activeCollection).id
    if (await renameCollection(id, name)) setSmartDialog(null)
  }
  const removeSmart = async () => {
    if (!await ask(`刪除智慧書單「${activeSmart.name}」？只會刪掉這份條件，作品不會被刪除。`, { confirm: '刪除書單', danger: true })) return
    await deleteCollection(activeSmart.id)
    applySearch('')
  }
  const removeManual = async () => {
    if (await ask(`刪除書單「${activeCollection.name}」？書單裡的作品不會被刪除。`, { confirm: '刪除書單', danger: true })) await deleteCollection(activeCollection.id)
  }
  const saveManual = async name => {
    const made = await createCollection(name)
    if (made) { setSmartDialog(null); setActiveSmart(null); setActiveCollection({ ...made }) }
  }
  const newSmart = () => { setActiveCollection(null); setActiveSmart(null); goTo('gallery'); setSmartDraft(true); setFilterOpen(true) }
  const updateSmart = async () => {
    try { const saved = await api(`/collections/${activeSmart.id}`, { method: 'PATCH', body: JSON.stringify({ query: filterSearch() }) }); setActiveSmart(current => ({ ...current, query: saved.query })); await loadCollections(); setMessage(`已更新「${saved.name}」的條件`) } catch (error) { setMessage(errorNote(error)) }
  }
  const renameCollection = async (id, name) => {
    try { await api(`/collections/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }); await loadCollections(); setActiveCollection(current => current?.id === id ? { ...current, name } : current); setActiveSmart(current => current?.id === id ? { ...current, name } : current); return true } catch (error) { setMessage(errorNote(error)); return false }
  }
  const deleteCollection = async id => {
    try { await api(`/collections/${id}`, { method: 'DELETE' }); await loadCollections(); if (activeCollection?.id === id) setActiveCollection(null); if (activeSmart?.id === id) setActiveSmart(null); setSelected(current => current && { ...current, collections: (current.collections || []).filter(cid => cid !== id) }); setMessage('已刪除書單') } catch (error) { setMessage(errorNote(error)) }
  }
  // 加入或移出書單；回傳更新後的作品書單清單
  const changeMembership = async (ids, collectionId, action) => {
    try {
      await api(`/collections/${collectionId}/items`, { method: 'POST', body: JSON.stringify({ ids, action }) })
      const update = entry => ids.includes(entry.id) ? { ...entry, collections: action === 'add' ? [...new Set([...(entry.collections || []), collectionId])] : (entry.collections || []).filter(cid => cid !== collectionId) } : entry
      setItems(current => current.map(update)); setSelected(current => current && update(current))
      await loadCollections()
      if (activeCollection?.id === collectionId && action === 'remove') load()
      return true
    } catch (error) { setMessage(errorNote(error)); return false }
  }
  const showList = kind => {
    setActiveCollection(null); setActiveSmart(null); setDeadOnly(false); setSelectedTags([]); setExcludedTags([]); setQuery(''); setSource(''); setKind('')
    setUnratedOnly(kind === 'unrated'); setState(kind === 'reading' ? '閱讀中' : kind === 'unrated' ? '已完食' : ''); setSort('opened'); goTo('gallery')
  }
  const openCollection = collection => {
    if (collection?.query) { setActiveCollection(null); applySearch(collection.query); setActiveSmart(collection); goTo('gallery'); return }
    if (!collection && activeSmart) { leaveSmart(); return }
    setActiveSmart(null); setActiveCollection(collection)
    if (collection) { setDeadOnly(false); goTo('gallery') }
  }

  const visit = async item => {
    if (!item?.id) return
    pendingVisit.current = { id: item.id, at: Date.now() }
    try { await api(`/resources/${item.id}/open`, { method: 'POST' }) } catch { /* 閱讀事件失敗不影響開啟連結 */ }
    bumpSidebar()  // 開連結不改狀態（可能只是確認還有效），看完回來由提示卡決定
  }
  // 批次編輯：點卡片改為勾選；Shift + 點擊可一次選取一段範圍。
  const lastPicked = useRef(null)
  const toggleBatch = useCallback((item, event) => {
    setBatch(current => {
      const next = new Set(current || [])
      const list = itemsRef.current
      if (event?.shiftKey && lastPicked.current != null) {
        const a = list.findIndex(entry => entry.id === lastPicked.current), b = list.findIndex(entry => entry.id === item.id)
        if (a >= 0 && b >= 0) for (const entry of list.slice(Math.min(a, b), Math.max(a, b) + 1)) next.add(entry.id)
      } else if (next.has(item.id)) next.delete(item.id)
      else next.add(item.id)
      lastPicked.current = item.id
      return next
    })
  }, [])
  const runBatch = async (action, extra = {}) => {
    const ids = [...(batch || [])]
    if (!ids.length) return
    if (action === 'delete' && !await ask(`確定將選取的 ${ids.length} 件移出典藏？一小時內可以復原。`, { confirm: '移出典藏', danger: true })) return
    try {
      const result = await api('/resources/batch', { method: 'POST', body: JSON.stringify({ ids, action, ...extra }) })
      setMessage(action === 'delete' ? `已移除 ${result.count} 件` : `已更新 ${result.count} 件`, result.undo)
      if (action === 'delete') { ids.forEach(announceRemoved); setBatch(new Set()) }
      if (action.endsWith('_tags')) loadTagCategories(true)
      load()
    } catch (error) { setMessage(errorNote(error)) }
  }
  const remove = async () => {
    if (!selected?.id || !await ask(`確定將「${titleFor(selected)}」移出典藏？`, { confirm: '移出典藏', danger: true })) return
    try { const result = await api(`/resources/${selected.id}`, { method: 'DELETE' }); announceRemoved(selected.id); formDirty.current = false; closeShowcase(); setMessage('已移除典藏', result?.undo); load() } catch (error) { setMessage(errorNote(error)) }
  }
  // 隨機開啟一件：先問總數，再用隨機位移取一筆
  const randomPick = async (filter, empty) => {
    try {
      const first = await api(`/resources?limit=1${filter}`)
      if (!first.total) { setMessage(empty); return }
      const pick = await api(`/resources?limit=1&sort=created${filter}&offset=${Math.floor(Math.random() * first.total)}`)
      open(pick.items[0])
    } catch (error) { setMessage(errorNote(error)) }
  }
  // 隨機開啟一件未讀；tags 有值時只從帶有其中任一標籤的作品挑（口味地圖的「挑一本」）。只抽作品，不抽網站頁面、創作者頁
  const randomUnread = (tags = []) => {
    const extra = tags.map(tag => `&tags=${encodeURIComponent(tag)}`).join('') + (tags.length > 1 ? '&tag_mode=any' : '')
    return randomPick(`&works=true&status=%E6%9C%AA%E8%AE%80${extra}`, '沒有未讀的典藏')
  }
  // 抽抽（搜尋旁的按鈕）：完全隨機，不看狀態、評分、篩選，整個典藏任何一件都可能抽到
  const drawAny = () => randomPick('', '典藏是空的，先收錄幾件作品吧')
  // 每次重新建立：動作裡會用到目前的頁面與 load，記住舊的會拿到舊的條件
  const actions = [
    { id: 'new', icon: '＋', label: '收錄新典藏', hint: 'N', keywords: 'add new 新增', run: () => open(EMPTY) },
    { id: 'draw', icon: '⚄', label: '抽抽：完全隨機抽一件', keywords: 'random draw 抽 隨機', run: () => drawAny() },
    { id: 'random', icon: '⚄', label: '隨機抽一件未讀', keywords: 'random 深潛 驚喜', run: () => randomUnread() },
    { id: 'import', icon: '⇊', label: '一次收錄', keywords: 'import bulk 匯入 批次 收錄 網址', run: () => setPanel('import') },
    { id: 'undo', icon: '↶', label: '復原上一個動作', hint: 'Ctrl Z', keywords: 'undo 復原 還原 刪除', run: undoLatest },
    { id: 'unrated', icon: '★', label: '列出待評分的作品', keywords: 'rating 評分 待評分', run: () => showList('unrated') },
    ...(TOUCH ? [] : [{ id: 'keys', icon: '?', label: '鍵盤快捷鍵', hint: '?', keywords: 'shortcut keyboard 說明 help', run: () => setPanel('keys') }]),
    ...INSIGHT_TABS.map(([id, label]) => ({ id: `insight-${id}`, icon: tabPage(id) === 'tidy' ? '✓' : '✦', label: `開啟${label}`, keywords: 'insight 關聯 整理 duplicate 健檢 維護', run: () => { setInsightTab(id); goTo(tabPage(id)) } })),
    ...PAGES.map(([id, icon, label]) => ({ id: `page-${id}`, icon, label: `前往${label}`, keywords: id, run: () => goTo(id) })),
    { id: 'backup', icon: '↧', label: '開啟備份中心', keywords: 'backup restore 還原 封面', run: () => setPanel('backup') },
    { id: 'download', icon: '⤓', label: '下載完整備份', keywords: 'backup export 匯出', run: () => { const link = document.createElement('a'); link.href = '/api/backup'; link.download = ''; link.click() } },
    ...['', ...MAIN_STATES].map(value => ({ id: `status-${value || 'all'}`, icon: '◐', label: value ? `只看「${value}」` : '顯示全部狀態', keywords: 'filter 篩選 狀態', run: () => { goTo('gallery'); setState(value) } })),
  ]

  const [crumb, headline] = page === 'creator' ? ['資料頁', creator] : PAGE_TITLES[page]
  const prevNext = useMemo(() => {
    if (!selected?.id || !context) return {}
    const index = context.findIndex(entry => entry.id === selected.id)
    return index < 0 ? {} : { prev: context[index - 1], next: context[index + 1] }
  }, [selected, context])

  return <div className={`oas-shell nav-${navVariant}`}><div className="oas-ambient" aria-hidden="true"><i className="is-on" /><i /></div><div className="oas-grain" aria-hidden="true" />
    {navVariant !== 'phone' && <Sidebar variant={navVariant} onToggle={toggleNav} onShowList={showList} tidyCounts={tidyCounts}
      page={page} goTo={goTo} openById={openById} onPalette={() => setPaletteOpen(true)} onBackup={() => setPanel('backup')} onDuplicates={() => { setInsightTab('duplicates'); goTo('tidy') }} onAttention={() => { setInsightTab('attention'); goTo('tidy') }} onTagSuggest={() => { setInsightTab('rules'); goTo('tidy') }} refreshKey={sidebarKey}
      collections={collections} activeCollection={activeCollection || activeSmart} onCollection={openCollection} onShortcuts={() => setPanel('keys')}
      onCreateCollection={createCollection} onNewSmart={newSmart} onRenameCollection={renameCollection} onDeleteCollection={deleteCollection} />}
    <button type="button" className="skip-link" onClick={() => document.getElementById('oas-main')?.focus()}>跳到主要內容</button>
    <main className="oas-main" id="oas-main" tabIndex={-1}>
      <header className="oas-topbar"><div className="crumb">個人圖書館 / <b>{crumb}</b></div><div className="top-actions">
        <SearchBox value={query} onChange={text => { setQuery(text); if (text && page !== 'gallery') goTo('gallery') }} onOpenItem={item => open(item)} onTag={openTag} />
        <button type="button" className="draw-button" onClick={drawAny} title="抽抽：從整個典藏完全隨機抽一件" aria-label="抽抽"><i aria-hidden="true">⚄</i><span>抽抽</span></button>
        <button type="button" className="palette-trigger for-keyboard" onClick={() => setPaletteOpen(true)} title="指令面板">指令 <kbd>Ctrl K</kbd></button>
        <button className="add-button" onClick={() => open(EMPTY)}>＋ 收錄典藏</button>
      </div></header>
      {page === 'gallery' || page === 'home' ? null : <section className={`page-title${page === 'creator' ? ' is-name' : ''}`}><h1>{headline}</h1></section>}
      {page === 'creator' ? <CreatorView key={`${creator}-${dataVersion}`} name={creator} onOpen={open} onHover={hoverAmbient} onQuick={quick} onProfile={openProfile} onFilterTags={filterTags} onPickUnread={randomUnread} notify={setMessage} density={density} />
        : page === 'taste' || page === 'tidy' ? <InsightsView key={dataVersion} page={page} tab={tab} setTab={setInsightTab} onOpenItem={item => open(item)} onFilterTags={tags => filterTags(tags, 'any')} onPickUnread={randomUnread} onChanged={() => load()} notify={setMessage} counts={tidyCounts} onBackup={() => setPanel('backup')} />
        : page === 'stats' ? <StatsView key={dataVersion} onTag={openTag} onStatus={status => filterTags([], 'all', status)} notify={setMessage} />
        : page === 'home' ? <HomeView onOpen={open} onHover={hoverAmbient} onQuick={quick} onTag={openTag} onShowList={showList} onCollection={openCollection} collections={collections} linkOf={item => linkFor(item, sourceList)} onVisit={visit} notify={setMessage} version={dataVersion} personal={navVariant === 'phone'} /> : <>
        <h1 className="sr-only">書庫</h1>
        <section className="filter-row"><nav className="status-rail" aria-label="閱讀狀態" style={{ '--n': statusTabs.length, '--i': Math.max(0, statusTabs.indexOf(state)) }}><i aria-hidden="true" />{statusTabs.map(value => <button key={value || 'all'} type="button" aria-pressed={state === value} className={state === value ? 'is-active' : ''} onClick={() => setState(value)}><span>{value || '全部'}</span><b>{(value ? dashboard?.status_counts?.[value] : dashboard?.total) || ''}</b></button>)}</nav></section>
        {navVariant === 'phone' && <section className="list-chips" aria-label="書單">
          {collections.map(collection => { const active = (activeCollection || activeSmart)?.id === collection.id; return <button key={collection.id} type="button" className={active ? 'is-active' : undefined} aria-pressed={active} onClick={() => openCollection(active ? null : collection)}><i aria-hidden="true">{collection.query ? '⌕' : '▤'}</i>{collection.name}<small>{collection.count}</small></button> })}
          <button type="button" className="is-add" onClick={() => setSmartDialog('choose')}>＋ 新增書單</button>
        </section>}
        {showDeepStrip && <DeepStrip seed={dashboard.deep_dive} paused={Boolean(selected)} onOpen={open} />}
        <section className="gallery-heading"><h2>{activeCollection ? `書單：${activeCollection.name}` : activeSmart ? `智慧書單：${activeSmart.name}` : '全部作品'}{activeCollection && (navVariant === 'phone' ? <span className="smart-actions"><button type="button" onClick={() => setSmartDialog('rename')}>改名</button><button type="button" onClick={removeManual}>刪除</button><button type="button" onClick={() => setActiveCollection(null)}>離開</button></span> : <button type="button" className="query-chip" onClick={() => setActiveCollection(null)} title="回到全部典藏">離開書單 ×</button>)}{activeSmart && <span className="smart-actions"><button type="button" onClick={() => setSmartDialog('rename')}>改名</button><button type="button" onClick={removeSmart}>刪除</button><button type="button" onClick={leaveSmart} title="回到全部典藏">離開</button></span>}</h2>
          <div className="gallery-tools"><span>{total > items.length ? `顯示 ${items.length} / ${total} 件` : `${items.length} 件`}</span>{!batch && <span className="gallery-hint">懸停封面可直接評分，或按 1–5</span>}
            <div className="density" role="group" aria-label="卡片大小">{(navVariant === 'phone' ? PHONE_DENSITIES : DENSITIES).map(([value, label]) => { const on = navVariant === 'phone' ? (density === 's') === (value === 's') : density === value; return <button key={value} type="button" aria-pressed={on} className={on ? 'is-active' : ''} onClick={() => changeDensity(value)} title={navVariant === 'phone' ? label : `卡片${label}`}>{label}</button> })}</div>
            <button type="button" className="dropdown-trigger filter-open" aria-haspopup="dialog" onClick={() => setFilterOpen(true)}><span>篩選</span>{filterCount > 0 && <b>{filterCount}</b>}</button>
            <Dropdown small ariaLabel="排序" value={sort} onChange={setSort} options={SORTS} />
            {(dashboard?.dead_links > 0 || deadOnly) && <button type="button" className={`batch-toggle dead-toggle${deadOnly ? ' is-active' : ''}`} onClick={() => setDeadOnly(!deadOnly)} title="只看來源已失效的作品">來源失效 {dashboard?.dead_links ?? 0}</button>}
            <button type="button" className={`batch-toggle${batch ? ' is-active' : ''}`} onClick={() => setBatch(batch ? null : new Set())}>{batch ? '完成' : '批次選取'}</button>
          </div></section>
        {(applied.length > 0 || smartDirty) && <section className="applied-row" aria-label="已套用的條件">
          <div className="applied-chips">{applied.map(item => <button key={item.key} type="button" className={item.excluded ? 'is-excluded' : undefined} onClick={item.remove} aria-label={`移除條件 ${item.label}`}>{item.label}<i aria-hidden="true">×</i></button>)}</div>
          <div className="applied-actions">
            {selectedTags.length > 1 && <button type="button" onClick={() => setTagMode(tagMode === 'all' ? 'any' : 'all')} title="切換多個標籤的比對方式">{tagMode === 'all' ? '標籤要同時符合' : '標籤符合其一'}</button>}
            {!activeSmart && applied.length > 1 && <button type="button" onClick={clearFilters}>全部清除</button>}
            {!activeCollection && !activeSmart && !batch && applied.length > 0 && <button type="button" className="is-strong" onClick={() => setSmartDialog('new')} title="把這些條件存到側欄，內容會自動更新">存成智慧書單</button>}
          </div>
          {smartDirty && <div className="smart-dirty" role="status"><span>條件改過了，書單還沒更新</span>
            <button type="button" className="is-strong" disabled={!hasFilters} onClick={updateSmart}>儲存變更</button>
            <button type="button" disabled={!hasFilters} onClick={() => setSmartDialog('new')}>另存新書單</button>
            <button type="button" onClick={() => applySearch(activeSmart.query)}>還原</button>
          </div>}
        </section>}
        {items.length === 0 && loadingList ? <SkeletonGrid columns={Math.max(1, Math.floor((viewport - (navVariant === 'full' ? 232 : navVariant === 'mini' ? 76 : 0) - 150) / ((DENSITIES.find(([value]) => value === density)?.[2] || 220) + 14)))} />
          : items.length === 0 ? <EmptyResults query={query.trim()} didYouMean={didYouMean} hasFilters={hasFilters} otherFilters={Boolean(state || source || kind || selectedTags.length || excludedTags.length || deadOnly || unratedOnly)}
            onSearch={setQuery} onClearOthers={() => { setState(''); setSource(''); setKind(''); setSelectedTags([]); setExcludedTags([]); setDeadOnly(false); setUnratedOnly(false) }} /> : null}
        <Masonry minColumn={navVariant === 'phone' && density === 's' ? 140 : DENSITIES.find(([value]) => value === density)?.[2] || 220} className={`density-${density}${loadingList && items.length ? ' is-refreshing' : ''}`} items={items} renderItem={item => <Card key={item.id} item={item} onOpen={batch ? toggleBatch : openInGallery} onHover={hoverAmbient} onQuick={batch ? null : quick} picked={batch ? batch.has(item.id) : undefined} />} />
        {items.length < total && <div className="load-more"><button type="button" onClick={loadMore} disabled={loadingMore}>{loadingMore ? '載入中…' : `再載入 ${Math.min(PAGE_SIZE, total - items.length)} 件（尚有 ${total - items.length} 件）`}</button></div>}
      </>}
    </main>
    {/* 放在主畫面外面：展示頁開著時主畫面是隱藏的，提示訊息放裡面會看不到 */}
    {message && <div className={`oas-toast${message.undo ? ' has-undo' : ''}${message.error ? ' is-error' : ''}`} role={message.error ? 'alert' : 'status'}><span>{message.text}</span>
      {message.undo && <button type="button" className="toast-undo" onClick={() => { const id = message.undo; setMessage(''); undoAction(id) }}>復原<kbd>Ctrl Z</kbd></button>}
      <button type="button" className="toast-close" onClick={() => setMessage('')} aria-label="關閉提示">×</button></div>}
    {selected && <Showcase key={selected.id ? 'item' : 'new'} item={selected} direction={direction} link={linkFor(selected, sourceList)} prev={prevNext.prev} next={prevNext.next} onNavigate={navigate} onClose={closeShowcase} onQuick={quick} onSave={save} onRemove={remove} onTag={openTag} onVisit={visit} onBulk={async () => { if (formDirty.current && !await confirmLeave()) return; formDirty.current = false; closeShowcase(); setPanel('import') }} dirtyRef={formDirty} onOpenDuplicate={openDuplicate} flipFrom={flipFrom} coverHint={coverHint} collections={collections.filter(collection => !collection.query)} onMembership={changeMembership} onCreateCollection={createCollection} onRelinked={saved => { setSelected(saved); load(); setMessage(saved.added_tags ? `已改用 ${saved.source_name}，補上 ${saved.added_tags} 個標籤` : `已改用 ${saved.source_name}`) }} />}
    {warmItem && !selected && <div className="showcase-warm" aria-hidden="true" inert=""><Showcase warm key="warm" item={warmItem} onClose={() => {}} onQuick={() => Promise.resolve()} /></div>}
    <AskHost />
    {panel === 'backup' && <BackupCenter close={() => setPanel(null)} notify={setMessage} reload={() => { load(); loadCollections(); loadTagCategories(true); setDataVersion(value => value + 1); api('/sources').then(setSourceList).catch(() => {}) }} />}
    {batch && page === 'gallery' && <BatchBar collections={collections.filter(collection => !collection.query)} onCollection={(collectionId, action) => changeMembership([...batch], collectionId, action).then(ok => ok && setMessage(action === 'add' ? `已加入書單（${batch.size} 件）` : `已移出書單（${batch.size} 件）`))} count={batch.size} onSelectAll={() => setBatch(new Set(items.map(item => item.id)))} onClear={() => setBatch(new Set())} onRun={runBatch} onDone={() => setBatch(null)} />}
    {panel === 'keys' && <ShortcutHelp close={() => setPanel(null)} />}
    {panel === 'import' && <ImportDialog dirtyRef={formDirty} close={() => setPanel(null)} notify={setMessage} onDone={() => { load(); bumpSidebar() }} onUndo={undoAction} />}
    <HoverPreview disabled={Boolean(selected || batch || paletteOpen || panel)} />
    <OfflineBanner onBack={() => { load(); loadCollections(); bumpSidebar() }} />
    {!selected && <BackToTop raised={Boolean(batch && page === 'gallery')} />}
    {navVariant === 'phone' && <BottomTabs page={page} goTo={goTo} tidyCounts={tidyCounts} />}
    {returning && <ReturnPrompt key={returning.id} item={returning} onQuick={(item, patch) => quick(item, patch).then(saved => { bumpSidebar(); return saved })} onOpen={() => { const item = returning; setReturning(null); if (selected?.id !== item.id) open(item) }} onClose={() => setReturning(null)} />}
    {filterOpen && <FilterSheet included={selectedTags} excluded={excludedTags} mode={tagMode} kind={kind} source={source} sources={sources} total={total} loading={loadingList} filterParams={filterParams} drafting={smartDraft} canSave={hasFilters}
      onIncluded={setSelectedTags} onExcluded={setExcludedTags} onMode={setTagMode} onKind={setKind} onSource={setSource} onClearAll={() => { setKind(''); setSource(''); setSelectedTags([]); setExcludedTags([]) }} onSave={() => setSmartDialog('new')} onClose={() => { setFilterOpen(false); setSmartDraft(false) }} />}
    {smartDialog === 'new' && <SmartDialog title="存成智慧書單" initial={smartName()} applied={applied} total={total} submit="儲存" onSubmit={saveSmart} onClose={() => setSmartDialog(null)} />}
    {smartDialog === 'rename' && (activeSmart || activeCollection) && <SmartDialog title={activeSmart ? '替智慧書單改名' : '替書單改名'} initial={(activeSmart || activeCollection).name} submit="儲存" onSubmit={renameSmart} onClose={() => setSmartDialog(null)} />}
    {smartDialog === 'choose' && <ListTypeDialog onManual={() => setSmartDialog('manual')} onSmart={() => { setSmartDialog(null); newSmart() }} onClose={() => setSmartDialog(null)} />}
    {smartDialog === 'manual' && <SmartDialog title="新增手動書單" initial="" submit="建立" onSubmit={saveManual} onClose={() => setSmartDialog(null)} />}
    {paletteOpen && <CommandPalette actions={actions} onClose={() => setPaletteOpen(false)} onOpenItem={item => open(item)} onTag={filterTag} />}
  </div>
}

// 批次編輯工具列：固定在畫面底部，對已選作品一次設定狀態、評分、類型或增刪標籤。
function BatchBar({ count, onSelectAll, onClear, onRun, onDone, collections, onCollection }) {
  const [tagText, setTagText] = useState('')
  const [tagNames, setTagNames] = useState([])
  useEffect(() => { api('/tags').then(setTagNames).catch(() => {}) }, [])
  const tags = tagText.split(/[,，]/).map(value => value.trim()).filter(Boolean)
  const disabled = count === 0
  return <div className="batch-bar" role="toolbar" aria-label="批次編輯">
    <b>已選 {count} 件</b>
    <button type="button" className="ghost" onClick={onSelectAll}>全選已載入</button>
    {count > 0 && <button type="button" className="ghost" onClick={onClear}>取消選取</button>}
    <span className="batch-divider" />
    <Dropdown small ariaLabel="設定狀態" value="" onChange={value => value && onRun('status', { status: value })} options={[{ value: '', label: '設定狀態' }, ...STATUS_OPTIONS]} />
    <Dropdown small ariaLabel="設定評分" value="" onChange={value => value !== '' && onRun('rating', { rating: Number(value) })} options={[{ value: '', label: '設定評分' }, ...[5, 4.5, 4, 3.5, 3, 2.5, 2, 1.5, 1, 0.5, 0].map(value => ({ value: String(value), label: value ? `${'★'.repeat(Math.floor(value))}${value % 1 ? '½' : ''}　${value.toFixed(1)}` : '清除評分' }))]} />
    <Dropdown small ariaLabel="設定類型" value="" onChange={value => value && onRun('kind', { kind: value })} options={[{ value: '', label: '設定類型' }, ...KINDS.map(value => ({ value, label: value }))]} />
    {collections.length > 0 && <Dropdown small ariaLabel="加入書單" value="" onChange={value => value && !disabled && onCollection(Number(value), 'add')} options={[{ value: '', label: '加入書單' }, ...collections.map(collection => ({ value: String(collection.id), label: collection.name }))]} />}
    <span className="batch-divider" />
    <input className="batch-tags" list="oas-batch-tags" value={tagText} onChange={event => setTagText(event.target.value)} placeholder="標籤（逗號分隔）" disabled={disabled} />
    <datalist id="oas-batch-tags">{tagNames.map(name => <option key={name} value={name} />)}</datalist>
    <button type="button" className="ghost" disabled={disabled || !tags.length} onClick={() => { onRun('add_tags', { tags }); setTagText('') }}>＋ 加標籤</button>
    <button type="button" className="ghost" disabled={disabled || !tags.length} onClick={() => { onRun('remove_tags', { tags }); setTagText('') }}>－ 移除</button>
    <span className="batch-divider" />
    <button type="button" className="remove" disabled={disabled} onClick={() => onRun('delete')}>刪除</button>
    <button type="button" className="primary" onClick={onDone}>完成</button>
  </div>
}

function BackupCenter({ close, notify, reload }) {
  const dialog = useFocusTrap()
  const [mode, setMode] = useState('merge')
  const [busy, setBusy] = useState(false)
  // 公開版：備份放在 App 私有空間，匯出才存到使用者選的資料夾，選過就記住
  const [privateFiles, setPrivateFiles] = useState([])
  const [folder, setFolder] = useState(() => window.OASNative?.backupFolderName?.() || '')
  useEffect(() => {
    if (!isPublic()) return
    api('/backup/files').then(setPrivateFiles).catch(() => {})
    window.__oasFolderChanged = () => setFolder(window.OASNative?.backupFolderName?.() || '')
    return () => { delete window.__oasFolderChanged }
  }, [])
  const restoreFromApp = async name => {
    if (mode === 'replace' && !await ask('「取代」會先清空目前所有典藏，再寫入備份內容。確定繼續？', { confirm: '清空並取代', danger: true })) return
    setBusy(true)
    try {
      const data = await api(`/backup/files/${encodeURIComponent(name)}`)
      const report = await api(`/restore?mode=${mode}`, { method: 'POST', body: JSON.stringify(data) })
      notify(`還原完成：新增 ${report.created} 筆，略過 ${report.skipped} 筆，新增來源 ${report.sources_created} 個`)
      reload(); close()
    } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  const restore = async event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (mode === 'replace' && !await ask('「取代」會先清空目前所有典藏，再寫入備份內容。建議先匯出一份目前資料。確定繼續？', { confirm: '清空並取代', danger: true })) return
    setBusy(true)
    try {
      const data = JSON.parse(await file.text())
      const report = await api(`/restore?mode=${mode}`, { method: 'POST', body: JSON.stringify(data) })
      notify(`還原完成：新增 ${report.created} 筆，略過 ${report.skipped} 筆，新增來源 ${report.sources_created} 個`)
      reload(); close()
    } catch (error) { notify(error instanceof SyntaxError ? { text: '檔案不是有效的 JSON', error: true } : errorNote(error)) } finally { setBusy(false) }
  }
  return <div ref={dialog} className="oas-modal" role="dialog" aria-modal="true" aria-label="備份中心" onClick={event => event.target === event.currentTarget && close()}>
    <section className="oas-modal-card">
      <button className="drawer-close" onClick={close} aria-label="關閉備份中心">×</button>
      <h2>備份中心</h2>
      <p className="drawer-subtitle">完整備份包含典藏、標籤、評分、狀態、筆記、時間戳與來源規則，格式為可讀的 JSON。</p>
      {isPublic()
        ? <div className="backup-block"><h3>匯出</h3><p>備份平常存在 App 裡，解除安裝就會消失；要換手機或留底時，匯出到你選的資料夾。備份內容沒有加密，資料夾裡的檔案其他 App 可能讀得到。</p>
          {folder && <p className="maint-note">匯出位置：{folder}</p>}
          <div className="cover-actions"><a className="save" href="/api/backup" download>{folder ? '匯出完整備份' : '選資料夾並匯出'}</a>{folder && <button type="button" className="ghost" onClick={() => window.OASNative?.chooseBackupFolder?.()}>更換資料夾</button>}</div></div>
        : <div className="backup-block"><h3>匯出</h3><p>下載目前所有資料的快照。建議在大量編輯或還原前先匯出一次。</p><a className="save" href="/api/backup" download>下載完整備份</a></div>}
      <div className="backup-block"><h3>還原</h3><p>選擇先前匯出的 <code>oas-backup-*.json</code>。</p>
        <div className="mode-switch">{[['merge', '合併', '保留現有資料，只加入缺少的典藏'], ['replace', '取代', '清空後完整還原至備份狀態']].map(([value, label, hint]) => <label key={value} className={mode === value ? 'is-active' : ''}><input type="radio" name="restore-mode" value={value} checked={mode === value} onChange={() => setMode(value)} /><b>{label}</b><small>{hint}</small></label>)}</div>
        <label className={`save file-pick${busy ? ' is-busy' : ''}`}>{busy ? '還原中…' : '選擇備份檔'}<input type="file" accept="application/json,.json" disabled={busy} onChange={restore} /></label>
        {privateFiles.length > 0 && <><p className="pref-heading"><b>App 裡的備份</b></p><ul className="private-backups">{privateFiles.map(file => <li key={file.name}><span>{new Date(file.at).toLocaleString('zh-TW', { hour12: false })}<small>{file.name.startsWith('oas-auto-') ? '自動' : '手動'}・{formatBytes(file.size)}</small></span><button type="button" className="ghost" disabled={busy} onClick={() => restoreFromApp(file.name)}>還原</button></li>)}</ul></>}
      </div>
      <p className="backup-moved">封面快取、補齊作品資訊與巡檢來源在「整理 → 維護」；缺標題、封面或來源失效的作品在「整理 → 待整理」。</p>
    </section>
  </div>
}

// list：開啟時一起帶上的清單（書架、資料頁）。不用每次新建的 onOpen 函式傳，memo 才擋得住開關作品時整頁卡片重新渲染
// 深潛帶：每 6.5 秒換一件；封面載好才換，不會閃白。App 在背景、作品頁開著、游標停在上面時不換
function DeepStrip({ seed, paused, onOpen }) {
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

const Card = memo(function Card({ item, onOpen, onHover, onQuick, picked, list }) {
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

// 首頁：主視覺（閱讀中與深潛作品，最多 5 件，手動切換）＋橫向書架列（繼續閱讀、探索書架、書單）
// 回到首頁時先顯示上次的內容（不用等 API、不閃骨架），同時在背景以同一個 seed 更新
let homeCache = null
let homeHeroIndex = 0
function HomeView({ onOpen, onHover, onQuick, onTag, onShowList, onCollection, collections, linkOf, onVisit, notify, version, personal }) {
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
    api(`/home?seed=${seed}${personal ? '&personal=true' : ''}`).then(result => live && setData(result)).catch(error => live && notify(errorNote(error))).finally(() => live && setLoading(false))
    return () => { live = false }
  }, [seed, version, personal])  // version：復原、還原備份後重新載入；personal：手機直放橫放跨過寬度門檻
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
  useLayoutEffect(() => place(index), [index, data])
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
          <ShelfRow items={row.items} render={item => <Card key={item.id} item={item} onOpen={onOpen} list={row.items} onHover={onHover} onQuick={quickLocal} />} />
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
              {isFaceCover(item) ? <CoverFace item={item} /> : <img src={coverSrc(item, 480)} srcSet={`${coverSrc(item, 480)} 480w, ${coverSrc(item)} 1000w`} sizes="(max-width: 780px) 86vw, 420px" alt="" decoding="async" onError={hideBroken} />}
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

// 瀑布流：依封面比例把卡片依序放進目前最短的欄。追加項目只會接在欄底，既有卡片位置不變，因此「再載入」與圖片載入都不會跳動。
function Masonry({ items, renderItem, minColumn = 220, gap = 14, className = '' }) {
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

// 回到頂端：捲動超過一個半畫面才出現；批次工具列開著時往上避開。
// 典藏館零結果：說清楚找了什麼，給能直接按的下一步（拼字更正、拿掉其他篩選），再提示搜尋語法
function EmptyResults({ query, didYouMean, hasFilters, otherFilters, onSearch, onClearOthers }) {
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

function BackToTop({ raised }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const onScroll = () => setShown(window.scrollY > window.innerHeight * 1.5)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  return <button type="button" className={`back-to-top${shown ? ' is-shown' : ''}${raised ? ' is-raised' : ''}`} aria-label="回到頂端" title="回到頂端（Home）" tabIndex={shown ? 0 : -1} aria-hidden={!shown}
    onClick={event => {
      window.scrollTo({ top: 0, behavior: reducedMotion() ? 'auto' : 'smooth' })
      // 只有用鍵盤按（detail 為 0）才把焦點交給搜尋框；手指或滑鼠點不搶焦點，手機才不會跳出鍵盤、像是跳到搜尋
      if (event.detail === 0) document.querySelector('#oas-search')?.focus({ preventScroll: true })
      else event.currentTarget.blur()
    }}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" /></svg>
  </button>
}

function ShortcutHelp({ close }) {
  const dialog = useFocusTrap()
  return <div ref={dialog} className="oas-modal" role="dialog" aria-modal="true" aria-label="鍵盤快捷鍵" onClick={event => event.target === event.currentTarget && close()}>
    <section className="oas-modal-card shortcut-card">
      <button className="drawer-close" onClick={close} aria-label="關閉">×</button>
      <h2>鍵盤快捷鍵</h2>
      <p className="drawer-subtitle">在輸入框裡打字時，這些按鍵不會觸發。</p>
      {SHORTCUTS.map(([group, rows]) => <div key={group} className="shortcut-group"><h3>{group}</h3>
        <dl>{rows.map(([key, label]) => <div key={key + label}><dt>{key.split(' ').map(part => part === '＋' ? <span key={part}> ＋ </span> : <kbd key={part}>{part}</kbd>)}</dt><dd>{label}</dd></div>)}</dl>
      </div>)}
    </section>
  </div>
}

// 從外部網站回來：一鍵評分、標成已完食或閱讀中、記下看到哪；不擋畫面，只是開來看看就直接關掉，狀態不會變。
function ReturnPrompt({ item, onQuick, onOpen, onClose }) {
  const [editing, setEditing] = useState(false)
  const [progress, setProgress] = useState(item.progress || '')
  const done = item.status === '已完食'
  const reading = item.status === '閱讀中'
  // Esc 由 App 統一處理（先關其他視窗，最後才收起這張卡片）
  const saveProgress = event => { event.preventDefault(); onQuick(item, { progress: progress.trim() }).catch(() => {}); setEditing(false) }
  return <aside className="return-prompt" role="dialog" aria-label="剛剛在看的作品" style={{ '--cover-colour': colourFor(item) }}>
    <button type="button" className="return-cover" onClick={onOpen} title="開啟展示頁">{isFaceCover(item) ? <CoverFace item={item} width={240} /> : <img src={coverSrc(item, 240)} alt="" onError={hideBroken} />}</button>
    <div className="return-body">
      <p className="return-kicker">{done ? '已標成已完食' : reading ? '剛剛在看' : '剛剛開啟'}{item.progress ? `，看到 ${item.progress}` : ''}</p>
      <h3 title={titleFor(item)}>{shortTitle(item)}</h3>
      <div className="return-rate"><Stars value={item.rating} allowHalf onChange={rating => onQuick(item, { rating }).catch(() => {})} /><span>{item.rating > 0 ? Number(item.rating).toFixed(1) : '還沒評分'}</span></div>
      {editing ? <form className="return-progress" onSubmit={saveProgress}><input autoFocus value={progress} maxLength={40} onChange={event => setProgress(event.target.value)} placeholder="例如：第 3 話、p.45" aria-label="閱讀進度" /><button type="submit" className="primary">記下</button></form>
        : <div className="return-actions">
          {!done && <button type="button" className="primary" onClick={() => onQuick(item, { status: '已完食' }).catch(() => {})}>看完了</button>}
          {reading && <button type="button" className="ghost" onClick={() => setEditing(true)}>還沒看完，記進度</button>}
          {!done && !reading && <button type="button" className="ghost" onClick={() => onQuick(item, { status: '閱讀中' }).catch(() => {})}>還在看</button>}
          {done && <button type="button" className="ghost" onClick={onClose}>完成</button>}
        </div>}
    </div>
    <button type="button" className="return-close" onClick={onClose} aria-label="之後再說">×</button>
  </aside>
}

// 作者、社團、原作、角色的資料頁：全部作品、閱讀情況、常一起出現的人與標籤，也可以改分類。
function CreatorView({ name, onOpen, onHover, onQuick, onProfile, onFilterTags, onPickUnread, notify, density }) {
  const [profile, setProfile] = useState(null)
  const [items, setItems] = useState(null)
  const [missing, setMissing] = useState(false)
  const reload = () => {
    api(`/tags/profile?name=${encodeURIComponent(name)}`).then(setProfile).catch(() => setMissing(true))
    api(`/resources?limit=5000&sort=rating&tags=${encodeURIComponent(name)}`).then(result => setItems(result.items)).catch(() => setItems([]))
  }
  useEffect(reload, [name])
  const changeCategory = async category => {
    try { const result = await api('/tags/category', { method: 'PATCH', body: JSON.stringify({ name, category }) }); loadTagCategories(true); notify(`已把「${name}」改成「${CATEGORY_NAMES[category]}」`, result.undo); reload() } catch (error) { notify(errorNote(error)) }
  }
  const quickLocal = useCallback((item, patch) => onQuick(item, patch).then(saved => { setItems(current => current?.map(entry => entry.id === item.id ? { ...entry, ...saved } : entry)); return saved }), [onQuick])
  if (missing) return <p className="insight-empty">找不到「{name}」。這個標籤可能已經被合併或刪除。</p>
  if (!profile) return <p className="insight-empty">讀取中…</p>
  return <section className="creator" aria-label={`${name} 的資料頁`}>
    <div className="creator-head">
      <dl className="stat-tiles creator-stats">
        <div><dt>作品</dt><dd>{profile.count}</dd></div>
        <div><dt>未讀</dt><dd>{profile.unread}</dd></div>
        <div><dt>已完食</dt><dd>{profile.finished}</dd></div>
        <div><dt>平均評分</dt><dd className={profile.avg_rating ? 'is-rated' : ''}>{profile.avg_rating ? `★ ${profile.avg_rating.toFixed(1)}` : '—'}</dd><dd className="stat-note">{profile.rated} 件有評分</dd></div>
      </dl>
      <div className="creator-actions">
        <button type="button" className="primary" disabled={!profile.unread} onClick={() => onPickUnread([name])}>挑一本未讀</button>
        <button type="button" className="ghost" onClick={() => onFilterTags([name])}>在書庫篩選</button>
        <div className="creator-category"><span>分類</span><Dropdown small ariaLabel="標籤分類" value={profile.category} onChange={changeCategory} options={TAG_CATEGORIES.map(([value, label]) => ({ value, label }))} /></div>
      </div>
    </div>
    {profile.people.length > 0 && <div className="creator-related"><h3>常一起出現</h3><div>{profile.people.map(entry => <button key={entry.name} type="button" onClick={() => onProfile(entry.name)} title={`開啟 ${entry.name} 的資料頁`}><small>{CATEGORY_NAMES[entry.category]}</small>{entry.name}<b>{entry.count}</b></button>)}</div></div>}
    {profile.tags.length > 0 && <div className="creator-related"><h3>常見標籤</h3><div>{profile.tags.map(entry => <button key={entry.name} type="button" onClick={() => onFilterTags([name, entry.name])} title={`在書庫查看同時有 ${name} 與 #${entry.name} 的作品`}>#{entry.name}<b>{entry.count}</b></button>)}</div></div>}
    <h3 className="creator-works">全部作品<small>依評分排列</small></h3>
    {items === null ? <SkeletonGrid columns={4} count={8} /> : <Masonry minColumn={DENSITIES.find(([value]) => value === density)?.[2] || 220} className={`density-${density}`} items={items} renderItem={item => <Card key={item.id} item={item} onOpen={onOpen} list={items} onHover={onHover} onQuick={quickLocal} />} />}
  </section>
}
