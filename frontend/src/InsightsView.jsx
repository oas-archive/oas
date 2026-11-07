import { useEffect, useMemo, useState } from 'react'
import Maintenance from './Maintenance.jsx'
import { canReadPages, fillFromPages } from './pageReader.js'
import { JM_SEARCH, JM_SOURCE } from './sites.js'
import { announceRemoved, api, CATEGORY_NAMES, colourFor, CoverFace, coverSrc, Dropdown, hideBroken, isFaceCover, loadTagCategories, STATUS_COLOURS, TAG_CATEGORIES, shortTitle, titleFor, ask, errorNote } from './shared.jsx'

// 「口味」頁看收藏的口味分群；「整理」頁是要動手處理的事（缺資料、重複、標籤、背景維護）
export const RELATION_TABS = [['taste', '口味地圖']]
export const TIDY_TABS = [['attention', '待整理'], ['duplicates', '重複作品'], ['tags', '標籤健檢'], ['rules', '標籤規則'], ['maintenance', '維護']]
export const INSIGHT_TABS = [...RELATION_TABS, ...TIDY_TABS]
export const tabPage = id => TIDY_TABS.some(([tab]) => tab === id) ? 'tidy' : 'taste'
// 頁面目前顯示的分頁：記住的分頁不屬於這一頁時，顯示這一頁的第一個
export const shownTab = (page, tab) => { const tabs = page === 'tidy' ? TIDY_TABS : RELATION_TABS; return tabs.some(([id]) => id === tab) ? tab : tabs[0][0] }
const CATEGORY_LABEL = CATEGORY_NAMES
const REASONS = { title: '標題相同', similar_title: '標題相近', url: '網址相同', cover: '封面相同', cover_image: '封面圖片相同' }

// 「口味」與「整理」兩頁共用：page 決定列出哪些分頁（只有一個分頁時不顯示分頁列）
export default function InsightsView({ page, tab, setTab, onOpenItem, onFilterTags, onPickUnread, onChanged, notify, counts = {}, onBackup }) {
  const label = page === 'tidy' ? '整理' : '口味'
  // 手機上長說明只露兩行，點一下展開（樣式見 immersive.css）
  const toggleHint = event => { if (!event.target.closest('a,button,input')) event.target.closest('.insight-head p, .health-block > p')?.classList.toggle('is-open') }
  return <section className="insights" aria-label={label} onClick={toggleHint}>
    {page === 'tidy' && <div className="filter-row"><nav className="status-rail" role="tablist" aria-label={`${label}分頁`} style={{ '--n': TIDY_TABS.length, '--i': Math.max(0, TIDY_TABS.findIndex(([id]) => id === tab)) }}><i aria-hidden="true" />
      {TIDY_TABS.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'is-active' : ''} onClick={() => setTab(id)}><span>{label}</span><b>{counts[id] || ''}</b></button>)}
    </nav></div>}
    {tab === 'taste' && <TasteMap onFilterTags={onFilterTags} onPickUnread={onPickUnread} onOpenItem={onOpenItem} notify={notify} />}
    {tab === 'tags' && <TagHealth onOpenItem={onOpenItem} onChanged={onChanged} notify={notify} />}
    {tab === 'rules' && <TagRules onChanged={onChanged} notify={notify} />}
    {tab === 'attention' && <Attention onOpenItem={onOpenItem} onChanged={onChanged} notify={notify} />}
    {tab === 'duplicates' && <Duplicates onOpenItem={onOpenItem} onChanged={onChanged} notify={notify} />}
    {tab === 'maintenance' && <Maintenance notify={notify} reload={onChanged} onBackup={onBackup} />}
  </section>
}

function Thumb({ item, onOpen }) {
  return <button type="button" className="mini-cover" onClick={() => onOpen(item)} title={titleFor(item)} style={{ '--cover-colour': colourFor(item) }}>
    {isFaceCover(item) ? <CoverFace item={item} width={240} /> : <img src={coverSrc(item, 240)} alt="" loading="lazy" onError={hideBroken} />}
  </button>
}

function useLoad(path, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const reload = () => api(path).then(result => { setData(result); setError('') }).catch(failure => setError(failure.message))
  useEffect(() => { setData(null); reload() }, deps)
  return [data, reload, error]
}

function TasteMap({ onFilterTags, onPickUnread, onOpenItem }) {
  const [data, , error] = useLoad('/insights/taste')
  if (error) return <p className="insight-empty">{error}</p>
  if (!data) return <p className="insight-empty">正在分析你的收藏…</p>
  return <div className="insight-body">
    <header className="insight-head"><div><h2>口味地圖</h2><p>依標籤把收藏分成幾群。收得越多、評分越高的排越前面；常一起出現的標籤會併成同一群。按「挑一本」會從那群的未讀裡隨機開啟一件。</p></div></header>
    {data.themes.length === 0 ? <p className="insight-empty">典藏還太少，收錄更多作品後才看得出口味。</p> : <div className="taste-grid">
      {data.themes.map((theme, index) => <article key={theme.names.join('+')} className="taste-card">
        <div className="taste-covers">{theme.samples.map(item => <Thumb key={item.id} item={item} onOpen={onOpenItem} />)}</div>
        <div className="taste-copy">
          <h3><span className="taste-rank">{index + 1}</span>{theme.names.map(name => `${theme.category === 'tag' ? '#' : ''}${name}`).join('＋')}</h3>
          {theme.category !== 'tag' && <small className="taste-category">{CATEGORY_LABEL[theme.category]}</small>}
          <p className="taste-facts"><span><b>{theme.count}</b> 件</span><span>未讀 <b>{theme.unread}</b></span>{theme.finished > 0 && <span>已完食 {theme.finished}</span>}<span className={theme.avg_rating ? 'is-rated' : ''}>{theme.avg_rating ? `★ ${theme.avg_rating.toFixed(1)}（${theme.rated} 件有評分）` : '尚無評分'}</span></p>
          <div className="taste-meter" aria-hidden="true"><i style={{ width: `${(theme.finished / theme.count) * 100}%` }} /></div>
          <div className="taste-actions">
            <button type="button" className="primary" disabled={!theme.unread} onClick={() => onPickUnread(theme.names)}>挑一本</button>
            <button type="button" className="ghost" onClick={() => onFilterTags(theme.names)}>查看全部</button>
          </div>
        </div>
      </article>)}
    </div>}
  </div>
}

