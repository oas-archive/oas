import { useEffect, useMemo, useState } from 'react'
import GraphView from './GraphView.jsx'
import { api, CATEGORY_NAMES, colourFor, CoverFace, coverSrc, Dropdown, hideBroken, isFaceCover, loadTagCategories, STATUS_COLOURS, TAG_CATEGORIES, titleFor } from './shared.jsx'

export const INSIGHT_TABS = [['taste', '口味地圖'], ['tags', '標籤健檢'], ['duplicates', '重複作品'], ['graph', '關聯圖']]
const CATEGORY_LABEL = CATEGORY_NAMES
const REASONS = { title: '標題相同', url: '網址相同', cover: '封面相同' }

// 「關聯」頁：把標籤相似度變成可以馬上動手的事——挑下一本、整理標籤、清掉重複；原本的關聯圖收在最後一個分頁。
export default function InsightsView({ tab, setTab, focus, setFocus, onOpenId, onOpenItem, onFilterTags, onPickUnread, onChanged, notify }) {
  return <section className="insights" aria-label="關聯">
    <nav className="insight-tabs" role="tablist" aria-label="關聯分頁">
      {INSIGHT_TABS.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'is-active' : ''} onClick={() => setTab(id)}>{label}</button>)}
    </nav>
    {tab === 'taste' && <TasteMap onFilterTags={onFilterTags} onPickUnread={onPickUnread} onOpenItem={onOpenItem} notify={notify} />}
    {tab === 'tags' && <TagHealth onOpenItem={onOpenItem} onChanged={onChanged} notify={notify} />}
    {tab === 'duplicates' && <Duplicates onOpenItem={onOpenItem} onChanged={onChanged} notify={notify} />}
    {tab === 'graph' && <GraphView focus={focus} setFocus={setFocus} onOpen={onOpenId} notify={notify} />}
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

const PAIR_KEY = 'oas-kept-pairs'
const readKept = () => { try { return new Set(JSON.parse(localStorage.getItem(PAIR_KEY) || '[]')) } catch { return new Set() } }

function TagHealth({ onOpenItem, onChanged, notify }) {
  const [data, reload, error] = useLoad('/insights/tags')
  const [kept, setKept] = useState(readKept)
  const [swapped, setSwapped] = useState(() => new Set())
  const [rareFilter, setRareFilter] = useState('')
  const [rareAll, setRareAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const run = async (request, message) => {
    setBusy(true)
    try { const result = await request(); notify(message(result), result?.undo); loadTagCategories(true); onChanged(); await reload() } catch (failure) { notify(failure.message) } finally { setBusy(false) }
  }
  const merge = (source, target) => run(() => api('/tags/merge', { method: 'POST', body: JSON.stringify({ source, target }) }), result => `已把「${source}」併入「${target}」（${result.count} 件），之後抓到「${source}」也會自動換成「${target}」`)
  const setCategory = (name, category) => run(() => api('/tags/category', { method: 'PATCH', body: JSON.stringify({ name, category }) }), () => `已把「${name}」改成「${CATEGORY_LABEL[category]}」`)
  const remove = (name, block) => run(() => api('/tags/remove', { method: 'POST', body: JSON.stringify({ name, block }) }), () => block ? `已刪除並封鎖「${name}」` : `已刪除「${name}」`)
  const keep = pair => { const next = new Set(kept); next.add(pair); setKept(next); try { localStorage.setItem(PAIR_KEY, JSON.stringify([...next])) } catch { /* 只影響這次 */ } }
  const pairs = useMemo(() => (data?.paired || []).filter(entry => !kept.has(`${entry.target}|${entry.source}`)), [data, kept])
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
        <span>{titleFor(item)}</span><small>{item.source_name}{item.tags.length ? `，#${item.tags[0]}` : '，沒有標籤'}</small>
      </button>)}</div></section>}

    {data.rare_total > 0 && <section className="health-block"><h3>只用過一次的標籤<small>{data.rare_total} 個</small></h3><p>多半是拼法不同或太細的標籤。點名稱開啟那件作品；按 × 從作品移除，按「刪除並封鎖」之後補資料也不會再加回來。</p>
      <label className="search health-filter"><span>#</span><input value={rareFilter} onChange={event => setRareFilter(event.target.value)} placeholder="篩選標籤" /></label>
      <ul className="rare-list">{rare.slice(0, rareAll || rareFilter ? 300 : 60).map(entry => <li key={entry.name}>
        <button type="button" className="rare-name" onClick={() => api(`/resources/${entry.id}`).then(onOpenItem).catch(failure => notify(failure.message))} title={`開啟「${entry.title}」`}>{entry.name}</button>
        <button type="button" className="rare-remove" disabled={busy} onClick={() => remove(entry.name, false)} aria-label={`移除 ${entry.name}`} title="從作品移除">×</button>
        <button type="button" className="rare-block" disabled={busy} onClick={() => remove(entry.name, true)} title="移除並加入封鎖清單">刪除並封鎖</button>
      </li>)}</ul>
      {!rareAll && !rareFilter && rare.length > 60 && <button type="button" className="nav-more-toggle" onClick={() => setRareAll(true)}>顯示全部（{Math.min(rare.length, 300)}）</button>}
    </section>}

    {data.unused > 0 && <section className="health-block"><h3>沒有作品使用的標籤<small>{data.unused} 個</small></h3><p>作品刪除或改標籤後留下的空標籤，可以直接清除。</p>
      <button type="button" className="ghost" disabled={busy} onClick={() => run(() => api('/tags/cleanup', { method: 'POST' }), result => `已清除 ${result.removed} 個空標籤`)}>清除空標籤</button></section>}
  </div>
}

function Duplicates({ onOpenItem, onChanged, notify }) {
  const [data, reload, error] = useLoad('/duplicates')
  const [busy, setBusy] = useState(false)
  const act = async (request, message) => {
    setBusy(true)
    try { const result = await request(); notify(message, result?.undo); onChanged(); await reload() } catch (failure) { notify(failure.message) } finally { setBusy(false) }
  }
  const keepOne = (group, item) => {
    const others = group.items.filter(entry => entry.id !== item.id)
    if (!confirm(`保留「${titleFor(item)}」（${item.source_name}），把另外 ${others.length} 件的標籤、書單、評分與筆記併進來後刪除它們？`)) return
    act(() => api(`/resources/${item.id}/merge`, { method: 'POST', body: JSON.stringify({ ids: others.map(entry => entry.id) }) }), `已合併 ${others.length + 1} 件，保留「${titleFor(item)}」`)
  }
  if (error) return <p className="insight-empty">{error}</p>
  if (!data) return <p className="insight-empty">正在比對…</p>
  return <div className={`insight-body${busy ? ' is-busy' : ''}`}>
    <header className="insight-head"><div><h2>重複作品</h2><p>標題相同（忽略【】、[ ] 裡的社團與語言標記）、網址相同或封面相同的作品。選「保留這件」會把其他幾件的標籤、書單、最高評分與筆記併進來，再刪除其他幾件。{data.dismissed > 0 && `已有 ${data.dismissed} 組標成不是重複。`}</p></div></header>
    {data.groups.length === 0 ? <p className="insight-empty">沒有找到重複的作品。</p> : <div className="dup-groups">{data.groups.map(group => <section key={group.key} className="dup-group">
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
  </div>
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
