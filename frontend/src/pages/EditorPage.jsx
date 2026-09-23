import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import "../editor/editor.css";
import Canvas from "../editor/Canvas.jsx";
import LayersPanel from "../editor/LayersPanel.jsx";
import ProductPanel from "../editor/ProductPanel.jsx";
import PropertiesPanel from "../editor/PropertiesPanel.jsx";
import PageResizeDialog from "../editor/PageResizeDialog.jsx";
import ExportDialog from "../editor/ExportDialog.jsx";
import OrientationControl from "../editor/OrientationControl.jsx";
import { DIM, LeftDimension, LeftRuler, RULER, TopDimension, TopRuler } from "../editor/Rulers.jsx";
import { Fit, Redo, Undo, ZoomIn, ZoomOut } from "../editor/icons.jsx";
import { AlphaMaps } from "../editor/alphaMaps.js";
import { cloneWithNewIds, shiftNode, unionBox } from "../editor/model.js";
import { applyOps, buildIndex } from "../editor/ops.js";
import { UNIT_NAMES, fmt, fromUnit, toUnit, UNITS } from "../editor/units.js";
import { fitView, zoomAt } from "../editor/view.js";

const PX_PER_MM_100 = 96 / 25.4; // CorelDRAW's "100%" is 96 dpi

