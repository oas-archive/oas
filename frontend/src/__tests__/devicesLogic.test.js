// 「整理 → 其他裝置」的純邏輯：配對網址、倒數、QR 路徑、時間與事件文字
import { describe, expect, it } from 'vitest'
import { encode } from 'uqr'
import {
  addBlockedReason, checkItem, checksPass, checksSummary, countdownText, deviceKind, eventDetail, eventLabel, voidedAlert, eventWarns, inUse, levelLabel, pairUrl, qrPath,
  relativeTime, remoteStatus, secondsLeft, shortTime, splitDevices,
} from '../devicesLogic.js'

describe('pairUrl', () => {
  it('組出其他裝置要開的網址', () => {
    expect(pairUrl('192.168.50.10', 8080)).toBe('http://192.168.50.10:8080/')
    expect(pairUrl('192.168.50.10', 80)).toBe('http://192.168.50.10/')
    expect(pairUrl('fe80::1', 8080)).toBe('http://[fe80::1]:8080/')
    expect(pairUrl('', 8080)).toBe('')
  })
})

describe('倒數', () => {
  it('剩下的秒數不會是負的', () => {
    const now = Date.parse('2026-11-26T08:00:00Z')
    expect(secondsLeft('2026-11-26T08:05:00+00:00', now)).toBe(300)
    expect(secondsLeft('2026-11-26T07:59:00+00:00', now)).toBe(0)
    expect(secondsLeft('壞掉的時間', now)).toBe(0)
  })
  it('顯示成 分:秒', () => {
    expect(countdownText(300)).toBe('5:00')
    expect(countdownText(65)).toBe('1:05')
    expect(countdownText(0)).toBe('0:00')
  })
})

describe('qrPath', () => {
  it('每個深色格子一個方塊，留白會平移', () => {
    expect(qrPath([[true, false], [false, true]])).toBe('M0 0h1v1h-1zM1 1h1v1h-1z')
    expect(qrPath([[true]], 2)).toBe('M2 2h1v1h-1z')
  })
  it('真的配對網址編得出來', () => {
    const { data, size } = encode('http://192.168.50.10:8080/', { ecc: 'M', border: 0 })
    expect(size).toBeGreaterThanOrEqual(21)
    expect(data.length).toBe(size)
    expect(qrPath(data).length).toBeGreaterThan(100)
  })
})

describe('voidedAlert', () => {
  const events = [  // 新的在前
    { kind: 'unauthorized', ip: '192.168.50.30', time: 't5' },
    { kind: 'code_voided', ip: '192.168.50.30', time: 't4' },
    { kind: 'pair_failed', ip: '192.168.50.30', time: 't3' },
    { kind: 'pair_failed', ip: '192.168.50.31', time: 't2' },
    { kind: 'code_created', ip: '127.0.0.1', time: 't1' },
    { kind: 'pair_failed', ip: '192.168.50.99', time: 't0' },
  ]
  it('最近一組碼被猜到作廢：列出這組碼產生後猜錯的位址', () => {
    expect(voidedAlert(events)).toEqual({ time: 't4', ips: ['192.168.50.30', '192.168.50.31'] })
  })
  it('作廢後又產生新碼、配對成功或沒有事件時不提示', () => {
    expect(voidedAlert([{ kind: 'code_created', ip: '127.0.0.1' }, ...events])).toBe(null)
    expect(voidedAlert([{ kind: 'paired', ip: '192.168.50.21' }, ...events])).toBe(null)
    expect(voidedAlert([])).toBe(null)
    expect(voidedAlert()).toBe(null)
  })
})

