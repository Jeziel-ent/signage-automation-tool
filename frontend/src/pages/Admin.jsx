import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import {
  Activity, AlertTriangle, CheckCircle2, CircleSlash, Clock, Cpu, Database, FolderOpen, HardDrive, KeyRound, LayoutDashboard, Loader2, LogOut,
  MemoryStick, MonitorCheck, Power, ScrollText, Trash2, UserPlus, X, XCircle,
} from "lucide-react";
import { useAuth } from "../context/AuthContext.jsx";
import GlassLogin from "../components/GlassLogin.jsx";
import AdminFiles from "../components/AdminFiles.jsx";
import { fmtBytes } from "../utils/fileSize.js";
import { readError } from "../utils/authApi.js";
import {
  DAY_CHOICES, JOB_KINDS, ago, dayLabel, fmtSeconds, fmtUptime, jobResult, niceMax, presence, seriesTotals, sparkPath, usageTotals,
} from "../utils/adminView.js";
import { CLEAN_LABELS, describeEntry, entryDetails, storageRows } from "../utils/serverView.js";
import "./Admin.css";

const JSON_HEADERS = { "Content-Type": "application/json" };
const EMPTY_FORM = { email: "", name: "", password: "", role: "designer" };
const HISTORY = 60;                    // CPU / memory readings kept for the sparklines (one every 5 s = the last 5 minutes)
// the throughput chart's series (validated for the dark surface: blue / amber / red, CVD-separated)
const SERIES = [
  { key: "converted", label: "Converted", color: "var(--ad-s1)" },
  { key: "stopped", label: "Stopped", color: "var(--ad-s2)" },
  { key: "failed", label: "Failed", color: "var(--ad-s3)" },
];

/** Fetch `url` now and every `ms`; {data, at, error, reload}. */
function usePoll(url, ms) {
  const [state, setState] = useState({ data: null, at: null, error: null });
  const load = useCallback(async () => {
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(await readError(r, `${url} answered ${r.status}`));
      const data = await r.json();
      setState({ data, at: Date.now(), error: null });
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    }
  }, [url]);
  useEffect(() => {
    load();
    if (!ms) return undefined;
    const t = setInterval(load, ms);
    return () => clearInterval(t);
  }, [load, ms]);
  return { ...state, reload: load };
}

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

/** /admin: a full-screen monitor for administrators (no sidebar). Anyone signed out gets the sign-in card; a designer goes home. */
export default function AdminRoute() {
  const { user, authEnabled, isAdmin, needsSetup, login } = useAuth();
  if (authEnabled && !user) {
    return (
      <div className="adash adash-login">
        <div className="adash-login-box">
          <div className="adash-brand"><span className="dot" /> Signage Automation <span className="adash-muted">· Admin monitor</span></div>
          <GlassLogin onLogin={login} needsSetup={needsSetup} />
        </div>
      </div>
    );
  }
  if (authEnabled && !isAdmin) return <Navigate to="/" replace />;
  return <AdminDashboard />;
}

