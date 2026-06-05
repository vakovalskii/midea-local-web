// Обёртка над REST API бэкенда (FastAPI, backend/server.py).
// Токен (если сервер за токеном) хранится в localStorage и шлётся в заголовке.

const TOKEN_KEY = 'ac_api_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

function headers(extra) {
  const h = { ...extra }
  const t = getToken()
  if (t) h['Authorization'] = `Bearer ${t}`
  return h
}

export class AuthError extends Error {}

async function jsonOrThrow(res) {
  if (res.status === 401) throw new AuthError('нужен токен доступа')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export function listDevices() {
  return fetch('/api/devices', { headers: headers() }).then(jsonOrThrow)
}

export function getState(deviceId) {
  return fetch(`/api/devices/${deviceId}/state`, { headers: headers() }).then(jsonOrThrow)
}

export function setState(deviceId, patch) {
  return fetch(`/api/devices/${deviceId}/set`, {
    method: 'POST',
    headers: headers({ 'content-type': 'application/json' }),
    body: JSON.stringify(patch),
  }).then(jsonOrThrow)
}

// Realtime: подписка на /api/ws (есть только у центра; у standalone нет —
// тогда тихо откатываемся на REST-поллинг). Возвращает функцию закрытия.
export function openStateSocket({ onDevices, onState, onAuthError } = {}) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${location.host}/api/ws?token=${encodeURIComponent(getToken())}`
  let ws
  let closed = false
  let everOpen = false
  let fails = 0
  let retry

  function connect() {
    ws = new WebSocket(url)
    ws.onopen = () => { everOpen = true; fails = 0 }
    ws.onmessage = (ev) => {
      let m
      try { m = JSON.parse(ev.data) } catch { return }
      if (m.type === 'devices') onDevices?.(m.devices)
      else if (m.type === 'state') onState?.(m.id, m.state)
    }
    ws.onclose = (ev) => {
      if (ev.code === 4401) { onAuthError?.(); return }
      if (closed) return
      // если эндпоинта нет (standalone) — соединение не открывалось: не спамим
      if (!everOpen) { fails += 1; if (fails > 3) return }
      retry = setTimeout(connect, everOpen ? 3000 : 1500 * fails)
    }
    ws.onerror = () => { try { ws.close() } catch { /* noop */ } }
  }

  connect()
  return () => { closed = true; clearTimeout(retry); try { ws && ws.close() } catch { /* noop */ } }
}