describe('文字', () => {
  it('級別與事件的中文', () => {
    expect(levelLabel('standard')).toBe('標準')
    expect(levelLabel('readonly')).toBe('唯讀')
    expect(eventLabel('paired')).toBe('配對成功')
    expect(eventLabel('something_new')).toBe('something_new')
    expect(eventWarns('pair_failed')).toBe(true)
    expect(eventWarns('paired')).toBe(false)
  })
  it('事件細節：級別換中文、名稱不重複', () => {
    expect(eventDetail({ device_name: '測試平板', ip: '192.168.50.21', detail: '測試平板' })).toBe('測試平板、192.168.50.21')
    expect(eventDetail({ device_name: null, ip: '127.0.0.1', detail: 'readonly' })).toBe('127.0.0.1、唯讀')
    expect(eventDetail({ ip: '192.168.50.21', detail: '/api/resources' })).toBe('192.168.50.21、/api/resources')
  })
  it('時間：今年只顯示月日，其他年份加年', () => {
    const now = new Date(2026, 10, 26)
    expect(shortTime(new Date(2026, 10, 26, 8, 5).toISOString(), now)).toBe('11/26 08:05')
    expect(shortTime(new Date(2025, 0, 2, 23, 0).toISOString(), now)).toBe('2025/1/2 23:00')
    expect(shortTime(null, now)).toBe('—')
  })
  it('有效的裝置和已失效的分開', () => {
    const { active, inactive } = splitDevices([{ id: 1, status: 'active' }, { id: 2, status: 'revoked' }, { id: 3, status: 'expired' }])
    expect(active.map(item => item.id)).toEqual([1])
    expect(inactive.map(item => item.id)).toEqual([2, 3])
  })
})

// ---------- 新版頁面（樣本 A） ----------

const remote = (enabled, next) => ({ enabled, enabled_next: next, restart_needed: enabled !== next, addresses: ['192.168.50.10'], port: 8080 })
const allOk = () => ({
  windows: true,
  network: { status: 'ok', networks: [{ name: '家裡的 Wi-Fi', category: 'private', addresses: ['192.168.50.10'] }] },
  firewall: { status: 'ok', port: 8080, problems: [], public_python_rules: [], blocking_python_rules: [], rule: { name: 'OAS 區網' } },
  restart: { status: 'ok' },
  proxy: { status: 'ok', entries: [], port: 8080 },
})

describe('頁首狀態', () => {
  it('一個狀態句，不同時顯示設定值與目前值', () => {
    expect(remoteStatus(remote(false, false)).title).toBe('只有這台電腦能用典藏館')
    expect(remoteStatus(remote(true, true), 2)).toMatchObject({ tone: 'on', title: '家裡的其他裝置可以連進來', sub: '區網模式開啟中，2 台裝置可用' })
    expect(remoteStatus(remote(false, true))).toMatchObject({ tone: 'wait', title: '已設定開啟，重開典藏館後生效' })
    expect(remoteStatus(remote(true, false))).toMatchObject({ tone: 'wait', title: '已設定關閉，重開典藏館後生效' })
    expect(remoteStatus(null).title).toBe('讀取中…')
  })
})

describe('新增裝置能不能按', () => {
  it('依序：區網模式、重開、檢查清單', () => {
    expect(addBlockedReason(remote(false, false), null)).toBe('開啟區網模式後才能新增裝置。')
    expect(addBlockedReason(remote(true, false), allOk())).toBe('區網模式已設定關閉，不能再新增裝置。')
    expect(addBlockedReason(remote(false, true), allOk())).toBe('重開典藏館後才能新增裝置。')
    expect(addBlockedReason(remote(true, true), null)).toBe('正在檢查這台電腦的設定…')
    expect(addBlockedReason(remote(true, true), { ...allOk(), proxy: { status: 'fail', entries: [] } })).toBe('上面的檢查都通過後才能新增裝置。')
    expect(addBlockedReason(remote(true, true), allOk())).toBe('')
  })
  it('無法偵測不擋，只有沒通過才擋', () => {
    const unknown = { ...allOk(), network: { status: 'unknown', networks: [] }, firewall: { status: 'unknown' }, proxy: { status: 'unknown' } }
    expect(checksPass(unknown)).toBe(true)
    expect(checksSummary(unknown)).toBe('1 項通過，其他無法偵測')
    expect(checksPass({ ...allOk(), firewall: { status: 'fail' } })).toBe(false)
    expect(checksSummary({ ...allOk(), firewall: { status: 'fail' }, network: { status: 'fail' } })).toBe('還有 2 項要處理')
    expect(checksSummary(allOk())).toBe('全部通過')
    expect(checksPass(null)).toBe(false)
  })
})

