import { canWrite } from './access.js'
import { useCallback, useEffect, useState } from 'react'
import { SkeletonGrid } from './Extras.jsx'
import { Card, Masonry } from './Gallery.jsx'
import { api, CATEGORY_NAMES, DENSITIES, Dropdown, errorNote, loadTagCategories, TAG_CATEGORIES } from './shared.jsx'

// 作者、社團、原作、角色的資料頁：全部作品、閱讀情況、常一起出現的人與標籤，也可以改分類。
export default function CreatorView({ name, onOpen, onHover, onQuick, onProfile, onFilterTags, onPickUnread, notify, density }) {
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
        {canWrite() && <div className="creator-category"><span>分類</span><Dropdown small ariaLabel="標籤分類" value={profile.category} onChange={changeCategory} options={TAG_CATEGORIES.map(([value, label]) => ({ value, label }))} /></div>}
      </div>
    </div>
    {profile.people.length > 0 && <div className="creator-related"><h3>常一起出現</h3><div>{profile.people.map(entry => <button key={entry.name} type="button" onClick={() => onProfile(entry.name)} title={`開啟 ${entry.name} 的資料頁`}><small>{CATEGORY_NAMES[entry.category]}</small>{entry.name}<b>{entry.count}</b></button>)}</div></div>}
    {profile.tags.length > 0 && <div className="creator-related"><h3>常見標籤</h3><div>{profile.tags.map(entry => <button key={entry.name} type="button" onClick={() => onFilterTags([name, entry.name])} title={`在書庫查看同時有 ${name} 與 #${entry.name} 的作品`}>#{entry.name}<b>{entry.count}</b></button>)}</div></div>}
    <h3 className="creator-works">全部作品<small>依評分排列</small></h3>
    {items === null ? <SkeletonGrid columns={4} count={8} /> : <Masonry minColumn={DENSITIES.find(([value]) => value === density)?.[2] || 220} className={`density-${density}`} items={items} renderItem={item => <Card key={item.id} item={item} onOpen={onOpen} list={items} onHover={onHover} onQuick={onQuick ? quickLocal : null} />} />}
  </section>
}
