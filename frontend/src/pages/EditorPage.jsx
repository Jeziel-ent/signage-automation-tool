import { useParams } from "react-router-dom";

/** Placeholder for Phase C. Opened in a new tab from the Automation page's
 * per-shop "Editor" button once a shop's conversion has finished.
 */
export default function EditorPage() {
  const { jobId, shopId } = useParams();
  return (
    <div style={{ fontFamily: "var(--font-sans, system-ui, sans-serif)", padding: 40, background: "#121212", color: "#fff", minHeight: "100vh" }}>
      <h1 style={{ marginTop: 0 }}>Editor</h1>
      <p>
        Job <code>{jobId}</code>, shop <code>{shopId}</code>
      </p>
      <p style={{ color: "#a0a0a0" }}>The full canvas editor (Phase C) isn't built yet.</p>
    </div>
  );
}
