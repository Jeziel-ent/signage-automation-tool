import { NavLink, Route, Routes } from "react-router-dom";
import Automation from "./pages/Automation.jsx";
import RecentlyGenerated from "./pages/RecentlyGenerated.jsx";
import EditorPage from "./pages/EditorPage.jsx";

export default function App() {
  return (
    <Routes>
      {/* The editor opens in its own browser tab (window.open), so it's a
          top-level route with no sidebar shell around it - see the
          "Editor" button in the shops table. */}
      <Route path="/editor/:jobId/:shopId" element={<EditorPage />} />
      <Route path="/*" element={<Shell />} />
    </Routes>
  );
}

function Shell() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="dot" />
          Signage Automation
        </div>
        <nav>
          <NavLink to="/" end className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
            Automation
          </NavLink>
          <NavLink to="/recent" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
            Recently generated
          </NavLink>
        </nav>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Automation />} />
          <Route path="/recent" element={<RecentlyGenerated />} />
        </Routes>
      </main>
    </div>
  );
}