function AdminDashboard() {
  const { user, authEnabled, logout } = useAuth();
  const now = useNow();
  const health = usePoll("/api/admin/health", 5000);
  const overview = usePoll("/api/admin/overview", 5000);
  const status = usePoll("/api/admin/status", 15000);
  const [cpuHist, setCpuHist] = useState([]);
  const [memHist, setMemHist] = useState([]);
  const lastAt = useRef(null);
  useEffect(() => {            // one sparkline point per real reading, never interpolated
    const h = health.data;
    if (!h || health.at === lastAt.current) return;
    lastAt.current = health.at;
    setCpuHist((v) => [...v, h.cpu_pct].slice(-HISTORY));
    setMemHist((v) => [...v, h.memory?.pct ?? null].slice(-HISTORY));
  }, [health.data, health.at]);

  const updated = Math.max(health.at || 0, overview.at || 0);
  const stale = updated && now - updated > 20000;
  const problems = status.data?.problems || [];

  return (
    <div className="adash">
      <header className="adash-top">
        <div className="adash-brand"><span className="dot" /> Signage Automation <span className="adash-muted">· Admin monitor</span></div>
        <div className="adash-top-right">
          <span className={"adash-pill " + (problems.length ? "bad" : status.data ? "good" : "")} title={problems.map((p) => p.title).join(" · ")}>
            {problems.length ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}
            {status.data ? (problems.length ? `${problems.length} problem${problems.length === 1 ? "" : "s"}` : "All systems normal") : "Checking..."}
          </span>
          <span className={"adash-pill " + (stale || health.error ? "bad" : "live")}>
            <span className="adash-live-dot" />
            {health.error ? "Server not answering" : updated ? `Live · updated ${Math.max(0, Math.round((now - updated) / 1000))} s ago` : "Connecting..."}
          </span>
          <Link className="adash-btn ghost" to="/"><LayoutDashboard size={15} /> Workspace</Link>
          {authEnabled && user && (
            <>
              <span className="adash-muted adash-user" title={user.email}>{user.email}</span>
              <button type="button" className="adash-btn ghost" onClick={logout}><LogOut size={15} /> Sign out</button>
            </>
          )}
        </div>
      </header>

      <Kpis h={health.data} o={overview.data} cpuHist={cpuHist} memHist={memHist} />

      <main className="adash-grid">
        <Throughput o={overview.data} />
        <Queue o={overview.data} now={now} />
        <People />
        <SideTabs status={status} problems={problems} />
      </main>
    </div>
  );
}

// ------------------------------------------------------------------ top tiles

function Spark({ values, label }) {
  const d = sparkPath(values, 120, 28);
  return d ? (
    <svg className="adash-spark" viewBox="0 0 120 28" preserveAspectRatio="none" role="img" aria-label={`${label}, last ${values.length} readings`}>
      <path d={d} />
    </svg>
  ) : <div className="adash-spark-empty adash-muted">collecting readings...</div>;
}

function Tile({ icon: Icon, label, value, sub, level, children }) {
  return (
    <div className={"adash-tile " + (level || "")}>
      <div className="adash-tile-head"><Icon size={15} /> {label}{level && level !== "ok" && level !== "unknown" && <span className={"adash-lvl " + level}>{level === "bad" ? "High" : "Rising"}</span>}</div>
      <div className="adash-tile-value">{value}</div>
      {children}
      <div className="adash-tile-sub">{sub}</div>
    </div>
  );
}

function Kpis({ h, o, cpuHist, memHist }) {
  if (!h) return <section className="adash-kpis"><div className="adash-tile"><Loader2 className="spin" size={18} /></div></section>;
  const lv = h.levels || {};
  const totals = seriesTotals(o?.series);
  const today = o?.series?.[o.series.length - 1];
  return (
    <section className="adash-kpis" aria-label="Server and work at a glance">
      <Tile icon={Cpu} label="CPU" level={lv.cpu} value={h.cpu_pct == null ? "n/a" : `${h.cpu_pct.toFixed(0)}%`}
        sub={`${h.cpu_count} core${h.cpu_count === 1 ? "" : "s"}${h.load ? ` · load ${h.load.join(" / ")}` : ""}`}>
        <Spark values={cpuHist} label="CPU" />
      </Tile>
      <Tile icon={MemoryStick} label="Memory" level={lv.memory} value={h.memory ? `${h.memory.pct.toFixed(0)}%` : "n/a"}
        sub={h.memory ? `${fmtBytes(h.memory.used)} of ${fmtBytes(h.memory.total)}` : "psutil is not installed"}>
        <Spark values={memHist} label="Memory" />
      </Tile>
      <Tile icon={HardDrive} label="Disk" level={lv.disk} value={`${h.disk.pct ?? "?"}%`} sub={`${fmtBytes(h.disk.free)} free of ${fmtBytes(h.disk.total)}`}>
        <div className="adash-bar"><span style={{ width: `${Math.min(100, Math.max(1, h.disk.pct || 0))}%` }} /></div>
      </Tile>
      <Tile icon={MonitorCheck} label="Designer PCs" level={h.agents_total && !h.agents_online ? "warn" : "ok"}
        value={<>{h.agents_online}<span className="adash-of"> / {h.agents_total}</span></>} sub={h.agents_total ? "online now · run the CorelDRAW work" : "no PC connected yet"} />
      <Tile icon={Activity} label="Jobs now" level={h.queue.queued && !h.agents_online ? "warn" : "ok"}
        value={<>{h.queue.running}<span className="adash-of"> running</span></>}
        sub={`${h.queue.queued} waiting · ${h.queue.failed_24h} failed in 24 h`} />
      <Tile icon={Clock} label="Boards, 14 days" value={o ? totals.converted : "..."}
        sub={o ? `today ${today?.converted ?? 0} · median ${fmtSeconds(o.median_convert_s)} per board` : ""} />
      <Tile icon={Database} label="Site uptime" value={fmtUptime(h.program_uptime_s)}
        sub={`server up ${fmtUptime(h.system_uptime_s)} · site uses ${h.program_memory ? fmtBytes(h.program_memory) : "n/a"} · database ${fmtBytes(h.database_bytes)}`} />
    </section>
  );
}

