import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { Activity, Cloud, Cpu, HardDrive, Loader2, MemoryStick, Trash2, UserPlus, Users as UsersIcon } from "lucide-react";
import { useAuth } from "../context/AuthContext.jsx";
import { fmtBytes } from "../utils/fileSize.js";
import { readError } from "../utils/authApi.js";
import { DAY_CHOICES, ago, fmtUptime, presence, spacesPct, usageTotals } from "../utils/adminView.js";
import ServerPage from "./Server.jsx";
import "../components/Agent.css";
import "./Server.css";
import "./Admin.css";

const JSON_HEADERS = { "Content-Type": "application/json" };
const EMPTY_FORM = { email: "", name: "", password: "", role: "designer" };

function Meter({ icon: Icon, label, value, sub, pct, level }) {
  return (
    <div className={"adm-meter " + (level || "unknown")}>
      <div className="adm-meter-head"><Icon size={16} /> {label}</div>
      <div className="adm-meter-value">{value}</div>
      {pct != null && <div className="adm-bar"><span style={{ width: `${Math.min(100, Math.max(2, pct))}%` }} /></div>}
      <div className="srv-note">{sub}</div>
    </div>
  );
}

function Health() {
  const [h, setH] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      const r = await fetch("/api/admin/health").catch(() => null);
      if (r?.ok && alive) setH(await r.json());
    };
    load();
    const t = setInterval(load, 10000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!h) return <section className="ws-card srv-card"><Loader2 className="spin" size={18} /></section>;
  const lv = h.levels;
  return (
    <section className="ws-card srv-card">
      <h3><Activity size={17} /> Server health</h3>
      <div className="adm-meters">
        <Meter icon={Cpu} label="CPU" level={lv.cpu} pct={h.cpu_pct} value={h.cpu_pct == null ? "n/a" : `${h.cpu_pct.toFixed(0)}%`}
          sub={`${h.cpu_count} core${h.cpu_count === 1 ? "" : "s"}${h.load ? ` · load ${h.load.join(" / ")}` : ""}`} />
        <Meter icon={MemoryStick} label="Memory" level={lv.memory} pct={h.memory?.pct} value={h.memory ? `${h.memory.pct.toFixed(0)}%` : "n/a"}
          sub={h.memory ? `${fmtBytes(h.memory.used)} of ${fmtBytes(h.memory.total)}` : "psutil is not installed"} />
        <Meter icon={HardDrive} label="Disk" level={lv.disk} pct={h.disk.pct} value={`${h.disk.pct ?? "?"}%`} sub={`${fmtBytes(h.disk.free)} free of ${fmtBytes(h.disk.total)}`} />
      </div>
      <p className="srv-note">
        Up {fmtUptime(h.system_uptime_s)} (site running {fmtUptime(h.program_uptime_s)}) · the site itself uses {h.program_memory ? fmtBytes(h.program_memory) : "n/a"} of memory ·
        database {fmtBytes(h.database_bytes)} · {h.agents_online} of {h.agents_total} designer PC{h.agents_total === 1 ? "" : "s"} online ·
        jobs: {h.queue.running} running, {h.queue.queued} waiting, {h.queue.failed_24h} failed in the last day
      </p>
    </section>
  );
}

function SpacesCard() {
  const [s, setS] = useState(null);
  const load = useCallback(async (refresh = false) => {
    const r = await fetch(`/api/admin/spaces${refresh ? "?refresh=1" : ""}`);
    if (r.ok) setS(await r.json());
  }, []);
  useEffect(() => { load(); }, [load]);
  const pct = spacesPct(s);
  return (
    <section className="ws-card srv-card">
      <h3><Cloud size={17} /> Spaces storage</h3>
      {!s && <Loader2 className="spin" size={18} />}
      {s && !s.configured && <p className="srv-note">{s.message}</p>}
      {s?.configured && s.error && <p className="srv-problem">{s.error}</p>}
      {s?.configured && !s.error && (
        <>
          <div className="adm-meter-value">{fmtBytes(s.bytes)} <span className="srv-note">used in {s.bucket} ({s.region})</span></div>
          {pct != null && <div className="adm-bar"><span style={{ width: `${Math.max(2, pct)}%` }} /></div>}
          <p className="srv-note">{s.objects.toLocaleString()} files{pct != null ? ` · ${pct.toFixed(1)}% of ${fmtBytes(s.quota_bytes)}` : " · set SIGNAGE_SPACES_QUOTA_GB to see the percentage"} · counted {ago(s.checked_at)}</p>
          <div><button type="button" className="btn ghost small" onClick={() => load(true)}>Count again</button></div>
        </>
      )}
    </section>
  );
}

