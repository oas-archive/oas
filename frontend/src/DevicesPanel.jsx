// 「整理 → 其他裝置」（2026-11-26 改版，樣本 A：精簡單欄）。只在電腦本機顯示（InsightsView 的 devicesAvailable）。
// 頁首一行狀態＋開關（開啟前跳風險確認、勾「我了解」才能開）→ 開啟後的檢查清單（全部通過才能新增裝置）→ 配對卡 → 裝置清單（操作收進「⋯」）
// → 收起來的已撤銷裝置、連線紀錄、完整說明 → 頁尾「全部撤銷」。純邏輯在 devicesLogic.js
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { encode } from 'uqr'
import { api, ask, copyText, Dropdown, errorNote, useFocusTrap } from './shared.jsx'
import {
  addBlockedReason, CHECK_KEYS, checkItem, checksPass, checksSummary, countdownText, deviceKind, eventDetail, eventLabel, eventWarns, inUse,
  LEVEL_HINTS, levelLabel, LEVELS, pairUrl, qrPath, relativeTime, remoteStatus, secondsLeft, shortTime, splitDevices, voidedAlert,
} from './devicesLogic.js'

const FIREWALL_POLL_MS = 4000
const FIREWALL_POLL_LIMIT = 45  // 約 3 分鐘：使用者在系統管理員視窗看完、按 Enter 的時間

