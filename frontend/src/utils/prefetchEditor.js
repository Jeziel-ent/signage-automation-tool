// The editor is its own lazily loaded chunk (App.jsx). It opens in a NEW tab, so warming it up here means the browser already has
// the chunk in its cache when that tab asks for it. Called on hover/focus of an "Open in editor" button; the import runs once.
let started = null;
export const loadEditorPage = () => import("../pages/EditorPage.jsx");
export function prefetchEditor() {
  if (!started) started = loadEditorPage().catch(() => { started = null; });
  return started;
}
