import { useEffect, useState } from 'react'
import { api, STATUS_COLOURS, STATES } from './shared.jsx'

const monthLabel = key => `${Number(key.slice(5))} 月`
const percent = value => `${Math.round(value * 100)}%`

// 統計：全部由 /api/stats 即時計算。完食時間從 v0.12 才開始記錄，之前完食的作品不會出現在「每月完食」。
export default function StatsView({ onTag, onStatus, notify }) {
  const [data, setData] = useState(null)
  useEffect(() => { api('/stats').then(setData).catch(error => notify(error.message)) }, [])
  if (!data) return <p className="insight-empty">正在統計…</p>
  const { totals } = data
  const ratingMax = Math.max(1, ...['1', '2', '3', '4', '5'].map(key => data.ratings[key]))
  return <section className="stats" aria-label="統計">
    <dl className="stat-tiles">
      <div><dt>典藏</dt><dd>{totals.total}</dd><small>今年收錄 {totals.this_year}</small></div>
      <div><dt>已完食</dt><dd>{totals.finished}</dd><small>佔 {percent(totals.finished_ratio)}</small></div>
      <div><dt>有評分</dt><dd>{totals.rated}</dd><small>{totals.avg_rating ? `平均 ★ ${totals.avg_rating.toFixed(1)}` : '還沒有評分'}</small></div>
      <div><dt>開啟過</dt><dd>{totals.opened}</dd><small>寫過筆記 {totals.notes}</small></div>
      <div><dt>標籤</dt><dd>{totals.tags}</dd><small>不重複的標籤數</small></div>
    </dl>

    <div className="stat-grid">
      <section className="stat-card is-wide"><h3>每月收錄與完食</h3><p>近 12 個月。{data.finished_tracking_since ? '' : '完食時間從這一版開始記錄，之後標成「已完食」的作品才會算進來。'}</p>
        <MonthBars months={data.months} series={[['收錄', data.added, 'is-added'], ['完食', data.finished, 'is-finished']]} />
      </section>

      <section className="stat-card"><h3>閱讀狀態</h3>
        <ul className="stat-bars">{STATES.map(status => <li key={status}>
          <button type="button" onClick={() => onStatus(status)} title={`在典藏館只看「${status}」`}><span><i className="status-dot" style={{ background: STATUS_COLOURS[status] }} />{status}</span><b>{data.statuses[status] || 0}</b></button>
          <i style={{ width: `${((data.statuses[status] || 0) / Math.max(1, totals.total)) * 100}%`, background: STATUS_COLOURS[status] }} />
        </li>)}</ul>
      </section>

      <section className="stat-card"><h3>評分分布</h3><p>{data.ratings.unrated} 件還沒有評分。</p>
        <ul className="stat-bars">{['5', '4', '3', '2', '1'].map(star => <li key={star}>
          <span className="stat-label"><span className="is-rated">{'★'.repeat(Number(star))}</span><b>{data.ratings[star]}</b></span>
          <i className="is-gold" style={{ width: `${(data.ratings[star] / ratingMax) * 100}%` }} />
        </li>)}</ul>
      </section>

      <RankCard title="最常收藏的標籤" rows={data.tags} onTag={onTag} hash />
      <RankCard title="作者" rows={data.artists} onTag={onTag} empty="還沒有分類為「作者」的標籤（補齊作品資訊後會出現）。" />
      <RankCard title="原作" rows={data.parodies} onTag={onTag} empty="還沒有分類為「原作」的標籤。" />

      <section className="stat-card"><h3>類型與來源</h3>
        <ul className="stat-pairs">{data.kinds.map(([name, count]) => <li key={name}><span>{name}</span><b>{count}</b></li>)}</ul>
        <ul className="stat-pairs is-sources">{data.sources.map(([name, count]) => <li key={name}><span>{name}</span><b>{count}</b></li>)}</ul>
      </section>
    </div>
  </section>
}

function MonthBars({ months, series }) {
  const max = Math.max(1, ...series.flatMap(([, values]) => values))
  return <div className="month-chart">
    <div className="month-legend">{series.map(([label, , tone]) => <span key={label} className={tone}><i />{label}</span>)}</div>
    <ol className="month-bars">{months.map((month, index) => <li key={month} title={series.map(([label, values]) => `${label} ${values[index]}`).join('，')}>
      <div>{series.map(([label, values, tone]) => <i key={label} className={tone} style={{ height: `${(values[index] / max) * 100}%` }}>{values[index] > 0 && <b>{values[index]}</b>}</i>)}</div>
      <span>{monthLabel(month)}</span>
    </li>)}</ol>
  </div>
}

function RankCard({ title, rows, onTag, hash = false, empty = '' }) {
  const max = Math.max(1, ...rows.map(row => row.count))
  return <section className="stat-card"><h3>{title}</h3>
    {rows.length === 0 ? <p>{empty}</p> : <ul className="stat-bars">{rows.map(row => <li key={row.name}>
      <button type="button" onClick={() => onTag(row.name)} title={`在典藏館查看 ${row.name}`}><span>{hash ? '#' : ''}{row.name}</span><b>{row.count}{row.avg_rating ? <small className="is-rated">★ {row.avg_rating.toFixed(1)}</small> : null}</b></button>
      <i style={{ width: `${(row.count / max) * 100}%` }} />
    </li>)}</ul>}
  </section>
}
