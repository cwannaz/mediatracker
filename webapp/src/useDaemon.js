import { useCallback, useEffect, useRef, useState } from 'react'

// Minimal JSON-over-WebSocket client for the MediaTracker daemon.
// Each request sends {cmd, ...} and resolves with the matching {ok, cmd, ...}
// reply. The daemon answers requests in order, so a FIFO of pending resolvers is
// enough for this local single-client GUI.
const DEFAULT_URL = 'ws://127.0.0.1:8830'

export function useDaemon(url = DEFAULT_URL) {
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const pending = useRef([])

  useEffect(() => {
    let closed = false
    let retry

    const connect = () => {
      const ws = new WebSocket(url)
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = setTimeout(connect, 2000)
      }
      ws.onmessage = (ev) => {
        const resolve = pending.current.shift()
        if (resolve) {
          try { resolve(JSON.parse(ev.data)) } catch { resolve({ ok: false, error: 'bad json' }) }
        }
      }
    }
    connect()

    return () => {
      closed = true
      clearTimeout(retry)
      wsRef.current?.close()
    }
  }, [url])

  const send = useCallback((cmd, fields = {}) => {
    return new Promise((resolve, reject) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error('daemon not connected'))
        return
      }
      pending.current.push(resolve)
      ws.send(JSON.stringify({ cmd, ...fields }))
    })
  }, [])

  return { connected, send }
}
