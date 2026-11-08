import { Suspense, lazy, useEffect, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { ChevronLeft, ChevronRight, History, Layers, LogOut, MonitorCheck, Printer, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import Automation from "./pages/Automation.jsx";
import { MasterProvider } from "./context/MasterContext.jsx";
import RecentlyGenerated from "./pages/RecentlyGenerated.jsx";
import CreatePrintFile from "./pages/CreatePrintFile.jsx";
import Corrections from "./pages/Corrections.jsx";
import Masters from "./pages/Masters.jsx";
import AgentPage from "./pages/Agent.jsx";
import AdminPage from "./pages/Admin.jsx";
import AgentBanner from "./components/AgentBanner.jsx";
import { AgentProvider, useAgent } from "./context/AgentContext.jsx";
import { useAuth } from "./context/AuthContext.jsx";
import { loadEditorPage } from "./utils/prefetchEditor.js";

// The splash's title and button are light and render immediately; it lazy-loads its own 3D canvas (three.js, ~1 MB).
import SplashScreen from "./components/SplashScreen.jsx";
// The editor (canvas, panels, ops engine) is only needed in its own tab: out of the Automation bundle, prefetched on hover.
const EditorPage = lazy(loadEditorPage);

// Keys an earlier version used to skip the splash after the first visit. The splash now shows on EVERY load (refresh and
// hard reload included), so any leftover flag is cleared once at start-up rather than left behind.
const LEGACY_SPLASH_KEYS = ["signage.splashSeen", "splashSeen", "hasSeenSplash"];
function clearLegacySplashFlags() {
  try {
    LEGACY_SPLASH_KEYS.forEach((k) => {
      sessionStorage.removeItem(k);
      localStorage.removeItem(k);
    });
  } catch {
    /* storage blocked - nothing to clear */
  }
}

export default function App() {
  return (
    <Routes>
      {/* The editor opens in its own browser tab (window.open), so it's a
          top-level route with no sidebar shell around it - see the
          "Editor" button in the shops table. */}
      <Route path="/editor/:jobId/:shopId" element={<Suspense fallback={<div className="editor-route-fallback" />}><EditorPage /></Suspense>} />
      <Route path="/*" element={<Shell />} />
    </Routes>
  );
}

// the little status light next to "Agent" in the sidebar: green connected, amber waiting, red nothing on this computer
const DOT = { connected: "on", remote_online: "on", reconnecting: "warn", needs_pair: "warn", not_running: "bad", not_installed: "bad" };
function AgentDot() {
  const { phase } = useAgent();
  return <span className={"agent-nav-dot " + (DOT[phase] || "")} aria-hidden="true" />;
}

const NAV = [
  { to: "/", end: true, label: "Automation", Icon: Workflow },
  { to: "/masters", end: false, label: "Masters", Icon: Layers },
  { to: "/recent", end: false, label: "Recently generated", Icon: History },
  { to: "/print-file", end: false, label: "Create Print File", Icon: Printer },
  { to: "/corrections", end: false, label: "Learned corrections", Icon: ShieldCheck },
  { to: "/agent", end: false, label: "Agent", Icon: MonitorCheck, dot: true },
];

const COLLAPSE_KEY = "signage.sidebarCollapsed";
function readCollapsed() {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false; // private window / blocked storage: just start expanded
  }
}

// Full-viewport workspace: the sidebar is pinned (never scrolls) and only the main pane scrolls.
function Shell() {
  const { user, authEnabled, logout } = useAuth();
  const locked = authEnabled && !user; // signing in happens on the launch screen; until then nothing else loads (no API calls)
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [showSplash, setShowSplash] = useState(true); // always: no storage check, so it appears on every page load
  useEffect(clearLegacySplashFlags, []);
  const startAutomation = () => setShowSplash(false);
  const onOpenSplash = () => setShowSplash(true);   // the sidebar's "Welcome screen" button re-launches it without a refresh
  const toggle = () =>
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !c;
    });
  return (
    <div className="app-shell">
      <AnimatePresence>
        {showSplash && <SplashScreen key="splash" onStart={startAutomation} />}
      </AnimatePresence>
      {!locked && (
        <AgentProvider>
          <aside className={"app-sidebar" + (collapsed ? " collapsed" : "")} aria-label="Sidebar">
            <div className="sidebar-brand">
              <span className="dot" />
              <span className="sidebar-label">Signage Automation</span>
            </div>
            <nav>
              {NAV.map(({ to, end, label, Icon, dot }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  data-tip={label}
                  className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
                >
                  <Icon size={19} />
                  <span className="sidebar-label">{label}</span>
                  {dot && <AgentDot />}
                </NavLink>
              ))}
            </nav>
            {authEnabled && user && (
              <>
                <div className="sidebar-user sidebar-label" title={user.email}>{user.name || user.email} ({user.role})</div>
                <button className="sidebar-toggle" onClick={logout} data-tip="Sign out" aria-label="Sign out">
                  <LogOut size={18} />
                  <span className="sidebar-label">Sign out</span>
                </button>
              </>
            )}
            <button className="sidebar-toggle" onClick={onOpenSplash} data-tip="Welcome screen" aria-label="Show welcome screen">
              <Sparkles size={18} />
              <span className="sidebar-label">Welcome screen</span>
            </button>
            <button
              className="sidebar-toggle"
              onClick={toggle}
              data-tip={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed}
            >
              {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
              <span className="sidebar-label">Collapse</span>
            </button>
          </aside>
          <main className="app-main">
            {/* the master template registry, loaded once for the workspace (context/MasterContext.jsx) */}
            <MasterProvider>
              {/* Automation is never unmounted: leaving it for another page only hides it, so its queue, uploads and scroll are still
                  there when you come back (a route element would be rebuilt from scratch every visit). */}
              <AgentBanner />
            <Automation />
              <Routes>
                <Route path="/" element={null} />
                <Route path="/masters" element={<Masters />} />
                <Route path="/recent" element={<RecentlyGenerated />} />
                <Route path="/print-file" element={<CreatePrintFile />} />
                <Route path="/corrections" element={<Corrections />} />
                <Route path="/admin" element={<AdminPage />} />
                <Route path="/users" element={<Navigate to="/admin" replace />} />
                <Route path="/agent" element={<AgentPage />} />
                <Route path="/server" element={<Navigate to="/admin" replace />} />
              </Routes>
            </MasterProvider>
          </main>
        </AgentProvider>
      )}
    </div>
  );
}