export default function DevicesPanel({ notify }) {
  const [remote, setRemote] = useState(null)
  const [items, setItems] = useState(null)
  const [events, setEvents] = useState(null)
  const [checks, setChecks] = useState(null)
  const [checking, setChecking] = useState(false)
  const [newId, setNewId] = useState(null)  // 剛配對成功的裝置：清單上標「剛加入」
  const [logOpen, setLogOpen] = useState(false)
  const loadDevices = () => api('/devices').then(result => { setItems(result.items); return result.items }).catch(error => { notify(errorNote(error)); return null })
  const loadEvents = () => api('/devices/events').then(result => setEvents(result.items)).catch(() => {})
  const reloadAll = () => { loadDevices(); loadEvents() }
  const checkSeq = useRef(0)
  const runChecks = async () => {
    const seq = ++checkSeq.current
    setChecking(true)
    try {
      const result = await api('/devices/checks')
      if (seq === checkSeq.current) setChecks(result)
      return result
    } catch (error) { notify(errorNote(error)); return null } finally { if (seq === checkSeq.current) setChecking(false) }
  }
  useEffect(() => {
    api('/devices/remote').then(setRemote).catch(error => notify(errorNote(error)))
    reloadAll()
    const timer = setInterval(reloadAll, 30000)  // 最後連線時間、新的事件
    return () => clearInterval(timer)
  }, [])
  const wantsLan = Boolean(remote?.enabled_next)
  useEffect(() => { if (wantsLan) runChecks(); else setChecks(null) }, [wantsLan])

  const changeRemote = async (enabled, removeFirewall = false) => {
    try {
      const result = await api('/devices/remote', { method: 'PUT', body: JSON.stringify({ enabled }) })
      setRemote(result)
      loadEvents()
      notify(enabled ? (result.enabled ? '區網模式維持開啟' : '已設定開啟。關掉典藏館視窗再打開，其他裝置才連得到。') : '已設定關閉，重開典藏館後生效')
      if (removeFirewall) await firewall(true)
    } catch (error) { notify(errorNote(error)) }
  }
  // 防火牆設定：後端開系統管理員視窗（Windows 會跳 UAC），之後每 4 秒重新檢查，直到規則出現（或移除）為止
  const pollTimer = useRef(null)
  useEffect(() => () => clearTimeout(pollTimer.current), [])
  const firewall = async (remove = false) => {
    try {
      const { result } = await api('/devices/firewall', { method: 'POST', body: JSON.stringify({ remove }) })
      if (result === 'cancelled') { notify('已取消：Windows 詢問時選了「否」，防火牆沒有變更'); return }
      notify(remove ? '已開啟移除防火牆規則的視窗，看完說明按 Enter 關閉' : '已開啟防火牆設定視窗：照說明做完按 Enter 關閉，這裡會自動重新檢查')
      if (remove) return
      clearTimeout(pollTimer.current)
      let left = FIREWALL_POLL_LIMIT
      const poll = async () => {
        const result = await runChecks()
        if (result?.firewall?.status === 'ok' || --left <= 0) return
        pollTimer.current = setTimeout(poll, FIREWALL_POLL_MS)
      }
      pollTimer.current = setTimeout(poll, FIREWALL_POLL_MS)
    } catch (error) { notify(errorNote(error)) }
  }

  const [dialog, setDialog] = useState(null)  // 'on' | 'off'
  const { active, inactive } = splitDevices(items || [])
  const status = remoteStatus(remote, active.length)
  const alert = voidedAlert(events || [])
  return <div className="insight-body devices-page">
    <header className="insight-head"><div><h2>其他裝置</h2><p>讓家裡的手機、平板用瀏覽器連到這台電腦的典藏館。這一頁只有這台電腦看得到。</p></div></header>

    <section className={`devices-status is-${status.tone}`} aria-live="polite">
      <span className="devices-dot" aria-hidden="true" />
      <div className="devices-status-text"><b>{status.title}</b>{status.sub && <span>{status.sub}</span>}</div>
      {remote && <button type="button" className="devices-switch" role="switch" aria-checked={remote.enabled_next} aria-label="區網模式" onClick={() => setDialog(remote.enabled_next ? 'off' : 'on')} />}
    </section>

    {wantsLan && <CheckList checks={checks} checking={checking} onRecheck={runChecks} onFirewall={() => firewall(false)} />}

    {alert && <div className="pair-alert" role="alert"><p><b>有裝置在猜配對碼，碼已作廢</b>（{shortTime(alert.time)}{alert.ips.length ? `，來源 ${alert.ips.join('、')}` : ''}）。認不得這個位址就檢查家裡的網路。</p>
      <button type="button" className="ghost subtle" onClick={() => { setLogOpen(true); requestAnimationFrame(() => document.querySelector('.devices-log')?.scrollIntoView({ behavior: 'smooth', block: 'start' })) }}>看紀錄</button></div>}

    <DeviceSection items={items} active={active} remote={remote} checks={checks} newId={newId} notify={notify} reload={reloadAll}
      onPaired={async startedAt => {
        const list = await loadDevices()
        loadEvents()
        const added = (list || []).find(item => item.status === 'active' && Date.parse(item.created_at) >= startedAt - 5000)
        if (added) { setNewId(added.id); notify(`已加入「${added.name}」，權限是${levelLabel(added.level)}`) }
      }} onPairEvent={loadEvents} />

    <div className="devices-folds">
      {inactive.length > 0 && <details className="devices-fold"><summary>已撤銷的裝置<small>{inactive.length}</small></summary>
        <ul className="device-list is-dim">{inactive.map(device => <li key={device.id} className="device-row">
          <DeviceIcon kind={deviceKind(device.name)} />
          <div className="device-main"><span className="device-name">{device.name}</span>
            <span className="device-meta"><span>{device.status === 'expired' ? '90 天沒用，已失效' : `撤銷於 ${shortTime(device.revoked_at)}`}</span><span>最後連線 {relativeTime(device.last_seen_at)}</span></span></div>
        </li>)}</ul></details>}
      <details className="devices-fold devices-log" open={logOpen} onToggle={event => setLogOpen(event.currentTarget.open)}>
        <summary>連線紀錄<small>保留最近 200 筆</small></summary>
        <EventList events={events} />
      </details>
      <details className="devices-fold"><summary>風險與設定說明<small>防火牆、連不到、不用時收回</small></summary><HelpText /></details>
    </div>

    {active.length > 0 && <RevokeAll count={active.length} notify={notify} reload={reloadAll} />}

    {dialog === 'on' && <RiskDialog onClose={() => setDialog(null)} onConfirm={() => { setDialog(null); changeRemote(true) }} />}
    {dialog === 'off' && <OffDialog firewallRule={Boolean(checks?.firewall?.rule)} windows={checks ? checks.windows !== false : true} onClose={() => setDialog(null)}
      onConfirm={removeFirewall => { setDialog(null); changeRemote(false, removeFirewall) }} />}
  </div>
}

