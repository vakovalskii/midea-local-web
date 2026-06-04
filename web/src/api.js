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