describe('檢查清單文字', () => {
  it('公用網路：說明怎麼改，並給開設定的按鈕', () => {
    const item = checkItem('network', { network: { status: 'fail', networks: [{ name: '咖啡店', category: 'public', addresses: ['10.20.0.5'] }] } })
    expect(item.detail).toContain('「咖啡店」是公用網路')
    expect(item.action).toBe('network-settings')
    expect(checkItem('network', allOk()).action).toBe(null)
    expect(checkItem('network', null).detail).toContain('無法偵測')
  })
  it('防火牆：列出問題、給設定按鈕；允許公用網路的舊 Python 規則只提醒', () => {
    const fail = checkItem('firewall', { firewall: { status: 'fail', problems: ['還沒有「OAS 區網」規則'], public_python_rules: [] } })
    expect(fail.detail).toContain('還沒有「OAS 區網」規則')
    expect(fail.action).toBe('firewall')
    const ok = checkItem('firewall', { firewall: { ...allOk().firewall, public_python_rules: [{ name: 'python.exe' }, { name: 'pythonw.exe' }] } })
    expect(ok.status).toBe('ok')
    expect(ok.action).toBe(null)
    expect(ok.extra[0]).toContain('2 條 Python 的舊規則允許「公用網路」')
  })
  it('重開與轉送', () => {
    expect(checkItem('restart', { restart: { status: 'fail' } }).detail).toContain('關掉典藏館視窗再打開')
    const proxy = checkItem('proxy', { proxy: { status: 'fail', port: 8080, entries: [{ listen: '0.0.0.0:8080', connect: '127.0.0.1:8080' }] } })
    expect(proxy.detail).toContain('0.0.0.0:8080 → 127.0.0.1:8080')
    expect(checkItem('proxy', allOk()).detail).toContain('代理軟體偵測不到')
  })
})

describe('清單上的時間與圖示', () => {
  const now = Date.parse('2026-11-26T10:00:00Z')
  it('相對時間', () => {
    expect(relativeTime(null, now)).toBe('還沒連線')
    expect(relativeTime('2026-11-26T09:59:40Z', now)).toBe('剛剛')
    expect(relativeTime('2026-11-26T09:57:00Z', now)).toBe('3 分鐘前')
    expect(relativeTime('2026-11-26T07:30:00Z', now)).toBe('2 小時前')
    expect(relativeTime('2026-11-22T10:00:00Z', now)).toBe('4 天前')
    expect(relativeTime('2026-09-01T10:00:00Z', now)).toMatch(/^9\/1 /)
    expect(relativeTime('壞掉', now)).toBe('—')
  })
  it('5 分鐘內算正在使用', () => {
    expect(inUse('2026-11-26T09:57:00Z', now)).toBe(true)
    expect(inUse('2026-11-26T09:50:00Z', now)).toBe(false)
    expect(inUse(null, now)).toBe(false)
  })
  it('從名稱猜裝置種類', () => {
    expect(deviceKind('手機 Chrome')).toBe('phone')
    expect(deviceKind('客廳的平板')).toBe('tablet')
    expect(deviceKind('iPad Safari')).toBe('tablet')
    expect(deviceKind('書房的電腦')).toBe('laptop')
    expect(deviceKind('Windows Edge')).toBe('laptop')
    expect(deviceKind('')).toBe('phone')
  })
})