// ---------- 開啟後的檢查清單 ----------

const CHECK_MARKS = { ok: '✓', fail: '!', unknown: '?' }
const CHECK_STATES = { ok: '通過', fail: '要處理', unknown: '無法偵測' }

function CheckList({ checks, checking, onRecheck, onFirewall }) {
  const [busy, setBusy] = useState(false)
  const runFirewall = async () => { setBusy(true); try { await onFirewall() } finally { setBusy(false) } }
  return <section className="devices-checks" aria-label="開始前的檢查" aria-busy={checking}>
    <header><h3>開始前的檢查</h3><small>{checks ? checksSummary(checks) : '檢查中…'}</small>
      <button type="button" className="ghost subtle" disabled={checking} onClick={onRecheck}>{checking ? '檢查中…' : '重新檢查'}</button></header>
    <ol>{CHECK_KEYS.map(key => {
      const item = checkItem(key, checks)
      const state = checks ? item.status : 'pending'
      return <li key={key} className={`devices-check is-${state}`}>
        <span className="devices-check-mark" aria-hidden="true">{checks ? CHECK_MARKS[item.status] : ''}</span>
        <div className="devices-check-text">
          <b>{item.title}<span className="sr-only">：{checks ? CHECK_STATES[item.status] : '檢查中'}</span></b>
          {checks && <p>{item.detail}</p>}
          {checks && item.extra?.map(text => <p key={text} className="devices-check-extra">{text}</p>)}
          {checks && item.action === 'network-settings' && <a className="ghost devices-check-action" href={checks.network?.settings_uri || 'ms-settings:network-status'}>開啟網路設定</a>}
          {checks && item.action === 'firewall' && checks.windows !== false && <button type="button" className="ghost devices-check-action" disabled={busy} onClick={runFirewall}>{busy ? '等待 Windows 確認…' : '設定防火牆'}</button>}
        </div>
      </li>
    })}</ol>
  </section>
}

// ---------- 裝置：新增（配對卡）與清單 ----------

function DeviceSection({ items, active, remote, checks, newId, notify, reload, onPaired, onPairEvent }) {
  const [level, setLevel] = useState('standard')
  const [pairing, setPairing] = useState(null)  // { code, expires_at, level, addresses, port, startedAt }
  const [busy, setBusy] = useState(false)
  const blocked = addBlockedReason(remote, checks)
  const start = async () => {
    setBusy(true)
    try {
      const result = await api('/devices/pairing', { method: 'POST', body: JSON.stringify({ level }) })
      setPairing({ ...result, startedAt: Date.now() })
      onPairEvent()
    } catch (error) { notify(errorNote(error)) } finally { setBusy(false) }
  }
  return <section className="devices-section" aria-label="裝置">
    {pairing && <PairCard pairing={pairing} notify={notify} onDone={async (outcome) => {
      setPairing(null)
      if (outcome === 'paired') onPaired(pairing.startedAt)
      else onPairEvent()
    }} />}
    <header><h3>裝置<small>{active.length} 台</small></h3>
      {!pairing && <button type="button" className="save" disabled={Boolean(blocked) || busy} onClick={start}>{busy ? '產生中…' : '新增裝置'}</button>}</header>
    {!pairing && blocked && <p className="devices-hint">{blocked}</p>}
    {!pairing && !blocked && <div className="devices-level"><span>新裝置的權限</span>
      <div className="density" role="group" aria-label="新裝置的權限">{LEVELS.map(([value, label]) => <button key={value} type="button" aria-pressed={level === value} className={level === value ? 'is-active' : ''} onClick={() => setLevel(value)}>{label}</button>)}</div>
      <small>{LEVEL_HINTS[level]}</small></div>}
    {!items ? <p className="devices-hint">讀取中…</p>
      : active.length ? <ul className={`device-list${remote && !remote.enabled ? ' is-dim' : ''}`}>{active.map(device => <DeviceRow key={device.id} device={device} isNew={device.id === newId} notify={notify} reload={reload} />)}</ul>
        : <div className="devices-empty"><b>還沒有其他裝置</b>{remote?.enabled && remote?.enabled_next ? '按「新增裝置」，在手機上輸入配對碼就會出現在這裡。' : '開啟區網模式、重開典藏館後，就能新增裝置。'}</div>}
    {active.length > 0 && remote && !remote.enabled && <p className="devices-hint">區網模式沒有開，這些裝置暫時連不進來；裝置會保留。</p>}
  </section>
}

