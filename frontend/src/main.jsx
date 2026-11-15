import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import AuthGate from "./components/AuthGate.jsx";
import "./theme.css";
import "./theme-tokens.css"; // dark / light colour tokens used by every stylesheet
import "./styles.css";
import "./workspace-dark.css"; // the workspace theme (scoped to the sidebar shell; the editor has editor/editor-dark.css)
import "./ui-refresh.css";    // loaded last: palette, buttons, surfaces, motion - looks only

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthGate>
        <App />
      </AuthGate>
    </BrowserRouter>
  </React.StrictMode>,
);