export default function EditorPage() {
  const { jobId, shopId } = useParams();
  const [load, setLoad] = useState({ phase: "loading", progress: 0, step: "" });
  const [attempt, setAttempt] = useState(0);
  const [base, setBase] = useState(null);
  const [assetBase, setAssetBase] = useState("");
  const [ops, setOps] = useState([]);
  const [cursor, setCursor] = useState(0);
  const [sel, setSel] = useState([]);
  const [ctx, setCtx] = useState(null);
  const [unit, setUnit] = useState("in");
  const [view, setView] = useState({ zoom: 1, x: 0, y: 0 });
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [showRender, setShowRender] = useState(false);
  const [saveState, setSaveState] = useState("saved");
  const [toast, setToast] = useState("");
  const [pointer, setPointer] = useState(null);
  const [snap, setSnap] = useState(true);
  const [fonts, setFonts] = useState({ available: false, fonts: [] });
  const [textPreview, setTextPreview] = useState(null); // {id, font, content} while the Text/Font fields have an uncommitted change - live canvas preview only, never an op
  const [shop, setShop] = useState(null);
  const [pageChange, setPageChange] = useState(null); // {w, h} in mm while the page-size dialog is open
  const [pageKey, setPageKey] = useState(0);
  const [showExport, setShowExport] = useState(false);
  const [editing, setEditing] = useState(null); // id of the text object being typed into
  const [converting, setConverting] = useState(false); // an orientation-conversion request is in flight
  const lastNudge = useRef({ at: 0 });
  const alphaMaps = useMemo(() => new AlphaMaps(), []);
  const clip = useRef(null);
  const idCounter = useRef(0);
  const pasteCount = useRef(0);
  const fitted = useRef(false);
  const sizeMeasured = useRef(false);
  const retry = useRef(false);
  const lastSaved = useRef("[]");
  const toastTimer = useRef(null);

  const say = useCallback((msg) => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 4500);
  }, []);

  useEffect(() => {
    fetch("/api/fonts").then((r) => r.json()).then(setFonts).catch(() => {});
    fetch(`/api/v2/shops/${shopId}/status`).then((r) => r.json()).then(setShop).catch(() => {});
  }, [shopId]);

  // ------------------------------------------------------------------ load
  useEffect(() => {
    let cancelled = false;
    let timer;
    async function go() {
      try {
        const r = await fetch(`/api/editor/${jobId}/${shopId}/scene${retry.current ? "?retry=1" : ""}`);
        if (cancelled) return;
        if (r.status === 202) {
          retry.current = false;
          const b = await r.json();
          setLoad({ phase: "building", progress: b.progress_pct, step: b.step });
          timer = setTimeout(go, 800);
          return;
        }
        if (!r.ok) {
          let detail = "";
          try {
            detail = (await r.json()).detail;
          } catch {
            /* not json */
          }
          setLoad({ phase: "error", status: r.status, error: detail || `HTTP ${r.status}` });
          return;
        }
        retry.current = false;
        const { ops: saved, asset_base: ab, ...scene } = await r.json();
        let usable = saved;
        try {
          applyOps(scene, saved);
        } catch (e) {
          usable = [];
          say(`Saved edits could not be replayed and were ignored: ${e.message}`);
        }
        setBase(scene);
        setAssetBase(ab);
        setOps(usable);
        setCursor(usable.length);
        lastSaved.current = JSON.stringify(usable);
        setLoad({ phase: "ready" });
      } catch (e) {
        if (!cancelled) setLoad({ phase: "error", error: e.message });
      }
    }
    setLoad({ phase: "loading", progress: 0, step: "" });
    go();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobId, shopId, attempt, say]);

  // ----------------------------------------------------------------- scene
  const scene = useMemo(() => {
    if (!base) return null;
    try {
      return applyOps(base, ops.slice(0, cursor));
    } catch {
      return base;
    }
  }, [base, ops, cursor]);

  useEffect(() => {
    if (scene && !fitted.current && sizeMeasured.current) {
      fitted.current = true;
      setView(fitView(size, scene.page));
    }
  }, [scene, size]);

  // drop selection/context that no longer exist (after undo, delete, ungroup ...)
  useEffect(() => {
    if (!scene) return;
    const idx = buildIndex(scene);
    setSel((s) => (s.every((i) => idx.has(i)) ? s : s.filter((i) => idx.has(i))));
    setCtx((c) => (c && !idx.has(c) ? null : c));
  }, [scene]);

  const onCanvasSize = useCallback((s) => {
    sizeMeasured.current = true;
    setSize(s);
  }, []);

  const nextId = useCallback(() => `n${Date.now().toString(36)}${++idCounter.current}`, []);

  const commit = useCallback(
    (op, opts = {}) => {
      if (!scene) return false;
      let result;
      try {
        result = applyOps(scene, [op]);
      } catch (e) {
        say(e.message.replace(/^op #0 \(\w+\): /, ""));
        return false;
      }
      const now = Date.now();
      const prev = ops[cursor - 1];
      // Holding an arrow key must not create one undo step per repeat: merge consecutive nudges of the same objects.
      if (opts.coalesce && cursor === ops.length && prev && prev.op === "move" && now - lastNudge.current.at < 800 &&
          prev.ids.length === op.ids.length && prev.ids.every((i, k) => i === op.ids[k])) {
        const merged = { ...prev, dx: Math.round((prev.dx + op.dx) * 1e4) / 1e4, dy: Math.round((prev.dy + op.dy) * 1e4) / 1e4 };
        setOps((o) => [...o.slice(0, cursor - 1), merged]);
        lastNudge.current.at = now;
        return true;
      }
      lastNudge.current.at = opts.coalesce ? now : 0;
      setOps((o) => [...o.slice(0, cursor), op]);
      setCursor((c) => c + 1);
      if (op.op === "group") {
        setSel([op.group_id]);
      } else if (op.op === "ungroup") {
        const g = buildIndex(scene).get(op.id);
        setSel(g.node.children.map((c) => c.id));
        setCtx(g.parent ? g.parent.id : null);
      } else if (op.op === "delete") {
        setSel([]);
      } else if (op.op === "paste") {
        setSel(op.nodes.map((n) => n.id));
        setCtx(buildIndex(result).get(op.parent).isLayer ? null : op.parent);
      }
      return true;
    },
    [scene, cursor, ops, say],
  );

  // A whole batch of ops as ONE undo step (e.g. orientation conversion's page + per-zone resizes).
  // Deliberately not `opsList.forEach(commit)`: commit()'s setOps updater slices on the CURRENT
  // `cursor`, which is still the stale, pre-batch value for every call made in the same render pass -
  // a second commit() in the same tick would slice off the first call's op instead of extending it.
  // Applying/appending the whole list in one pair of state updates avoids that entirely.
  const commitMany = useCallback(
    (opsList) => {
      if (!scene || !opsList.length) return false;
      try {
        applyOps(scene, opsList);
      } catch (e) {
        say(e.message.replace(/^op #\d+ \(\w+\): /, ""));
        return false;
      }
      setOps((o) => [...o.slice(0, cursor), ...opsList]);
      setCursor((c) => c + opsList.length);
      return true;
    },
    [scene, cursor, say],
  );

  const convertOrientation = useCallback(
    async (targetW, targetH, label) => {
      if (!scene || converting) return;
      setConverting(true);
      try {
        const r = await fetch("/api/scene/convert-orientation", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scene, target_w: targetW, target_h: targetH }),
        });
        if (!r.ok) throw new Error(((await r.json()).detail) || `HTTP ${r.status}`);
        const { ops: newOps } = await r.json();
        if (commitMany(newOps)) {
          setSel([]);
          setCtx(null);
          setView(fitView(size, { width: targetW, height: targetH }));
          say(`Converted to ${label} - Ctrl+Z to undo`);
        }
      } catch (e) {
        say(`Could not convert orientation: ${e.message}`);
      } finally {
        setConverting(false);
      }
    },
    [scene, converting, commitMany, size, say],
  );

  const undo = useCallback(() => setCursor((c) => Math.max(0, c - 1)), []);
  const redo = useCallback(() => setCursor((c) => Math.min(ops.length, c + 1)), [ops.length]);
  const select = useCallback((ids, nextCtx) => {
    setSel(ids);
    setCtx(nextCtx);
    setTextPreview(null); // an uncommitted text/font preview belongs to the previously selected text only
  }, []);

  // -------------------------------------------------------------- autosave
  const flush = useCallback(async () => {
    const body = JSON.stringify(ops.slice(0, cursor));
    if (body === lastSaved.current) return true;
    setSaveState("saving");
    try {
      const r = await fetch(`/api/editor/${jobId}/${shopId}/ops`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: `{"ops":${body}}` });
      if (!r.ok) throw new Error(((await r.json()).detail) || `HTTP ${r.status}`);
      lastSaved.current = body;
      setSaveState("saved");
      return true;
    } catch (e) {
      setSaveState("error");
      say(`Could not save edits: ${e.message}`);
      return false;
    }
  }, [ops, cursor, jobId, shopId, say]);

  useEffect(() => {
    if (load.phase !== "ready") return undefined;
    if (JSON.stringify(ops.slice(0, cursor)) === lastSaved.current) return undefined;
    setSaveState("dirty");
    const t = setTimeout(flush, 600);
    return () => clearTimeout(t);
  }, [ops, cursor, load.phase, flush]);

  // ------------------------------------------------------------- clipboard
  const copySelection = useCallback(
    (cut) => {
      if (!scene || !sel.length) return;
      const idx = buildIndex(scene);
      const nodes = sel.map((id) => idx.get(id)?.node).filter(Boolean);
      clip.current = { nodes: structuredClone(nodes), cut };
      pasteCount.current = 0;
      if (cut && !commit({ op: "delete", ids: sel })) clip.current = null;
      else say(`${cut ? "Cut" : "Copied"} ${nodes.length} object${nodes.length > 1 ? "s" : ""}`);
    },
    [scene, sel, commit, say],
  );

  const paste = useCallback(() => {
    if (!scene || !clip.current) return;
    const idx = buildIndex(scene);
    const anchor = ctx || (sel.length && idx.get(sel[0]) ? idx.get(sel[0]).layer.id : null) || [...scene.layers].reverse().find((l) => !l.locked)?.id;
    if (!anchor) return;
    pasteCount.current += 1;
    const off = clip.current.cut && pasteCount.current === 1 ? 0 : Math.max(10, 24 / view.zoom) * (clip.current.cut ? pasteCount.current - 1 : pasteCount.current);
    const nodes = clip.current.nodes.map((n) => {
      const c = cloneWithNewIds(n, nextId);
      shiftNode(c, off, -off);
      return c;
    });
    commit({ op: "paste", parent: anchor, nodes });
  }, [scene, sel, ctx, view.zoom, commit, nextId]);

  const selectAll = useCallback(() => {
    if (!scene) return;
    const idx = buildIndex(scene);
    let list;
    let nextCtx = null;
    if (ctx) {
      list = idx.get(ctx).node.children;
      nextCtx = ctx;
    } else {
      const layer = (sel.length && idx.get(sel[0]) ? idx.get(sel[0]).layer : null) || [...scene.layers].reverse().find((l) => !l.locked && l.visible !== false);
      list = layer ? layer.children : [];
    }
    setSel(list.filter((n) => n.visible !== false && !n.locked).map((n) => n.id));
    setCtx(nextCtx);
  }, [scene, ctx, sel]);

  // ---------------------------------------------------------- text editing
  const startEdit = useCallback(
    (id) => {
      const n = scene && buildIndex(scene).get(id);
      if (!n || !n.node.text) return;
      if (n.node.locked || n.layer.locked) return say("This text is locked and cannot be edited.");
      setSel([id]);
      setEditing(id);
    },
    [scene, say],
  );
  const applyText = useCallback(
    (id, content) => {
      commit({ op: "text", id, content });
      setEditing(null);
    },
    [commit],
  );

  // ------------------------------------------------------------- shortcuts
  const keys = useRef({});
  keys.current = { undo, redo, copySelection, paste, selectAll, commit, sel, ctx, scene, unit, startEdit };
  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable) return;
      const k = keys.current;
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (mod && key === "z") {
        e.preventDefault();
        e.shiftKey ? k.redo() : k.undo();
      } else if (mod && key === "y") {
        e.preventDefault();
        k.redo();
      } else if (mod && key === "c") {
        e.preventDefault();
        k.copySelection(false);
      } else if (mod && key === "x") {
        e.preventDefault();
        k.copySelection(true);
      } else if (mod && key === "v") {
        e.preventDefault();
        k.paste();
      } else if (mod && key === "a") {
        e.preventDefault();
        k.selectAll();
      } else if (mod && key === "g") {
        e.preventDefault();
        if (k.sel.length >= 2) k.commit({ op: "group", ids: k.sel, group_id: `n${Date.now().toString(36)}g`, name: "Group" });
      } else if (mod && key === "u") {
        e.preventDefault();
        const n = k.sel.length === 1 && k.scene ? buildIndex(k.scene).get(k.sel[0])?.node : null;
        if (n && n.kind === "group") k.commit({ op: "ungroup", id: n.id });
      } else if (e.key === "F2") {
        e.preventDefault();
        if (k.sel.length === 1) k.startEdit(k.sel[0]);
      } else if (e.key.startsWith("Arrow") && k.sel.length) {
        // nudge: 0.1 in (2.54 mm) like CorelDRAW; 1 mm when working in mm/cm. Shift = x10, Ctrl = x0.1.
        e.preventDefault();
        const base = k.unit === "mm" || k.unit === "cm" ? 1 : 2.54;
        const step = base * (e.shiftKey ? 10 : 1) * (mod ? 0.1 : 1);
        const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
        const dy = e.key === "ArrowDown" ? -step : e.key === "ArrowUp" ? step : 0;
        k.commit({ op: "move", ids: k.sel, dx, dy }, { coalesce: true });
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (k.sel.length) {
          e.preventDefault();
          k.commit({ op: "delete", ids: k.sel });
        }
      } else if (e.key === "Escape") {
        if (k.ctx) {
          const g = k.scene ? buildIndex(k.scene).get(k.ctx) : null;
          setSel([k.ctx]); // leaving a group selects the group itself, like CorelDRAW
          setCtx(g && g.parent ? g.parent.id : null);
        } else setSel([]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ----------------------------------------------------------------- render
  if (load.phase !== "ready") {
    return (
      <div className="ed-splash">
        <div className="ed-splash-card">
          <h1>Editor</h1>
          {(load.phase === "loading" || load.phase === "building") && (
            <>
              <p>
                {load.phase === "building"
                  ? "CorelDRAW is rendering every object of this board so it can be edited here."
                  : "Opening…"}
              </p>
              <div className="progress-bar"><div className="progress-fill" style={{ width: `${load.progress || 3}%` }} /></div>
              <p className="ed-hint">
                {load.step && load.step.startsWith("images ")
                  ? `Rendering objects ${load.step.slice(7)}`
                  : load.step === "page_image"
                    ? "Rendering the full-page reference"
                    : load.step || "Starting"}
                {load.progress ? ` · ${load.progress}%` : ""}
              </p>
            </>
          )}
          {load.phase === "error" && (
            <>
              <p className="err">{load.error}</p>
              {load.status === 503 && <p className="ed-hint">The machine is low on free memory. Close other programs (or restart the PC), then retry.</p>}
              {load.status === 409 && <p className="ed-hint">Convert this shop on the Automation page first.</p>}
              <button className="btn" onClick={() => { retry.current = true; setAttempt((a) => a + 1); }}>Retry</button>{" "}
              <a className="btn ghost" href="/recent">Recently generated</a>
            </>
          )}
        </div>
      </div>
    );
  }

  const pageW = scene.page.width;
  const pageH = scene.page.height;
  const zoomBy = (f) => setView((v) => zoomAt(v, pageH, f, size.w / 2, size.h / 2));
  const zoomPct = Math.round((view.zoom / PX_PER_MM_100) * 100);
  const idxNow = buildIndex(scene);
  const selNodes = sel.map((id) => idxNow.get(id)?.node).filter(Boolean);
  const selBox = unionBox(selNodes);
  const ctxNode = ctx ? idxNow.get(ctx)?.node : null;

  return (
    <div className="ed-root">
      <header className="ed-top">
        <span className="dot" />
        <strong>Signage Editor</strong>
        <span className="ed-top-sub">job {jobId.slice(0, 8)} · shop {shopId.slice(0, 8)}</span>
        <span className="ed-top-spacer" />
        <span className={`ed-save ed-save-${saveState}`}>
          {saveState === "saved" ? "All edits saved" : saveState === "saving" ? "Saving…" : saveState === "dirty" ? "Unsaved edits" : "Save failed"}
        </span>
      </header>

      <div className="ed-toolbar">
        <button className="ed-icon-btn" title="Undo (Ctrl+Z)" disabled={cursor === 0} onClick={undo}><Undo /> Undo</button>
        <button className="ed-icon-btn" title="Redo (Ctrl+Shift+Z)" disabled={cursor >= ops.length} onClick={redo}><Redo /> Redo</button>
        <span className="ed-sep" />
        <button className="ed-icon-btn" title="Zoom out" onClick={() => zoomBy(1 / 1.25)}><ZoomOut /></button>
        <span className="ed-zoom">{zoomPct}%</span>
        <button className="ed-icon-btn" title="Zoom in" onClick={() => zoomBy(1.25)}><ZoomIn /></button>
        <button className="ed-icon-btn" title="Fit page" onClick={() => setView(fitView(size, scene.page))}><Fit /> Fit</button>
        <span className="ed-sep" />
        <PageSize key={pageKey} pageW={pageW} pageH={pageH} unit={unit} onCommit={(w, h) => setPageChange({ w, h })} />
        <select className="ed-select" value={unit} onChange={(e) => setUnit(e.target.value)} aria-label="Units">
          {UNIT_NAMES.map((u) => <option key={u}>{u}</option>)}
        </select>
        <span className="ed-sep" />
        <OrientationControl unit={unit} disabled={converting} onConvert={convertOrientation} />
        <span className="ed-sep" />
        <label className="ed-check" title="Snap to page edges/centre and other objects while dragging (hold Alt to bypass)">
          <input type="checkbox" checked={snap} onChange={(ev) => setSnap(ev.target.checked)} /> Snap
        </label>
        <label className="ed-check" title="Show CorelDRAW's own full-page render of the converted board instead of the per-object images (edits are hidden while on)">
          <input type="checkbox" checked={showRender} onChange={(e) => setShowRender(e.target.checked)} /> Corel page render
        </label>
        <span className="ed-top-spacer" />
        <button className="btn" onClick={() => setShowExport(true)}>
          Save and Generate
        </button>
      </div>

      <div className="ed-main">
        <div className="ed-stage" style={{ gridTemplateColumns: `${DIM}px ${RULER}px 1fr`, gridTemplateRows: `${DIM}px ${RULER}px 1fr` }}>
          <div className="ed-corner" style={{ gridArea: "1 / 1 / 3 / 3" }} />
          <div style={{ gridArea: "1 / 3" }}><TopDimension width={size.w} view={view} pageW={pageW} unit={unit} /></div>
          <div style={{ gridArea: "2 / 3" }}><TopRuler width={size.w} view={view} unit={unit} cursor={pointer} /></div>
          <div style={{ gridArea: "3 / 1" }}><LeftDimension height={size.h} view={view} pageH={pageH} unit={unit} /></div>
          <div style={{ gridArea: "3 / 2" }}><LeftRuler height={size.h} view={view} pageH={pageH} unit={unit} cursor={pointer} /></div>
          <div style={{ gridArea: "3 / 3", position: "relative", minWidth: 0, minHeight: 0, overflow: "hidden" }}>
            <Canvas
              scene={scene}
              assetBase={assetBase}
              sel={sel}
              ctx={ctx}
              view={view}
              setView={setView}
              showRender={showRender}
              snap={snap}
              fonts={fonts}
              textPreview={textPreview}
              editingId={editing}
              onEditText={startEdit}
              onTextApply={applyText}
              onEditEnd={() => setEditing(null)}
              alphaMaps={alphaMaps}
              onSelect={select}
              onCommit={commit}
              onToast={say}
              onCursor={setPointer}
              onSize={onCanvasSize}
            />
          </div>
        </div>
        <aside className="ed-side">
          <PropertiesPanel scene={scene} sel={sel} unit={unit} onCommit={commit} fonts={fonts} onTextPreview={setTextPreview} />
          <ProductPanel scene={scene} sel={sel} jobId={jobId} shopId={shopId} onSelect={select} onCommit={commit} onToast={say} />
          <LayersPanel scene={scene} sel={sel} ctx={ctx} onSelect={select} onCommit={commit} nextId={() => `n${Date.now().toString(36)}g${++idCounter.current}`} />
        </aside>
      </div>

      <footer className="ed-status">
        <span>{pointer ? `X ${fmt(pointer.x, unit)}  Y ${fmt(pointer.y, unit)} ${unit}` : "—"}</span>
        <span>
          {selNodes.length
            ? `${selNodes.length} selected · ${fmt(selBox.w, unit)} × ${fmt(selBox.h, unit)} ${unit}`
            : "Nothing selected"}
          {ctxNode ? ` · inside “${ctxNode.name || "Group"}” (Esc to leave)` : ""}
        </span>
        <span>{ops.length ? `${cursor}/${ops.length} operations` : "No edits"}</span>
        <span>{scene.stats && scene.stats.mock ? "Mock scene (no CorelDRAW)" : `${scene.stats?.leaves ?? "?"} rendered objects`}</span>
      </footer>

      {showExport && (
        <ExportDialog
          jobId={jobId}
          shopId={shopId}
          scene={scene}
          opsCount={cursor}
          fonts={fonts}
          flush={flush}
          onClose={() => setShowExport(false)}
        />
      )}

      {pageChange && (
        <PageResizeDialog
          current={{ w: pageW, h: pageH }}
          next={pageChange}
          unit={unit}
          shop={shop || { name: "Board" }}
          jobId={jobId}
          onPageOnly={() => { commit({ op: "page", width: Math.round(pageChange.w * 1e4) / 1e4, height: Math.round(pageChange.h * 1e4) / 1e4 }); setPageChange(null); }}
          onCancel={() => { setPageChange(null); setPageKey((k) => k + 1); }}
        />
      )}

      {toast && <div className="ed-toast" role="status">{toast}</div>}
    </div>
  );
}

function PageSize({ pageW, pageH, unit, onCommit }) {
  const [w, setW] = useState("");
  const [h, setH] = useState("");
  const show = (mm) => {
    const s = toUnit(mm, unit).toFixed(UNITS[unit].decimals);
    return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
  };
  useEffect(() => {
    setW(show(pageW));
    setH(show(pageH));
  }, [pageW, pageH, unit]); // eslint-disable-line react-hooks/exhaustive-deps

  const apply = () => {
    const nw = parseFloat(w);
    const nh = parseFloat(h);
    if (!(nw > 0) || !(nh > 0)) {
      setW(show(pageW));
      setH(show(pageH));
      return;
    }
    const mmW = fromUnit(nw, unit);
    const mmH = fromUnit(nh, unit);
    if (Math.abs(mmW - pageW) > 1e-3 || Math.abs(mmH - pageH) > 1e-3) onCommit(mmW, mmH);
  };
  const key = (e) => e.key === "Enter" && e.currentTarget.blur();
  return (
    <span className="ed-pagesize" title="Page size (changes the page only - objects stay where they are)">
      <span>Page</span>
      <input aria-label="Page width" value={w} onChange={(e) => setW(e.target.value)} onBlur={apply} onKeyDown={key} />
      <span>×</span>
      <input aria-label="Page height" value={h} onChange={(e) => setH(e.target.value)} onBlur={apply} onKeyDown={key} />
    </span>
  );
}
