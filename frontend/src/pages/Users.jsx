import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext.jsx";
import { readError } from "../utils/authApi.js";
import "../components/AuthGate.css";

const JSON_HEADERS = { "Content-Type": "application/json" };

/** Administrator page: add accounts, change role, reset a password, disable. */
export default function Users() {
  const { user: me, isAdmin } = useAuth();
  const [users, setUsers] = useState([]);
  const [msg, setMsg] = useState(null);   // {kind: "ok" | "err", text}
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "designer" });

  const load = useCallback(async () => {
    const r = await fetch("/api/users");
    if (r.ok) setUsers((await r.json()).users);
    else setMsg({ kind: "err", text: await readError(r, "Could not load the users") });
  }, []);
  useEffect(() => { if (isAdmin) load(); }, [isAdmin, load]);

  if (!isAdmin) return <div className="users-page"><h2>Users</h2><p>Only an administrator can manage accounts.</p></div>;

  const call = async (url, method, body, okText) => {
    setMsg(null);
    const r = await fetch(url, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });
    if (!r.ok) return setMsg({ kind: "err", text: await readError(r, "That did not work") });
    setMsg({ kind: "ok", text: okText });
    await load();
    return true;
  };

  const add = async (e) => {
    e.preventDefault();
    if (await call("/api/users", "POST", form, `Account created for ${form.email}`)) {
      setForm({ email: "", name: "", password: "", role: "designer" });
    }
  };
  const resetPassword = (u) => {
    const password = window.prompt(`New password for ${u.email} (at least 8 characters). They are signed out at once.`);
    if (password) call(`/api/users/${u.id}`, "PATCH", { password }, `Password changed for ${u.email}`);
  };

  return (
    <div className="users-page">
      <h2>Users</h2>
      <p>Administrators manage masters, corrections and accounts; designers convert, edit and export.</p>
      {msg && <div className={"users-msg " + msg.kind} role="status">{msg.text}</div>}
      <table className="users-table">
        <thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th /></tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className={u.active ? "" : "off"}>
              <td>{u.email}{u.id === me?.id ? " (you)" : ""}</td>
              <td>{u.name}</td>
              <td>
                <select value={u.role} aria-label={`Role of ${u.email}`}
                  onChange={(e) => call(`/api/users/${u.id}`, "PATCH", { role: e.target.value }, `${u.email} is now ${e.target.value}`)}>
                  <option value="designer">designer</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td>{u.active ? "active" : "disabled"}</td>
              <td>
                <button className="btn ghost small" onClick={() => resetPassword(u)}>Reset password</button>{" "}
                <button className="btn ghost small" onClick={() => call(`/api/users/${u.id}`, "PATCH", { active: !u.active }, `${u.email} ${u.active ? "disabled" : "enabled"}`)}>
                  {u.active ? "Disable" : "Enable"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="users-form" onSubmit={add}>
        <label>Email<input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
        <label>Name<input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Password<input type="password" required minLength={8} autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
        <label>Role
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="designer">designer</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button className="btn" type="submit">Add user</button>
      </form>
    </div>
  );
}
