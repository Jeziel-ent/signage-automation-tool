import "./PageHeader.css";

/** The title block every page starts with: one heading, one plain sentence about the page, and optional actions on the right. */
export default function PageHeader({ title, description, children }) {
  return (
    <header className="pg-head">
      <div className="pg-head-text">
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {children && <div className="pg-head-actions">{children}</div>}
    </header>
  );
}
