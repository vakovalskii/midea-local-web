import React, { useEffect, useRef, useState } from 'react'
import { listDevices, getState, setState, getToken, setToken, AuthError, openStateSocket } from './api.js'

const MIN = 16
const MAX = 30

// Человекочитаемые имена точек (агентов). Неизвестные — капитализируем.
const AGENT_LABELS = { office: 'Офис', home: 'Дом' }
const agentLabel = (a) => AGENT_LABELS[a] || (a ? a[0].toUpperCase() + a.slice(1) : '')

// Группировка устройств по точке (агенту), сохраняя порядок появления.
function groupByAgent(devices) {
  const m = new Map()
  for (const d of devices) {
    const k = d.agent || ''
    if (!m.has(k)) m.set(k, [])
    m.get(k).push(d)
  }
  return [...m.entries()]
}

const MODES = [
  { key: 'COOL', label: 'Холод', icon: '❄️' },
  { key: 'HEAT', label: 'Тепло', icon: '☀️' },
  { key: 'AUTO', label: 'Авто', icon: '🌗' },
  { key: 'DRY', label: 'Сушка', icon: '💧' },
  { key: 'FAN_ONLY', label: 'Вентил.', icon: '🌀' },
]

const FANS = [
  { key: 'AUTO', label: 'Авто' },
  { key: 'SILENT', label: 'Тихо' },
  { key: 'LOW', label: '1' },
  { key: 'MEDIUM', label: '2' },
  { key: 'HIGH', label: '3' },
  { key: 'MAX', label: 'Макс' },
]

const SWINGS = [
  { key: 'OFF', label: 'Стоп', icon: '⏸' },
  { key: 'VERTICAL', label: 'Вверх-вниз', icon: '↕' },
  { key: 'HORIZONTAL', label: 'Лево-право', icon: '↔' },
  { key: 'BOTH', label: 'Обе', icon: '✣' },
]

const ACCENTS = {
  COOL: ['#38bdf8', '#0ea5e9'],
  HEAT: ['#fb923c', '#f97316'],
  AUTO: ['#2dd4bf', '#14b8a6'],
  DRY: ['#a78bfa', '#8b5cf6'],
  FAN_ONLY: ['#94a3b8', '#64748b'],
}

function Dial({ target, power, accent, onAdjust, busy }) {
  const R = 130
  const C = 2 * Math.PI * R
  const frac = (target - MIN) / (MAX - MIN)
  const sweep = 0.75
  const dash = C * sweep
  const offset = dash * (1 - frac)
  return (
    <div className="dial" data-off={!power}>
      <svg viewBox="0 0 300 300" className="dial-svg">
        <circle cx="150" cy="150" r={R} className="dial-track"
          strokeDasharray={`${dash} ${C}`} transform="rotate(135 150 150)" />
        <circle cx="150" cy="150" r={R} className="dial-fill"
          stroke={power ? accent[0] : '#2a3340'}
          strokeDasharray={`${dash} ${C}`} strokeDashoffset={offset}
          transform="rotate(135 150 150)" />
      </svg>
      <div className="dial-center">
        <div className="dial-temp">{target}<span className="deg">°</span></div>
        <div className="dial-sub">{power ? 'целевая' : 'выключен'}</div>
      </div>
      <button className="dial-btn up" disabled={busy || !power} onClick={() => onAdjust(1)} aria-label="теплее">＋</button>
      <button className="dial-btn down" disabled={busy || !power} onClick={() => onAdjust(-1)} aria-label="холоднее">－</button>
    </div>
  )
}

// Экран ввода токена доступа
function TokenGate({ onSaved, error }) {
  const [val, setVal] = useState(getToken())
  return (
    <div className="screen center">
      <div className="gate">
        <div className="title">Доступ к кондиционеру</div>
        <div className="muted small" style={{ marginBottom: 14 }}>
          Введите токен доступа (AC_API_TOKEN с сервера)
        </div>
        <input className="token-input" type="password" value={val}
          placeholder="токен" onChange={(e) => setVal(e.target.value)} />
        {error && <div className="err small" style={{ marginTop: 8 }}>{error}</div>}
        <button className="token-save" onClick={() => { setToken(val.trim()); onSaved() }}>
          Сохранить и войти
        </button>
      </div>
    </div>
  )
}