// 配對卡：8 位數配對碼（5 分鐘、一次有效）＋QR＋倒數。每 3 秒問伺服器碼還在不在：不在了（不是自己取消、也沒過期）就是用掉了或被猜錯作廢
function PairCard({ pairing, notify, onDone }) {
  const [left, setLeft] = useState(() => secondsLeft(pairing.expires_at))
  const [address, setAddress] = useState(pairing.addresses[0] || '')
  const done = useRef(false)
  const finish = outcome => { if (done.current) return; done.current = true; onDone(outcome) }
  useEffect(() => {
    const tick = setInterval(() => setLeft(secondsLeft(pairing.expires_at)), 1000)
    const poll = setInterval(() => api('/devices/pairing').then(status => {
      if (status.active || done.current) return
      if (secondsLeft(pairing.expires_at) <= 0) { notify('配對碼已過期。要加裝置請重新產生。'); finish('expired'); return }
      finish('paired')
    }).catch(() => {}), 3000)
    return () => { clearInterval(tick); clearInterval(poll) }
  }, [])
  useEffect(() => { if (left <= 0) { notify('配對碼已過期。要加裝置請重新產生。'); finish('expired') } }, [left])
  const cancel = async () => {
    if (done.current) return
    done.current = true
    try { await api('/devices/pairing', { method: 'DELETE' }) } catch (error) { notify(errorNote(error)) }
    onDone('cancelled')
  }
  const url = pairUrl(address, pairing.port)
  const copy = async text => notify(await copyText(text) ? '已複製' : '這台電腦不能複製，請手動選取')
  return <section className="devices-pair" aria-label="新增裝置">
    <header><h3>新增裝置</h3><button type="button" className="ghost subtle" onClick={cancel}>取消</button></header>
    <div className="pair-show">
      <QrCode text={url} />
      <div className="pair-code-box">
        <p>用手機掃描 QR，或在瀏覽器開下面的網址，輸入配對碼。權限是{levelLabel(pairing.level)}。</p>
        <span className="pair-code" aria-label={`配對碼 ${pairing.code.split('').join(' ')}`}>{pairing.code.slice(0, 4)}<i aria-hidden="true" />{pairing.code.slice(4)}</span>
        <span className={`pair-left${left <= 30 ? ' is-low' : ''}`} role="timer" aria-live="off">剩 {countdownText(left)}</span>
        {pairing.addresses.length > 1 ? <Dropdown small ariaLabel="區網位址" value={address} onChange={setAddress} options={pairing.addresses.map(value => ({ value, label: pairUrl(value, pairing.port) }))} />
          : <code className="pair-url">{url || '找不到這台電腦的區網位址'}</code>}
      </div>
    </div>
    <div className="cover-actions">
      {url && <button type="button" className="ghost" onClick={() => copy(url)}>複製網址</button>}
      <button type="button" className="ghost" onClick={() => copy(pairing.code)}>複製配對碼</button>
    </div>
  </section>
}

function QrCode({ text }) {
  if (!text) return null
  const { data, size } = encode(text, { ecc: 'M', border: 0 })
  const margin = 2
  return <svg className="pair-qr" viewBox={`0 0 ${size + margin * 2} ${size + margin * 2}`} role="img" aria-label={`QR 碼：${text}`} shapeRendering="crispEdges">
    <rect width="100%" height="100%" fill="#eef1ff" /><path d={qrPath(data, margin)} fill="#080b12" />
  </svg>
}

const ICON_PATHS = {
  phone: <><rect x="7" y="3" width="10" height="18" rx="2.4" /><path d="M11 18h2" /></>,
  tablet: <><rect x="4.5" y="3" width="15" height="18" rx="2.4" /><path d="M11 18h2" /></>,
  laptop: <><rect x="5" y="5" width="14" height="10" rx="1.6" /><path d="M3 19h18" /></>,
}
const DeviceIcon = ({ kind }) => <svg className="device-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">{ICON_PATHS[kind]}</svg>

