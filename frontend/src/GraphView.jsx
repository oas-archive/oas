import { useEffect, useRef, useState } from 'react'
import { api, colourFor, coverSrc, hideBroken, setAmbientColour, STATUS_COLOURS, titleFor } from './shared.jsx'

// 關聯圖譜：以 canvas 繪製力導向圖。彈簧長度依標籤相似度（越像越近），節點顏色取自封面主色。
// 只在模擬進行或互動時重繪；懸停狀態放在 ref 中，不觸發 React 重新渲染整張圖。
const GRAPH_LIMIT = 400
export default function GraphView({ focus, setFocus, onOpen, notify }) {
  const [tag, setTag] = useState('')
  const [tagInput, setTagInput] = useState('')
  const [tagNames, setTagNames] = useState([])
  const [data, setData] = useState(null)
  const [tip, setTip] = useState(null)
  const wrapRef = useRef(null)
  const canvasRef = useRef(null)
  const engineRef = useRef(null)
  const handlers = useRef({})
  handlers.current = { onOpen, setFocus }

  useEffect(() => { api('/tags').then(setTagNames).catch(() => {}) }, [])
  useEffect(() => {
    let live = true
    setData(null); setTip(null)
    const params = focus ? `focus=${focus}` : tag ? `tag=${encodeURIComponent(tag)}` : `limit=${GRAPH_LIMIT}`
    api(`/graph?${params}`).then(result => live && setData(result)).catch(error => live && notify(error.message))
    return () => { live = false }
  }, [focus, tag])

  useEffect(() => {
    const canvas = canvasRef.current, wrap = wrapRef.current
    if (!data || !canvas || !wrap) return
    const ctx = canvas.getContext('2d')
    const nodes = data.nodes.map((node, index) => {
      const angle = index * 2.39996, radius = 14 * Math.sqrt(index + 0.5)  // 向日葵排列：初始位置固定，每次結果一致
      return { ...node, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, vx: 0, vy: 0, r: 3.2 + Math.sqrt(node.degree) * 1.7, colour: colourFor(node), neighbours: new Set() }
    })
    const byId = new Map(nodes.map(node => [node.id, node]))
    const links = data.edges.map(edge => ({ ...edge, a: byId.get(edge.source), b: byId.get(edge.target) })).filter(link => link.a && link.b)
    for (const link of links) { link.a.neighbours.add(link.b.id); link.b.neighbours.add(link.a.id) }
    const focusNode = focus ? byId.get(focus) : null
    if (focusNode) { focusNode.x = 0; focusNode.y = 0 }
    const engine = { nodes, links, alpha: 1, view: { x: 0, y: 0, k: 1 }, width: 0, height: 0, dpr: 1, hovered: null, drag: null, fitted: false, interacted: false, frame: 0 }
    engineRef.current = engine

    const tick = () => {
      const alpha = engine.alpha
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j]
          let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy
          if (d2 > 90000) continue
          if (d2 < 1) { dx = (i - j) * 0.01 + 0.5; dy = 0.5; d2 = 1 }
          const force = -80 * alpha / d2
          a.vx += dx * force; a.vy += dy * force; b.vx -= dx * force; b.vy -= dy * force
        }
      }
      for (const link of links) {
        const { a, b } = link
        const dx = b.x + b.vx - a.x - a.vx, dy = b.y + b.vy - a.y - a.vy
        const distance = Math.sqrt(dx * dx + dy * dy) || 1
        const rest = 26 + 150 * (1 - link.similarity)
        const pull = (distance - rest) / distance * alpha * (0.15 + 0.6 * link.similarity) * 0.5
        a.vx += dx * pull; a.vy += dy * pull; b.vx -= dx * pull; b.vy -= dy * pull
      }
      for (const node of nodes) {
        node.vx -= node.x * 0.012 * alpha; node.vy -= node.y * 0.012 * alpha  // 向中心的微弱引力，避免孤立群組飄走
        if (node.fx !== undefined) { node.x = node.fx; node.y = node.fy; node.vx = node.vy = 0; continue }
        node.vx *= 0.6; node.vy *= 0.6
        node.x += node.vx; node.y += node.vy
      }
      if (focusNode && focusNode.fx === undefined) { focusNode.x = 0; focusNode.y = 0 }
      engine.alpha += (0 - engine.alpha) * 0.02
    }

    const fit = () => {
      if (!nodes.length || !engine.width) return
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (const node of nodes) { minX = Math.min(minX, node.x); maxX = Math.max(maxX, node.x); minY = Math.min(minY, node.y); maxY = Math.max(maxY, node.y) }
      const k = Math.min(2.4, 0.9 * Math.min(engine.width / Math.max(60, maxX - minX), engine.height / Math.max(60, maxY - minY)))
      engine.view = { k, x: engine.width / 2 - k * (minX + maxX) / 2, y: engine.height / 2 - k * (minY + maxY) / 2 }
    }

    const draw = () => {
      const { view, width, height, dpr, hovered } = engine
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, width, height)
      ctx.save(); ctx.translate(view.x, view.y); ctx.scale(view.k, view.k)
      const lit = hovered ? hovered.neighbours : null
      const ambient = getComputedStyle(document.documentElement).getPropertyValue('--ambient').trim() || '#8c82ff'
      ctx.lineCap = 'round'
      for (const link of links) {
        const active = hovered && (link.a === hovered || link.b === hovered)
        ctx.globalAlpha = active ? 0.85 : hovered ? 0.035 : 0.07 + link.similarity * 0.3
        ctx.strokeStyle = active ? ambient : '#aab4ff'
        ctx.lineWidth = (active ? 1.4 : 0.5 + link.similarity * 1.6) / view.k
        ctx.beginPath(); ctx.moveTo(link.a.x, link.a.y); ctx.lineTo(link.b.x, link.b.y); ctx.stroke()
      }
      const scale = 1 / Math.sqrt(view.k)
      for (const node of nodes) {
        const dim = hovered && node !== hovered && !lit.has(node.id)
        ctx.globalAlpha = dim ? 0.18 : node.status === '棄坑' ? 0.45 : 1
        ctx.fillStyle = node.colour
        ctx.beginPath(); ctx.arc(node.x, node.y, node.r * scale, 0, Math.PI * 2); ctx.fill()
        if (node === hovered || node === focusNode) {
          ctx.globalAlpha = 0.9; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.6 / view.k
          ctx.beginPath(); ctx.arc(node.x, node.y, node.r * scale + 3 / view.k, 0, Math.PI * 2); ctx.stroke()
        }
      }
      ctx.restore()
      // 標籤以螢幕座標繪製，字級不隨縮放改變；只標示懸停節點與其鄰居，或放大後連結最多的節點。
      ctx.globalAlpha = 1
      ctx.font = '11px Inter, "Noto Sans TC", "Microsoft JhengHei", sans-serif'
      ctx.textBaseline = 'middle'
      // 依重要性排序後逐一放置，與已放置標籤重疊者略過，避免文字糊成一團。
      const rank = node => node === hovered ? 1e9 : node === focusNode ? 1e8 : node.degree
      const labelled = (hovered ? nodes.filter(node => node === hovered || lit.has(node.id)) : nodes.filter(node => node === focusNode || node.degree >= (view.k > 1.8 ? 2 : 5)))
        .sort((a, b) => rank(b) - rank(a))
      const placed = []
      for (const node of labelled) {
        if (placed.length >= 40) break
        const sx = node.x * view.k + view.x, sy = node.y * view.k + view.y
        if (sx < -50 || sy < -20 || sx > width + 50 || sy > height + 20) continue
        const text = titleFor(node).slice(0, 18)
        const w = ctx.measureText(text).width + 8
        const box = { x: sx + node.r * scale * view.k + 4, y: sy - 8, w, h: 16 }
        if (placed.some(other => box.x < other.x + other.w && other.x < box.x + box.w && box.y < other.y + other.h && other.y < box.y + box.h)) continue
        placed.push(box)
        ctx.fillStyle = '#080b12cc'
        ctx.fillRect(box.x, box.y, box.w, box.h)
        ctx.fillStyle = node === hovered ? '#ffffff' : '#c8d0e9'
        ctx.fillText(text, box.x + 4, sy)
      }
    }

    const loop = () => {
      engine.frame = 0
      const running = engine.alpha > 0.004
      if (running) { tick(); if (engine.alpha > 0.3) tick() }
      if (!engine.interacted && (!engine.fitted || running)) { fit(); if (!running) engine.fitted = true }
      draw()
      if (running) engine.frame = requestAnimationFrame(loop)
    }
    const request = () => { if (!engine.frame) engine.frame = requestAnimationFrame(loop) }
    const reheat = (target = 0.3) => { engine.alpha = Math.max(engine.alpha, target); request() }
    engine.request = request
    engine.relayout = () => { engine.interacted = false; engine.fitted = false; reheat(0.8) }
    engine.fit = () => { fit(); engine.interacted = true; request() }

    const resize = () => {
      const rect = wrap.getBoundingClientRect()
      engine.dpr = window.devicePixelRatio || 1
      engine.width = rect.width; engine.height = rect.height
      canvas.width = Math.round(rect.width * engine.dpr); canvas.height = Math.round(rect.height * engine.dpr)
      canvas.style.width = `${rect.width}px`; canvas.style.height = `${rect.height}px`
      if (!engine.interacted) engine.fitted = false
      request()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(wrap); resize()

    const toWorld = event => {
      const rect = canvas.getBoundingClientRect()
      const sx = event.clientX - rect.left, sy = event.clientY - rect.top
      return { sx, sy, x: (sx - engine.view.x) / engine.view.k, y: (sy - engine.view.y) / engine.view.k }
    }
    const hit = point => {
      const scale = 1 / Math.sqrt(engine.view.k)
      let best = null, bestDistance = Infinity
      for (const node of nodes) {
        const distance = Math.hypot(node.x - point.x, node.y - point.y)
        if (distance < node.r * scale + 5 / engine.view.k && distance < bestDistance) { best = node; bestDistance = distance }
      }
      return best
    }
    const setHovered = node => {
      if (engine.hovered === node) return
      engine.hovered = node
      canvas.style.cursor = node ? 'pointer' : engine.drag ? 'grabbing' : 'grab'
      if (node) setAmbientColour(node.colour, true)
      request()
    }
    const showTip = (node, point) => setTip(node ? { node, x: point.sx, y: point.sy } : null)

    const onPointerDown = event => {
      const point = toWorld(event)
      const node = hit(point)
      canvas.setPointerCapture(event.pointerId)
      engine.drag = { node, startX: point.sx, startY: point.sy, viewX: engine.view.x, viewY: engine.view.y, moved: false }
      if (node) { node.fx = node.x; node.fy = node.y }
      showTip(null)
    }
    const onPointerMove = event => {
      const point = toWorld(event)
      const drag = engine.drag
      if (drag) {
        if (Math.hypot(point.sx - drag.startX, point.sy - drag.startY) > 4) drag.moved = true
        if (!drag.moved) return
        engine.interacted = true
        if (drag.node) { drag.node.fx = point.x; drag.node.fy = point.y; reheat(0.25) }
        else { engine.view.x = drag.viewX + point.sx - drag.startX; engine.view.y = drag.viewY + point.sy - drag.startY; canvas.style.cursor = 'grabbing'; request() }
        return
      }
      const node = hit(point)
      setHovered(node)
      showTip(node, point)
    }
    const onPointerUp = event => {
      const drag = engine.drag
      engine.drag = null
      if (!drag) return
      if (drag.node) { delete drag.node.fx; delete drag.node.fy }
      canvas.style.cursor = engine.hovered ? 'pointer' : 'grab'
      if (drag.node && !drag.moved) handlers.current.onOpen(drag.node.id)
      else if (drag.node) showTip(drag.node, toWorld(event))
    }
    const onLeave = () => { if (!engine.drag) { setHovered(null); showTip(null) } }
    const onWheel = event => {
      event.preventDefault()
      const point = toWorld(event)
      const k = Math.min(8, Math.max(0.15, engine.view.k * Math.exp(-event.deltaY * 0.0015)))
      engine.view = { k, x: point.sx - point.x * k, y: point.sy - point.y * k }
      engine.interacted = true
      showTip(null)
      request()
    }
    const onDoubleClick = event => {
      const node = hit(toWorld(event))
      if (node) handlers.current.setFocus(node.id)
    }
    canvas.addEventListener('pointerdown', onPointerDown)
    canvas.addEventListener('pointermove', onPointerMove)
    canvas.addEventListener('pointerup', onPointerUp)
    canvas.addEventListener('pointercancel', onPointerUp)
    canvas.addEventListener('pointerleave', onLeave)
    canvas.addEventListener('wheel', onWheel, { passive: false })
    canvas.addEventListener('dblclick', onDoubleClick)
    canvas.style.cursor = 'grab'
    request()
    return () => {
      cancelAnimationFrame(engine.frame)
      observer.disconnect()
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('pointercancel', onPointerUp)
      canvas.removeEventListener('pointerleave', onLeave)
      canvas.removeEventListener('wheel', onWheel)
      canvas.removeEventListener('dblclick', onDoubleClick)
    }
  }, [data])

  const focusNode = focus && data?.nodes.find(node => node.id === focus)
  const applyTag = event => { event.preventDefault(); setFocus(null); setTag(tagInput.trim()) }
  const clear = () => { setFocus(null); setTag(''); setTagInput('') }
  const tipNode = tip?.node
  return <section className="graph" aria-label="關聯圖譜">
    <div className="graph-head">
      <div><h2>關聯星系</h2><span>節點越近、連線越亮，代表共用越多冷門標籤；常見標籤（如語言、翻譯）幾乎不計分。</span></div>
      <form className="graph-tools" onSubmit={applyTag}>
        <label className="search graph-tag"><span>#</span><input list="oas-tag-names" value={tagInput} onChange={event => setTagInput(event.target.value)} placeholder="只看某個標籤" /></label>
        <datalist id="oas-tag-names">{tagNames.map(name => <option key={name} value={name} />)}</datalist>
        <button type="submit" className="ghost">篩選</button>
        <button type="button" className="ghost" onClick={() => engineRef.current?.relayout()} title="重新計算排列">重新排列</button>
        <button type="button" className="ghost" onClick={() => engineRef.current?.fit()} title="縮放至全部節點">置中</button>
      </form>
    </div>
    <div className="graph-status">
      {focus ? <button type="button" className="graph-chip" onClick={clear}>以「{focusNode ? titleFor(focusNode) : `#${focus}`}」為中心（兩層關聯）×</button>
        : tag ? <button type="button" className="graph-chip" onClick={clear}>標籤 #{tag} ×</button>
        : <span>關聯最緊密的 {GRAPH_LIMIT} 件</span>}
      {data && <span>{data.nodes.length} 件、{data.edges.length} 條關聯{!focus && !tag && data.isolated ? `，另有 ${data.isolated} 件沒有可連結的標籤` : ''}</span>}
      <span className="graph-hint">拖曳平移、滾輪縮放、點擊開啟、雙擊以該作品為中心</span>
    </div>
    <div ref={wrapRef} className="graph-canvas">
      <canvas ref={canvasRef} />
      {!data && <p className="graph-empty">正在計算關聯…</p>}
      {data && data.nodes.length === 0 && <p className="graph-empty">{tag ? `沒有帶 #${tag} 的作品` : '還沒有足夠的共用標籤形成關聯。'}</p>}
      {tipNode && <div className="graph-tip" style={{ left: Math.min(tip.x + 16, (engineRef.current?.width || 600) - 250), top: Math.max(8, tip.y - 20), '--node': tipNode.colour || colourFor(tipNode) }}>
        {tipNode.image_url && <img src={coverSrc(tipNode, 240)} alt="" onError={hideBroken} />}
        <div><b>{titleFor(tipNode)}</b><small><i className="status-dot" style={{ background: STATUS_COLOURS[tipNode.status] }} />{tipNode.status}　{tipNode.rating > 0 ? `★ ${Number(tipNode.rating).toFixed(1)}` : '未評分'}　{tipNode.degree} 條關聯</small>
          <p>{tipNode.tags.slice(0, 6).map(name => <span key={name}>{name}</span>)}</p></div>
      </div>}
    </div>
  </section>
}