// 舊版把「一起出現」的保留紀錄存在瀏覽器本機，手機和電腦對不上；現在存資料庫，開 App 時把本機舊紀錄搬過去一次。
const PAIR_KEY = 'oas-kept-pairs'
export const migrateKeptPairs = () => {
  try {
    const pairs = JSON.parse(localStorage.getItem(PAIR_KEY) || '[]')
    if (!pairs.length) return
    api('/insights/tags/keep-pairs', { method: 'POST', body: JSON.stringify({ pairs }) }).then(() => localStorage.removeItem(PAIR_KEY)).catch(() => {})
  } catch { /* 下次再搬 */ }
}

function TagHealth({ onOpenItem, onChanged, notify }) {
  const [data, reload, error] = useLoad('/insights/tags')
  const [swapped, setSwapped] = useState(() => new Set())
  const [rareFilter, setRareFilter] = useState('')
  const [rareAll, setRareAll] = useState(false)
  const [picked, setPicked] = useState(null)  // 只用過一次的標籤：null 為一般模式，否則為已選標籤名稱的 Set
  const [busy, setBusy] = useState(false)
  const run = async (request, message) => {
    setBusy(true)
    try { const result = await request(); notify(message(result), result?.undo); loadTagCategories(true); onChanged(); await reload(); return true } catch (failure) { notify(errorNote(failure)); return false } finally { setBusy(false) }
  }
  const merge = (source, target) => run(() => api('/tags/merge', { method: 'POST', body: JSON.stringify({ source, target }) }), result => `已把「${source}」併入「${target}」（${result.count} 件），之後抓到「${source}」也會自動換成「${target}」`)
  const setCategory = (name, category) => run(() => api('/tags/category', { method: 'PATCH', body: JSON.stringify({ name, category }) }), () => `已把「${name}」改成「${CATEGORY_LABEL[category]}」`)
  // 刪除只動現有作品，之後收錄的作品仍會帶上；封鎖是永久略過，所以先確認
  const BLOCK_NOTE = '封鎖後，之後收錄、補資料時都會略過這個名稱（翻譯成它的原文標籤也一樣）。'
  const remove = async (name, block) => {
    if (block && !await ask(`刪除並封鎖「${name}」？${BLOCK_NOTE}`, { confirm: '刪除並封鎖', danger: true })) return
    run(() => api('/tags/remove', { method: 'POST', body: JSON.stringify({ name, block }) }), () => block ? `已刪除並封鎖「${name}」` : `已刪除「${name}」`)
  }
  // 一次刪除多個標籤：先確認，只記一筆復原
  const removeMany = async (names, block) => {
    if (!names.length || !await ask(`刪除 ${names.length} 個標籤${block ? `並加入封鎖清單？${BLOCK_NOTE}` : '？只從現有作品移除，之後收錄的作品仍會帶上。'}之後可以按「復原」一次還原。`, { confirm: '刪除', danger: true })) return
    run(() => api('/tags/remove-many', { method: 'POST', body: JSON.stringify({ names, block }) }), result => `已刪除 ${result.count} 個標籤（${result.works} 件作品）${block ? '，並加入封鎖清單' : ''}`)
      .then(done => { if (done) setPicked(null) })  // 失敗時保留已選的標籤
  }
  // 只用過一次但你要留著的標籤（例如 kasou）：保留後不再列出，可以取消
  const keepRare = (name, value = true) => run(() => api('/insights/tags/keep', { method: 'POST', body: JSON.stringify({ name, keep: value }) }), () => value ? `已保留「${name}」，不再列在只用過一次的標籤` : `已取消保留「${name}」`)
  const togglePick = name => setPicked(current => { const next = new Set(current); if (next.has(name)) next.delete(name); else next.add(name); return next })
  const keep = pair => run(() => api('/insights/tags/keep-pairs', { method: 'POST', body: JSON.stringify({ pairs: [pair] }) }), () => '已保留這一組，不再列出')
  const pairs = data?.paired || []
  const rare = useMemo(() => (data?.rare || []).filter(entry => !rareFilter || entry.name.toLowerCase().includes(rareFilter.toLowerCase())), [data, rareFilter])
  if (error) return <p className="insight-empty">{error}</p>
  if (!data) return <p className="insight-empty">正在檢查標籤…</p>
  const clean = !data.suspicious.length && !data.similar.length && !pairs.length && !data.orphans_total && !data.rare_total && !data.unused
  return <div className={`insight-body${busy ? ' is-busy' : ''}`}>
    <header className="insight-head"><div><h2>標籤健檢</h2><p>從標籤的使用方式找出需要整理的地方，共 {data.tag_total} 個標籤。合併後會自動記成同義詞，之後補資料抓到舊名稱也會換成新名稱。</p></div></header>
    {clean && <p className="insight-empty">標籤很乾淨，沒有需要整理的地方。</p>}

    {data.suspicious.length > 0 && <section className="health-block is-warn"><h3>分類可能錯了<small>{data.suspicious.length} 個</small></h3><p>作者、社團、原作、角色通常只出現在少數作品；這些卻被很多作品帶著，多半是補資料時分錯了類。</p>
      <ul className="health-list">{data.suspicious.map(entry => <li key={entry.name}>
        <span className="health-tags"><code>{entry.name}</code><small>目前是「{CATEGORY_LABEL[entry.category]}」，{entry.count} 件（{Math.round(entry.share * 100)}%）</small></span>
        <span className="health-actions"><button type="button" className="ghost" disabled={busy} onClick={() => setCategory(entry.name, 'tag')}>改成一般標籤</button><Dropdown small ariaLabel={`${entry.name} 的分類`} value={entry.category} onChange={category => setCategory(entry.name, category)} options={TAG_CATEGORIES.map(([value, label]) => ({ value, label }))} /></span>
      </li>)}</ul></section>}

    <CategoryEditor busy={busy} onSave={setCategory} />

    {data.similar.length > 0 && <section className="health-block"><h3>名稱幾乎一樣<small>{data.similar.length} 組</small></h3><p>只差大小寫、空白、連字號或單複數，建議合併成一個。</p>
      <ul className="health-list">{data.similar.map(entry => entry.sources.map(source => <li key={source.name}>
        <span className="health-tags"><code>{source.name}</code><small>{source.count} 件</small><i aria-hidden="true">→</i><code>{entry.target}</code><small>{entry.count} 件</small></span>
        <button type="button" className="ghost" disabled={busy} onClick={() => merge(source.name, entry.target)}>合併</button>
      </li>))}</ul></section>}

    {pairs.length > 0 && <section className="health-block"><h3>幾乎總是一起出現<small>{pairs.length} 組</small></h3><p>兩個標籤九成以上同時出現。可能是同義詞（可以合併），也可能只是常見組合（按「保持分開」就不再提醒）。</p>
      <ul className="health-list">{pairs.map(entry => {
        const pair = `${entry.target}|${entry.source}`
        const [from, to] = swapped.has(pair) ? [entry.target, entry.source] : [entry.source, entry.target]
        const counts = { [entry.target]: entry.target_count, [entry.source]: entry.source_count }
        return <li key={pair}>
          <span className="health-tags"><code>{from}</code><small>{counts[from]} 件</small><button type="button" className="swap" onClick={() => { const next = new Set(swapped); next.has(pair) ? next.delete(pair) : next.add(pair); setSwapped(next) }} title="交換合併方向" aria-label="交換合併方向">⇄</button><code>{to}</code><small>{counts[to]} 件，一起出現 {entry.together} 次</small></span>
          <span className="health-actions"><button type="button" className="ghost" disabled={busy} onClick={() => merge(from, to)}>併入「{to}」</button><button type="button" className="ghost subtle" onClick={() => keep(pair)}>保持分開</button></span>
        </li>
      })}</ul></section>}

    {data.orphans_total > 0 && <section className="health-block"><h3>幾乎沒有標籤的作品<small>{data.orphans_total} 件</small></h3><p>沒有或只有一個標籤，所以找不到相似作品、也不會出現在口味地圖裡。點開後按 <kbd>E</kbd> 再按「補齊資訊」，或手動加上標籤。</p>
      <div className="orphan-row">{data.orphans.map(item => <button key={item.id} type="button" className="orphan" onClick={() => onOpenItem(item)} style={{ '--cover-colour': colourFor(item) }}>
        <i>{isFaceCover(item) ? <CoverFace item={item} width={240} /> : <img src={coverSrc(item, 240)} alt="" loading="lazy" onError={hideBroken} />}</i>
        <span>{shortTitle(item)}</span><small>{item.source_name}{item.tags.length ? `，#${item.tags[0]}` : '，沒有標籤'}</small>
      </button>)}</div></section>}

    {data.rare_total > 0 && <section className="health-block"><h3>只用過一次的標籤<small>{data.rare_total} 個</small></h3><p>多半是拼法不同或太細的標籤；翻譯或合併過的標籤是你整理過的名稱，不列在這裡。點名稱開啟那件作品；按 × 從作品移除（之後收錄的作品仍會帶上）；按「刪除並封鎖」則永久略過，之後收錄、補資料都不會再加回來；要留著的按「保留」，之後不再列在這裡。要一次刪很多個，按「選取多個」或「全部刪除」。</p>
      <label className="search health-filter"><span>#</span><input value={rareFilter} onChange={event => setRareFilter(event.target.value)} placeholder="篩選標籤" /></label>
      <div className="rare-tools">{picked ? <>
        <span>已選 {picked.size} 個</span>
        <button type="button" className="ghost" disabled={busy} onClick={() => setPicked(new Set(rare.map(entry => entry.name)))}>全選{rareFilter ? '符合的' : ''}（{rare.length}）</button>
        <button type="button" className="ghost danger" disabled={busy || !picked.size} onClick={() => removeMany([...picked], false)}>刪除</button>
        <button type="button" className="ghost danger" disabled={busy || !picked.size} onClick={() => removeMany([...picked], true)}>刪除並封鎖</button>
        <button type="button" className="ghost" onClick={() => setPicked(null)}>取消</button>
      </> : <>
        <button type="button" className="ghost" disabled={busy} onClick={() => setPicked(new Set())}>選取多個</button>
        <button type="button" className="ghost danger" disabled={busy || !rare.length} onClick={() => removeMany(rare.map(entry => entry.name), false)}>{rareFilter ? `刪除符合的 ${rare.length} 個` : `全部刪除（${rare.length}）`}</button>
      </>}</div>
      <ul className={`rare-list${picked ? ' is-picking' : ''}`}>{rare.slice(0, rareAll || rareFilter ? 300 : 60).map(entry => <li key={entry.name} className={picked?.has(entry.name) ? 'is-picked' : undefined}>
        {picked
          ? <button type="button" className="rare-name" aria-pressed={picked.has(entry.name)} onClick={() => togglePick(entry.name)}>{picked.has(entry.name) ? '✓ ' : ''}{entry.name}</button>
          : <button type="button" className="rare-name" onClick={() => api(`/resources/${entry.id}`).then(onOpenItem).catch(failure => notify(errorNote(failure)))} title={`開啟「${entry.title}」`}>{entry.name}</button>}
        {!picked && <button type="button" className="rare-remove" disabled={busy} onClick={() => remove(entry.name, false)} aria-label={`移除 ${entry.name}`} title="從作品移除">×</button>}
        {!picked && <button type="button" className="rare-block" disabled={busy} onClick={() => keepRare(entry.name)} title="這個標籤要留著，不再列在這裡">保留</button>}
        {!picked && <button type="button" className="rare-block" disabled={busy} onClick={() => remove(entry.name, true)} title="移除並加入封鎖清單">刪除並封鎖</button>}
      </li>)}</ul>
      {data.kept?.length > 0 && <p className="rare-kept">已保留：{data.kept.map(name => <button key={name} type="button" disabled={busy} onClick={() => keepRare(name, false)} title="取消保留，重新列在這裡">{name} ×</button>)}</p>}
      {!rareAll && !rareFilter && rare.length > 60 && <button type="button" className="nav-more-toggle" onClick={() => setRareAll(true)}>顯示全部（{Math.min(rare.length, 300)}）</button>}
    </section>}

    {data.unused > 0 && <section className="health-block"><h3>沒有作品使用的標籤<small>{data.unused} 個</small></h3><p>作品刪除或改標籤後留下的空標籤，可以直接清除。</p>
      <button type="button" className="ghost" disabled={busy} onClick={() => run(() => api('/tags/cleanup', { method: 'POST' }), result => `已清除 ${result.removed} 個空標籤`)}>清除空標籤</button></section>}
  </div>
}

