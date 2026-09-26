import { useEffect, useState } from 'react'

const STATUS_LABEL = { pending: '待处理', running: '领取中', done: '已出结论' }
const statusLabel = (s) => STATUS_LABEL[s] || s
const fmtTime = (s) => new Date(s).toLocaleString()

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [page, setPage] = useState('jobs')

  async function api(path, options = {}) {
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

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    localStorage.setItem('print_token', data.access_token)
    localStorage.setItem('print_role', data.role)
    setToken(data.access_token)
    setRole(data.role)
  }

  function leave() {
    localStorage.clear()
    setToken('')
    setRole('')
  }

  if (!token) {
    return (
      <main>
        <h1>印刷套准复核台</h1>
        <p>提交后接口只入队。另一进程领走偏差并写结论，页面轮询到结论出现。</p>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button onClick={enter}>登录</button>
        <p>printer / print123456 可送复核与交班；checker / check123456 只看</p>
      </main>
    )
  }

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <nav>
        <button onClick={() => setPage('jobs')} disabled={page === 'jobs'}>复核台</button>
        <button onClick={() => setPage('handovers')} disabled={page === 'handovers'}>交班清单</button>
        <button onClick={leave}>退出</button>
      </nav>
      {page === 'jobs' ? <JobsPage api={api} role={role} /> : <HandoverPage api={api} role={role} />}
    </main>
  )
}

function JobsPage({ api, role }) {
  const [rows, setRows] = useState([])
  const [sheet, setSheet] = useState('插页-02')
  const [machine, setMachine] = useState('一号机')
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
  }, [])

  async function send() {
    setError('')
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          sheet,
          machine,
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
          <input value={sheet} onChange={(e) => setSheet(e.target.value)} />
          <input value={machine} onChange={(e) => setMachine(e.target.value)} />
          <input value={cyan} onChange={(e) => setCyan(e.target.value)} />
          <input value={magenta} onChange={(e) => setMagenta(e.target.value)} />
          <button onClick={send}>送复核</button>
        </p>
      )}
      {error && <p>{error}</p>}
      <table>
        <thead>
          <tr><th>印张</th><th>机台</th><th>青</th><th>品</th><th>状态</th><th>结论</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.sheet}</td>
              <td>{row.machine}</td>
              <td>{row.cyan_mm}</td>
              <td>{row.magenta_mm}</td>
              <td>{statusLabel(row.status)}</td>
              <td>{row.verdict || '等待'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function HandoverPage({ api, role }) {
  const [list, setList] = useState([])
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')

  async function refresh() {
    try {
      setList(await api('/api/handovers'))
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function handover() {
    setError('')
    try {
      const created = await api('/api/handovers', { method: 'POST' })
      await refresh()
      setDetail(created)
    } catch (err) {
      setError(err.message)
    }
  }

  async function open(id) {
    setError('')
    try {
      setDetail(await api(`/api/handovers/${id}`))
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      {role === 'writer' && (
        <p>
          <button onClick={handover}>一键交班</button>
        </p>
      )}
      {error && <p>{error}</p>}
      <h2>历史快照</h2>
      <table>
        <thead>
          <tr><th>快照号</th><th>交班人</th><th>交班时间</th><th>笔数</th><th></th></tr>
        </thead>
        <tbody>
          {list.map((h) => (
            <tr key={h.id}>
              <td>{h.id}</td>
              <td>{h.created_by}</td>
              <td>{fmtTime(h.created_at)}</td>
              <td>{h.item_count}</td>
              <td><button onClick={() => open(h.id)}>查看明细</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {detail && (
        <section>
          <h2>快照明细 #{detail.id}</h2>
          <p>交班人 {detail.created_by}，交班时间 {fmtTime(detail.created_at)}。快照只读，保存交班当时的待处理与领取中。</p>
          <table>
            <thead>
              <tr><th>编号</th><th>印张</th><th>机台</th><th>交班时状态</th></tr>
            </thead>
            <tbody>
              {detail.items.map((it) => (
                <tr key={it.job_id}>
                  <td>{it.job_id}</td>
                  <td>{it.sheet}</td>
                  <td>{it.machine}</td>
                  <td>{statusLabel(it.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </section>
  )
}