function Designers() {
  const { user: me } = useAuth();
  const [days, setDays] = useState(30);
  const [users, setUsers] = useState(null);
  const [msg, setMsg] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [confirming, setConfirming] = useState(null);   // user id waiting for "Yes, delete"

  const load = useCallback(async () => {
    const r = await fetch(`/api/admin/designers?days=${days}`);
    if (r.ok) setUsers((await r.json()).users);
    else setMsg({ kind: "err", text: await readError(r, "Could not load the designers") });
  }, [days]);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const call = async (url, method, body, okText) => {
    setMsg(null);
    const r = await fetch(url, { method, headers: JSON_HEADERS, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) { setMsg({ kind: "err", text: await readError(r, "That did not work") }); return false; }
    setMsg({ kind: "ok", text: okText });
    await load();
    return true;
  };
  const add = async (e) => {
    e.preventDefault();
    if (await call("/api/users", "POST", form, `Account created for ${form.email}`)) setForm(EMPTY_FORM);
  };
  const resetPassword = (u) => {
    const password = window.prompt(`New password for ${u.email} (at least 8 characters). They are signed out at once.`);
    if (password) call(`/api/users/${u.id}`, "PATCH", { password }, `Password changed for ${u.email}`);
  };
  const remove = async (u) => {
    setConfirming(null);
    await call(`/api/users/${u.id}`, "DELETE", null, `Deleted ${u.email}`);
  };

  const totals = usageTotals(users || []);
  return (
    <section className="ws-card srv-card">
      <h3><UsersIcon size={17} /> Designers and their usage</h3>
      <div className="srv-filters">
        <label className="srv-note">Usage over the last
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Usage period">
            {DAY_CHOICES.map((d) => <option key={d} value={d}>{d} days</option>)}
          </select>
        </label>
      </div>
      {msg && <p className={"users-msg " + msg.kind} role="status">{msg.text}</p>}
      {!users ? <Loader2 className="spin" size={18} /> : (
        <div className="adm-scroll">
          <table className="srv-table adm-users">
            <thead>
              <tr><th>Person</th><th>Now</th><th>Computers</th><th>Last active</th><th className="num">Sign-ins</th><th className="num">Boards</th><th className="num">Exports</th><th className="num">Failed</th><th /></tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const p = presence(u);
                const mine = u.id === me?.id;
                return (
                  <tr key={u.id} className={u.active ? "" : "off"} data-email={u.email}>
                    <td>
                      <b>{u.name || u.email}</b>{mine ? " (you)" : ""}
                      <div className="srv-note">{u.email}
                        <select value={u.role} disabled={mine} aria-label={`Role of ${u.email}`}
                          onChange={(e) => call(`/api/users/${u.id}`, "PATCH", { role: e.target.value }, `${u.email} is now ${e.target.value}`)}>
                          <option value="designer">designer</option><option value="admin">admin</option>
                        </select>
                      </div>
                    </td>
                    <td><span className={"adm-dot " + p.key} />{u.active ? p.label : "Disabled"}</td>
                    <td>{u.pcs.length ? u.pcs.map((pc) => <div key={pc.name} className="srv-note"><span className={"adm-dot " + (pc.online ? "working" : "away")} />{pc.name}{pc.corel?.length ? ` · Corel ${pc.corel[0]}` : ""}</div>) : <span className="srv-note">none</span>}</td>
                    <td>{ago(u.last_active)}</td>
                    <td className="num">{u.logins}</td>
                    <td className="num">{u.converted}</td>
                    <td className="num">{u.exported}</td>
                    <td className={"num" + (u.failed ? " bad" : "")}>{u.failed}</td>
                    <td className="adm-actions">
                      <button type="button" className="btn ghost small" onClick={() => resetPassword(u)}>Reset password</button>
                      {!mine && <button type="button" className="btn ghost small" onClick={() => call(`/api/users/${u.id}`, "PATCH", { active: !u.active }, `${u.email} ${u.active ? "disabled" : "enabled"}`)}>{u.active ? "Disable" : "Enable"}</button>}
                      {!mine && (confirming === u.id ? (
                        <span className="adm-confirm">Delete for good?
                          <button type="button" className="btn small danger" onClick={() => remove(u)}>Yes, delete</button>
                          <button type="button" className="btn ghost small" onClick={() => setConfirming(null)}>No</button>
                        </span>
                      ) : <button type="button" className="btn ghost small" aria-label={`Delete ${u.email}`} onClick={() => setConfirming(u.id)}><Trash2 size={14} /> Delete</button>)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr><td colSpan="4">All people</td><td className="num">{totals.logins}</td><td className="num">{totals.converted}</td><td className="num">{totals.exported}</td><td className="num">{totals.failed}</td><td /></tr>
            </tfoot>
          </table>
        </div>
      )}

      <form className="adm-form" onSubmit={add}>
        <h4><UserPlus size={15} /> Create a user</h4>
        <label>Email<input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
        <label>Name<input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Password<input type="password" required minLength={8} autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
        <label>Role
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="designer">designer</option><option value="admin">admin</option>
          </select>
        </label>
        <button className="btn" type="submit">Create user</button>
      </form>
    </section>
  );
}

/** /admin - typed into the address bar, not linked from the sidebar. Administrators only; anyone else is sent back to the home page. */
export default function AdminPage() {
  const { isAdmin, authEnabled, user } = useAuth();
  if (authEnabled && !user) return null;
  if (authEnabled && !isAdmin) return <Navigate to="/" replace />;
  return (
    <div className="srv-page adm-page">
      <h2>Admin</h2>
      <Health />
      <Designers />
      <SpacesCard />
      <ServerPage embedded />
    </div>
  );
}