// 標籤規則：所有「原文 → 統一後名稱」依最後的名稱分組列出，可以新增、修改、刪除；封鎖清單可以解除。
// 規則存在典藏裡，收錄、補資料、編輯、手機同步都照同一份規則換名稱。
function TagRules({ onChanged, notify }) {
  const [data, reload, error] = useLoad('/tags/rules')
  const [filter, setFilter] = useState('')
  const [draft, setDraft] = useState({ alias: '', canonical: '' })
  const [editing, setEditing] = useState(null)  // { alias, canonical }：正在修改的那條規則
  const [all, setAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const run = async (request, message) => {
    setBusy(true)
    try { const result = await request(); notify(message(result), result?.undo); loadTagCategories(true); onChanged(); await reload(); return true } catch (failure) { notify(errorNote(failure)); return false } finally { setBusy(false) }
  }
  const save = (alias, canonical) => run(() => api('/tags/rules', { method: 'PUT', body: JSON.stringify({ alias, canonical }) }),
    result => result.merged ? `已新增規則「${result.alias} → ${result.canonical}」，現有的 ${result.merged} 件也改成「${result.canonical}」` : `已儲存規則「${result.alias} → ${result.final}」，之後收錄會自動換`)
  const removeRule = async alias => {
    if (!await ask(`刪除規則「${alias}」？之後收錄時「${alias}」不再自動換名稱；已經換好的作品標籤不變。`, { confirm: '刪除規則', danger: true })) return
    run(() => api(`/tags/rules?alias=${encodeURIComponent(alias)}`, { method: 'DELETE' }), () => `已刪除規則「${alias}」`)
  }
  const unblock = name => run(() => api('/tags/unblock', { method: 'POST', body: JSON.stringify({ name }) }), () => `已解除封鎖「${name}」，之後收錄會重新帶上`)
  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    const byFinal = new Map()
    for (const row of data?.aliases || []) {
      if (needle && ![row.alias, row.canonical, row.final].some(value => value.toLowerCase().includes(needle))) continue
      if (!byFinal.has(row.final)) byFinal.set(row.final, { final: row.final, count: row.count, rows: [] })
      byFinal.get(row.final).rows.push(row)
    }
    return [...byFinal.values()].sort((a, b) => b.count - a.count || a.final.localeCompare(b.final))
  }, [data, filter])
  const blocked = useMemo(() => (data?.blocked || []).filter(name => !filter || name.includes(filter.trim().toLowerCase())), [data, filter])
  if (error) return <p className="insight-empty">{error}</p>
  if (!data) return <p className="insight-empty">正在讀取規則…</p>
  const submit = event => { event.preventDefault(); if (draft.alias.trim() && draft.canonical.trim()) save(draft.alias, draft.canonical).then(done => done && setDraft({ alias: '', canonical: '' })) }
  const shown = all || filter ? groups : groups.slice(0, 60)
  return <div className={`insight-body${busy ? ' is-busy' : ''}`}>
    <header className="insight-head"><div><h2>標籤規則</h2><p>共 {data.aliases.length} 條規則、{data.blocked.length} 個封鎖。收錄、補資料、編輯時，左邊的原文會自動換成右邊統一後的名稱（不分大小寫）；「合併標籤」也會自動新增一條。</p></div></header>
    <TagSuggestions onApplied={() => { loadTagCategories(true); onChanged(); reload() }} notify={notify} />
    <form className="health-block rule-add" onSubmit={submit}>
      <h3>新增規則</h3>
      <div className="rule-add-row">
        <input value={draft.alias} onChange={event => setDraft({ ...draft, alias: event.target.value })} placeholder="原文，例如 bikini" aria-label="原文" maxLength={100} />
        <i aria-hidden="true">→</i>
        <input value={draft.canonical} onChange={event => setDraft({ ...draft, canonical: event.target.value })} placeholder="統一後的名稱，例如 泳裝" aria-label="統一後的名稱" list="oas-rule-finals" maxLength={100} />
        <button type="submit" className="ghost" disabled={busy || !draft.alias.trim() || !draft.canonical.trim()}>新增</button>
      </div>
      <datalist id="oas-rule-finals">{groups.slice(0, 400).map(group => <option key={group.final} value={group.final} />)}</datalist>
      <p>典藏裡已經有這個原文標籤時，會一起改成統一後的名稱（可以復原）。</p>
    </form>
    <label className="search health-filter"><span>#</span><input value={filter} onChange={event => setFilter(event.target.value)} placeholder="搜尋原文或名稱" /></label>
    <section className="health-block"><h3>統一後的名稱<small>{groups.length} 個</small></h3>
      {groups.length === 0 ? <p>{filter ? '沒有符合的規則。' : '還沒有規則。合併標籤或在上面新增後會出現在這裡。'}</p> : <ul className="health-list rule-list">{shown.map(group => <li key={group.final}>
        <span className="rule-final"><code>{group.final}</code><small>{group.count} 件</small></span>
        <span className="rule-aliases">{group.rows.map(row => editing?.alias === row.alias
          ? <form key={row.alias} className="rule-edit" onSubmit={event => { event.preventDefault(); save(row.alias, editing.canonical).then(done => done && setEditing(null)) }}>
            <span>{row.alias} →</span>
            <input autoFocus value={editing.canonical} onChange={event => setEditing({ ...editing, canonical: event.target.value })} onKeyDown={event => event.key === 'Escape' && (event.stopPropagation(), setEditing(null))} aria-label={`${row.alias} 統一後的名稱`} list="oas-rule-finals" maxLength={100} />
            <button type="submit" className="ghost" disabled={busy}>儲存</button><button type="button" className="ghost subtle" onClick={() => setEditing(null)}>取消</button>
          </form>
          : <span key={row.alias} className="rule-alias">
            <button type="button" onClick={() => setEditing({ alias: row.alias, canonical: row.canonical })} title={row.canonical !== row.final ? `先換成「${row.canonical}」，再換成「${row.final}」。點一下修改` : '點一下修改'}>{row.alias}{row.canonical !== row.final && <small>經「{row.canonical}」</small>}</button>
            <button type="button" className="rule-remove" disabled={busy} onClick={() => removeRule(row.alias)} aria-label={`刪除規則 ${row.alias}`} title="刪除這條規則">×</button>
          </span>)}</span>
      </li>)}</ul>}
      {!all && !filter && groups.length > 60 && <button type="button" className="nav-more-toggle" onClick={() => setAll(true)}>顯示全部（{groups.length}）</button>}
    </section>
    {blocked.length > 0 && <section className="health-block"><h3>封鎖清單<small>{blocked.length} 個</small></h3><p>這些名稱在收錄、補資料時一律略過（翻譯成它的原文也一樣）。解除後之後收錄會重新帶上，已經略過的作品不會自動補回。</p>
      <p className="rule-blocked">{blocked.map(name => <button key={name} type="button" disabled={busy} onClick={() => unblock(name)} title="解除封鎖">{name} ×</button>)}</p></section>}
  </div>
}

