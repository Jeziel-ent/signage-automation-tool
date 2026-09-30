import { useEffect, useRef, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { Building2, CheckCircle2, Download, ExternalLink, FileCode2, FolderArchive, Printer, FileSpreadsheet, FileCheck2, FolderOpen, Play, Plus, Store, Trash2, X } from "lucide-react";
import UploadDropzone from "../components/UploadDropzone.jsx";
import BrandSelect from "../components/BrandSelect.jsx";
import AnimatedCount from "../components/AnimatedCount.jsx";
import ExportModal from "../components/ExportModal.jsx";
import PrintFileModal from "../components/PrintFileModal.jsx";
import GenerateZipModal from "../components/GenerateZipModal.jsx";
import { useSteppedProgress } from "../hooks/useSteppedProgress.js";
import { parseShopWorkbook } from "../utils/shopImport.js";
import { BOARD_TYPES, DEFAULT_BOARD_TYPE, applyDefaultUnit, cdrDownloadUrl, followsEnglish, isDraft, nameEditPatch, resetForNewMaster, shopPayload, toDraftRow, withAutoTamil, withFreshAutoTamil } from "../utils/shopPayload.js";
import { toTamil } from "../utils/tamilTranslit.js";
import { prefetchEditor } from "../utils/prefetchEditor.js";
import { batchStats, fmtEta, monotonicProgress, recordFinishes, smoothEta } from "../utils/batchStats.js";
import { fmtBytes } from "../utils/fileSize.js";

const UNITS = ["in", "ft"];
// One shared unit per board (applies to both width and height); the server stores it on both dimensions.
const TA_DEBOUNCE_MS = 300; // auto-Tamil waits this long after the last keystroke in the English name
const emptyShopForm = (unit = "in") => ({ name: "", shop_name_local: "", ta_auto: true, width: "", height: "", unit, board_type: DEFAULT_BOARD_TYPE });

const DEFAULT_UNIT_KEY = "signage.defaultUnit";
function readDefaultUnit() {
  try {
    const u = localStorage.getItem(DEFAULT_UNIT_KEY);
    return u === "ft" || u === "in" ? u : "in";
  } catch {
    return "in"; // storage blocked: inches, as before
  }
}

// CorelEngine's own named steps (see backend/app/engines.py's step() closure
// and CLAUDE.md "Production hardening"), each with the cumulative percent
// reached once that step is confirmed complete - mirrors main.py's
// _STEP_PERCENT. Exported shape reused by useSteppedProgress.
export const CONVERT_STEPS = [
  { key: "launch", endPct: 10 },
  { key: "open", endPct: 25 },
  { key: "tile_resize", endPct: 55 },
  { key: "bitmaps", endPct: 60 },
  { key: "saveas", endPct: 75 },
  { key: "pdf", endPct: 88 },
  { key: "png", endPct: 97 },
];

export default function Automation() {
  const [brands, setBrands] = useState([]);
  const [brand, setBrand] = useState("");
  const defaultBrandApplied = useRef(false);
  const [addingBrand, setAddingBrand] = useState(false);
  const [newBrand, setNewBrand] = useState("");

  // Dual-master templates: one upload per orientation, both optional. Shops attach to the first master that
  // exists (landscape preferred) and carry both ids; the server converts from the master matching each
  // shop's TARGET orientation (width > height = landscape; square and portrait = portrait), falling back to the other one.
  const [landscapeJob, setLandscapeJob] = useState(null); // {id, preview_url, preview_error}
  const [portraitJob, setPortraitJob] = useState(null);
  const job = landscapeJob || portraitJob;
  const [shops, setShops] = useState([]);
  const [defaultUnit, setDefaultUnit] = useState(readDefaultUnit);
  const [activeSheet, setActiveSheet] = useState(""); // Shops Queue sheet tab ("" = all sheets)
  const [shopForm, setShopForm] = useState(() => emptyShopForm(readDefaultUnit()));
  const [shopError, setShopError] = useState("");
  const [stepEstimates, setStepEstimates] = useState({});
  // One timer polls every converting/queued shop in a single request (GET /api/v2/shop-statuses) - not one timer + one
  // request per shop, which a large Convert All turned into dozens of requests a second.
  const polling = useRef(new Set());
  const pollTimer = useRef(null);
  const taTimers = useRef(new Map()); // shop id -> pending auto-Tamil timer (debounced English typing)

  useEffect(() => {
    fetch("/api/v2/brands")
      .then((r) => r.json())
      .then((list) => {
        setBrands(list);
        // Default to Adinn when it exists (case-insensitive), so the workspace opens ready to use; only once, never overriding a choice.
        const adinn = list.find((b) => b.toLowerCase() === "adinn");
        if (adinn && !defaultBrandApplied.current) {
          defaultBrandApplied.current = true;
          setBrand((cur) => cur || adinn);
        }
      })
      .catch(() => {});
    // Measured average step durations (seconds), used to pace the smoothed
    // per-row progress animation - see useSteppedProgress. Fetched once per
    // page load, not on every status poll.
    fetch("/api/v2/step-estimates")
      .then((r) => r.json())
      .then(setStepEstimates)
      .catch(() => {});
  }, []);

  useEffect(
    () => () => {
      clearInterval(pollTimer.current);
    },
    [],
  );

  async function addBrand() {
    const name = newBrand.trim();
    if (!name) return;
    const r = await fetch("/api/v2/brands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const list = await r.json();
    setBrands(list);
    setBrand(name);
    setNewBrand("");
    setAddingBrand(false);
  }

  // Bumped whenever the masters change; an in-flight convert / Convert All started under older masters stops at its next step.
  const masterGen = useRef(0);
  const [queueNotice, setQueueNotice] = useState("");
  const [exportShop, setExportShop] = useState(null); // the done shop whose Export modal is open
  const [showPrintFile, setShowPrintFile] = useState(false);
  const [showZip, setShowZip] = useState(false); // the Generate ZIP modal

  // Uploading, replacing or removing a master invalidates every conversion in the queue: each saved shop (done, converting, queued,
  // failed) becomes a fresh draft with the same name and size, so its next Convert creates a new shop on the NEW master instead of
  // re-running the old one. Status polling and batch tracking for the old shops stop. The old shops are not deleted - their boards
  // stay under Recently generated, and a conversion the server already started finishes there.
  function resetShopsQueueStatus(reason) {
    masterGen.current += 1;
    polling.current.clear();
    clearInterval(pollTimer.current);
    pollTimer.current = null;
    setIsBatchConverting(false);
    setBatch(null);
    setBatchSummary("");
    setImportReport(null);
    setShopError("");
    const { shops: next, reset } = resetForNewMaster(shops);
    setShops(next);
    if (reset) setQueueNotice(`${reason} - ${reset} shop${reset === 1 ? "" : "s"} reset to Convert. Earlier results stay under Recently generated.`);
  }

  // A master upload: remember the job plus the file name/size for the badge, then reset the queue's conversions.
  function onUploaded(orientation, body, fileName, fileSize) {
    const job = { ...body, fileName, fileSize };
    const replacing = orientation === "landscape" ? landscapeJob : portraitJob;
    if (orientation === "landscape") setLandscapeJob(job);
    else setPortraitJob(job);
    resetShopsQueueStatus(`${orientation === "landscape" ? "Landscape" : "Portrait"} master ${replacing ? "replaced" : "added"}`);
  }

  function removeMaster(orientation) {
    const other = orientation === "landscape" ? portraitJob : landscapeJob;
    if (!other && shops.length > 0 && !window.confirm("Removing the only master clears the shops list in this view (converted shops stay under Recently generated). Continue?")) return;
    if (orientation === "landscape") setLandscapeJob(null);
    else setPortraitJob(null);
    resetShopsQueueStatus(`${orientation === "landscape" ? "Landscape" : "Portrait"} master removed`);
    if (!other) {
      setShops([]);
      setQueueNotice("");
    }
  }

  async function addShop() {
    setShopError("");
    const f = shopForm.ta_auto ? { ...shopForm, shop_name_local: toTamil(shopForm.name) } : shopForm;
    if (!f.name.trim() || !(+f.width > 0) || !(+f.height > 0)) {
      setShopError("Shop name, width and height are required");
      return;
    }
    const r = await fetch(`/api/v2/jobs/${job.id}/shops`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...shopPayload(f),
        sno: shops.length + 1, // the S.no this row gets - output files are numbered by it
        landscape_master_id: landscapeJob?.id || null,
        portrait_master_id: portraitJob?.id || null,
      }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      setShopError(body.detail || "Could not add shop");
      return;
    }
    const shop = await r.json();
    setShops((s) => [...s, { ...shop, unit: shop.width_unit, ta_auto: f.ta_auto && !!f.shop_name_local }]);
    setShopForm(emptyShopForm(defaultUnit));
    setShowAddRow(false);
  }

  // Excel / CSV bulk import: parsed entirely in the browser (SheetJS) into editable draft rows; the server only
  // hears about a row when it is converted. Bad rows are listed, never silently dropped.
  const [importReport, setImportReport] = useState(null); // {file, added, errors:[{row, reason}], note}
  const [importing, setImporting] = useState(false);
  const importInputRef = useRef(null);
  const [showAddRow, setShowAddRow] = useState(false);
  const [shopsDrag, setShopsDrag] = useState(false);

  async function importFile(file) {
    if (!file) return;
    setImporting(true);
    setImportReport(null);
    try {
      // every sheet of the workbook: each becomes a tab in the Shops Queue (a CSV / one-sheet file has no tabs)
      const { sheets } = await parseShopWorkbook(file, { defaultUnit });
      const withShops = sheets.filter((sh) => !sh.missing.length && sh.shops.length);
      const missingText = (m) => `Could not find the ${m.map((k) => (k === "size" ? "size (a Size column like 10*4, or Width and Height columns)" : "shop name")).join(" or the ")}.`;
      if (!withShops.length) {
        const first = sheets.find((sh) => sh.missing.length) || sheets[0];
        setImportReport({ file: file.name, added: 0, errors: [], note: first && first.missing.length ? missingText(first.missing) : "No data rows found." });
        return;
      }
      const multi = sheets.length > 1;
      // Browser only - no request is made here. Rows become local drafts in the table (fully editable); a draft is
      // saved to the server the moment it is converted. A row without a Tamil name gets one transliterated from its
      // English name (editable; marked "auto" until typed over).
      const parsedShops = withShops.flatMap((sh) => sh.shops).map(withAutoTamil);
      const errors = withShops
        .flatMap((sh) => sh.errors.map((e) => ({ ...e, sheet: multi ? sh.name : undefined })))
        .sort((a, b) => (a.sheet || "").localeCompare(b.sheet || "") || (a.row ?? 0) - (b.row ?? 0));
      const skippedSheets = multi ? sheets.filter((sh) => !withShops.includes(sh)).map((sh) => sh.name) : [];
      const added = parsedShops.length;
      setShops((s) => [...s, ...parsedShops.map(toDraftRow)]);
      const defaulted = parsedShops.filter((x) => x.unitSource === "default").length;
      const translated = parsedShops.filter((x) => x.ta_auto).length;
      setImportReport({
        file: file.name, added, errors, defaulted, translated, unit: defaultUnit,
        sheets: multi ? withShops.map((sh) => `${sh.name} (${sh.shops.length})`) : [],
        note: skippedSheets.length ? `No shop list found on sheet${skippedSheets.length === 1 ? "" : "s"}: ${skippedSheets.join(", ")}` : "",
      });
    } catch (e) {
      setImportReport({ file: file.name, added: 0, errors: [], note: `Import failed: ${e.message}` });
    } finally {
      setImporting(false);
      if (importInputRef.current) importInputRef.current.value = "";
    }
  }

  function pollShop(shopId) {
    polling.current.add(shopId);
    if (pollTimer.current) return;
    pollTimer.current = setInterval(async () => {
      const ids = [...polling.current];
      if (!ids.length) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
        return;
      }
      let data;
      try {
        const r = await fetch(`/api/v2/shop-statuses?ids=${ids.map(encodeURIComponent).join(",")}`);
        if (!r.ok) return;
        data = await r.json();
      } catch {
        return; // a network blip: try again on the next tick
      }
      for (const [id, st] of Object.entries(data)) {
        if (st.status === "done" || st.status === "failed") polling.current.delete(id);
      }
      // (after a master reset the rows are new drafts with new ids, so a late response for an old shop matches nothing)
      setShops((s) => s.map((x) => (data[x.id] ? { ...x, ...data[x.id] } : x)));
    }, 800);
  }

  function editShop(shopId, patch) {
    // a unit picked by hand is an override: the Default Unit no longer changes that row
    const p = "unit" in patch ? { ...patch, unitSource: "manual" } : patch;
    // typing the English name re-transliterates the Tamil one while it is still automatic - 300 ms after the last keystroke
    // (nameEditPatch leaves the Tamil name alone here; the timer, blur-save and convert fill it)
    setShops((s) => s.map((x) => (x.id === shopId ? { ...x, ...nameEditPatch(x, p, { deferTamil: true }) } : x)));
    if ("name" in p) {
      clearTimeout(taTimers.current.get(shopId));
      taTimers.current.set(shopId, setTimeout(() => {
        taTimers.current.delete(shopId);
        setShops((s) => s.map((x) => (x.id === shopId && followsEnglish(x) ? withFreshAutoTamil(x) : x)));
      }, TA_DEBOUNCE_MS));
    }
  }

  // Changing the Default Unit re-labels every row that took the default (an Excel row with no unit, not yet converted) -
  // never a unit read from the file, picked by hand, or on a converted/queued shop - and the new-shop form. Saved rows
  // are PATCHed like any inline edit; drafts change locally.
  function changeDefaultUnit(unit) {
    if (unit === defaultUnit) return;
    setDefaultUnit(unit);
    try {
      localStorage.setItem(DEFAULT_UNIT_KEY, unit);
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
    const { shops: next, changed } = applyDefaultUnit(shops, unit);
    if (changed.length) {
      setShops(next);
      changed.filter((id) => !isDraft({ id })).forEach((id) => saveShop(id, { unit }));
    }
    setShopForm((f) => ({ ...f, unit }));
  }

  // Persist an inline edit when the field loses focus / a unit changes (convert also re-sends everything, so a
  // conversion never depends on this having landed).
  async function saveShop(shopId, override) {
    if (isDraft({ id: shopId })) return; // a draft lives in the browser until it is converted
    // a pending auto-Tamil update is applied now, so the save carries the Tamil name that matches the English one
    const x = withFreshAutoTamil({ ...shops.find((y) => y.id === shopId), ...override });
    if (!x.name || !(+x.width > 0) || !(+x.height > 0)) return; // incomplete edit: keep it local, convert will complain
    const r = await fetch(`/api/v2/shops/${shopId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(shopPayload(x)),
    });
    if (!r.ok) setShopError((await r.json().catch(() => ({}))).detail || "Could not save the change");
    else setShopError("");
  }

  async function deleteShop(shopId) {
    if (isDraft({ id: shopId })) return setShops((s) => s.filter((x) => x.id !== shopId));
    const r = await fetch(`/api/v2/shops/${shopId}`, { method: "DELETE" });
    if (r.ok) setShops((s) => s.filter((x) => x.id !== shopId));
    else setShopError((await r.json().catch(() => ({}))).detail || "Could not remove the shop");
  }

  async function convertShop(shopId) {
    // conversions keep the master's own fonts (no font fields are sent); a pending auto-Tamil update is applied first
    let current = withFreshAutoTamil(shops.find((x) => x.id === shopId) || null);
    if (!current) return null;
    setShopError("");
    const gen = masterGen.current;
    // the row's S.no in the table goes with it: every output file is named "<S.no> - <W> X <H> <Unit> - <Type> - <NAME>"
    const masters = { landscape_master_id: landscapeJob?.id || null, portrait_master_id: portraitJob?.id || null,
      sno: shops.findIndex((x) => x.id === shopId) + 1 };
    let id = shopId;
    if (isDraft(current)) {
      // First save: create the shop from the row's CURRENT (possibly edited) values, then convert that.
      const c = await fetch(`/api/v2/jobs/${job.id}/shops`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...shopPayload(current), ...masters }),
      });
      if (!c.ok) {
        setShopError(`${current.name || "Shop"}: ${(await c.json().catch(() => ({}))).detail || "could not save the shop"}`);
        return null;
      }
      const saved = await c.json();
      if (gen !== masterGen.current) return null; // the masters changed while saving: don't convert against the old one
      id = saved.id;
      current = { ...saved, ...shopPayload(current) };
      setShops((s) => s.map((x) => (x.id === shopId ? { ...saved, ...shopPayload(current), status: "new", progress_pct: 0 } : x)));
    }
    setShops((s) => s.map((x) => (x.id === id ? { ...x, status: "queued", progress_pct: 0 } : x)));
    // Send the CURRENT row values and master ids with every conversion: a shop row keeps the ids it was created with
    // (a portrait master uploaded after the shop was added would otherwise be ignored) and inline edits may not
    // have been saved yet.
    const r = await fetch(`/api/v2/shops/${id}/convert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...shopPayload(current), ...masters }),
    });
    if (gen !== masterGen.current) return null; // reset while the request was in flight: that row is gone from the queue
    if (r.ok || r.status === 409) {
      pollShop(id);
      return id;
    }
    setShops((s) => s.map((x) => (x.id === id ? { ...x, status: "new" } : x)));
    setShopError(`${current.name || "Shop"}: ${(await r.json().catch(() => ({}))).detail || "could not start the conversion"}`);
    return null;
  }

  // ---- sheet tabs: one per Excel sheet the rows came from (+ "Added manually" for rows without one), shown when there is
  // more than one group. Convert All works on the tab being viewed; S.no stays the row's position in the whole queue.
  const NO_SHEET = "\u0000manual";
  const sheetOf = (x) => x.sheet_name || NO_SHEET;
  const sheetTabs = [...new Set(shops.map(sheetOf))];
  const showSheetTabs = sheetTabs.length > 1;
  const currentSheet = showSheetTabs && sheetTabs.includes(activeSheet) ? activeSheet : "";
  const inSheet = (x) => !currentSheet || sheetOf(x) === currentSheet;
  const convertible = shops.filter((x) => inSheet(x) && (x.status === "new" || x.status === "failed"));
  // converted shops for "Create Print File", numbered by their S.no in the table
  const printable = shops.map((x, i) => ({ ...x, no: i + 1 })).filter((x) => x.status === "done" && !isDraft(x));

  // ---- batch conversion: Convert All swaps for a progress banner until every shop in the batch has finished
  const [isBatchConverting, setIsBatchConverting] = useState(false);
  const [batch, setBatch] = useState(null); // {startedAt, total, ids: [real shop ids that started], notStarted, finishTimes}
  // what the banner SHOWS: the % never below what it already showed in this batch, the ETA smoothed (see batchStats.js)
  const [batchView, setBatchView] = useState({ peak: 0, eta: null });
  const [now, setNow] = useState(Date.now());
  const [batchSummary, setBatchSummary] = useState("");

  async function convertAll() {
    const todo = convertible.map((x) => x.id);
    if (!todo.length) return;
    setBatchSummary("");
    setBatch({ startedAt: Date.now(), total: todo.length, ids: [], notStarted: 0, finishTimes: [] });
    setBatchView({ peak: 0, eta: null }); // a new batch starts from 0 with no leftover estimate
    setIsBatchConverting(true);
    // one after another: saving drafts numbers the shops (seq_no), which must not race. The server then converts
    // the queued shops one at a time.
    const gen = masterGen.current;
    for (const id of todo) {
      if (gen !== masterGen.current) return; // a master changed mid-batch: the queue was reset, stop queuing
      const realId = await convertShop(id);
      if (gen !== masterGen.current) return;
      setBatch((b) => (!b ? b : realId ? { ...b, ids: [...b.ids, realId] } : { ...b, notStarted: b.notStarted + 1 }));
    }
  }

  useEffect(() => {
    if (!isBatchConverting) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [isBatchConverting]);

  const stats = batchStats(batch, shops, now);
  const { batchProgress, estimatedTimeRemaining, currentShopIndex } = stats;
  const settledCount = stats.done + stats.failed;
  useEffect(() => { // stamp each shop's finish, for the moving-average ETA
    setBatch((b) => {
      if (!b) return b;
      const finishTimes = recordFinishes(b.finishTimes, settledCount, Date.now());
      return finishTimes === b.finishTimes ? b : { ...b, finishTimes };
    });
  }, [settledCount]);
  useEffect(() => {
    if (!isBatchConverting) return;
    setBatchView((v) => ({ peak: monotonicProgress(v.peak, batchProgress), eta: smoothEta(v.eta, estimatedTimeRemaining, now) }));
  }, [isBatchConverting, batchProgress, estimatedTimeRemaining, now]);
  useEffect(() => {
    if (isBatchConverting && stats.allSettled) {
      setIsBatchConverting(false);
      setBatchSummary(`Batch finished: ${stats.done} converted${stats.failed + batch.notStarted ? `, ${stats.failed + batch.notStarted} failed` : ""}.`);
    }
  }, [isBatchConverting, stats.allSettled, stats.done, stats.failed, batch]);

  function openEditor(shop) {
    window.open(`/editor/${shop.job_id || job.id}/${shop.id}`, "_blank");
  }

  // The editor tab's "Save Changes" saves, tells this tab, and closes itself - say so here, where the user lands.
  const shopsRef = useRef(shops);
  shopsRef.current = shops;
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return undefined;
    const ch = new BroadcastChannel("signage-editor");
    ch.onmessage = (e) => {
      const m = e.data || {};
      if (m.type !== "saved") return;
      const shop = shopsRef.current.find((x) => x.id === m.shopId);
      if (shop) setQueueNotice(`Edits to "${shop.name}" saved. Use the download button on its row to export the files.`);
    };
    return () => ch.close();
  }, []);

  const bothMasters = !!(landscapeJob && portraitJob);
  const masterBadge = bothMasters ? "Dual-Master Ready" : landscapeJob ? "Landscape master only" : portraitJob ? "Portrait master only" : "No master yet";

  return (
    <div className="ws-page">
      {/* BAR 1 - brand selector + status badges */}
      <header className="ws-bar">
        <div className="ws-bar-left">
          <span className="ws-bar-title">
            <Building2 size={14} /> Brand
          </span>
          <BrandSelect value={brand} options={brands} onChange={setBrand} />
          {addingBrand ? (
            <>
              <input
                className="ws-input"
                autoFocus
                placeholder="New brand name"
                value={newBrand}
                onChange={(e) => setNewBrand(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addBrand()}
              />
              <button className="ws-cta" onClick={addBrand} disabled={!newBrand.trim()}>
                Add
              </button>
              <button className="ws-cta-ghost" onClick={() => setAddingBrand(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button className="ws-cta" onClick={() => setAddingBrand(true)}>
              <Plus size={14} /> New Brand
            </button>
          )}
        </div>
        <div className="ws-badges">
          <span className="ws-badge">
            <Store size={13} /> <AnimatedCount value={shops.length} /> Shop{shops.length === 1 ? "" : "s"} Loaded
          </span>
          <span className={"ws-badge" + (bothMasters ? " ok" : "")}>
            <span className={"ws-dot" + (bothMasters ? " live" : "")} aria-hidden="true" /> {masterBadge}
          </span>
        </div>
      </header>

      <div className="ws-stack">
        {/* TOP - master templates, the two cards side by side */}
        <section className="ws-card ws-masters" aria-label="Master Templates">
          <div className="ws-card-head">
            <h2>Master Templates</h2>
          </div>
          <div className="ws-card-body">
            {/* two side-by-side upload cards, both always visible (no tab switch); each becomes a file badge once filled */}
            <div className="mc-grid">
              {[
                ["landscape", landscapeJob, { title: "Landscape Master", help: "Drag & drop .cdr file (used for boards W:H ≥ 1.25)", browse: "Browse Landscape", tone: "red" }],
                ["portrait", portraitJob, { title: "Portrait Master", help: "Drag & drop .cdr file (used for boards W:H < 1.25, incl. square)", browse: "Browse Portrait", tone: "rose" }],
              ].map(([orientation, mjob, card]) =>
                mjob ? (
                  <div key={orientation} className="master-card filled" data-slot={orientation}>
                    <div className="mc-filled-head">{card.title}</div>
                    <div className="master-thumb">
                      {mjob.preview_url ? (
                        <img src={mjob.preview_url} alt={`${orientation} master preview`} />
                      ) : (
                        <div className="preview-missing">{mjob.preview_error || "Preview not available"}</div>
                      )}
                    </div>
                    <div className="file-badge">
                      <FileCheck2 size={18} className="ok" />
                      <div className="file-badge-text">
                        <div className="file-badge-name" title={mjob.fileName}>{mjob.fileName || "master.cdr"}</div>
                        <div className="file-badge-size">{fmtBytes(mjob.fileSize)}</div>
                      </div>
                      <button className="icon-btn" onClick={() => removeMaster(orientation)} title={`Remove the ${orientation} master`} aria-label={`Remove the ${orientation} master`}>
                        <X size={16} />
                      </button>
                    </div>
                  </div>
                ) : (
                  <div key={orientation} className="mc-slot" data-slot={orientation}>
                    <UploadDropzone
                      card={card}
                      disabled={!brand}
                      brand={brand}
                      orientation={orientation}
                      label={orientation}
                      onUploaded={(body, name, size) => onUploaded(orientation, body, name, size)}
                    />
                  </div>
                ),
              )}
            </div>
            {!brand && <p className="hint hero-note">Pick or create a brand above to enable uploads.</p>}
          </div>
        </section>

        {/* BOTTOM - shops queue, full width */}
        <section className="ws-card ws-queue" aria-label="Shops Queue">
          <div className="ws-card-head">
            <h2>Shops Queue</h2>
            <div className="ws-actions">

              <input
                ref={importInputRef}
                type="file"
                accept=".xlsx,.xls,.csv"
                hidden
                data-testid="shop-import-input"
                onChange={(e) => importFile(e.target.files[0])}
              />
              {/* hidden until a sheet (or sample data) has put shops in the queue; the empty-state card is the way in until then.
                  The file input above stays mounted - the empty-state "Import Excel File" button uses it. */}
              {shops.length > 0 && (
                <>
                  <button className="btn-gradient" disabled={!job || importing} onClick={() => importInputRef.current?.click()}>
                    <FileSpreadsheet size={16} /> {importing ? "Importing..." : "Import Excel (.xlsx / .csv)"}
                  </button>
                  <button className="btn ghost" disabled={!job} onClick={() => setShowAddRow((v) => !v)}>
                    <Plus size={15} /> Add Shop
                  </button>
                </>
              )}
            </div>
          </div>

          {queueNotice && (
            <div className="import-report queue-notice" role="status">
              <span>{queueNotice}</span>
              <button className="icon-btn" onClick={() => setQueueNotice("")} aria-label="Dismiss">×</button>
            </div>
          )}
          {importReport && (
                <div className="import-report" role="status">
                  {importReport.added > 0 && (
                    <div>Successfully imported {importReport.added} shop{importReport.added === 1 ? "" : "s"} from {importReport.file}</div>
                  )}
                  {importReport.errors.length > 0 && (
                    <div>{importReport.errors.length} row{importReport.errors.length === 1 ? "" : "s"} skipped:</div>
                  )}
                  {importReport.sheets?.length > 0 && <div>Sheets: {importReport.sheets.join(", ")} - one tab each below.</div>}
                  {importReport.translated > 0 && (
                    <div>
                      Tamil names were written automatically for {importReport.translated} shop{importReport.translated === 1 ? "" : "s"} (marked "auto") - please check them.
                    </div>
                  )}
                  {importReport.defaulted > 0 && (
                    <div>
                      {importReport.defaulted === importReport.added ? "No unit found in the sheet" : `No unit found for ${importReport.defaulted} row${importReport.defaulted === 1 ? "" : "s"}`}
                      {" - using the default unit ("}{defaultUnit}{"). The Unit dropdown in the table header changes them."}
                    </div>
                  )}
                  {importReport.note && <div className="err">{importReport.note}</div>}
                  {importReport.errors.length > 0 && (
                    <ul className="err">
                      {importReport.errors.map((e, i) => (
                        <li key={i}>{e.sheet ? `${e.sheet}, row` : "Row"} {e.row}: {e.reason}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
          {shopError && <div className="err ws-error">{shopError}</div>}
          {!job || (shops.length === 0 && !showAddRow) ? (
            <div className="ws-card-body">
              <ShopsEmptyState
                hasJob={!!job}
                importing={importing}
                dragOver={shopsDrag}
                setDragOver={setShopsDrag}
                onImportClick={() => importInputRef.current?.click()}
                onManual={() => setShowAddRow(true)}
                onDropFile={importFile}
              />
            </div>
          ) : (
            <>
              {showSheetTabs && (
                <div className="sheet-tabs" role="tablist" aria-label="Excel sheets">
                  <span className="sheet-tabs-label">{sheetTabs.filter((t) => t !== NO_SHEET).length} sheet{sheetTabs.filter((t) => t !== NO_SHEET).length === 1 ? "" : "s"}</span>
                  {["", ...sheetTabs].map((t) => {
                    const n = t ? shops.filter((x) => sheetOf(x) === t).length : shops.length;
                    return (
                      <button key={t || "all"} role="tab" aria-selected={currentSheet === t} className={"sheet-tab" + (currentSheet === t ? " active" : "")}
                        onClick={() => setActiveSheet(t)} title={t && t !== NO_SHEET ? `Sheet "${t}" of the imported workbook` : undefined}>
                        {t === "" ? "All sheets" : t === NO_SHEET ? "Added manually" : t} <span className="sheet-count">{n}</span>
                      </button>
                    );
                  })}
                </div>
              )}
              <div className="ws-table-wrap">
              <div className="ws-table-scroll">
                <table className="shops-table">
                  <thead>
                    <tr>
                      <th>S.no</th>
                      <th>Shop name (EN)</th>
                      <th>Shop name (TA)</th>
                      <th>Width</th>
                      <th>Height</th>
                      <th className="unit-th">
                    {/* the page's Default Unit: rows whose size had no unit (and new manual shops) follow it; a unit read
                        from the sheet or picked in a row keeps its own value */}
                    <span className="unit-th-inner">
                      <span>Unit</span>
                      <select value={defaultUnit} onChange={(e) => changeDefaultUnit(e.target.value)} aria-label="Default unit"
                        title="Default unit for rows without one in the sheet (and for new shops). Units read from the sheet or picked in a row are kept.">
                        <option value="ft">ft</option>
                        <option value="in">in</option>
                      </select>
                    </span>
                  </th>
                      <th>Type of board</th>
                      <th>Convert</th>
                      <th>Editor</th>
                      <th>Delete</th>
                    </tr>
                  </thead>
                  <tbody>
                    {showAddRow && <NewShopRow seqNo={shops.length + 1} form={shopForm} setForm={setShopForm} onAdd={addShop} onCancel={() => setShowAddRow(false)} />}
                    {shops.map((s, i) => inSheet(s) && (
                      <ShopRow
                        key={s.id}
                        index={i + 1}
                        shop={s}
                        onEdit={(patch) => editShop(s.id, patch)}
                        onSave={(override) => saveShop(s.id, override)}
                        onDelete={() => deleteShop(s.id)}
                        onConvert={() => convertShop(s.id)}
                        onOpen={() => openEditor(s)}
                        onExport={() => setExportShop(s)}
                        stepEstimates={stepEstimates}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              </div>
              <div className="ws-card-foot">
                {isBatchConverting ? (
                  <BatchBanner
                    progress={monotonicProgress(batchView.peak, batchProgress)}
                    index={currentShopIndex}
                    total={batch?.total ?? 0}
                    remaining={batchView.eta ? batchView.eta.value : null}
                  />
                ) : (
                  <>
                    {batchSummary && <span className="ws-summary">{batchSummary}</span>}
                    <button
                      className="btn-outline-red"
                      onClick={() => setShowPrintFile(true)}
                      disabled={!printable.length}
                      title={printable.length ? "Create the Print Details summary sheet for converted shops" : "Convert at least one shop first"}
                    >
                      <Printer size={15} /> Create Print File
                    </button>
                    <button
                      className="btn-outline-dark"
                      onClick={() => setShowZip(true)}
                      disabled={!printable.length}
                      title={printable.length ? "JPG, CDR and PDF of every converted shop in one ZIP - download it or get a WeTransfer link" : "Convert at least one shop first"}
                    >
                      <FolderArchive size={15} /> Generate ZIP
                    </button>
                    <button
                      className="btn-outline-dark foot-split"
                      onClick={() => downloadUrl(cdrDownloadUrl(printable))}
                      disabled={!printable.length}
                      title={printable.length ? "The CDR (vector source) of every converted shop in one ZIP" : "Convert at least one shop first"}
                    >
                      <FileCode2 size={15} /> Download All CDRs
                    </button>
                    {convertible.length > 0 && (
                      <button className="btn-gradient" onClick={convertAll}>
                        <Play size={15} /> Convert All ({convertible.length})
                      </button>
                    )}
                  </>
                )}
              </div>
            </>
          )}
        </section>
      </div>
      {/* each modal in its own AnimatePresence: closing plays its exit animation before it unmounts */}
      <AnimatePresence>
        {showZip && <GenerateZipModal key="zip" shops={printable} onClose={() => setShowZip(false)} />}
      </AnimatePresence>
      <AnimatePresence>
        {showPrintFile && <PrintFileModal key="print" shops={printable} brand={brand} onClose={() => setShowPrintFile(false)} />}
      </AnimatePresence>
      <AnimatePresence>
        {exportShop && (
          <ExportModal
            key={exportShop.id}
            jobId={exportShop.job_id || (job && job.id)}
            shopId={exportShop.id}
            shopName={exportShop.name}
            onClose={() => setExportShop(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

// Start a download without leaving the page (the server names the file).
function downloadUrl(url) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// Empty state of the Shops Queue: an upload hero (click or drop a sheet) with the quick-start actions.
function ShopsEmptyState({ hasJob, importing, dragOver, setDragOver, onImportClick, onManual, onDropFile }) {
  return (
    <>
      <div
        className={"empty-hero" + (dragOver ? " drag" : "") + (hasJob ? "" : " disabled")}
        onClick={() => hasJob && onImportClick()}
        onDragOver={(e) => {
          e.preventDefault();
          if (hasJob) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (hasJob) onDropFile(e.dataTransfer.files[0]);
        }}
        role="button"
        tabIndex={hasJob ? 0 : -1}
        onKeyDown={(e) => hasJob && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onImportClick())}
      >
        <div className="hero-icon sheet">
          <FileSpreadsheet size={40} />
        </div>
        <div className="hero-title">Import Shop Details Sheet</div>
        <div className="hero-help">Supports .xlsx, .xls, and .csv files directly parsed in your browser via SheetJS</div>
        <div className="hero-actions" onClick={(e) => e.stopPropagation()}>
          <button className="btn-gradient" disabled={!hasJob || importing} onClick={onImportClick}>
            <FolderOpen size={15} /> {importing ? "Importing..." : "Import Excel File"}
          </button>
          <button type="button" className="hero-outline" disabled={!hasJob} onClick={onManual}>
            <Plus size={14} className="plus" /> Enter Manually
          </button>
        </div>
        {!hasJob && <div className="hero-note">Upload a master template first - shops convert from it.</div>}
      </div>
    </>
  );
}

// One editable row: name, width, height and the shared unit are live inputs (disabled while the shop is queued or
// converting, or once it is done - its output would no longer match the row); text/number fields save on blur, the unit
// on change.
function ShopRow({ shop, index, onEdit, onSave, onDelete, onConvert, onOpen, onExport, stepEstimates }) {
  const locked = shop.status === "queued" || shop.status === "converting" || shop.status === "done";
  const field = (key, label, extra = {}) => (
    <input
      value={shop[key] ?? ""}
      disabled={locked}
      aria-label={label}
      onChange={(e) => onEdit({ [key]: e.target.value })}
      onBlur={() => onSave()}
      {...extra}
    />
  );
  return (
    <tr data-shop-id={shop.id}>
      <td>{index}</td>
      <td className="name-cell">{field("name", "Shop name (English)", { type: "text", title: shop.name || undefined })}</td>
      <td>
        <div className="ta-cell">
          {field("shop_name_local", "Shop name (Tamil)", { type: "text", className: "ta-input" + (shop.ta_auto ? " auto" : ""), lang: "ta", placeholder: "\u2014",
            title: shop.shop_name_local || undefined })}
          {shop.ta_auto && shop.shop_name_local ? <span className="ta-auto" title="Written automatically from the English name - check it; typing here makes it yours">auto</span> : null}
        </div>
      </td>
      <td>{field("width", "Width", { type: "number", min: "0", step: "any" })}</td>
      <td>{field("height", "Height", { type: "number", min: "0", step: "any" })}</td>
      <td>
        <select
          value={shop.unit || "in"}
          disabled={locked}
          aria-label="Unit"
          onChange={(e) => {
            onEdit({ unit: e.target.value });
            onSave({ unit: e.target.value });
          }}
        >
          {UNITS.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
      </td>
      <td>
        <BoardTypeSelect
          value={shop.board_type}
          disabled={locked}
          onChange={(v) => {
            onEdit({ board_type: v });
            onSave({ board_type: v });
          }}
        />
      </td>
      <td>
        <ConvertCell shop={shop} onConvert={onConvert} onExport={onExport} stepEstimates={stepEstimates} />
      </td>
      <td>
        {shop.status === "done" ? (
          <button className="btn small icon-label" onMouseEnter={prefetchEditor} onFocus={prefetchEditor} onClick={onOpen}>
            <ExternalLink size={13} /> Open
          </button>
        ) : (
          "\u2014"
        )}
      </td>
      <td>
        <button
          className="icon-btn"
          disabled={shop.status === "queued" || shop.status === "converting"}
          onClick={onDelete}
          title="Remove this shop"
          aria-label="Remove this shop"
        >
          <Trash2 size={16} />
        </button>
      </td>
    </tr>
  );
}

// "Type of board": the standard list plus the row's own value when an import brought another one ("2 Nos Double Side GSB").
function BoardTypeSelect({ value, disabled, onChange }) {
  const v = value || DEFAULT_BOARD_TYPE;
  const options = BOARD_TYPES.includes(v) ? BOARD_TYPES : [...BOARD_TYPES, v];
  return (
    <select className="type-select" value={v} disabled={disabled} aria-label="Type of board" onChange={(e) => onChange(e.target.value)}>
      {options.map((t) => (
        <option key={t} value={t}>{t}</option>
      ))}
    </select>
  );
}

function NewShopRow({ seqNo, form, setForm, onAdd, onCancel }) {
  // the Tamil name follows the English one 300 ms after typing stops, until it is typed over (form.ta_auto)
  useEffect(() => {
    if (!form.ta_auto) return undefined;
    const t = setTimeout(() => setForm((f) => (f.ta_auto ? { ...f, shop_name_local: toTamil(f.name) } : f)), TA_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [form.name, form.ta_auto]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <tr className="new-shop-row">
      <td>{seqNo}</td>
      <td>
        <input autoFocus placeholder="Shop name" value={form.name} title={form.name || undefined}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          onKeyDown={(e) => e.key === "Enter" && onAdd()} />
      </td>
      <td>
        <input className="ta-input" lang="ta" placeholder="Tamil name (optional)" value={form.shop_name_local} onChange={(e) => setForm({ ...form, shop_name_local: e.target.value, ta_auto: false })} onKeyDown={(e) => e.key === "Enter" && onAdd()} />
      </td>
      <td>
        <input type="number" min="0" step="any" value={form.width} onChange={(e) => setForm({ ...form, width: e.target.value })} />
      </td>
      <td>
        <input type="number" min="0" step="any" value={form.height} onChange={(e) => setForm({ ...form, height: e.target.value })} />
      </td>
      <td>
        <select value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })}>
          {UNITS.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
      </td>
      <td>
        <BoardTypeSelect value={form.board_type} onChange={(v) => setForm({ ...form, board_type: v })} />
      </td>
      <td colSpan={3}>
        <button className="btn" onClick={onAdd}>
          Add shop
        </button>{" "}
        <button className="btn ghost" onClick={onCancel}>
          Cancel
        </button>
      </td>
    </tr>
  );
}

// The Convert column: a button while a shop is new, then a status badge (queued / processing with the eased % / completed).
function ConvertCell({ shop, onConvert, onExport, stepEstimates }) {
  // Called unconditionally (hooks can't be conditional) - it's a no-op
  // until `shop.status === "converting"` actually starts reporting steps.
  const smoothedPct = useSteppedProgress(CONVERT_STEPS, shop.step, shop.status === "done", stepEstimates);

  if (shop.status === "new") {
    return (
      <button className="btn small icon-label" onClick={onConvert}>
        <Play size={13} /> Convert
      </button>
    );
  }
  if (shop.status === "queued") return <span className="badge badge-queued" title="Waiting its turn - CorelDRAW converts one board at a time">Pending</span>;
  if (shop.status === "converting") {
    // Eased toward (but capped just below) the next real step threshold - never a straight jump to the backend's
    // last-polled value, and never 100% here (that only happens once status flips to "done").
    return <span className="badge badge-processing">Converting... {smoothedPct}%</span>;
  }
  if (shop.status === "done") {
    return (
      <span className="done-cell">
        <span className="badge badge-done">
          <CheckCircle2 size={13} /> Completed
        </span>
        <button className="icon-btn row-dl-btn" onClick={onExport} title="Download / export this shop's files" aria-label={`Export files for ${shop.name}`}>
          <Download size={16} />
        </button>
      </span>
    );
  }
  if (shop.status === "failed") {
    return (
      <div>
        <span className="badge badge-failed" title={shop.error}>
          Failed
        </span>{" "}
        <button className="btn small" onClick={onConvert}>
          Retry
        </button>
        {shop.error && <div className="err small-text" style={{ maxWidth: 260 }}>{shop.error}</div>}
      </div>
    );
  }
  return null;
}

// Replaces the Convert All button while a batch runs: circular % loader, "Converting i of n" + time estimate, linear bar.
function BatchBanner({ progress, index, total, remaining }) {
  const R = 22;
  const C = 2 * Math.PI * R;
  return (
    <div className="batch-banner" role="status" aria-live="polite">
      {/* the % is an HTML label centred over the ring (exactly centred, tabular digits, themed by CSS) - the old SVG text
          sat on a hand-tuned baseline and was drawn in near-black, invisible on the dark workspace */}
      <div className="batch-ring progress-circle-container" aria-hidden="true">
        <svg width="56" height="56" viewBox="0 0 56 56">
          <circle className="progress-circle-track" cx="28" cy="28" r={R} fill="none" strokeWidth="5" />
          <circle
            className="progress-circle-fill" cx="28" cy="28" r={R} fill="none" strokeWidth="5"
            strokeDasharray={C} strokeDashoffset={C * (1 - progress / 100)} transform="rotate(-90 28 28)"
          />
        </svg>
        <span className="progress-circle-text">{Math.round(progress)}%</span>
      </div>
      <div className="batch-text">
        <div>
          Converting {index} of {total} &bull; {fmtEta(remaining)}
        </div>
        <div className="batch-bar" aria-hidden="true">
          <div className="batch-bar-fill" style={{ width: `${progress}%` }} />
        </div>
      </div>
    </div>
  );
}