// 一台裝置一排：名稱、權限、最後連線；改名、權限、撤銷收進「⋯」選單
function DeviceRow({ device, isNew, notify, reload }) {
  const [menu, setMenu] = useState(false)
  const [editing, setEditing] = useState(null)
  const root = useRef(null)
  const trigger = useRef(null)
  useEffect(() => {
    if (!menu) return undefined
    const close = event => { if (!root.current?.contains(event.target)) setMenu(false) }
    document.addEventListener('pointerdown', close)
    root.current?.querySelector('[role^="menuitem"]')?.focus()
    return () => document.removeEventListener('pointerdown', close)
  }, [menu])
  const closeMenu = () => { setMenu(false); trigger.current?.focus() }
  const onMenuKey = event => {
    const entries = [...root.current.querySelectorAll('[role^="menuitem"]')]
    const index = entries.indexOf(document.activeElement)
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu() }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      entries[(index + (event.key === 'ArrowDown' ? 1 : -1) + entries.length) % entries.length]?.focus()
    }
    if (event.key === 'Tab') setMenu(false)
  }
  const change = async (patch, done) => {
    setMenu(false)
    try { await api(`/devices/${device.id}`, { method: 'PATCH', body: JSON.stringify(patch) }); notify(done); reload() } catch (error) { notify(errorNote(error)) }
  }
  const revoke = async () => {
    setMenu(false)
    if (!await ask(`撤銷「${device.name}」？它會立刻登出，要再用得重新配對。`, { confirm: '撤銷', danger: true })) return
    try { await api(`/devices/${device.id}/revoke`, { method: 'POST' }); notify(`已撤銷「${device.name}」`); reload() } catch (error) { notify(errorNote(error)) }
  }
  const rename = event => {
    event.preventDefault()
    const name = editing.trim()
    setEditing(null)
    if (name && name !== device.name) change({ name }, `已改名為「${name}」`)
  }
  return <li ref={root} className={`device-row${isNew ? ' is-new' : ''}`}>
    <DeviceIcon kind={deviceKind(device.name)} />
    <div className="device-main">
      {editing !== null ? <form className="device-rename" onSubmit={rename}>
        <input autoFocus value={editing} maxLength={60} onChange={event => setEditing(event.target.value)} onKeyDown={event => event.key === 'Escape' && (event.stopPropagation(), setEditing(null))} aria-label="裝置名稱" />
        <button type="submit" className="save">儲存</button><button type="button" className="ghost" onClick={() => setEditing(null)}>取消</button>
      </form> : <>
        <span className="device-name">{device.name}{isNew && <em className="device-new">剛加入</em>}</span>
        <span className="device-meta"><span className={`device-level is-${device.level}`}>{levelLabel(device.level)}</span><span>{inUse(device.last_seen_at) ? '正在使用' : `最後連線 ${relativeTime(device.last_seen_at)}`}</span></span>
      </>}
    </div>
    {editing === null && <button ref={trigger} type="button" className="device-menu-button" aria-haspopup="menu" aria-expanded={menu} aria-label={`${device.name} 的選項`} onClick={() => setMenu(!menu)}>⋯</button>}
    {menu && <div className="device-menu" role="menu" aria-label={`${device.name} 的選項`} onKeyDown={onMenuKey}>
      <button type="button" role="menuitem" onClick={() => { setMenu(false); setEditing(device.name) }}>改名</button>
      {LEVELS.map(([value, label]) => <button key={value} type="button" role="menuitemradio" aria-checked={device.level === value} onClick={() => device.level === value ? setMenu(false) : change({ level: value }, `「${device.name}」改成${label}`)}>
        {value === 'standard' ? '標準權限' : '唯讀'}{device.level === value && <b aria-hidden="true">✓</b>}</button>)}
      <p className="device-menu-info">配對 {shortTime(device.created_at)}{device.last_ip && <><br />位址 {device.last_ip}</>}</p>
      <button type="button" role="menuitem" className="is-danger" onClick={revoke}>撤銷</button>
    </div>}
  </li>
}

