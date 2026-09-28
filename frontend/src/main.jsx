import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import "./theme.css";
import "./styles.css";
import "./workspace-dark.css"; // the workspace's dark-glass theme (scoped to the sidebar shell; the editor stays light)

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