export default function App() {
  const [needToken, setNeedToken] = useState(false)
  const [devices, setDevices] = useState(null)
  const [devId, setDevId] = useState(null)
  const [state, setSt] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const optimistic = useRef(null)
  // рефы для актуальных значений внутри WS-колбэков (избегаем устаревших замыканий)
  const devIdRef = useRef(null)
  const busyRef = useRef(false)
  devIdRef.current = devId
  busyRef.current = busy

  // 1) загрузка списка устройств
  async function loadDevices() {
    try {
      const list = await listDevices()
      setNeedToken(false)
      setDevices(list)
      setDevId((cur) => cur ?? (list[0] && list[0].id))
    } catch (e) {
      if (e instanceof AuthError) setNeedToken(true)
      else setError('нет связи с сервером')
    }
  }
  useEffect(() => { loadDevices() }, [])

  // 2) опрос состояния выбранного устройства
  async function load() {
    if (!devId) return
    try {
      const s = await getState(devId)
      setError(null)
      if (!busy) setSt(s)
      else if (optimistic.current) setSt((p) => ({ ...s, ...optimistic.current }))
    } catch (e) {
      if (e instanceof AuthError) setNeedToken(true)
      else setError('нет связи с кондиционером')
    }
  }
  useEffect(() => {
    if (!devId) return
    setSt(null)
    load()
    const t = setInterval(load, 12000)
    return () => clearInterval(t)
  }, [devId])

  // 3) realtime по WS (центр). Обновляет список устройств и состояние вживую.
  useEffect(() => {
    if (needToken) return
    const close = openStateSocket({
      onDevices: (list) => {
        setDevices(list)
        setDevId((cur) => cur ?? (list[0] && list[0].id))
      },
      onState: (id, st) => {
        if (id !== devIdRef.current) return
        if (busyRef.current) return // не затирать оптимистичное во время команды
        setSt(st)
        setError(null)
      },
      onAuthError: () => setNeedToken(true),
    })
    return close
  }, [needToken])

  async function send(patch) {
    setBusy(true)
    optimistic.current = patch
    setSt((s) => ({ ...s, ...patch }))
    try {
      const s = await setState(devId, patch)
      setSt(s); setError(null)
    } catch (e) {
      if (e instanceof AuthError) setNeedToken(true)
      else { setError('команда не прошла'); load() }
    } finally {
      optimistic.current = null
      setBusy(false)
    }
  }

  if (needToken) {
    return <TokenGate error={error} onSaved={() => { setError(null); loadDevices() }} />
  }

  if (!devices || !state) {
    return (
      <div className="screen center">
        <div className="loader" />
        <div className="muted">{error || 'подключаюсь…'}</div>
      </div>
    )
  }

  const accent = ACCENTS[state.mode] || ACCENTS.COOL
  const current = devices.find((d) => d.id === devId)

  return (
    <div className="screen" style={{ '--a1': accent[0], '--a2': accent[1] }} data-off={!state.power}>
      <div className="glow" />
      <header className="head">
        <div>
          {devices.length > 1 ? (
            <select className="dev-select" value={devId} onChange={(e) => setDevId(e.target.value)}>
              {groupByAgent(devices).map(([agent, list]) =>
                agent ? (
                  <optgroup key={agent} label={agentLabel(agent)}>
                    {list.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </optgroup>
                ) : (
                  list.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)
                )
              )}
            </select>
          ) : (
            <div className="title">{current ? current.name : 'Кондиционер'}</div>
          )}
          <div className="muted small">
            {current ? [current.agent && agentLabel(current.agent), current.ip].filter(Boolean).join(' · ') : ''}
          </div>
        </div>
        <button className={`power ${state.power ? 'on' : ''}`} disabled={busy}
          onClick={() => send({ power: !state.power })} aria-label="питание">⏻</button>
      </header>

      <Dial target={state.target} power={state.power} accent={accent} busy={busy}
        onAdjust={(d) => {
          const next = Math.min(MAX, Math.max(MIN, state.target + d))
          if (next !== state.target) send({ target: next })
        }} />

      <div className="readouts">
        <div className="chip"><span className="chip-val">{state.indoor ?? '—'}°</span><span className="chip-lbl">в комнате</span></div>
        <div className="chip"><span className="chip-val">{state.outdoor ?? '—'}°</span><span className="chip-lbl">на улице</span></div>
      </div>

      <div className="section">
        <div className="section-lbl">Режим</div>
        <div className="grid modes">
          {MODES.map((m) => (
            <button key={m.key} className={`tile ${state.mode === m.key ? 'active' : ''}`}
              disabled={busy || !state.power} onClick={() => send({ mode: m.key })}>
              <span className="tile-ico">{m.icon}</span><span className="tile-lbl">{m.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="section">
        <div className="section-lbl">Вентилятор</div>
        <div className="grid fans">
          {FANS.map((f) => (
            <button key={f.key} className={`pill ${state.fan === f.key ? 'active' : ''}`}
              disabled={busy || !state.power} onClick={() => send({ fan: f.key })}>{f.label}</button>
          ))}
        </div>
      </div>

      <div className="section">
        <div className="section-lbl">Шторки (обдув)</div>
        <div className="grid swings">
          {SWINGS.map((s) => (
            <button key={s.key} className={`tile ${state.swing === s.key ? 'active' : ''}`}
              disabled={busy || !state.power} onClick={() => send({ swing: s.key })}>
              <span className="tile-ico">{s.icon}</span><span className="tile-lbl">{s.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="section">
        <div className="grid toggles">
          <button className={`toggle ${state.turbo ? 'active' : ''}`}
            disabled={busy || !state.power} onClick={() => send({ turbo: !state.turbo })}>
            <span className="toggle-ico">🚀</span><span className="toggle-txt">Турбо (буст)</span>
            <span className="toggle-state">{state.turbo ? 'вкл' : 'выкл'}</span>
          </button>
          <button className="toggle" disabled={busy || !state.power} onClick={() => send({ display: true })}>
            <span className="toggle-ico">💡</span><span className="toggle-txt">Подсветка (LED)</span>
            <span className="toggle-state">переключить</span>
          </button>
        </div>
      </div>

      <footer className="foot muted small">
        {error ? <span className="err">{error}</span> : <span>{busy ? 'отправляю…' : 'на связи'}</span>}
      </footer>
    </div>
  )
}