function RevokeAll({ count, notify, reload }) {
  const revokeAll = async () => {
    if (!await ask(`撤銷全部 ${count} 台裝置？它們會立刻登出，要再用得重新配對。`, { confirm: '全部撤銷', danger: true })) return
    try { const result = await api('/devices/revoke-all', { method: 'POST' }); notify(`已撤銷 ${result.revoked} 台裝置`); reload() } catch (error) { notify(errorNote(error)) }
  }
  return <footer className="devices-foot"><button type="button" className="ghost danger" onClick={revokeAll}>全部撤銷</button><span>讓所有裝置立刻登出</span></footer>
}

function EventList({ events }) {
  const [all, setAll] = useState(false)
  if (!events) return <p className="devices-hint">讀取中…</p>
  if (!events.length) return <p className="devices-hint">還沒有紀錄。配對、撤銷、沒配對的裝置來連線都會記在這裡。</p>
  const shown = all ? events : events.slice(0, 12)
  return <>
    <ol className="device-events">
      {shown.map(event => <li key={event.id} className={eventWarns(event.kind) ? 'is-warn' : undefined}>
        <time dateTime={event.time}>{shortTime(event.time)}</time>
        <span className="event-kind">{eventLabel(event.kind)}</span>
        <span className="event-who">{eventDetail(event)}</span>
      </li>)}
    </ol>
    {events.length > 12 && <button type="button" className="ghost subtle" onClick={() => setAll(!all)}>{all ? '顯示較少' : `顯示全部（${events.length}）`}</button>}
  </>
}

// ---------- 對話框（沿用全站的 .oas-modal） ----------

function Dialog({ label, onClose, children, className = '' }) {
  const ref = useFocusTrap()
  useEffect(() => {
    const onKey = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); onClose() } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])
  return createPortal(<div className="oas-modal ask-layer" onClick={event => event.target === event.currentTarget && onClose()}>
    <section ref={ref} className={`oas-modal-card devices-dialog ${className}`} role="dialog" aria-modal="true" aria-label={label}>{children}</section>
  </div>, document.body)
}

const RISKS = [
  ['只在家裡的私人網路開。', '咖啡廳、公司、宿舍、飯店這類公用網路不要開。', true],
  ['連線沒有加密。', '同一個 Wi‑Fi 裡的人有機會偷看內容或盜用登入。發現不對就撤銷那台裝置，它會立刻失效。', true],
  ['家裡每台裝置都看得到配對頁。', '沒有配對碼進不來，但它們知道這台電腦開著典藏館。'],
  ['要先設定防火牆一次。', '用系統管理員身分執行防火牆設定；Windows 跳出「允許存取」時只勾「私人網路」。'],
  ['不要同時開代理或埠轉送。', '經過它們的連線會被當成這台電腦本人，不用配對就能做所有事。', true],
  ['只在同一台路由器底下有效。', '電腦直接接數據機撥號、或手機用行動網路時連不到。'],
]

function RiskDialog({ onClose, onConfirm }) {
  const [agree, setAgree] = useState(false)
  const [more, setMore] = useState(false)
  return <Dialog label="開啟區網模式前，請先看完" onClose={onClose}>
    <h2>開啟區網模式前，請先看完</h2>
    <ul className="devices-risks">{RISKS.map(([head, body, key]) => <li key={head} className={key ? 'is-key' : undefined}><b>{head}</b>{body}</li>)}</ul>
    {more && <HelpText />}
    <label className="pref-row devices-agree"><input type="checkbox" checked={agree} onChange={event => setAgree(event.target.checked)} /><span>我了解上面的風險，這是我家裡的私人網路</span></label>
    <div className="smart-dialog-actions">
      {!more && <button type="button" className="ghost subtle devices-more" onClick={() => setMore(true)}>了解更多</button>}
      <button type="button" className="ghost" onClick={onClose}>先不要</button>
      <button type="button" className="save" disabled={!agree} onClick={onConfirm}>開啟區網模式</button>
    </div>
  </Dialog>
}

