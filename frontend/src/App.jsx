import { useEffect, useState } from 'react'

const STATUS_TEXT = {
  pending: '待处理',
  running: '领取中',
  done: '已出结论',
}

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [view, setView] = useState('jobs')

  if (!token) {
    return (
      <Login
        username={username}
        password={password}
        onUsername={setUsername}
        onPassword={setPassword}
        onToken={(t, r) => {
          setToken(t)
          setRole(r)
        }}
      />
    )
  }

  function leave() {
    localStorage.clear()
    setToken('')
    setRole('')
    setView('jobs')
  }

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <nav>
        <button onClick={() => setView('jobs')} disabled={view === 'jobs'}>作业台</button>
        <button onClick={() => setView('handovers')} disabled={view === 'handovers'}>交班清单</button>
        <button onClick={leave}>退出</button>
      </nav>
      {view === 'jobs' ? <JobsView token={token} role={role} /> : <HandoversView token={token} role={role} />}
    </main>
  )
}

function Login({ username, password, onUsername, onPassword, onToken }) {
  const [error, setError] = useState('')

  async function enter() {
    setError('')
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || '登录失败')
      localStorage.setItem('print_token', data.access_token)
      localStorage.setItem('print_role', data.role)
      onToken(data.access_token, data.role)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <p>提交后接口只入队。另一进程领走偏差并写结论，页面轮询到结论出现。</p>
      <input value={username} onChange={(e) => onUsername(e.target.value)} />
      <input type="password" value={password} onChange={(e) => onPassword(e.target.value)} />
      <button onClick={enter}>登录</button>
      {error && <p>{error}</p>}
      <p>printer / print123456 可送复核、可交班；checker / check123456 只看，可翻交班快照</p>
    </main>
  )
}

function useApi(token) {
  return async (path, options = {}) => {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }
}

function JobsView({ token, role }) {
  const api = useApi(token)
  const [rows, setRows] = useState([])
  const [sheet, setSheet] = useState('插页-02')
  const [press, setPress] = useState('1号机')
  const [cyan, setCyan] = useState('0.08')
  const [magenta, setMagenta] = useState('0.02')
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/api/jobs'))
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [token])

  async function send() {
    setError('')
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          sheet,
          press,
          cyan_mm: Number(cyan),
          magenta_mm: Number(magenta),
        }),
      })
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      {role === 'writer' && (
        <p>
          <input value={sheet} onChange={(e) => setSheet(e.target.value)} placeholder="印张" />
          <input value={press} onChange={(e) => setPress(e.target.value)} placeholder="机台" />
          <input value={cyan} onChange={(e) => setCyan(e.target.value)} placeholder="青偏差mm" />
          <input value={magenta} onChange={(e) => setMagenta(e.target.value)} placeholder="品偏差mm" />
          <button onClick={send}>送复核</button>
        </p>
      )}
      {error && <p>{error}</p>}
      <table>
        <thead>
          <tr><th>编号</th><th>印张</th><th>机台</th><th>青</th><th>品</th><th>状态</th><th>结论</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.id}</td>
              <td>{row.sheet}</td>
              <td>{row.press || '—'}</td>
              <td>{row.cyan_mm}</td>
              <td>{row.magenta_mm}</td>
              <td>{STATUS_TEXT[row.status] || row.status}</td>
              <td>{row.verdict || '等待'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function HandoversView({ token, role }) {
  const api = useApi(token)
  const [snapshots, setSnapshots] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function loadList() {
    setSnapshots(await api('/api/handovers'))
  }

  useEffect(() => {
    loadList()
    const timer = setInterval(loadList, 3000)
    return () => clearInterval(timer)
  }, [token])

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null)
      return
    }
    let cancelled = false
    api(`/api/handovers/${selectedId}`).then((data) => {
      if (!cancelled) setDetail(data)
    })
    return () => {
      cancelled = true
    }
  }, [selectedId, token])

  async function handover() {
    setError('')
    setBusy(true)
    try {
      const created = await api('/api/handovers', { method: 'POST' })
      await loadList()
      setSelectedId(created.id)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>交班清单</h2>
      {role === 'writer' ? (
        <p>
          <button onClick={handover} disabled={busy}>一键交班</button>
          <span> 点交班记下当时所有待处理与领取中的编号、印张、机台，快照只读。</span>
        </p>
      ) : (
        <p>只读账号可翻阅交班历史与快照明细，但不能交班。</p>
      )}
      {error && <p>{error}</p>}
      <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
        <table>
          <thead>
            <tr><th>快照</th><th>交班时间</th><th>交班人</th><th>笔数</th></tr>
          </thead>
          <tbody>
            {snapshots.map((s) => (
              <tr
                key={s.id}
                onClick={() => setSelectedId(s.id)}
                style={{ cursor: 'pointer', fontWeight: s.id === selectedId ? 'bold' : 'normal' }}
              >
                <td>#{s.id}</td>
                <td>{new Date(s.created_at).toLocaleString('zh-CN')}</td>
                <td>{s.created_by}</td>
                <td>{s.item_count}</td>
              </tr>
            ))}
            {snapshots.length === 0 && (
              <tr><td colSpan={4}>尚无交班快照</td></tr>
            )}
          </tbody>
        </table>
        {detail && (
          <table>
            <thead>
              <tr>
                <th colSpan={6}>
                  快照 #{detail.id}（{new Date(detail.created_at).toLocaleString('zh-CN')} 由 {detail.created_by} 交班，共 {detail.item_count} 笔，只读）
                </th>
              </tr>
              <tr><th>编号</th><th>印张</th><th>机台</th><th>青</th><th>品</th><th>交班时状态</th></tr>
            </thead>
            <tbody>
              {detail.items.map((item) => (
                <tr key={item.job_id}>
                  <td>{item.job_id}</td>
                  <td>{item.sheet}</td>
                  <td>{item.press || '—'}</td>
                  <td>{item.cyan_mm}</td>
                  <td>{item.magenta_mm}</td>
                  <td>{STATUS_TEXT[item.status] || item.status}</td>
                </tr>
              ))}
              {detail.items.length === 0 && (
                <tr><td colSpan={6}>交班时没有待处理或领取中的印张</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
