import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import CommandPalette from './CommandPalette.jsx'
import { cancelPreview, HoverPreview, ImportDialog, OfflineBanner, schedulePreview, SearchBox, SkeletonGrid } from './Extras.jsx'
import InsightsView, { INSIGHT_TABS } from './InsightsView.jsx'
import Showcase from './Showcase.jsx'
import Sidebar, { PAGES } from './Sidebar.jsx'
import StatsView from './StatsView.jsx'
import TagPicker from './TagPicker.jsx'
import {
  api, colourFor, coverRatio, coverSrc, Dropdown, EMPTY, formatBytes, getHoveredItem, hideBroken, hoverAmbient, isEditing, KINDS, loadTagCategories, CoverFace, isFaceCover,
  CATEGORY_NAMES, linkFor, noteText, PAGE_SIZE, setAmbientColour, Stars, STATES, STATUS_COLOURS, STATUS_OPTIONS, TAG_CATEGORIES, titleFor,
} from './shared.jsx'

const SORTS = [{ value: 'updated', label: '最近更新' }, { value: 'created', label: '收錄時間' }, { value: 'opened', label: '最近開啟' }, { value: 'rating', label: '評分高到低' }, { value: 'title', label: '標題' }]
const PAGE_TITLES = { gallery: ['全部典藏', '今晚想走進哪個世界？'], explore: ['漫遊探索', '讓意外帶路。'], graph: ['關聯', '看出口味，也順手整理收藏。'], stats: ['統計', '你的收藏，用數字看。'], creator: ['資料頁', ''] }
// 這些分類的標籤點下去開資料頁，一般標籤則在典藏館篩選
const PROFILE_CATEGORIES = ['artist', 'group', 'parody', 'character']
// 從外部網站回來時，離開超過這麼久才詢問（太短多半只是切出去看一眼）
const RETURN_PROMPT_AFTER = 15000
// 卡片大小：瀑布流每欄的最小寬度
const DENSITIES = [['s', '小', 160], ['m', '中', 220], ['l', '大', 300]]
// 智慧書單與網址共用的篩選參數（不含頁面與書單）
const FILTER_KEYS = ['q', 'status', 'source', 'kind', 'tag', 'match', 'dead', 'sort']
const SHORTCUTS = [
  ['任何地方', [['/', '搜尋'], ['Ctrl K', '指令面板'], ['N', '收錄新典藏'], ['?', '顯示這份快捷鍵'], ['Home', '回到頂端'], ['Esc', '關閉視窗或離開批次選取']]],
  ['典藏卡片（懸停或用 Tab 聚焦時）', [['1–5', '評分，0 清除'], ['Enter', '開啟']]],
  ['展示頁', [['← →', '上一件、下一件'], ['1–5', '評分'], ['E', '編輯'], ['Esc', '關閉']]],
  ['批次選取', [['Shift ＋ 點擊', '一次選取一段範圍']]],
]
// 筆記裡只有系統自動寫的「原連結」「合併自」時，不算有筆記
const hasNote = item => Boolean(noteText(item))

