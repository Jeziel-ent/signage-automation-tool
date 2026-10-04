import { Suspense, lazy, useEffect, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { NavLink, Route, Routes } from "react-router-dom";
import { ChevronLeft, ChevronRight, History, Sparkles, Workflow } from "lucide-react";
import Automation from "./pages/Automation.jsx";
import { MasterProvider } from "./context/MasterContext.jsx";
import RecentlyGenerated from "./pages/RecentlyGenerated.jsx";
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

const NAV = [
  { to: "/", end: true, label: "Automation", Icon: Workflow },
  { to: "/recent", end: false, label: "Recently generated", Icon: History },
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
      <aside className={"app-sidebar" + (collapsed ? " collapsed" : "")} aria-label="Sidebar">
        <div className="sidebar-brand">
          <span className="dot" />
          <span className="sidebar-label">Signage Automation</span>
        </div>
        <nav>
          {NAV.map(({ to, end, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              data-tip={label}
              className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
            >
              <Icon size={19} />
              <span className="sidebar-label">{label}</span>
            </NavLink>
          ))}
        </nav>
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
          <Routes>
            <Route path="/" element={<Automation />} />
            <Route path="/recent" element={<RecentlyGenerated />} />
          </Routes>
        </MasterProvider>
      </main>
    </div>
  );
}