// 建議併入：還不在規則裡、但看起來就是某個現有標籤的（英文、簡體、寫法不同）。每次打開都重新計算，新收錄的作品也算在內。
// 「建立規則並併入」走 PUT /tags/rules（可以復原）；「略過」記在典藏裡，之後不再建議這一組。
const CONFIDENCE_LABEL = { high: '很可能', medium: '可能', low: '猜測' }
function TagSuggestions({ onApplied, notify }) {
  const [data, reload, error] = useLoad('/tags/suggestions')
  const [all, setAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const run = async (request, message, changed) => {
    setBusy(true)
    try { const result = await request(); notify(message(result), result?.undo); if (changed) onApplied(); await reload() } catch (failure) { notify(errorNote(failure)) } finally { setBusy(false) }
  }
  const apply = entry => run(() => api('/tags/rules', { method: 'PUT', body: JSON.stringify({ alias: entry.source, canonical: entry.target }) }),
    result => `已把「${entry.source}」併入「${entry.target}」${result.merged ? `（${result.merged} 件）` : ''}，之後收錄也會自動換`, true)
  const skip = (entry, value = true) => run(() => api('/tags/suggestions/skip', { method: 'POST', body: JSON.stringify({ source: entry.source, target: entry.target, skip: value }) }),
    () => `已略過「${entry.source} → ${entry.target}」，之後不再建議`)
  const restore = () => run(() => api('/tags/suggestions/skip', { method: 'POST', body: JSON.stringify({ skip: false }) }), () => '已恢復所有略過的建議')
  if (error) return <p className="insight-empty">{error}</p>
  if (!data) return <section className="health-block"><h3>建議併入</h3><p>正在比對標籤…</p></section>
  const shown = all ? data.items : data.items.slice(0, 40)
  return <section className={`health-block suggest-block${busy ? ' is-busy' : ''}`}><h3>建議併入<small>{data.total} 個</small></h3>
    <p>還沒有規則、但看起來就是某個現有標籤的：英文或簡體的寫法、只差大小寫或單複數、翻譯包裡的同義詞，以及幾乎總是一起出現的。按「建立規則並併入」後，現有作品改掛右邊的名稱，之後收錄也會自動換（可以復原）；不對的按「略過」。</p>
    {data.total === 0 ? <p className="suggest-empty">目前沒有要建議的標籤。新收錄或補資料後有新標籤時，會自動出現在這裡。</p>
      : <ul className="health-list suggest-list">{shown.map(entry => <li key={`${entry.source}|${entry.target}`}>
        <span className="suggest-main">
          <span className="health-tags"><code>{entry.source}</code><small>{entry.source_count} 件</small><i aria-hidden="true">→</i><code>{entry.target}</code><small>{entry.target_exists ? `${entry.target_count} 件` : '新名稱'}</small><b className={`suggest-confidence is-${entry.confidence}`}>{CONFIDENCE_LABEL[entry.confidence]}</b></span>
          <small className="suggest-reason">{entry.reason}</small>
        </span>
        <span className="health-actions"><button type="button" className="ghost" disabled={busy} onClick={() => apply(entry)}>建立規則並併入</button><button type="button" className="ghost subtle" disabled={busy} onClick={() => skip(entry)}>略過</button></span>
      </li>)}</ul>}
    {!all && data.items.length > 40 && <button type="button" className="nav-more-toggle" onClick={() => setAll(true)}>顯示全部（{data.items.length}）</button>}
    {data.skipped > 0 && <p className="suggest-skipped">已略過 {data.skipped} 組。<button type="button" disabled={busy} onClick={restore}>全部恢復</button></p>}
  </section>
}

// 待整理：缺標題或封面、來源失效的作品，依「可以重試」「要手動補」「來源失效」分組，每組說明該怎麼處理
const ATTENTION_GROUPS = [
  ['retry', '自動補資料沒成功', '多半是當時被網站限流或連線不穩，按「全部重試」。紳士有些作品要登入才看得到，重試也補不到：按「前往來源」登入後打開，再分享給 OAS。'],
  ['manual', '要你手動補', '禁漫、MissAV 等有防護的網站，典藏館的伺服器讀不到。手機 App 按「試著讀取」會用內建的瀏覽器一件一件打開作品頁補上標題與標籤。禁漫的封面載不到，多半是作品換了編號：按「到禁漫搜尋」用標題找新編號，打開後分享給 OAS，再把舊的這件刪除或合併。按「前往來源」打開作品頁：手機在瀏覽器按「分享」選 OAS，電腦按「收錄到 OAS」書籤；出現「這件已在典藏中」時按「補上它的空白欄位」。'],
  ['dead', '來源失效', '巡檢或補資料時來源網站回應「找不到」。常見原因是編號記到錯的網站（例如禁漫的編號被當成 N 網）：按「比對同編號」看 N 網、紳士、禁漫有沒有同一個編號，選對的那個改用；有標題的也可以打開作品按「來源失效？」用標題搜尋。'],
]
// 禁漫換了編號時（舊編號的作品頁不存在、封面也載不到），拿標題去禁漫搜尋新編號：去掉 [漢化組]、(場次) 等，多語標題只取第一段
const jmQuery = title => title.replace(/\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）/g, ' ').split(/[/|｜]/)[0].replace(/\s+/g, ' ').trim().slice(0, 40)
// 需要真正封面的類型；其他類型（文章、論壇、創作者頁）可以用網站圖示當封面
const COVER_KINDS = ['漫畫', '動畫', '影片']

function Attention({ onOpenItem, onChanged, notify }) {
  const [data, reload, error] = useLoad('/attention')
  const [busy, setBusy] = useState(false)
  const [matches, setMatches] = useState(null)  // 比對同編號的結果：{ results: { 作品 id: [候選] }, errors: { 作品 id: [訊息] } }；'loading' 為比對中
  // 在作品頁刪除後回到這裡，清單也要跟著拿掉
  useEffect(() => { window.addEventListener('oas-item-removed', reload); return () => window.removeEventListener('oas-item-removed', reload) }, [])
  const remove = async item => {
    if (!await ask(`確定將「${titleFor(item)}」移出典藏？一小時內可以復原。`, { confirm: '移出典藏', danger: true })) return
    setBusy(true)
    try {
      const result = await api(`/resources/${item.id}`, { method: 'DELETE' })
      announceRemoved(item.id); notify('已移除典藏', result?.undo); onChanged()
    } catch (failure) { notify(errorNote(failure)) } finally { setBusy(false) }
  }
  const retry = async ids => {
    setBusy(true)
    try {
      const result = await api('/attention/retry', { method: 'POST', body: JSON.stringify({ ids }) })
      notify(result.queued ? `已重新排入 ${result.queued} 件，在背景補資料` : '沒有可以重試的作品')
      onChanged(); await reload()
    } catch (failure) { notify(errorNote(failure)) } finally { setBusy(false) }
  }
  const matchAll = async () => {
    setMatches('loading')
    try { setMatches(await api('/attention/same-code')) } catch (failure) { setMatches(null); notify(errorNote(failure)) }
  }
  const fetchManual = async () => {
    setBusy(true)
    try {
      if (canReadPages()) {  // 手機 App：一件一件用看不到的瀏覽器打開作品頁，交給伺服器解析後補上
        const rows = data.groups.manual.filter(item => /^https?:\/\//.test(item.url || ''))
        const filled = await fillFromPages(api, rows, (index, total) => notify(`讀取作品頁 ${index}/${total}…`))
        notify(`讀了 ${rows.length} 件作品頁，補到 ${filled} 件`)
        onChanged(); await reload()
        return
      }
      const result = await api('/attention/fetch-manual', { method: 'POST' })
      notify(`試著讀取 ${result.tried} 件，補到 ${result.filled} 件${result.limited.length ? `；${result.limited.join('、')} 被限流，稍後再試` : ''}`)
      onChanged(); await reload()
    } catch (failure) { notify(errorNote(failure)) } finally { setBusy(false) }
  }
  // 同編號的作品已在典藏：把失效的這件（評分、筆記、書單、標籤）併進去後刪除它
  const mergeInto = async (item, row) => {
    if (!await ask(`把「${titleFor(item)}」併進典藏裡的 ${row.source_name} ${row.code}？
評分、筆記、書單與標籤會保留，失效的這件會刪除（一小時內可以復原）。`, { confirm: '併入' })) return
    setBusy(true)
    try {
      const result = await api(`/resources/${row.owned_id}/merge`, { method: 'POST', body: JSON.stringify({ ids: [item.id] }) })
      announceRemoved(item.id); notify(`已併進 ${row.source_name} ${row.code}`, result?.undo)
      onChanged(); await reload()
    } catch (failure) { notify(errorNote(failure)) } finally { setBusy(false) }
  }
  const useSiteIcon = async item => {
    setBusy(true)
    try {
      const updated = await api(`/resources/${item.id}/site-icon`, { method: 'POST' })
      notify(updated.image_kind === 'icon' ? '已改用網站圖示當封面' : '已改用頁面的代表圖當封面')
      onChanged(); await reload()
    } catch (failure) { notify(errorNote(failure)) } finally { setBusy(false) }
  }
  const openOwned = id => api(`/resources/${id}`).then(onOpenItem).catch(failure => notify(errorNote(failure)))
  const relink = async (item, row) => {
    if (!await ask(`${row.unconfirmed ? '注意：這個候選只確認到封面，作品可能已下架或是章節編號，請先點縮圖到網站確認。\n\n' : ''}「${titleFor(item)}」改用 ${row.source_name} ${row.code}？
原本的連結會記在筆記裡。`, { confirm: '改用這個' })) return
    setBusy(true)
    try {
      const moved = await api(`/resources/${item.id}/relink`, { method: 'POST', body: JSON.stringify({ source_name: row.source_name, code: row.code }) })
      notify(`已改用 ${row.source_name} ${row.code}${moved.title ? '' : '；標題要到作品頁分享補上'}`)
      onChanged(); await reload()
    } catch (failure) { notify(errorNote(failure)) } finally { setBusy(false) }
  }
  if (error) return <p className="insight-empty">{error}</p>
  if (!data) return <p className="insight-empty">正在整理…</p>
  const total = ATTENTION_GROUPS.reduce((sum, [key]) => sum + data.groups[key].length, 0)
  return <div className={`insight-body${busy ? ' is-busy' : ''}`}>
    <header className="insight-head"><div><h2>待整理</h2><p>缺標題、缺封面或來源失效的作品，依處理方式分組。{data.queued > 0 && `背景還在補 ${data.queued} 件，補好就不會出現在這裡。`}</p></div></header>
    {total === 0 ? <p className="insight-empty">都整理好了，沒有缺資料的作品。</p> : <div className="dup-groups"><h3 className="sr-only">作品清單</h3>{ATTENTION_GROUPS.filter(([key]) => data.groups[key].length).map(([key, label, hint]) => <section key={key} className="dup-group">
      <header><span>{label}（{data.groups[key].length}）</span>{key === 'retry' && <button type="button" className="ghost subtle" disabled={busy} onClick={() => retry([])}>全部重試</button>}{key === 'manual' && <button type="button" className="ghost subtle" disabled={busy} onClick={fetchManual}>{busy ? '處理中…' : '試著讀取'}</button>}{key === 'dead' && <button type="button" className="ghost subtle" disabled={busy || matches === 'loading'} onClick={matchAll}>{matches === 'loading' ? '比對中…' : matches ? '重新比對' : '比對同編號'}</button>}</header>
      <p className="attention-hint">{hint}</p>
      <div className="dup-items">{data.groups[key].map(item => <article key={item.id} className="dup-item" style={{ '--cover-colour': colourFor(item) }}>
        <Thumb item={item} onOpen={onOpenItem} />
        <div>
          <h4 title={titleFor(item)}>{titleFor(item)}</h4>
          <p><span>{item.source_name}</span>{item.missing.length > 0 && <span className="attention-missing">缺{item.missing.join('、')}</span>}</p>
          <p className="dup-code" title={item.code}>{item.code}</p>
          <div className="dup-actions">
            {(key === 'manual' || key === 'retry') && item.url && <a className={key === 'manual' ? 'primary' : 'ghost'} href={item.url} target="_blank" rel="noreferrer noopener">前往來源 ↗</a>}
            {item.source_name === JM_SOURCE && item.title && item.missing.some(name => name.startsWith('封面')) && jmQuery(item.title) && <a className="ghost" href={JM_SEARCH + encodeURIComponent(jmQuery(item.title))} target="_blank" rel="noreferrer noopener" title="禁漫常會換編號：用標題找新的編號">到禁漫搜尋 ↗</a>}
            {key !== 'dead' && item.missing.includes('封面') && !COVER_KINDS.includes(item.kind) && <button type="button" className="ghost" disabled={busy} onClick={() => useSiteIcon(item)} title="文章、論壇、創作者頁沒有封面時，用網站圖示就好">用網站圖示</button>}
            <button type="button" className="ghost" onClick={() => onOpenItem(item)}>開啟</button>
            <button type="button" className="ghost subtle" disabled={busy} onClick={() => remove(item)}>刪除</button>
          </div>
          {key === 'dead' && matches && matches !== 'loading' && /^\d+$/.test(item.code.trim()) && <SameCode rows={matches.results[item.id] || []} errors={matches.errors[item.id] || []} busy={busy} onPick={row => relink(item, row)} onMerge={row => mergeInto(item, row)} onOpenOwned={openOwned} />}
        </div>
      </article>)}</div>
    </section>)}</div>}
  </div>
}

// 比對同編號的候選：縮圖點開來源網站確認，按「改用」換成那個網站；已在典藏的可以合併過去。
// 有網站查失敗（連線失敗、被限流）時另外列出，不說成「沒有這個編號」。
function SameCode({ rows, errors, busy, onPick, onMerge, onOpenOwned }) {
  const failed = errors.length > 0 && <p className="same-code is-empty is-warn">{errors.join('；')}</p>
  if (!rows.length) return failed || <p className="same-code is-empty">N 網、紳士、禁漫都沒有這個編號</p>
  return <>
    <ul className="same-code">{rows.map(row => <li key={`${row.source_name}-${row.code}`}>
      <a href={row.url} target="_blank" rel="noreferrer noopener" title="在來源網站開啟確認"><i>{row.thumb && <img src={row.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" onError={hideBroken} />}</i></a>
      <span><b>{row.source_name} {row.code}{row.unconfirmed ? '（未確認）' : ''}</b><small>{row.title}</small></span>
      {row.owned_id
        ? <><button type="button" className="ghost" onClick={() => onOpenOwned(row.owned_id)}>開啟</button><button type="button" className="primary" disabled={busy} onClick={() => onMerge(row)}>合併到它</button></>
        : <button type="button" className={row.unconfirmed ? 'ghost' : 'primary'} disabled={busy} onClick={() => onPick(row)}>{row.unconfirmed ? '確認後改用' : '改用'}</button>}
    </li>)}</ul>
    {failed}
  </>
}

function Duplicates({ onOpenItem, onChanged, notify }) {
  const [data, reload, error] = useLoad('/duplicates')
  const [busy, setBusy] = useState(false)
  const act = async (request, message) => {
    setBusy(true)
    try { const result = await request(); notify(message, result?.undo); onChanged(); await reload() } catch (failure) { notify(errorNote(failure)) } finally { setBusy(false) }
  }
  const keepOne = async (group, item) => {
    const others = group.items.filter(entry => entry.id !== item.id)
    if (!await ask(`保留「${titleFor(item)}」（${item.source_name}），把另外 ${others.length} 件的標籤、書單、評分與筆記併進來後刪除它們？`, { confirm: '合併' })) return
    act(async () => {
      const result = await api(`/resources/${item.id}/merge`, { method: 'POST', body: JSON.stringify({ ids: others.map(entry => entry.id) }) })
      others.forEach(entry => announceRemoved(entry.id))  // 首頁有快取，要通知它拿掉
      return result
    }, `已合併 ${others.length + 1} 件，保留「${titleFor(item)}」`)
  }
  if (error) return <p className="insight-empty">{error}</p>
  if (!data) return <p className="insight-empty">正在比對…</p>
  return <div className={`insight-body${busy ? ' is-busy' : ''}`}>
    <header className="insight-head"><div><h2>重複作品</h2><p>標題相同（忽略【】、[ ] 裡的社團與語言標記）、網址相同或封面相同的作品。選「保留這件」會把其他幾件的標籤、書單、最高評分與筆記併進來，再刪除其他幾件。{data.dismissed > 0 && `已有 ${data.dismissed} 組標成不是重複。`}</p></div></header>
    {data.groups.length === 0 ? <p className="insight-empty">沒有找到重複的作品。</p> : <div className="dup-groups"><h3 className="sr-only">作品清單</h3>{data.groups.map(group => <section key={group.key} className="dup-group">
      <header><span>{group.reasons.map(reason => REASONS[reason]).join('、')}</span><button type="button" className="ghost subtle" disabled={busy} onClick={() => act(() => api('/duplicates/dismiss', { method: 'POST', body: JSON.stringify({ ids: group.items.map(item => item.id) }) }), '已標成不是重複')}>不是重複</button></header>
      <div className="dup-items">{group.items.map(item => <article key={item.id} className="dup-item" style={{ '--cover-colour': colourFor(item) }}>
        <Thumb item={item} onOpen={onOpenItem} />
        <div>
          <h4 title={titleFor(item)}>{titleFor(item)}</h4>
          <p><span>{item.source_name}</span><span><i className="status-dot" style={{ background: STATUS_COLOURS[item.status] }} />{item.status}</span>{item.rating > 0 && <span className="is-rated">★ {Number(item.rating).toFixed(1)}</span>}<span>{item.tags.length} 個標籤</span></p>
          <p className="dup-code" title={item.code}>{item.code}</p>
          <div className="dup-actions"><button type="button" className="primary" disabled={busy} onClick={() => keepOne(group, item)}>保留這件</button><button type="button" className="ghost" onClick={() => onOpenItem(item)}>開啟</button></div>
        </div>
      </article>)}</div>
    </section>)}</div>}
    {data.dismissed > 0 && <DismissedGroups busy={busy} onOpenItem={onOpenItem} onRestore={key => act(() => api('/duplicates/undismiss', { method: 'POST', body: JSON.stringify({ key }) }), '已還原，這組重新列入重複作品')} />}
  </div>
}

// 標成「不是重複」的組合：展開才載入，按錯了可以還原
function DismissedGroups({ busy, onOpenItem, onRestore }) {
  const [groups, setGroups] = useState(null)
  const load = () => api('/duplicates/dismissed').then(result => setGroups(result.groups)).catch(() => setGroups([]))
  return <details className="dup-dismissed" onToggle={event => { if (event.currentTarget.open) load() }}>
    <summary>已標成不是重複的組合</summary>
    {!groups ? <p className="insight-empty">讀取中…</p> : groups.length === 0 ? <p className="insight-empty">沒有可以還原的組合（組內作品已刪到只剩一件）。</p>
      : <ul>{groups.map(group => <li key={group.key}>
        <span>{group.items.map(item => <button key={item.id} type="button" className="dup-dismissed-item" onClick={() => onOpenItem(item)} title={titleFor(item)}>{titleFor(item)}<small>{item.source_name}</small></button>)}</span>
        <button type="button" className="ghost" disabled={busy} onClick={async () => { await onRestore(group.key); load() }}>還原</button>
      </li>)}</ul>}
  </details>
}

// 手動改任何標籤的分類：輸入名稱（有提示）後選分類。
function CategoryEditor({ busy, onSave }) {
  const [names, setNames] = useState([])
  const [categories, setCategories] = useState({})
  const [name, setName] = useState('')
  useEffect(() => { api('/tags').then(setNames).catch(() => {}); loadTagCategories().then(setCategories) }, [])
  const known = names.includes(name.trim())
  const current = categories[name.trim()] || 'tag'
  return <section className="health-block"><h3>修改標籤分類</h3><p>分類決定標籤在展示頁的分組，以及點下去是開資料頁（作者、社團、原作、角色）還是在典藏館篩選。</p>
    <div className="category-editor">
      <label className="search health-filter"><span>#</span><input list="oas-category-tags" value={name} onChange={event => setName(event.target.value)} placeholder="輸入標籤名稱" aria-label="標籤名稱" /></label>
      <datalist id="oas-category-tags">{names.map(entry => <option key={entry} value={entry} />)}</datalist>
      {known ? <><small>目前是「{CATEGORY_LABEL[current]}」，改成</small><Dropdown small ariaLabel="新分類" value={current} onChange={category => onSave(name.trim(), category).then(() => loadTagCategories().then(setCategories))} options={TAG_CATEGORIES.map(([value, label]) => ({ value, label }))} /></>
        : name.trim() && <small>找不到這個標籤</small>}
    </div>
    {busy && <small className="category-busy">儲存中…</small>}
  </section>
}