// 共享元素轉場：封面從卡片位置放大成展示頁的封面，關閉時再縮回去。不支援 View Transitions 的瀏覽器直接切換。
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const transition = (update, { from, to } = {}) => {
  if (!document.startViewTransition || reducedMotion()) { update(); return }
  let target = null
  if (from) from.style.viewTransitionName = 'oas-cover'
  const running = document.startViewTransition(() => {
    if (from) from.style.viewTransitionName = ''
    flushSync(update)
    target = typeof to === 'function' ? to() : to
    if (target) target.style.viewTransitionName = 'oas-cover'
  })
  // 連續快速切換時前一個轉場會被略過（AbortError），屬正常情況，不需要報錯。
  running.ready.catch(() => {})
  running.finished.catch(() => {}).finally(() => { if (target) target.style.viewTransitionName = '' })
}
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
  const [query, setQuery] = useState(() => urlState.get('q') || '')
  const [state, setState] = useState(() => urlState.get('status') || '')
  const [source, setSource] = useState(() => urlState.get('source') || '')
  const [selectedTags, setSelectedTags] = useState(() => urlState.getAll('tag'))
  const [tagMode, setTagMode] = useState(() => urlState.get('match') === 'any' ? 'any' : 'all')
  const [selected, setSelected] = useState(null)
  const [context, setContext] = useState(null)
  const [direction, setDirection] = useState(0)
  const [page, setPage] = useState(() => PAGES.some(([id]) => id === urlState.get('page')) || (urlState.get('page') === 'creator' && urlState.get('name')) ? urlState.get('page') : 'gallery')
  const [creator, setCreator] = useState(() => urlState.get('name') || '')
  // 提示訊息：{ text, undo }；undo 是後端復原紀錄的 id，有值時提示旁邊顯示「復原」並停留較久
  const [message, setMessageState] = useState(null)
  const setMessage = useCallback((text, undoId = null) => setMessageState(text ? { text, undo: undoId } : null), [])
  const [dataVersion, setDataVersion] = useState(0)  // 復原後讓關聯頁、資料頁重新載入
  const [unratedOnly, setUnratedOnly] = useState(() => urlState.get('rated') === '0')
  const [returning, setReturning] = useState(null)  // 從外部網站回來時詢問的作品
  const pendingVisit = useRef(null)
  const [total, setTotal] = useState(0)
  const [sourceList, setSourceList] = useState([])
  const [panel, setPanel] = useState(null)
  const [graphFocus, setGraphFocus] = useState(null)
  const [insightTab, setInsightTab] = useState(() => INSIGHT_TABS.some(([id]) => id === urlState.get('view')) ? urlState.get('view') : 'taste')
  const [density, setDensity] = useState(() => { try { return localStorage.getItem('oas-density') || 'm' } catch { return 'm' } })
  const changeDensity = value => { setDensity(value); try { localStorage.setItem('oas-density', value) } catch { /* 只影響這次 */ } }
  const [activeSmart, setActiveSmart] = useState(null)
  const [savingSmart, setSavingSmart] = useState(null)  // 儲存智慧書單時輸入中的名稱
  const [paletteOpen, setPaletteOpen] = useState(false)
  const itemsRef = useRef(items)
  itemsRef.current = items

  const [loadingMore, setLoadingMore] = useState(false)
  const [loadingList, setLoadingList] = useState(true)  // 換篩選時重新載入中：沒有卡片時顯示輪廓，有卡片時淡化
  const [kind, setKind] = useState(() => urlState.get('kind') || '')
  const [sort, setSort] = useState(() => SORTS.some(option => option.value === urlState.get('sort')) ? urlState.get('sort') : 'updated')
  const [deadOnly, setDeadOnly] = useState(() => urlState.get('dead') === '1')
  const [collections, setCollections] = useState([])
  const [activeCollection, setActiveCollection] = useState(() => Number(urlState.get('list')) ? { id: Number(urlState.get('list')), name: '' } : null)
  const [sidebarKey, setSidebarKey] = useState(0)
  const bumpSidebar = () => setSidebarKey(value => value + 1)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const [navPreference, setNavPreference] = useState(() => { try { return localStorage.getItem('oas-nav') } catch { return null } })
  const [drawerOpen, setDrawerOpen] = useState(false)
  useEffect(() => {
    const onResize = () => setViewport(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  // 手機寬度用抽屜；其他寬度依使用者選擇，沒選過時窄視窗（< 1100px）預設迷你
  const navVariant = viewport < 780 ? 'drawer' : (navPreference || (viewport < 1100 ? 'mini' : 'full'))
  const toggleNav = () => {
    const next = navVariant === 'mini' ? 'full' : 'mini'
    setNavPreference(next)
    try { localStorage.setItem('oas-nav', next) } catch { /* 無法保存時只影響這次 */ }
  }
  const [batch, setBatch] = useState(null)  // 批次編輯：null 為一般模式，否則為已選作品 id 的 Set
  const filterParams = `sort=${sort}${unratedOnly ? `&opened=true&rated=false&not_status=${encodeURIComponent('閱讀中')}` : ''}${deadOnly ? '&dead=true' : ''}${activeCollection ? `&collection=${activeCollection.id}` : ''}${kind ? `&kind=${encodeURIComponent(kind)}` : ''}&q=${encodeURIComponent(query)}${state ? `&status=${encodeURIComponent(state)}` : ''}${source ? `&source=${encodeURIComponent(source)}` : ''}${selectedTags.map(tag => `&tags=${encodeURIComponent(tag)}`).join('')}${selectedTags.length > 1 ? `&tag_mode=${tagMode}` : ''}`
  const filterQuery = () => filterParams
  const load = async (reset = false) => {
    setLoadingList(true)
    try {
      // 重新載入時保留已展開的數量，避免儲存後畫面跳回第一頁
      const limit = reset ? PAGE_SIZE : Math.min(Math.max(PAGE_SIZE, Math.ceil(itemsRef.current.length / PAGE_SIZE) * PAGE_SIZE), 1000)
      const [resources, stats] = await Promise.all([api(`/resources?limit=${limit}&${filterQuery()}`), api('/dashboard')])
      setItems(resources.items); setTotal(resources.total ?? resources.items.length); setDashboard(stats); bumpSidebar()
    } catch (error) { setMessage(error.message) } finally { setLoadingList(false) }
  }
  const loadMore = async () => {
    setLoadingMore(true)
    try {
      const resources = await api(`/resources?limit=${PAGE_SIZE}&offset=${items.length}&${filterQuery()}`)
      setItems(current => [...current, ...resources.items.filter(item => !current.some(existing => existing.id === item.id))])
      setTotal(resources.total ?? total)
    } catch (error) { setMessage(error.message) } finally { setLoadingMore(false) }
  }
  useEffect(() => { const timer = setTimeout(() => load(true), 180); return () => clearTimeout(timer) }, [filterParams])
  useEffect(() => { api('/sources').then(setSourceList).catch(() => {}) }, [])
  // 「收錄到 OAS」書籤帶來的資料（?add=JSON）：開啟預先填好的收錄表單，並清掉網址參數。
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get('add')
    if (!raw) return
    window.history.replaceState(null, '', window.location.pathname)
    let data
    try { data = JSON.parse(raw) } catch { setMessage('書籤資料格式不正確'); return }
    const draft = { ...EMPTY, title: String(data.title || ''), image_url: String(data.image_url || ''), tags: Array.isArray(data.tags) ? data.tags.map(String) : [], source_url: String(data.url || '') }
    api('/metadata', { method: 'POST', body: JSON.stringify({ url: draft.source_url, fetch: false }) })
      .then(info => open({ ...draft, source_name: info.source_name, code: info.code, duplicate: info.duplicate }))
      .catch(() => open({ ...draft, code: draft.source_url }))
  }, [])
  useEffect(() => { if (!message) return; const timer = setTimeout(() => setMessage(''), message.undo ? 10000 : 3800); return () => clearTimeout(timer) }, [message])

  const quick = useCallback(async (item, patch) => {
    const apply = values => {
      setItems(current => current.map(entry => entry.id === item.id ? { ...entry, ...values } : entry))
      setSelected(current => current?.id === item.id ? { ...current, ...values } : current)
    }
    apply(patch)
    try {
      const saved = await api(`/resources/${item.id}/quick`, { method: 'PATCH', body: JSON.stringify(patch) })
      apply({ rating: saved.rating, status: saved.status, link_dead: saved.link_dead, progress: saved.progress, finished_at: saved.finished_at })
      setReturning(current => current?.id === item.id ? { ...current, ...saved } : current)
      api('/dashboard').then(setDashboard).catch(() => {})
      return saved
    } catch (error) {
      apply({ rating: item.rating, status: item.status, progress: item.progress })
      setReturning(current => current?.id === item.id ? { ...current, rating: item.rating, status: item.status, progress: item.progress } : current)
      setMessage(error.message)
      throw error
    }
  }, [])

  // 開啟展示頁：list 是目前所在的清單（典藏館或某個書架），供 ← / → 切換。
  const open = useCallback((item, list = null) => {
    setAmbientColour(colourFor(item), true)
    if (!window.history.state?.oasItem) window.history.pushState({ ...window.history.state, oasItem: item?.id ?? 'new' }, '')
    transition(() => { setDirection(0); setSelected(item); setContext(list) }, { from: item?.id ? cardCover(item.id) : null })
  }, [])
  // 展示頁內切換：不用 View Transition（整頁快照重繪成本高、會閃），由展示頁自己做輕量的方向動畫。
  const navigate = useCallback((item, step = 0) => {
    setAmbientColour(colourFor(item), true)
    const latest = itemsRef.current.find(entry => entry.id === item.id) || item
    setDirection(step); setSelected(latest)
  }, [])
  // 關閉時封面縮回原卡片（若卡片仍在畫面內）。
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const closeNow = () => {
    const id = selectedRef.current?.id
    transition(() => { setSelected(null); setContext(null) }, { to: () => id && cardCover(id, false) })
  }
  // 關閉展示頁一律走「返回」，讓瀏覽紀錄和畫面一致；popstate 會呼叫 closeNow
  const closeShowcase = () => { if (window.history.state?.oasItem) window.history.back(); else closeNow() }
  // 其他方式離開展示頁（篩選標籤、跳到關聯圖…）時，把這一筆紀錄改成一般頁面，之後按返回不會重新打開
  const forgetShowcase = () => { if (window.history.state?.oasItem) window.history.replaceState({ ...window.history.state, oasItem: undefined }, '') }
  useEffect(() => {
    // 第一筆紀錄記下開啟時的頁面，從其他頁按返回才回得來（例如一開始就在統計頁）
    // 重新整理時舊的「展示頁」紀錄要清掉，否則按返回會整頁重新載入，而不是關閉展示頁
    window.history.replaceState({ ...window.history.state, oasPage: window.history.state?.oasPage || page, oasCreator: window.history.state?.oasCreator || creator || undefined, oasItem: undefined }, '')
    const onPop = event => {
      const state = event.state || {}
      if (selectedRef.current && !state.oasItem) { closeNow(); return }
      setPage(current => {
        const target = state.oasPage || 'gallery'
        if (target !== current) { window.scrollTo({ top: 0 }); if (state.oasCreator) setCreator(state.oasCreator) }
        return target
      })
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  // 圖譜節點只帶摘要欄位；開啟前取回完整資料，避免儲存時把筆記等欄位寫成空白。
  const openById = useCallback(async id => {
    try { open(await api(`/resources/${id}`)) } catch (error) { setMessage(error.message) }
  }, [open])
  const openInGallery = useCallback(item => open(item, itemsRef.current), [open])
  const openDuplicate = id => api(`/resources/${id}`).then(item => { setDirection(0); setSelected(item); setContext(null) }).catch(error => setMessage(error.message))
  // 換頁：記一筆瀏覽紀錄，滑鼠返回鍵或 Alt + ← 可以回到上一頁
  const pushPage = (target, name = '') => {
    if (target === page && (target !== 'creator' || name === creator)) return
    forgetShowcase()
    window.history.pushState({ oasPage: target, oasCreator: name || undefined }, '')
  }
  const showInGraph = item => { setGraphFocus(item.id); setInsightTab('graph'); pushPage('graph'); transition(() => { setSelected(null); setPage('graph') }); window.scrollTo({ top: 0 }) }
  const filterTags = (tags, mode = 'all', status = '') => { pushPage('gallery'); setActiveSmart(null); setActiveCollection(null); setDeadOnly(false); setUnratedOnly(false); setKind(''); setTagMode(mode); transition(() => { setSelected(null); setPage('gallery'); setState(status); setSource(''); setQuery(''); setSelectedTags(tags) }); window.scrollTo({ top: 0 }) }
  const filterTag = tag => filterTags([tag])
  const openProfile = name => { pushPage('creator', name); transition(() => { setSelected(null); setCreator(name); setPage('creator') }); window.scrollTo({ top: 0 }) }
  // 展示頁、統計頁的標籤：作者、社團、原作、角色開資料頁，其餘在典藏館篩選
  const openTag = async tag => { const categories = await loadTagCategories(); PROFILE_CATEGORIES.includes(categories[tag]) ? openProfile(tag) : filterTag(tag) }
  const toggleTag = tag => setSelectedTags(current => current.includes(tag) ? current.filter(value => value !== tag) : [...current, tag])
  const goTo = target => { pushPage(target); transition(() => setPage(target)); window.scrollTo({ top: 0 }) }
  // 復原：提示訊息的「復原」按鈕、Ctrl + Z、指令面板都走這裡
  const undoAction = async id => {
    try {
      const result = await api(`/undo/${id}`, { method: 'POST' })
      setMessage(`已復原：${result.label}${result.kept ? `（${result.kept} 件收錄後已被編輯，保留下來）` : ''}`)
      setDataVersion(value => value + 1); loadTagCategories(true); loadCollections(); load()
    } catch (error) { setMessage(error.message) }
  }
  const undoLatest = async () => {
    try { const latest = await api('/undo/latest'); latest ? undoAction(latest.id) : setMessage('一小時內沒有可以復原的動作') } catch (error) { setMessage(error.message) }
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
        if (panel) { setPanel(null); return }
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
  const deepDive = dashboard?.deep_dive || items.filter(item => item.status === '未讀').sort((a, b) => b.rating - a.rating)[0]
  const save = async payload => {
    try {
      const saved = selected?.id ? await api(`/resources/${selected.id}`, { method: 'PATCH', body: JSON.stringify(payload) }) : await api('/resources', { method: 'POST', body: JSON.stringify(payload) })
      setMessage('典藏已儲存'); setAmbientColour(colourFor(saved), true); setSelected(saved); load()
      return true
    } catch (error) { setMessage(error.message); return false }
  }
  const loadCollections = () => api('/collections').then(list => {
    setCollections(list)
    // 從網址還原的書單只有 id，載入後補上名稱；書單已不存在就離開篩選
    setActiveCollection(current => current && !current.name ? (list.find(entry => entry.id === current.id) || null) : current)
  }).catch(() => {})
  // 篩選條件同步到網址（預設值不寫，網址保持乾淨）
  useEffect(() => {
    const params = new URLSearchParams()
    if (page !== 'gallery') params.set('page', page)
    if (query) params.set('q', query)
    if (state) params.set('status', state)
    if (source) params.set('source', source)
    if (kind) params.set('kind', kind)
    selectedTags.forEach(tag => params.append('tag', tag))
    if (selectedTags.length > 1 && tagMode === 'any') params.set('match', 'any')
    if (sort !== 'updated') params.set('sort', sort)
    if (activeCollection) params.set('list', String(activeCollection.id))
    if (deadOnly) params.set('dead', '1')
    if (unratedOnly) { params.set('opened', '1'); params.set('rated', '0') }
    if (page === 'graph' && insightTab !== 'taste') params.set('view', insightTab)
    if (page === 'creator' && creator) params.set('name', creator)
    if (selected?.id) params.set('item', String(selected.id))
    const next = params.toString()
    // 保留 history.state（返回鍵用的紀錄），只改網址
    if (next !== window.location.search.replace(/^\?/, '')) window.history.replaceState(window.history.state, '', next ? `?${next}` : window.location.pathname)
  }, [page, query, state, source, kind, selectedTags, tagMode, sort, activeCollection, deadOnly, unratedOnly, insightTab, creator, selected?.id])
  // 網址帶 item 時（重新整理、書籤）直接開啟那件作品
  useEffect(() => { const id = Number(urlState.get('item')); if (id) openById(id) }, [])
  useEffect(() => { loadCollections() }, [])
  const createCollection = async (name, query = '') => {
    try { const made = await api('/collections', { method: 'POST', body: JSON.stringify({ name, query }) }); await loadCollections(); setMessage(query ? `已建立智慧書單「${made.name}」（${made.count} 件，會依條件自動更新）` : `已建立書單「${made.name}」`); return made } catch (error) { setMessage(error.message); return null }
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
    if (deadOnly) params.set('dead', '1')
    if (unratedOnly) { params.set('opened', '1'); params.set('rated', '0'); params.set('not_status', '閱讀中') }
    if (sort !== 'updated') params.set('sort', sort)
    return params.toString()
  }
  const hasFilters = Boolean(query || state || source || kind || selectedTags.length || deadOnly || unratedOnly)
  const applySearch = search => {
    const params = new URLSearchParams(search)
    setQuery(params.get('q') || ''); setState(params.get('status') || ''); setSource(params.get('source') || ''); setKind(params.get('kind') || '')
    setSelectedTags(params.getAll('tag')); setTagMode(params.get('match') === 'any' ? 'any' : 'all'); setDeadOnly(params.get('dead') === '1'); setUnratedOnly(params.get('rated') === '0')
    setSort(SORTS.some(option => option.value === params.get('sort')) ? params.get('sort') : 'updated')
  }
  const leaveSmart = () => { setActiveSmart(null); applySearch('') }
  const saveSmart = async event => {
    event.preventDefault()
    if (!savingSmart?.trim()) { setSavingSmart(null); return }
    const made = await createCollection(savingSmart.trim(), filterSearch())
    if (made) { setActiveSmart({ ...made }); setSavingSmart(null) }
  }
  const updateSmart = async () => {
    try { const saved = await api(`/collections/${activeSmart.id}`, { method: 'PATCH', body: JSON.stringify({ query: filterSearch() }) }); setActiveSmart(current => ({ ...current, query: saved.query })); await loadCollections(); setMessage(`已更新「${saved.name}」的條件`) } catch (error) { setMessage(error.message) }
  }
  const renameCollection = async (id, name) => {
    try { await api(`/collections/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }); await loadCollections(); setActiveCollection(current => current?.id === id ? { ...current, name } : current); setActiveSmart(current => current?.id === id ? { ...current, name } : current); return true } catch (error) { setMessage(error.message); return false }
  }
  const deleteCollection = async id => {
    try { await api(`/collections/${id}`, { method: 'DELETE' }); await loadCollections(); if (activeCollection?.id === id) setActiveCollection(null); if (activeSmart?.id === id) setActiveSmart(null); setSelected(current => current && { ...current, collections: (current.collections || []).filter(cid => cid !== id) }); setMessage('已刪除書單') } catch (error) { setMessage(error.message) }
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
    } catch (error) { setMessage(error.message); return false }
  }
  const showList = kind => {
    setActiveCollection(null); setActiveSmart(null); setDeadOnly(false); setSelectedTags([]); setQuery(''); setSource(''); setKind('')
    setUnratedOnly(kind === 'unrated'); setState(kind === 'reading' ? '閱讀中' : ''); setSort('opened'); goTo('gallery')
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
    // 開始閱讀未讀作品時，自動改為「閱讀中」
    if (item.status === '未讀') quick(item, { status: '閱讀中' }).then(() => { setMessage('已標記為「閱讀中」'); bumpSidebar() }).catch(() => {})
    else bumpSidebar()
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
    if (action === 'delete' && !confirm(`確定將選取的 ${ids.length} 件移出典藏？一小時內可以復原。`)) return
    try {
      const result = await api('/resources/batch', { method: 'POST', body: JSON.stringify({ ids, action, ...extra }) })
      setMessage(action === 'delete' ? `已移除 ${result.count} 件` : `已更新 ${result.count} 件`, result.undo)
      if (action === 'delete') setBatch(new Set())
      if (action.endsWith('_tags')) loadTagCategories(true)
      load()
    } catch (error) { setMessage(error.message) }
  }
  const remove = async () => {
    if (!selected?.id || !confirm(`確定將「${titleFor(selected)}」移出典藏？`)) return
    try { const result = await api(`/resources/${selected.id}`, { method: 'DELETE' }); closeShowcase(); setMessage('已移除典藏', result?.undo); load() } catch (error) { setMessage(error.message) }
  }
  // 隨機開啟一件未讀；tags 有值時只從帶有其中任一標籤的作品挑（口味地圖的「挑一本」）
  const randomUnread = async (tags = []) => {
    try {
      const extra = tags.map(tag => `&tags=${encodeURIComponent(tag)}`).join('') + (tags.length > 1 ? '&tag_mode=any' : '')
      const first = await api(`/resources?limit=1&status=%E6%9C%AA%E8%AE%80${extra}`)
      if (!first.total) { setMessage('沒有未讀的典藏'); return }
      const pick = await api(`/resources?limit=1&status=%E6%9C%AA%E8%AE%80&sort=created${extra}&offset=${Math.floor(Math.random() * first.total)}`)
      open(pick.items[0])
    } catch (error) { setMessage(error.message) }
  }
  const actions = useMemo(() => [
    { id: 'new', icon: '＋', label: '收錄新典藏', hint: 'N', keywords: 'add new 新增', run: () => open(EMPTY) },
    { id: 'random', icon: '⚄', label: '隨機抽一件未讀', keywords: 'random 深潛 驚喜', run: () => randomUnread() },
    { id: 'import', icon: '⇊', label: '一次收錄多個網址', keywords: 'import bulk 匯入 批次 收錄 網址', run: () => setPanel('import') },
    { id: 'undo', icon: '↶', label: '復原上一個動作', hint: 'Ctrl Z', keywords: 'undo 復原 還原 刪除', run: undoLatest },
    { id: 'unrated', icon: '★', label: '列出待評分的作品', keywords: 'rating 評分 待評分', run: () => showList('unrated') },
    { id: 'keys', icon: '?', label: '鍵盤快捷鍵', hint: '?', keywords: 'shortcut keyboard 說明 help', run: () => setPanel('keys') },
    ...INSIGHT_TABS.filter(([id]) => id !== 'graph').map(([id, label]) => ({ id: `insight-${id}`, icon: '✦', label: `開啟${label}`, keywords: 'insight 關聯 整理 duplicate 健檢', run: () => { setInsightTab(id); goTo('graph') } })),
    ...PAGES.map(([id, icon, label]) => ({ id: `page-${id}`, icon, label: `前往${label}`, keywords: id, run: () => goTo(id) })),
    { id: 'backup', icon: '↧', label: '開啟備份中心', keywords: 'backup restore 還原 封面', run: () => setPanel('backup') },
    { id: 'download', icon: '⤓', label: '下載完整備份', keywords: 'backup export 匯出', run: () => { const link = document.createElement('a'); link.href = '/api/backup'; link.download = ''; link.click() } },
    ...['', ...STATES].map(value => ({ id: `status-${value || 'all'}`, icon: '◐', label: value ? `只看「${value}」` : '顯示全部狀態', keywords: 'filter 篩選 狀態', run: () => { goTo('gallery'); setState(value) } })),
  ], [open])

  const [crumb, headline] = page === 'creator' ? ['資料頁', creator] : PAGE_TITLES[page]
  const prevNext = useMemo(() => {
    if (!selected?.id || !context) return {}
    const index = context.findIndex(entry => entry.id === selected.id)
    return index < 0 ? {} : { prev: context[index - 1], next: context[index + 1] }
  }, [selected, context])

  return <div className={`oas-shell nav-${navVariant}`}><div className="oas-ambient" aria-hidden="true" /><div className="oas-grain" aria-hidden="true" />
    <Sidebar variant={navVariant} open={drawerOpen} onToggle={toggleNav} onClose={() => setDrawerOpen(false)} onShowList={showList}
      page={page} goTo={goTo} openById={openById} onPalette={() => setPaletteOpen(true)} onBackup={() => setPanel('backup')} refreshKey={sidebarKey}
      collections={collections} activeCollection={activeCollection || activeSmart} onCollection={openCollection} onShortcuts={() => setPanel('keys')}
      onCreateCollection={createCollection} onRenameCollection={renameCollection} onDeleteCollection={deleteCollection} />
    <main className="oas-main">
      <header className="oas-topbar">{navVariant === 'drawer' && <button type="button" className="nav-toggle in-topbar" onClick={() => setDrawerOpen(true)} aria-label="開啟側欄" aria-expanded={drawerOpen}><span aria-hidden="true" /></button>}<div className="crumb">個人圖書館 / <b>{crumb}</b></div><div className="top-actions">
        <SearchBox value={query} onChange={text => { setQuery(text); if (text && page !== 'gallery') goTo('gallery') }} onOpenItem={item => open(item)} onTag={openTag} />
        <button type="button" className="palette-trigger" onClick={() => setPaletteOpen(true)} title="指令面板">⌘ <kbd>Ctrl K</kbd></button>
        <button className="add-button" onClick={() => open(EMPTY)}>＋ 收錄典藏</button>
      </div></header>
      {message && <div className={`oas-toast${message.undo ? ' has-undo' : ''}`} role="status"><span>{message.text}</span>
        {message.undo && <button type="button" className="toast-undo" onClick={() => { const id = message.undo; setMessage(''); undoAction(id) }}>復原<kbd>Ctrl Z</kbd></button>}
        <button type="button" className="toast-close" onClick={() => setMessage('')} aria-label="關閉提示">×</button></div>}
      {page === 'gallery' ? <section className="hero">
        <div className="hero-copy">
          <h1>{headline}</h1>
          <dl className="hero-stats">
            <div><dt>典藏</dt><dd>{dashboard?.total ?? '—'}</dd></div>
            <div><dt>未讀</dt><dd>{dashboard?.unread ?? '—'}</dd></div>
            <div><dt>已完食</dt><dd>{dashboard?.status_counts?.['已完食'] ?? '—'}</dd></div>
            <div className="hero-tags"><dt>偏好引力</dt><dd>{dashboard?.top_tags?.length ? dashboard.top_tags.slice(0, 4).map(tag => <button key={tag.name} type="button" className={selectedTags.includes(tag.name) ? 'is-active' : ''} onClick={() => toggleTag(tag.name)} title="點擊加入或取消篩選">#{tag.name}</button>) : dashboard ? '加入標籤後顯示' : '—'}</dd></div>
          </dl>
        </div>
        <button className="deep-dive" onClick={() => deepDive && open(deepDive)} style={{ '--cover-colour': colourFor(deepDive) }}>
          {deepDive?.image_url && <img src={coverSrc(deepDive, 360)} alt="" onError={hideBroken} />}
          <span>深潛區</span><b>{deepDive ? titleFor(deepDive) : '正在等待第一件未讀典藏'}</b><small>{deepDive ? '收藏超過半年、從未開啟的作品' : '從資料庫挑選一件作品開始收藏'}</small>
        </button>
      </section> : <section className="page-title"><h1>{headline}</h1></section>}
      {page === 'creator' ? <CreatorView key={`${creator}-${dataVersion}`} name={creator} onOpen={open} onHover={hoverAmbient} onQuick={quick} onProfile={openProfile} onFilterTags={filterTags} onPickUnread={randomUnread} notify={setMessage} density={density} />
        : page === 'graph' ? <InsightsView key={dataVersion} tab={insightTab} setTab={setInsightTab} focus={graphFocus} setFocus={setGraphFocus} onOpenId={openById} onOpenItem={item => open(item)} onFilterTags={tags => filterTags(tags, 'any')} onPickUnread={randomUnread} onChanged={() => load()} notify={setMessage} />
        : page === 'stats' ? <StatsView key={dataVersion} onTag={openTag} onStatus={status => filterTags([], 'all', status)} notify={setMessage} /> : page === 'explore' ? <ExploreView onOpen={open} onHover={hoverAmbient} onQuick={quick} onTag={filterTag} notify={setMessage} /> : <>
        <section className="filter-row"><div>{['', ...STATES].map(value => <button key={value || 'all'} className={state === value ? 'is-active' : ''} onClick={() => setState(value)}>{value || '全部'}{value && dashboard?.status_counts ? <small>{dashboard.status_counts[value] ?? 0}</small> : null}</button>)}</div><div className="filter-tools"><TagPicker selected={selectedTags} mode={tagMode} onChange={setSelectedTags} onModeChange={setTagMode} filterParams={filterParams} /><Dropdown ariaLabel="依類型篩選" value={kind} onChange={setKind} options={[{ value: '', label: '全部類型' }, ...KINDS.map(value => ({ value, label: value }))]} /><Dropdown ariaLabel="依來源篩選" value={source} onChange={setSource} options={[{ value: '', label: '全部來源' }, ...sources.map(value => ({ value, label: value }))]} /></div></section>
        {selectedTags.length > 0 && <section className="tag-chips" aria-label="已選標籤">{selectedTags.map(tag => <button key={tag} type="button" onClick={() => toggleTag(tag)}>#{tag}<i aria-hidden="true">×</i></button>)}{selectedTags.length > 1 && <button type="button" className="tag-mode" onClick={() => setTagMode(tagMode === 'all' ? 'any' : 'all')} title="切換比對方式">{tagMode === 'all' ? '全部符合' : '任一符合'} ⇄</button>}<button type="button" className="tag-clear" onClick={() => setSelectedTags([])}>清除標籤</button></section>}
        <section className="gallery-heading"><h2>{activeCollection ? `書單：${activeCollection.name}` : activeSmart ? `智慧書單：${activeSmart.name}` : '典藏展廳'}{activeCollection && <button type="button" className="query-chip" onClick={() => setActiveCollection(null)} title="回到全部典藏">離開書單 ×</button>}{activeSmart && <button type="button" className="query-chip" onClick={leaveSmart} title="清除條件，回到全部典藏">離開 ×</button>}{activeSmart && filterSearch() !== activeSmart.query && <button type="button" className="query-chip is-accent" onClick={updateSmart} title="把書單的條件改成目前的篩選">更新條件</button>}{unratedOnly && <button type="button" className="query-chip" onClick={() => setUnratedOnly(false)} title="開啟過但還沒評分的作品">待評分 ×</button>}{query && <button type="button" className="query-chip" onClick={() => setQuery('')}>「{query}」×</button>}</h2>
          <div className="gallery-tools"><span>{total > items.length ? `顯示 ${items.length} / ${total} 件` : `${items.length} 件`}</span>{!batch && <span className="gallery-hint">懸停封面可直接評分，或按 1–5</span>}
            {hasFilters && !activeCollection && !activeSmart && !batch && (savingSmart === null
              ? <button type="button" className="batch-toggle" onClick={() => setSavingSmart('')} title="把目前的篩選條件存成側欄的書單，內容會自動更新">儲存為智慧書單</button>
              : <form className="smart-save" onSubmit={saveSmart}><input autoFocus value={savingSmart} onChange={event => setSavingSmart(event.target.value)} onKeyDown={event => event.key === 'Escape' && (event.stopPropagation(), setSavingSmart(null))} placeholder="書單名稱" aria-label="智慧書單名稱" /><button type="submit" className="batch-toggle is-active">儲存</button></form>)}
            <div className="density" role="group" aria-label="卡片大小">{DENSITIES.map(([value, label]) => <button key={value} type="button" aria-pressed={density === value} className={density === value ? 'is-active' : ''} onClick={() => changeDensity(value)} title={`卡片${label}`}>{label}</button>)}</div>
            {(dashboard?.dead_links > 0 || deadOnly) && <button type="button" className={`batch-toggle dead-toggle${deadOnly ? ' is-active' : ''}`} onClick={() => setDeadOnly(!deadOnly)} title="只看來源已失效的作品">來源失效 {dashboard?.dead_links ?? 0}</button>}
            <Dropdown small ariaLabel="排序" value={sort} onChange={setSort} options={SORTS} />
            <button type="button" className={`batch-toggle${batch ? ' is-active' : ''}`} onClick={() => setBatch(batch ? null : new Set())}>{batch ? '完成' : '批次選取'}</button>
          </div></section>
        {items.length === 0 && loadingList ? <SkeletonGrid columns={Math.max(1, Math.floor((viewport - (navVariant === 'full' ? 232 : navVariant === 'mini' ? 76 : 0) - 150) / ((DENSITIES.find(([value]) => value === density)?.[2] || 220) + 14)))} />
          : items.length === 0 ? <p className="gallery-empty">沒有符合條件的作品。{hasFilters ? '試著拿掉一些篩選條件。' : '按右上角「收錄典藏」開始收藏。'}</p> : null}
        <Masonry minColumn={DENSITIES.find(([value]) => value === density)?.[2] || 220} className={`density-${density}${loadingList && items.length ? ' is-refreshing' : ''}`} items={items} renderItem={item => <Card key={item.id} item={item} onOpen={batch ? toggleBatch : openInGallery} onHover={hoverAmbient} onQuick={batch ? null : quick} picked={batch ? batch.has(item.id) : undefined} />} />
        {items.length < total && <div className="load-more"><button type="button" onClick={loadMore} disabled={loadingMore}>{loadingMore ? '載入中…' : `再載入 ${Math.min(PAGE_SIZE, total - items.length)} 件（尚有 ${total - items.length} 件）`}</button></div>}
      </>}
    </main>
    {selected && <Showcase key={selected.id ? 'item' : 'new'} item={selected} direction={direction} link={linkFor(selected, sourceList)} prev={prevNext.prev} next={prevNext.next} onNavigate={navigate} onClose={closeShowcase} onQuick={quick} onSave={save} onRemove={remove} onGraph={showInGraph} onTag={openTag} onVisit={visit} onBulk={() => { closeShowcase(); setPanel('import') }} onOpenDuplicate={openDuplicate} collections={collections.filter(collection => !collection.query)} onMembership={changeMembership} onCreateCollection={createCollection} onRelinked={saved => { setSelected(saved); load(); setMessage(saved.added_tags ? `已改用 ${saved.source_name}，補上 ${saved.added_tags} 個標籤` : `已改用 ${saved.source_name}`) }} />}
    {panel === 'backup' && <BackupCenter close={() => setPanel(null)} notify={setMessage} reload={() => { load(); api('/sources').then(setSourceList).catch(() => {}) }} />}
    {batch && page === 'gallery' && <BatchBar collections={collections.filter(collection => !collection.query)} onCollection={(collectionId, action) => changeMembership([...batch], collectionId, action).then(ok => ok && setMessage(action === 'add' ? `已加入書單（${batch.size} 件）` : `已移出書單（${batch.size} 件）`))} count={batch.size} onSelectAll={() => setBatch(new Set(items.map(item => item.id)))} onClear={() => setBatch(new Set())} onRun={runBatch} onDone={() => setBatch(null)} />}
    {panel === 'keys' && <ShortcutHelp close={() => setPanel(null)} />}
    {panel === 'import' && <ImportDialog close={() => setPanel(null)} notify={setMessage} onDone={() => { load(); bumpSidebar() }} onOpenId={openById} />}
    <HoverPreview disabled={Boolean(selected || batch || paletteOpen || panel)} />
    <OfflineBanner onBack={() => { load(); loadCollections(); bumpSidebar() }} />
    {!selected && <BackToTop raised={Boolean(batch && page === 'gallery')} />}
    {returning && <ReturnPrompt item={returning} onQuick={quick} onOpen={() => { const item = returning; setReturning(null); if (selected?.id !== item.id) open(item) }} onClose={() => setReturning(null)} />}
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
    <Dropdown small ariaLabel="設定評分" value="" onChange={value => value !== '' && onRun('rating', { rating: Number(value) })} options={[{ value: '', label: '設定評分' }, ...[5, 4, 3, 2, 1, 0].map(value => ({ value: String(value), label: value ? '★'.repeat(value) : '清除評分' }))]} />
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

function RefreshBlock({ notify }) {
  const [status, setStatus] = useState(null)
  const refreshStatus = () => api('/refresh/status').then(setStatus).catch(() => {})
  useEffect(() => { refreshStatus(); const timer = setInterval(refreshStatus, 2000); return () => clearInterval(timer) }, [])
  const start = async mode => {
    try { const result = await api(`/refresh?mode=${mode}`, { method: 'POST' }); notify(`已排入 ${result.queued} 件，約 ${Math.ceil(result.seconds / 60)} 分鐘`); refreshStatus() } catch (error) { notify(error.message) }
  }
  const running = status?.running
  return <div className="backup-block"><h3>補齊作品資訊</h3>
    <p>重新讀取 nhentai、紳士漫畫、Hanime1 的作品頁，補上缺少的封面與標籤，並為標籤分類（作者、社團、原作、角色）。只新增、不覆蓋；在背景依網站控制頻率（nhentai 等每 6 秒一件），新收錄的作品優先，關掉典藏館下次會接著做。</p>
    {status && (running || status.finished_at) && <>
      <div className="cover-meter"><span style={{ width: `${status.total ? (status.done / status.total) * 100 : 0}%` }} /></div>
      <p className="cover-numbers">{running ? '進行中' : '已完成'} <b>{status.done}</b> / {status.total}，新增標籤 {status.added_tags}、補上封面 {status.added_covers}、分類標籤 {status.categorised}{status.failed ? `、失敗 ${status.failed}` : ''}{status.message ? `。${status.message}` : ''}</p>
    </>}
    <div className="cover-actions">
      {running ? <button type="button" className="ghost" onClick={() => api('/refresh/stop', { method: 'POST' }).then(refreshStatus)}>停止</button> : <>
        <button type="button" className="save" onClick={() => start('missing')}>只補缺少的</button>
        <button type="button" className="ghost" onClick={() => start('all')}>全部重新整理</button>
      </>}
    </div>
  </div>
}

function LinkCheckBlock({ notify, reload }) {
  const [status, setStatus] = useState(null)
  const refreshStatus = () => api('/linkcheck/status').then(setStatus).catch(() => {})
  useEffect(() => { refreshStatus(); const timer = setInterval(refreshStatus, 3000); return () => clearInterval(timer) }, [])
  const wasRunning = useRef(false)
  useEffect(() => { if (wasRunning.current && status && !status.running && status.dead) reload(); wasRunning.current = Boolean(status?.running) }, [status?.running])
  const start = () => api('/linkcheck/start', { method: 'POST' }).then(() => { notify('開始巡檢來源，於背景進行'); refreshStatus() }).catch(error => notify(error.message))
  const running = status?.running
  return <div className="backup-block"><h3>定期巡檢來源</h3>
    <p>每件作品每 {status?.interval_days ?? 7} 天檢查一次來源頁面，網站回應「找不到」就標記為來源失效。典藏館開著時才會在背景進行（啟動 1 分半後開始，排在新收錄與補齊作品資訊之後），中途關掉下次會接著做。禁漫、MissAV 有防護，無法檢查。</p>
    {status && <p className="cover-numbers">{running ? (status.total ? <>進行中 <b>{status.done}</b> / {status.total}，發現失效 {status.dead}</> : '等待開始…') : status.finished_at ? <>上次巡檢檢查 {status.done} 件，發現失效 {status.dead}</> : '這次啟動尚未巡檢'}{status.message ? `。${status.message}` : ''}。待檢查 {status.pending} 件。</p>}
    {running && status.total > 0 && <div className="cover-meter"><span style={{ width: `${(status.done / status.total) * 100}%` }} /></div>}
    <div className="cover-actions">{running ? <button type="button" className="ghost" onClick={() => api('/linkcheck/stop', { method: 'POST' }).then(refreshStatus)}>停止</button> : <button type="button" className="ghost" disabled={!status?.pending} onClick={start}>立即巡檢</button>}</div>
  </div>
}

function BackupCenter({ close, notify, reload }) {
  const [mode, setMode] = useState('merge')
  const [busy, setBusy] = useState(false)
  const [coverStats, setCoverStats] = useState(null)
  const refreshCovers = () => api('/covers/status').then(setCoverStats).catch(() => {})
  useEffect(() => {
    refreshCovers()
    const timer = setInterval(refreshCovers, 2000)
    return () => clearInterval(timer)
  }, [])
  const startCovers = async retryFailed => {
    try {
      const result = await api(`/covers/cache${retryFailed ? '?retry_failed=true' : ''}`, { method: 'POST' })
      notify(result.queued ? `已排入 ${result.queued} 張封面，於背景下載` : '沒有需要下載的封面')
      refreshCovers()
    } catch (error) { notify(error.message) }
  }
  const batch = coverStats?.batch
  const restore = async event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (mode === 'replace' && !confirm('「取代」會先清空目前所有典藏，再寫入備份內容。建議先匯出一份目前資料。確定繼續？')) return
    setBusy(true)
    try {
      const data = JSON.parse(await file.text())
      const report = await api(`/restore?mode=${mode}`, { method: 'POST', body: JSON.stringify(data) })
      notify(`還原完成：新增 ${report.created} 筆，略過 ${report.skipped} 筆，新增來源 ${report.sources_created} 個`)
      reload(); close()
    } catch (error) { notify(error instanceof SyntaxError ? '檔案不是有效的 JSON' : error.message) } finally { setBusy(false) }
  }
  return <div className="oas-modal" role="dialog" aria-modal="true" aria-label="備份中心" onClick={event => event.target === event.currentTarget && close()}>
    <section className="oas-modal-card">
      <button className="drawer-close" onClick={close} aria-label="關閉備份中心">×</button>
      <h2>備份中心</h2>
      <p className="drawer-subtitle">完整備份包含典藏、標籤、評分、狀態、筆記、時間戳與來源規則，格式為可讀的 JSON。</p>
      <div className="backup-block"><h3>匯出</h3><p>下載目前所有資料的快照。建議在大量編輯或還原前先匯出一次。</p><a className="save" href="/api/backup" download>下載完整備份</a></div>
      <div className="backup-block"><h3>還原</h3><p>選擇先前匯出的 <code>oas-backup-*.json</code>。</p>
        <div className="mode-switch">{[['merge', '合併', '保留現有資料，只加入缺少的典藏'], ['replace', '取代', '清空後完整還原至備份狀態']].map(([value, label, hint]) => <label key={value} className={mode === value ? 'is-active' : ''}><input type="radio" name="restore-mode" value={value} checked={mode === value} onChange={() => setMode(value)} /><b>{label}</b><small>{hint}</small></label>)}</div>
        <label className={`save file-pick${busy ? ' is-busy' : ''}`}>{busy ? '還原中…' : '選擇備份檔'}<input type="file" accept="application/json,.json" disabled={busy} onChange={restore} /></label>
      </div>
      <div className="backup-block"><h3>封面快取</h3><p>將封面圖片保存在本機，來源網站移除圖片後仍可顯示。瀏覽時也會自動逐張快取；無法下載的封面會改由瀏覽器直接載入原始網址。</p>
        {coverStats ? <>
          <div className="cover-meter" aria-label="封面快取進度"><span style={{ width: `${coverStats.with_image ? (coverStats.cached / coverStats.with_image) * 100 : 0}%` }} /></div>
          <p className="cover-numbers">已快取 <b>{coverStats.cached}</b> / {coverStats.with_image}，無法下載 {coverStats.failed}、待處理 {coverStats.pending}、佔用 {formatBytes(coverStats.bytes)}{batch?.running ? `。背景進行中 ${batch.done} / ${batch.queued}` : ''}</p>
          <div className="cover-actions"><button type="button" className="save" disabled={batch?.running} onClick={() => startCovers(false)}>{batch?.running ? '快取中…' : '快取全部封面'}</button>{coverStats.failed > 0 && !batch?.running && <button type="button" className="ghost" onClick={() => startCovers(true)}>重試失敗項目</button>}</div>
        </> : <p>讀取中…</p>}
      </div>
      <RefreshBlock notify={notify} />
      <LinkCheckBlock notify={notify} reload={reload} />
    </section>
  </div>
}

const Card = memo(function Card({ item, onOpen, onHover, onQuick, picked }) {
  const stop = event => event.stopPropagation()
  const keyOpen = event => { if ((event.key === 'Enter' || event.key === ' ') && event.target === event.currentTarget) { event.preventDefault(); onOpen(item, event) } }
  const enter = event => { onHover(item); if (picked === undefined) schedulePreview(item, event.currentTarget.querySelector('.cover')) }
  return <article tabIndex={0} aria-label={titleFor(item)} onClick={event => { cancelPreview(); onOpen(item, event) }} onKeyDown={keyOpen} onMouseEnter={enter} onMouseLeave={cancelPreview} onFocus={() => onHover(item)} style={{ '--cover-colour': colourFor(item) }} className={picked === undefined ? undefined : `is-pickable${picked ? ' is-picked' : ''}`} aria-pressed={picked}>
    {picked !== undefined && <i className="pick-mark" aria-hidden="true">{picked ? '✓' : ''}</i>}
    {item.link_dead && <i className="dead-badge" title="來源已失效">失效</i>}
    <div className="cover" data-cover-id={item.id} style={{ '--cover-colour': colourFor(item), aspectRatio: `1 / ${isFaceCover(item) ? 1.15 : coverRatio(item)}` }}>{isFaceCover(item) ? <CoverFace item={item} /> : <img src={coverSrc(item, 360)} alt="" loading="lazy" decoding="async" onError={hideBroken} />}<div className="cover-glow" />
      {onQuick && item.id && <div className="quick" onClick={stop} onKeyDown={stop}><Stars value={item.rating} onChange={rating => onQuick(item, { rating }).catch(() => {})} small /><Dropdown small ariaLabel="閱讀狀態" value={item.status} options={STATUS_OPTIONS} onChange={status => onQuick(item, { status }).catch(() => {})} /></div>}
    </div>
    <div className="card-copy"><h3>{titleFor(item)}</h3><p className="card-meta"><span><i className="status-dot" style={{ background: STATUS_COLOURS[item.status] }} />{item.status}{item.status === '閱讀中' && item.progress ? ` ${item.progress}` : ''}</span><span className={item.rating > 0 ? 'is-rated' : undefined}>{item.rating > 0 ? `★ ${Number(item.rating).toFixed(1)}` : '未評分'}</span><span>{item.source_name}</span>{hasNote(item) && <span className="note-mark" title="有筆記" aria-label="有筆記">✎</span>}</p><div>{item.tags.slice(0, 3).map(tag => <span key={tag}>{tag}</span>)}</div></div></article>
})

function ExploreView({ onOpen, onHover, onQuick, onTag, notify }) {
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 100000))
  const [data, setData] = useState(null)
  const patchLocal = (id, values) => setData(current => current && { ...current, shelves: current.shelves.map(shelf => ({ ...shelf, items: shelf.items.map(entry => entry.id === id ? { ...entry, ...values } : entry) })) })
  const quickLocal = useCallback((item, patch) => {
    patchLocal(item.id, patch)
    return onQuick(item, patch).then(saved => { patchLocal(item.id, { rating: saved.rating, status: saved.status }); return saved })
      .catch(error => { patchLocal(item.id, { rating: item.rating, status: item.status }); throw error })
  }, [onQuick])
  useEffect(() => { api(`/explore?seed=${seed}`).then(setData).catch(error => notify(error.message)) }, [seed])
  if (!data) return <p className="explore-empty">正在整理探索書架…</p>
  return <section className="explore" aria-label="探索">
    <div className="explore-head"><div><h2>今晚的探索書架</h2><span>依你的典藏與閱讀紀錄即時計算；每次「換一批」都會重新抽選。</span></div><button type="button" className="ghost" onClick={() => setSeed(value => value + 1)}>換一批</button></div>
    {data.shelves.length === 0 && <p className="explore-empty">典藏還太少，先收錄幾件作品吧。</p>}
    {data.shelves.map(shelf => <section key={shelf.key} className="shelf">
      <header><h3>{shelf.tag ? <button type="button" className="shelf-tag" onClick={() => onTag(shelf.tag)} title="在典藏館中查看此標籤">{shelf.title}</button> : shelf.title}</h3><p>{shelf.subtitle}</p></header>
      <div className="shelf-row">{shelf.items.map(item => <Card key={item.id} item={item} onOpen={entry => onOpen(entry, shelf.items)} onHover={onHover} onQuick={quickLocal} />)}</div>
    </section>)}
  </section>
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
function BackToTop({ raised }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const onScroll = () => setShown(window.scrollY > window.innerHeight * 1.5)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  return <button type="button" className={`back-to-top${shown ? ' is-shown' : ''}${raised ? ' is-raised' : ''}`} aria-label="回到頂端" title="回到頂端（Home）" tabIndex={shown ? 0 : -1} aria-hidden={!shown}
    onClick={() => { window.scrollTo({ top: 0, behavior: reducedMotion() ? 'auto' : 'smooth' }); document.querySelector('#oas-search')?.focus({ preventScroll: true }) }}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" /></svg>
  </button>
}

function ShortcutHelp({ close }) {
  return <div className="oas-modal" role="dialog" aria-modal="true" aria-label="鍵盤快捷鍵" onClick={event => event.target === event.currentTarget && close()}>
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

// 從外部網站看完回來：一鍵評分、標成已完食，或記下看到哪；不擋畫面，可直接忽略。
function ReturnPrompt({ item, onQuick, onOpen, onClose }) {
  const [editing, setEditing] = useState(false)
  const [progress, setProgress] = useState(item.progress || '')
  const done = item.status === '已完食'
  // Esc 由 App 統一處理（先關其他視窗，最後才收起這張卡片）
  const saveProgress = event => { event.preventDefault(); onQuick(item, { progress: progress.trim() }).catch(() => {}); setEditing(false) }
  return <aside className="return-prompt" role="dialog" aria-label="剛剛在看的作品" style={{ '--cover-colour': colourFor(item) }}>
    <button type="button" className="return-cover" onClick={onOpen} title="開啟展示頁">{isFaceCover(item) ? <CoverFace item={item} width={240} /> : <img src={coverSrc(item, 240)} alt="" onError={hideBroken} />}</button>
    <div className="return-body">
      <p className="return-kicker">{done ? '已標成已完食' : '剛剛在看'}{item.progress ? `，看到 ${item.progress}` : ''}</p>
      <h3 title={titleFor(item)}>{titleFor(item)}</h3>
      <div className="return-rate"><Stars value={item.rating} allowHalf onChange={rating => onQuick(item, { rating }).catch(() => {})} /><span>{item.rating > 0 ? Number(item.rating).toFixed(1) : '還沒評分'}</span></div>
      {editing ? <form className="return-progress" onSubmit={saveProgress}><input autoFocus value={progress} maxLength={40} onChange={event => setProgress(event.target.value)} placeholder="例如：第 3 話、p.45" aria-label="閱讀進度" /><button type="submit" className="primary">記下</button></form>
        : <div className="return-actions">
          {!done && <button type="button" className="primary" onClick={() => onQuick(item, { status: '已完食' }).catch(() => {})}>看完了</button>}
          {!done && <button type="button" className="ghost" onClick={() => setEditing(true)}>還沒看完，記進度</button>}
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
    api(`/resources?limit=300&sort=rating&tags=${encodeURIComponent(name)}`).then(result => setItems(result.items)).catch(() => setItems([]))
  }
  useEffect(reload, [name])
  const changeCategory = async category => {
    try { const result = await api('/tags/category', { method: 'PATCH', body: JSON.stringify({ name, category }) }); loadTagCategories(true); notify(`已把「${name}」改成「${CATEGORY_NAMES[category]}」`, result.undo); reload() } catch (error) { notify(error.message) }
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
        <div><dt>平均評分</dt><dd className={profile.avg_rating ? 'is-rated' : ''}>{profile.avg_rating ? `★ ${profile.avg_rating.toFixed(1)}` : '—'}</dd><small>{profile.rated} 件有評分</small></div>
      </dl>
      <div className="creator-actions">
        <button type="button" className="primary" disabled={!profile.unread} onClick={() => onPickUnread([name])}>挑一本未讀</button>
        <button type="button" className="ghost" onClick={() => onFilterTags([name])}>在典藏館篩選</button>
        <div className="creator-category"><span>分類</span><Dropdown small ariaLabel="標籤分類" value={profile.category} onChange={changeCategory} options={TAG_CATEGORIES.map(([value, label]) => ({ value, label }))} /></div>
      </div>
    </div>
    {profile.people.length > 0 && <div className="creator-related"><h3>常一起出現</h3><div>{profile.people.map(entry => <button key={entry.name} type="button" onClick={() => onProfile(entry.name)} title={`開啟 ${entry.name} 的資料頁`}><small>{CATEGORY_NAMES[entry.category]}</small>{entry.name}<b>{entry.count}</b></button>)}</div></div>}
    {profile.tags.length > 0 && <div className="creator-related"><h3>常見標籤</h3><div>{profile.tags.map(entry => <button key={entry.name} type="button" onClick={() => onFilterTags([name, entry.name])} title={`在典藏館查看同時有 ${name} 與 #${entry.name} 的作品`}>#{entry.name}<b>{entry.count}</b></button>)}</div></div>}
    <h3 className="creator-works">全部作品<small>依評分排列</small></h3>
    {items === null ? <SkeletonGrid columns={4} count={8} /> : <Masonry minColumn={DENSITIES.find(([value]) => value === density)?.[2] || 220} className={`density-${density}`} items={items} renderItem={item => <Card key={item.id} item={item} onOpen={entry => onOpen(entry, items)} onHover={onHover} onQuick={quickLocal} />} />}
  </section>
}