function OffDialog({ firewallRule, windows, onClose, onConfirm }) {
  const [removeRule, setRemoveRule] = useState(firewallRule)
  return <Dialog label="關閉區網模式？" onClose={onClose}>
    <h2>關閉區網模式？</h2>
    <p className="devices-dialog-text">重開典藏館後，其他裝置都連不進來。已配對的裝置會保留，下次開啟時仍然有效；要讓它們失效，請先用「全部撤銷」。</p>
    {windows && <label className="pref-row"><input type="checkbox" checked={removeRule} onChange={event => setRemoveRule(event.target.checked)} />
      <span><b>一併移除防火牆規則</b><small>規則以埠為準，典藏館沒開也一直有效。Windows 會詢問是否允許（選「是」）。</small></span></label>}
    <div className="smart-dialog-actions">
      <button type="button" autoFocus className="ghost" onClick={onClose}>保持開啟</button>
      <button type="button" className="ghost danger is-strong-danger" onClick={() => onConfirm(windows && removeRule)}>{windows && removeRule ? '關閉並移除防火牆規則' : '關閉區網模式'}</button>
    </div>
  </Dialog>
}

// 完整說明（風險文案第 2 節）：頁面底部可展開、開啟對話框的「了解更多」共用
function HelpText() {
  return <div className="devices-help">
    <h4>這個功能做什麼</h4>
    <p>讓家裡的手機、平板、另一台電腦用瀏覽器連到這台電腦的典藏館。資料只存在這台電腦；其他裝置只是遠端操作它。電腦要開著典藏館視窗，其他裝置才連得到。</p>
    <h4>其他裝置能做什麼</h4>
    <ul><li><b>標準</b>（預設）：瀏覽、搜尋、收錄、編輯、評分、整理。</li><li><b>唯讀</b>：只能瀏覽和搜尋。</li>
      <li><b>永遠只有這台電腦能做</b>：還原或下載備份、清除封面、改來源規則、匯入標籤包、管理裝置、開關區網模式。</li></ul>
    <h4>風險，以及怎麼降低</h4>
    <dl>
      <dt>同一個 Wi‑Fi 的人能偷看或盜用登入</dt><dd>家用區網沒辦法用一般的加密連線（HTTPS），內容與登入憑證以明碼傳送。只在設了密碼的家用 Wi‑Fi 用；可疑時撤銷該裝置；不用時關閉。</dd>
      <dt>家裡每台裝置都碰得到典藏館</dt><dd>開啟後電腦接受整個區網的連線，只靠配對擋人。配對碼 8 位數、5 分鐘、只能用一次，猜錯 5 次作廢。</dd>
      <dt>公用網路上被別人連</dt><dd>公用 Wi‑Fi 上的人和你在同一個區網。網路設為「私人」；防火牆規則只在私人網路生效。</dd>
      <dt>代理或埠轉送繞過配對</dt><dd>典藏館把「來自這台電腦本機」的連線當成你本人。開區網模式時關掉代理的「允許區網連線」、portproxy、ssh -L、WSL／Docker 埠轉送；路由器不要轉送 8080。</dd>
      <dt>防火牆規則一直開著</dt><dd>規則以埠為準（TCP 8080），不管典藏館有沒有開。不用時關閉區網模式並勾「一併移除防火牆規則」，或執行 <code>tools\enable-lan-firewall.ps1 -Remove</code>。</dd>
    </dl>
    <h4>連不到時</h4>
    <ol><li>手機和電腦連同一台路由器的 Wi‑Fi（不是行動網路、不是訪客網路）。</li><li>電腦的網路設為「私人」：Windows 設定 → 網路和網際網路 → 目前的網路 → 內容。</li>
      <li>執行過防火牆設定；Windows 若曾替 Python 建了「封鎖」規則，要先刪掉。</li><li>電腦直接接數據機撥號（PPPoE）時沒有區網，其他裝置連不到。</li>
      <li>電腦換了 IP，就要用新網址重新配對；可在路由器替電腦設固定 IP。</li></ol>
    <h4>不用時怎麼收回</h4>
    <ol><li>關閉區網模式，重開典藏館。</li><li>需要的話按「全部撤銷」。</li><li>刪除防火牆規則：關閉時勾「一併移除防火牆規則」，或執行 <code>tools\enable-lan-firewall.ps1 -Remove</code>。</li></ol>
  </div>
}