// ------------------------------------------------------------------ throughput chart

function Throughput({ o }) {
  const [hover, setHover] = useState(null);
  const series = o?.series || [];
  const max = niceMax(Math.max(0, ...series.map((d) => d.converted + d.stopped + d.failed)));
  const W = 560, H = 190, L = 28, B = 22, T = 8;
  const plotW = W - L - 4, plotH = H - B - T;
  const slot = series.length ? plotW / series.length : 0;
  const barW = Math.max(4, Math.min(26, slot * 0.62));
  const y = (v) => T + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  const totals = seriesTotals(series);
  const hovered = hover != null ? series[hover] : null;

  return (
    <section className="adash-panel a-chart" aria-label="Boards per day">
      <div className="adash-panel-head">
        <h3><Activity size={16} /> Boards per day <span className="adash-muted">last 14 days (UTC)</span></h3>
        <div className="adash-legend" aria-label="Legend">
          {SERIES.map((s) => <span key={s.key}><i style={{ background: s.color }} /> {s.label} <b>{totals[s.key]}</b></span>)}
          <span className="adash-muted">Exports <b>{totals.exported}</b></span>
        </div>
      </div>
      {!o ? <Loader2 className="spin" size={18} /> : (
        <div className="adash-chart-wrap">
          <svg viewBox={`0 0 ${W} ${H}`} className="adash-chart" role="img"
            aria-label={`Boards converted per day: ${totals.converted} converted, ${totals.stopped} stopped, ${totals.failed} failed in 14 days`}
            onMouseLeave={() => setHover(null)}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={L} x2={W - 4} y1={y(t)} y2={y(t)} className="adash-grid-line" />
                <text x={L - 6} y={y(t) + 3} className="adash-axis" textAnchor="end">{t}</text>
              </g>
            ))}
            {series.map((d, i) => {
              const x = L + i * slot + (slot - barW) / 2;
              let acc = 0;
              const segs = SERIES.map((s) => {
                const v = d[s.key];
                if (!v) return null;
                const y1 = y(acc + v), y0 = y(acc);
                acc += v;
                return <rect key={s.key} x={x} y={y1} width={barW} height={Math.max(1, y0 - y1 - (acc > v ? 2 : 0))} rx="2" fill={s.color} />;
              });
              return (
                <g key={d.day} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
                  aria-label={`${dayLabel(d.day)}: ${d.converted} converted, ${d.stopped} stopped, ${d.failed} failed, ${d.exported} exports`}>
                  <rect x={L + i * slot} y={T} width={slot} height={plotH} className={"adash-hit" + (hover === i ? " on" : "")} />
                  {segs}
                  {(i % 2 === series.length % 2 || i === series.length - 1) && (
                    <text x={L + i * slot + slot / 2} y={H - 6} className="adash-axis" textAnchor="middle">{dayLabel(d.day)}</text>
                  )}
                </g>
              );
            })}
          </svg>
          {hovered && (
            <div className={"adash-tip" + (hover > series.length * 0.7 ? " end" : hover < series.length * 0.3 ? " start" : "")}
              style={{ left: `${((L + hover * slot + slot / 2) / W) * 100}%` }} role="status">
              <b>{dayLabel(hovered.day)}</b>
              {SERIES.map((s) => <div key={s.key}><i style={{ background: s.color }} />{s.label}<span>{hovered[s.key]}</span></div>)}
              <div className="adash-muted">Exports<span>{hovered.exported}</span></div>
            </div>
          )}
          {totals.converted + totals.stopped + totals.failed === 0 && <p className="adash-empty">No boards converted in the last 14 days.</p>}
        </div>
      )}
      {o && (
        <div className="adash-counts">
          <span><b>{o.counts.masters}</b> masters</span>
          <span><b>{o.counts.brands}</b> brands</span>
          <span><b>{o.counts.users}</b> active accounts</span>
          <span><b>{o.counts.shops}</b> shops in the queue</span>
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ live queue + recent jobs

function ResultBadge({ job }) {
  const r = jobResult(job);
  const Icon = r.key === "good" ? CheckCircle2 : r.key === "warn" ? CircleSlash : XCircle;
  return <span className={"adash-badge " + r.key} title={job.error || ""}><Icon size={13} /> {r.label}</span>;
}

function Queue({ o, now }) {
  const live = o?.live || [];
  const recent = o?.recent || [];
  return (
    <section className="adash-panel a-queue" aria-label="Jobs">
      <div className="adash-panel-head"><h3><MonitorCheck size={16} /> Running and waiting <span className="adash-count">{live.length}</span></h3></div>
      <div className="adash-scroll adash-live">
        {!o ? <Loader2 className="spin" size={18} /> : !live.length ? <p className="adash-empty">Nothing is running or waiting.</p> : (
          live.map((j) => (
            <div key={j.id} className={"adash-job " + (j.state === "queued" ? "waiting" : "running")}>
              <span className="adash-job-state">{j.state === "queued" ? <><Clock size={13} /> Waiting</> : <><Loader2 size={13} className="spin" /> {j.step || "Running"}</>}</span>
              <span className="adash-job-name">{j.shop || JOB_KINDS[j.kind] || j.kind}<span className="adash-muted"> · {JOB_KINDS[j.kind] || j.kind}</span></span>
              <span className="adash-muted">{j.pc || "no PC yet"} · {j.user_email || "?"}</span>
              <span className="adash-muted adash-job-age">{ago(j.created_at, now / 1000)}</span>
            </div>
          ))
        )}
      </div>
      <div className="adash-panel-head sub"><h3><Clock size={16} /> Recently finished</h3></div>
      <div className="adash-scroll">
        {o && !recent.length ? <p className="adash-empty">No finished jobs yet.</p> : (
          <table className="adash-table">
            <thead><tr><th>Job</th><th>Result</th><th className="num">Took</th><th>PC</th><th className="num">When</th></tr></thead>
            <tbody>
              {recent.map((j) => (
                <tr key={j.id}>
                  <td><b>{j.shop || "-"}</b> <span className="adash-muted">{JOB_KINDS[j.kind] || j.kind}</span></td>
                  <td><ResultBadge job={j} /></td>
                  <td className="num">{fmtSeconds(j.seconds)}</td>
                  <td className="adash-muted">{j.pc || "-"}</td>
                  <td className="num adash-muted">{ago(j.finished_at || j.created_at, now / 1000)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ people

function People() {
  const { user: me } = useAuth();
  const [days, setDays] = useState(30);
  const users = usePoll(`/api/admin/designers?days=${days}`, 15000);
  const [msg, setMsg] = useState(null);
  const [form, setForm] = useState(null);                // null = closed
  const [confirming, setConfirming] = useState(null);

  const call = async (url, method, body, okText) => {
    setMsg(null);
    const r = await fetch(url, { method, headers: JSON_HEADERS, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) { setMsg({ kind: "bad", text: await readError(r, "That did not work") }); return false; }
    setMsg({ kind: "good", text: okText });
    await users.reload();
    return true;
  };
  const add = async (e) => {
    e.preventDefault();
    if (await call("/api/users", "POST", form, `Account created for ${form.email}`)) setForm(null);
  };
  const resetPassword = (u) => {
    const password = window.prompt(`New password for ${u.email} (at least 8 characters). They are signed out at once.`);
    if (password) call(`/api/users/${u.id}`, "PATCH", { password }, `Password changed for ${u.email}`);
  };

  const list = users.data?.users;
  const totals = usageTotals(list || []);
  return (
    <section className="adash-panel a-people" aria-label="Designers">
      <div className="adash-panel-head">
        <h3><UserPlus size={16} /> Designers <span className="adash-count">{list?.length ?? ""}</span></h3>
        <div className="adash-head-tools">
          <label className="adash-muted">Usage over
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Usage period">
              {DAY_CHOICES.map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </label>
          <button type="button" className="adash-btn" onClick={() => setForm(form ? null : EMPTY_FORM)}>
            {form ? <><X size={15} /> Close</> : <><UserPlus size={15} /> Add user</>}
          </button>
        </div>
      </div>
      {form && (
        <form className="adash-form" onSubmit={add}>
          <input type="email" required placeholder="Email" aria-label="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input type="text" placeholder="Name" aria-label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input type="password" required minLength={8} placeholder="Password (8+)" aria-label="Password" autoComplete="new-password"
            value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <select value={form.role} aria-label="Role" onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="designer">designer</option><option value="admin">admin</option>
          </select>
          <button className="adash-btn primary" type="submit">Create</button>
        </form>
      )}
      {msg && <p className={"adash-msg " + msg.kind} role="status">{msg.text}</p>}
      <div className="adash-scroll">
        {!list ? <Loader2 className="spin" size={18} /> : (
          <table className="adash-table">
            <thead>
              <tr><th>Person</th><th>Now</th><th>PC</th><th>Last active</th><th className="num">Sign-ins</th><th className="num">Boards</th><th className="num">Exports</th><th className="num">Failed</th><th /></tr>
            </thead>
            <tbody>
              {list.map((u) => {
                const p = presence(u);
                const mine = u.id === me?.id;
                return (
                  <tr key={u.id} className={u.active ? "" : "off"}>
                    <td>
                      <b>{u.name || u.email}</b>{mine ? <span className="adash-muted"> (you)</span> : ""}
                      <div className="adash-muted adash-email">{u.email}
                        <select value={u.role} disabled={mine} aria-label={`Role of ${u.email}`}
                          onChange={(e) => call(`/api/users/${u.id}`, "PATCH", { role: e.target.value }, `${u.email} is now ${e.target.value}`)}>
                          <option value="designer">designer</option><option value="admin">admin</option>
                        </select>
                      </div>
                    </td>
                    <td><span className={"adash-dot " + p.key} />{u.active ? p.label : "Disabled"}</td>
                    <td>{u.pcs.length ? u.pcs.map((pc) => (
                      <div key={pc.name} className="adash-muted"><span className={"adash-dot " + (pc.online ? "working" : "away")} />{pc.name}{pc.corel?.length ? ` · Corel ${pc.corel[0]}` : ""}{pc.version ? ` · v${pc.version}` : ""}</div>
                    )) : <span className="adash-muted">none</span>}</td>
                    <td className="adash-muted">{ago(u.last_active)}</td>
                    <td className="num">{u.logins}</td>
                    <td className="num">{u.converted}</td>
                    <td className="num">{u.exported}</td>
                    <td className={"num" + (u.failed ? " bad" : "")}>{u.failed}</td>
                    <td className="adash-actions">
                      {confirming === u.id ? (
                        <span className="adash-confirm">Delete?
                          <button type="button" className="adash-btn danger" onClick={() => { setConfirming(null); call(`/api/users/${u.id}`, "DELETE", null, `Deleted ${u.email}`); }}>Yes</button>
                          <button type="button" className="adash-btn ghost" onClick={() => setConfirming(null)}>No</button>
                        </span>
                      ) : (
                        <>
                          <button type="button" className="adash-icon" title="Reset password" aria-label={`Reset password of ${u.email}`} onClick={() => resetPassword(u)}><KeyRound size={15} /></button>
                          {!mine && <button type="button" className="adash-icon" title={u.active ? "Disable" : "Enable"} aria-label={`${u.active ? "Disable" : "Enable"} ${u.email}`}
                            onClick={() => call(`/api/users/${u.id}`, "PATCH", { active: !u.active }, `${u.email} ${u.active ? "disabled" : "enabled"}`)}><Power size={15} /></button>}
                          {!mine && <button type="button" className="adash-icon danger" title="Delete" aria-label={`Delete ${u.email}`} onClick={() => setConfirming(u.id)}><Trash2 size={15} /></button>}
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr><td colSpan="4">All people</td><td className="num">{totals.logins}</td><td className="num">{totals.converted}</td><td className="num">{totals.exported}</td><td className="num">{totals.failed}</td><td /></tr>
            </tfoot>
          </table>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ activity / storage / alerts

function SideTabs({ status, problems }) {
  const [tab, setTab] = useState("activity");
  const tabs = [
    { key: "activity", label: "Activity", Icon: ScrollText },
    { key: "storage", label: "Storage", Icon: HardDrive },
    { key: "alerts", label: problems.length ? `Alerts (${problems.length})` : "Alerts", Icon: AlertTriangle },
  ];
  return (
    <section className="adash-panel a-side">
      <div className="adash-tabs" role="tablist">
        {tabs.map(({ key, label, Icon }) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>
      {tab === "activity" && <ActivityLog />}
      {tab === "storage" && <Storage />}
      {tab === "alerts" && <Alerts status={status} problems={problems} />}
    </section>
  );
}

const fmtTime = (t) => new Date(t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

function ActivityLog() {
  const [action, setAction] = useState("");
  const [q, setQ] = useState("");
  const url = `/api/admin/audit?limit=60&offset=0&action=${encodeURIComponent(action)}&q=${encodeURIComponent(q)}`;
  const log = usePoll(url, 15000);
  const data = log.data || { entries: [], total: 0, actions: [] };
  return (
    <>
      <div className="adash-filters">
        <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter by action">
          <option value="">All actions</option>
          {data.actions.map((a) => <option key={a} value={a}>{a.replaceAll("_", " ")}</option>)}
        </select>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search person, file, address..." aria-label="Search the log" />
      </div>
      <div className="adash-scroll">
        {!log.data ? <Loader2 className="spin" size={18} /> : !data.entries.length ? <p className="adash-empty">Nothing logged yet.</p> : (
          <ul className="adash-log">
            {data.entries.map((e) => (
              <li key={e.id}>
                <span className="adash-muted adash-log-when">{fmtTime(e.at)}</span>
                <span>{describeEntry(e)}{entryDetails(e) && <span className="adash-muted"> · {entryDetails(e)}</span>}</span>
                <span className="adash-muted adash-log-ip">{e.ip}</span>
              </li>
            ))}
          </ul>
        )}
        {data.total > data.entries.length && <p className="adash-muted adash-note">Showing the newest {data.entries.length} of {data.total}.</p>}
      </div>
    </>
  );
}

function Storage() {
  const storage = usePoll("/api/admin/storage", 60000);
  const [days, setDays] = useState("0");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [browsing, setBrowsing] = useState(false);
  const s = storage.data;
  const rows = useMemo(() => storageRows(s?.bytes, s?.total_bytes), [s]);

  async function clean(dryRun) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fetch("/api/admin/cleanup", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ days: Number(days), dry_run: dryRun }) });
      if (!r.ok) throw new Error(await readError(r, "The clean-up failed"));
      const res = await r.json();
      if (dryRun) setPreview(res);
      else {
        setPreview(null);
        setMsg({ kind: "good", text: `Removed ${res.deleted} item(s), freed ${fmtBytes(res.bytes)}.` });
        storage.reload();
      }
    } catch (e) {
      setMsg({ kind: "bad", text: e.message });
    } finally {
      setBusy(false);
    }
  }

  if (!s) return <div className="adash-scroll"><Loader2 className="spin" size={18} /></div>;
  return (
    <div className="adash-scroll">
      <div className="adash-storage-head">
        <p className="adash-note"><b>{fmtBytes(s.total_bytes)}</b> used by the site · <b>{fmtBytes(s.free_bytes)}</b> free on the disk</p>
        <button type="button" className="adash-btn" onClick={() => setBrowsing(true)}><FolderOpen size={15} /> Browse files</button>
      </div>
      {browsing && <AdminFiles onClose={() => setBrowsing(false)} onChanged={storage.reload} />}
      {rows.length ? (
        <table className="adash-table">
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td>{r.label}</td>
                <td className="num">{fmtBytes(r.bytes)}</td>
                <td className="adash-share"><span style={{ width: `${Math.max(2, r.share * 100)}%` }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="adash-empty">Nothing stored yet.</p>}
      <div className="adash-clean">
        <label className="adash-muted">Also remove exports and editor scenes older than
          <select value={days} onChange={(e) => { setDays(e.target.value); setPreview(null); }}>
            <option value="0">never (only temporary leftovers)</option>
            <option value="30">30 days</option><option value="60">60 days</option><option value="90">90 days</option><option value="180">180 days</option>
          </select>
        </label>
        <button type="button" className="adash-btn ghost" onClick={() => clean(true)} disabled={busy}>Check what would go</button>
        {preview && (
          <>
            <span className="adash-muted">
              {preview.would_delete ? `${preview.would_delete} item(s), ${fmtBytes(preview.bytes)}: ${Object.entries(preview.by_category).map(([k, v]) => `${CLEAN_LABELS[k] || k} ${fmtBytes(v.bytes)}`).join(", ")}` : "Nothing to remove."}
            </span>
            {preview.would_delete > 0 && <button type="button" className="adash-btn danger" onClick={() => clean(false)} disabled={busy}><Trash2 size={14} /> Remove them</button>}
          </>
        )}
      </div>
      <p className="adash-muted adash-note">Masters, converted boards and the database are never removed here.{s.retention_days ? ` Automatic clean-up every 6 hours: ${s.retention_days} days.` : " Automatic clean-up of old exports and scenes is off (SIGNAGE_RETENTION_DAYS)."}</p>
      {msg && <p className={"adash-msg " + msg.kind} role="status">{msg.text}</p>}
    </div>
  );
}

function Alerts({ status, problems }) {
  const spaces = usePoll("/api/admin/spaces", 0);
  const sp = spaces.data;
  return (
    <div className="adash-scroll">
      {!status.data ? <Loader2 className="spin" size={18} /> : problems.length ? problems.map((p) => (
        <div key={p.key} className="adash-alert bad"><AlertTriangle size={15} /><div><b>{p.title}.</b> {p.detail}</div></div>
      )) : <div className="adash-alert good"><CheckCircle2 size={15} /><div><b>Everything looks fine.</b> No problem is reported right now.</div></div>}
      {status.data && !status.data.webhook_configured && <p className="adash-muted adash-note">Chat alerts are off: set SIGNAGE_ALERT_WEBHOOK to be told when something goes wrong.</p>}
      <h4 className="adash-subhead">Spaces storage</h4>
      {!sp ? <Loader2 className="spin" size={16} /> : !sp.configured ? <p className="adash-muted adash-note">{sp.message}</p>
        : sp.error ? <p className="adash-msg bad">{sp.error}</p> : (
          <p className="adash-note"><b>{fmtBytes(sp.bytes)}</b> in {sp.bucket} ({sp.region}) · {sp.objects.toLocaleString()} files · counted {ago(sp.checked_at)}</p>
        )}
    </div>
  );
}
