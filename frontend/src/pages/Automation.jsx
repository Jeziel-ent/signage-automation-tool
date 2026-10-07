import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { finishedNotice, savedNotice, waitForExport } from "../utils/publishEdits.js";
import { learnedCount, nothingLearnedNotice, sparkleTitle } from "../utils/correctionsView.js";
import { CheckCircle2, Download, ExternalLink, Eye, FileSpreadsheet, FolderOpen, Play, Plus, Sparkles, Trash2 } from "lucide-react";
import "../components/MasterPanels.css";
import { useMasters } from "../context/MasterContext.jsx";
import { boardOrientation, fallbackWarning, masterCount as countMasters, masterFallback, masterLabel, mastersOf, primaryMaster, pickedMasterId, rowMasterIdsAuto } from "../utils/masters.js";
import { useSteppedProgress } from "../hooks/useSteppedProgress.js";
import { parseShopWorkbook } from "../utils/shopImport.js";
import { BOARD_TYPES, DEFAULT_BOARD_TYPE, LANGUAGES, applyDefaultUnit, followsEnglish, isDraft, nameEditPatch, rowSno, resetForNewMaster, shopPayload, toDraftRow, withFreshAutoTamil } from "../utils/shopPayload.js";
import { toTamil } from "../utils/tamilTranslit.js";
import { prefetchEditor } from "../utils/prefetchEditor.js";
import { batchFinishedText, batchStats, fmtEta, monotonicProgress, recordFinishes, smoothEta } from "../utils/batchStats.js";
import { importOutcome } from "../utils/importOutcome.js";
import { BrandControls, ImportReport, MasterPanel, StatusBadges, QueueActions, QueueModals, masterBadgeText, QueueFooter, SheetTabs } from "../components/QueueParts.jsx";
import { apiDetail, createShopFromDraft, fetchShopStatuses } from "../utils/shopApi.js";

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
  const [brand, setBrand] = useState("");
  const [addingBrand, setAddingBrand] = useState(false);
  const [newBrand, setNewBrand] = useState("");

  // Master templates: any number per orientation, from the server's registry (context/MasterContext.jsx), all optional. Each
  // row converts from the master it picks in the Master column (default: the first of its orientation); shops are saved on
  // the first master that exists and carry the ids of the masters they convert from (utils/masters.js rowMasterIds). The
  // server converts from the picked one when it matches the shop's TARGET orientation (W:H >= 1.25 = landscape; square
  // and portrait = portrait), else from that orientation's default, falling back to the other orientation.
  const registry = useMasters();
  const masters = useMemo(() => registry.forBrand(brand), [registry, brand]); // { landscape: [...], portrait: [...] }
  const [showManageMasters, setShowManageMasters] = useState(false);
  const landscapeJob = masters.landscape[0] || null;
  const portraitJob = masters.portrait[0] || null;
  const job = primaryMaster(masters);
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

  const brands = registry.brands; // shared with the Masters page (context/MasterContext.jsx)
  useEffect(() => {
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

  // ---- Corel Intelligence: the designers' editor corrections are always collected and used (server side, no switch, no approval);
  // a converted row's sparkle icon re-runs that board with the learned corrections for its size, the footer button does it for every row.
  const hidden = useLocation().pathname !== "/"; // this page stays mounted (just hidden) while another page is open - see App.jsx
  const [intelAvail, setIntelAvail] = useState({}); // done shop id -> number of learned corrections that fit its board

  async function addBrand() {
    const name = newBrand.trim();
    if (!name) return;
    try {
      await registry.addBrand(name);
    } catch (e) {
      setQueueNotice(e.message);
      return;
    }
    setBrand(name);
    setNewBrand("");
    setAddingBrand(false);
  }

  // Bumped whenever the masters change; an in-flight convert / Convert All started under older masters stops at its next step.
  const masterGen = useRef(0);
  const [queueNotice, setQueueNotice] = useState("");
  const [exportShop, setExportShop] = useState(null); // the done shop whose Export modal is open
  const [showGallery, setShowGallery] = useState(false); // the queue header's gallery of every converted board
  const [galleryShop, setGalleryShop] = useState(null); // the done shop whose preview gallery (Eye icon) is open
  const [showDownloadAll, setShowDownloadAll] = useState(false); // the footer's "Download All" format picker
  const [downloadShop, setDownloadShop] = useState(null); // the done shop whose quick Download popup is open ({...shop, no})
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

  // `m` = the registered master (POST /api/masters/upload's answer): add it to the registry, then reset the queue's conversions.
  const orientLabel = (m) => (m.orientation === "landscape" ? "Landscape" : "Portrait");
  function onMasterAdded(m) {
    registry.add(m);
    resetShopsQueueStatus(`${orientLabel(m)} master "${m.name}" added`);
  }

  // A master edited in the "Edit master" dialog (renamed, re-sized, orientation changed or its file replaced): a new file or orientation
  // invalidates the queue's conversions like an upload does; a rename or default size only refreshes the lists.
  function onMasterUpdated(m, { fileReplaced, orientationChanged } = {}) {
    registry.update(m);
    if (fileReplaced || orientationChanged) resetShopsQueueStatus(`${orientLabel(m)} master "${m.name}" updated`);
  }

  // Deletes the master from the registry (soft delete on the server: boards already made from it keep working).
  async function removeMaster(m) {
    const last = countMasters(masters) === 1;
    const ask = last && shops.length > 0
      ? `Delete "${m.name}"? It is the only master, so the shops list in this view is cleared (converted shops stay under Recently generated).`
      : `Delete ${orientLabel(m).toLowerCase()} master "${m.name}"? Boards already made from it stay under Recently generated.`;
    if (!window.confirm(ask)) return false;
    try {
      await registry.remove(m.id);
    } catch (e) {
      setQueueNotice(e.message);
      return false;
    }
    resetShopsQueueStatus(`${orientLabel(m)} master "${m.name}" deleted`);
    if (last) {
      setShops([]);
      setQueueNotice("");
    }
    return true;
  }

  // Another brand means another set of masters: rows saved on the old brand's masters are reset like after a master change.
  const shownBrand = useRef(brand);
  useEffect(() => {
    if (shownBrand.current === brand) return;
    const from = shownBrand.current;
    shownBrand.current = brand;
    if (from) resetShopsQueueStatus(`Brand changed to ${brand || "none"}`);
  }, [brand]); // eslint-disable-line react-hooks/exhaustive-deps

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
        ...rowMasterIdsAuto(f, masters),
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
      // every sheet of the workbook: each becomes a tab in the Shops Queue (a CSV / one-sheet file has no tabs). Browser only - no
      // request is made here: rows become local drafts (fully editable); a draft is saved to the server the moment it is converted.
      const { sheets } = await parseShopWorkbook(file, { defaultUnit });
      const { rows, report } = importOutcome(sheets, file.name, defaultUnit, masters);
      if (rows.length) setShops((s) => [...s, ...rows.map(toDraftRow)]);
      setImportReport(report);
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
      const data = await fetchShopStatuses(ids);
      if (!data) return; // a failed request or a network blip: try again on the next tick
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
    if (!r.ok) setShopError(await apiDetail(r, "Could not save the change"));
    else setShopError("");
  }

  async function deleteShop(shopId) {
    if (isDraft({ id: shopId })) return setShops((s) => s.filter((x) => x.id !== shopId));
    const r = await fetch(`/api/v2/shops/${shopId}`, { method: "DELETE" });
    if (r.ok) setShops((s) => s.filter((x) => x.id !== shopId));
    else setShopError(await apiDetail(r, "Could not remove the shop"));
  }

  async function convertShop(shopId, { useIntelligence = false } = {}) {
    // conversions keep the master's own fonts (no font fields are sent); a pending auto-Tamil update is applied first
    let current = withFreshAutoTamil(shops.find((x) => x.id === shopId) || null);
    if (!current) return null;
    setShopError("");
    const gen = masterGen.current;
    // the row's S.no in the table goes with it: every output file is named "<S.no> - <W> X <H> <Unit> - <Type> - <NAME>"
    // the master ids of THIS row: the chosen Master 1 / 2 of its orientation (utils/masters.js)
    const masterIds = { ...rowMasterIdsAuto(current, masters), sno: rowSno(current, shops.findIndex((x) => x.id === shopId)) };
    let id = shopId;
    if (isDraft(current)) {
      // First save: create the shop from the row's CURRENT (possibly edited) values, then convert that.
      const { saved, error } = await createShopFromDraft(job.id, current, { ...shopPayload(current), ...masterIds });
      if (error) {
        setShopError(error);
        return null;
      }
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
      body: JSON.stringify({ ...shopPayload(current), ...masterIds, ...(useIntelligence ? { use_intelligence: true } : {}) }),
    });
    if (gen !== masterGen.current) return null; // reset while the request was in flight: that row is gone from the queue
    if (r.ok || r.status === 409) {
      pollShop(id);
      return id;
    }
    setShops((s) => s.map((x) => (x.id === id ? { ...x, status: "new" } : x)));
    setShopError(`${current.name || "Shop"}: ${await apiDetail(r, "could not start the conversion")}`);
    return null;
  }

  // ---- sheet tabs: one per Excel sheet the rows came from (+ "Added manually" for rows without one), shown when there is
  // more than one group. Convert All works on the tab being viewed; S.no is the sheet's own serial, else the row's position in the whole queue.
  const NO_SHEET = "\u0000manual";
  const sheetOf = (x) => x.sheet_name || NO_SHEET;
  const sheetTabs = [...new Set(shops.map(sheetOf))];
  const showSheetTabs = sheetTabs.length > 1;
  const currentSheet = showSheetTabs && sheetTabs.includes(activeSheet) ? activeSheet : "";
  const inSheet = (x) => !currentSheet || sheetOf(x) === currentSheet;
  const convertible = shops.filter((x) => inSheet(x) && (x.status === "new" || x.status === "failed") && !x.skip_convert);
  // converted shops for "Create Print File", numbered by their S.no in the table (the sheet's own, else the position)
  const printable = shops.map((x, i) => ({ ...x, no: rowSno(x, i) })).filter((x) => x.status === "done" && !isDraft(x));

  // ---- batch conversion: Convert All swaps for a progress banner until every shop in the batch has finished
  const [isBatchConverting, setIsBatchConverting] = useState(false);
  const [batch, setBatch] = useState(null); // {startedAt, total, ids: [real shop ids that started], notStarted, finishTimes}
  // what the banner SHOWS: the % never below what it already showed in this batch, the ETA smoothed (see batchStats.js)
  const [batchView, setBatchView] = useState({ peak: 0, eta: null });
  const [now, setNow] = useState(Date.now());
  const [batchSummary, setBatchSummary] = useState("");

  // a row's own Convert button: warns first when it would be made from the other orientation's master
  function convertRow(shopId) {
    const row = shops.find((x) => x.id === shopId);
    const warning = row && fallbackWarning([row], masters);
    if (warning && !window.confirm(warning)) return null;
    return convertShop(shopId);
  }

  // done rows (saved on the server) and how many learned corrections fit each: refreshed when the set of done rows changes and
  // whenever an editor tab reports a save (that save may have just taught the model something)
  const doneIds = shops.filter((x) => x.status === "done" && !isDraft(x)).map((x) => x.id).join(",");
  const refreshIntel = () => {
    fetch(`/api/v2/intelligence/available?ids=${doneIds}`).then((r) => r.json()).then((d) => {
      setIntelAvail(d.available || {});
    }).catch(() => {});
  };
  const refreshIntelRef = useRef(refreshIntel);
  refreshIntelRef.current = refreshIntel; // the editor-save listener below is set up once and must call the latest version
  useEffect(refreshIntel, [doneIds]); // eslint-disable-line react-hooks/exhaustive-deps
  const intelRows = shops.filter((x) => x.status === "done" && intelAvail[x.id]);

  function applyIntelligence(shopId) {
    if (!intelAvail[shopId]) {
      setQueueNotice(nothingLearnedNotice());
      return null;
    }
    return convertShop(shopId, { useIntelligence: true });
  }

  async function applyIntelligenceAll() {
    const todo = intelRows.map((x) => x.id);
    if (!todo.length) return;
    setQueueNotice(`Corel Intelligence is re-running ${todo.length} board${todo.length === 1 ? "" : "s"} with learned corrections.`);
    const gen = masterGen.current;
    for (const id of todo) {
      if (gen !== masterGen.current) return;
      await convertShop(id, { useIntelligence: true });
    }
  }

  async function convertAll() {
    const todo = convertible.map((x) => x.id);
    if (!todo.length) return;
    const warning = fallbackWarning(convertible, masters);
    if (warning && !window.confirm(warning)) return;
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
      setBatchSummary(batchFinishedText(stats.done, stats.failed + batch.notStarted));
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
      if (shop) setQueueNotice(savedNotice(shop.name, m.publish));
      refreshIntelRef.current();
      if (shop && m.publish?.status === "building") {
        // tell the designer when the rebuild with the edits is finished
        waitForExport(m.jobId, m.shopId, m.publish.export_id).then((st) => setQueueNotice(finishedNotice(shop.name, st.status, st.error)));
      }
    };
    return () => ch.close();
  }, []);

  const bothMasters = !!(landscapeJob && portraitJob);
  const nMasters = countMasters(masters);
  const masterBadge = masterBadgeText(masters, bothMasters, nMasters);
  return (
    <div className="ws-page" hidden={hidden}>
      {/* BAR 1 - brand selector + status badges */}
      <header className="ws-bar">
        <BrandControls brand={brand} brands={brands} setBrand={setBrand} adding={addingBrand} setAdding={setAddingBrand} newBrand={newBrand}
          setNewBrand={setNewBrand} onAddBrand={addBrand} nMasters={nMasters} onManage={() => setShowManageMasters(true)} />
        <StatusBadges shopCount={shops.length}
          bothMasters={bothMasters} masterBadge={masterBadge} />
      </header>

      <div className="ws-stack">
        {/* TOP - master templates: a Landscape panel and a Portrait panel side by side */}
        <section className="ws-card ws-masters" aria-label="Master Templates">
          <div className="ws-card-head">
            <h2>Master Templates</h2>
            <button type="button" className="mc-secondary-toggle" onClick={() => setShowManageMasters(true)} disabled={!brand}>
              <Plus size={13} /> Add Master
            </button>
          </div>
          <div className="ws-card-body">
            <div className="mt-grid">{["landscape", "portrait"].map((o) => (
              <MasterPanel key={o} orientation={o} masters={masters} brand={brand} onRemove={removeMaster} onAdded={onMasterAdded} onUpdated={onMasterUpdated} />
            ))}</div>
            {registry.error && <p className="err">{registry.error}</p>}
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
              {/* hidden until a sheet has put shops in the queue; the empty-state card is the way in until then. The file input above stays
                  mounted - the empty-state "Import Excel File" button uses it. */}
              {shops.length > 0 && (
                <QueueActions printableCount={printable.length} hasJob={!!job} importing={importing}
                  onGallery={() => setShowGallery(true)} onImport={() => importInputRef.current?.click()} onAdd={() => setShowAddRow((v) => !v)} />
              )}
            </div>
          </div>

          {queueNotice && (
            <div className="import-report queue-notice" role="status">
              <span>{queueNotice}</span>
              <button className="icon-btn" onClick={() => setQueueNotice("")} aria-label="Dismiss">×</button>
            </div>
          )}
          {importReport && <ImportReport report={importReport} defaultUnit={defaultUnit} />}
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
              {showSheetTabs && <SheetTabs tabs={sheetTabs} shops={shops} current={currentSheet} noSheet={NO_SHEET} sheetOf={sheetOf} onPick={setActiveSheet} />}
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
                      <th>Language</th>
                      <th>Master</th>
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
                        index={rowSno(s, i)}
                        shop={s}
                        masters={masters}
                        onEdit={(patch) => editShop(s.id, patch)}
                        onSave={(override) => saveShop(s.id, override)}
                        onDelete={() => deleteShop(s.id)}
                        onConvert={() => convertRow(s.id)}
                        onOpen={() => openEditor(s)}
                        onExport={() => setDownloadShop({ ...s, no: rowSno(s, i) })}
                        onPreview={() => setGalleryShop(s)}
                        onIntelligence={() => applyIntelligence(s.id)}
                        intelCount={intelAvail[s.id] || 0}
                        stepEstimates={stepEstimates}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              </div>
              <QueueFooter
                banner={isBatchConverting ? (
                  <BatchBanner
                    progress={monotonicProgress(batchView.peak, batchProgress)}
                    index={currentShopIndex}
                    total={batch?.total ?? 0}
                    remaining={batchView.eta ? batchView.eta.value : null}
                  />
                ) : null}
                summary={batchSummary}
                printableCount={printable.length}
                intelCount={intelRows.length}
                convertibleCount={convertible.length}
                on={{ printFile: () => setShowPrintFile(true), zip: () => setShowZip(true), downloadAll: () => setShowDownloadAll(true), intelligence: applyIntelligenceAll, convertAll }}
              />
            </>
          )}
        </section>
      </div>
      <QueueModals
        brand={brand} masters={masters} printable={printable} job={job}
        show={{ zip: showZip, manage: showManageMasters, print: showPrintFile, gallery: showGallery, downloadAll: showDownloadAll }}
        galleryShop={galleryShop} downloadShop={downloadShop} exportShop={exportShop}
        close={{ zip: () => setShowZip(false), manage: () => setShowManageMasters(false), print: () => setShowPrintFile(false), gallery: () => setShowGallery(false),
          galleryShop: () => setGalleryShop(null), downloadAll: () => setShowDownloadAll(false), downloadShop: () => setDownloadShop(null), exportShop: () => setExportShop(null) }}
        onAdded={onMasterAdded} onDelete={removeMaster}
        onMoreOptions={() => { setExportShop(downloadShop); setDownloadShop(null); }}
      />
    </div>
  );
}

// Empty state of the Shops Queue: an upload hero (click or drop a sheet) with the quick-start actions.
function ShopsEmptyState({ hasJob, importing, dragOver, setDragOver, onImportClick, onManual, onDropFile }) {
  return (
      <div
        className={"empty-hero" + (dragOver ? " drag" : "") + (hasJob ? "" : " disabled")}
        onClick={(e) => hasJob && !e.target.closest(".hero-actions") && onImportClick()}
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
        onKeyDown={(e) => hasJob && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onImportClick())}
      >
        <div className="hero-icon sheet">
          <FileSpreadsheet size={40} />
        </div>
        <div className="hero-title">Import Shop Details Sheet</div>
        <div className="hero-help">Supports .xlsx, .xls, and .csv files directly parsed in your browser via SheetJS</div>
        <div className="hero-actions">
          <button className="btn-gradient" disabled={!hasJob || importing} onClick={onImportClick}>
            <FolderOpen size={15} /> {importing ? "Importing..." : "Import Excel File"}
          </button>
          <button type="button" className="hero-outline" disabled={!hasJob} onClick={onManual}>
            <Plus size={14} className="plus" /> Enter Manually
          </button>
        </div>
        {!hasJob && <div className="hero-note">Upload a master template first - shops convert from it.</div>}
      </div>
  );
}

// One editable row: name, width, height and the shared unit are live inputs (disabled while the shop is queued or
// converting, or once it is done - its output would no longer match the row); text/number fields save on blur, the unit
// on change.
function ShopRow({ shop, index, masters, onEdit, onSave, onDelete, onConvert, onOpen, onExport, onPreview, onIntelligence, intelCount, stepEstimates }) {
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
      <td className="name-cell">
        <NameField value={shop.name} label="Shop name (English)" disabled={locked} onChange={(v) => onEdit({ name: v })} onBlur={() => onSave()} />
      </td>
      <td>
        <div className="ta-cell">
          <NameField value={shop.shop_name_local} label="Shop name (Tamil)" disabled={locked} lang="ta" placeholder={"\u2014"}
            className={"ta-input" + (shop.ta_auto ? " auto" : "")} onChange={(v) => onEdit({ shop_name_local: v })} onBlur={() => onSave()} />
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
        <LanguageSelect
          value={shop.language}
          disabled={locked}
          hasTamil={!!(shop.shop_name_local || "").trim()}
          onChange={(v) => {
            onEdit({ language: v });
            onSave({ language: v });
          }}
        />
      </td>
      <td>
        <MasterCell shop={shop} masters={masters} locked={locked} onChoose={(id) => onEdit({ master_id: id })} />
      </td>
      <td>
        <ConvertCell shop={shop} onConvert={onConvert} onExport={onExport} onPreview={onPreview} onIntelligence={onIntelligence}
          intelCount={intelCount} stepEstimates={stepEstimates} />
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

// A shop-name cell: a one-line text box that WRAPS a long name onto more lines (growing in height) instead of cutting it
// off, so English and Tamil names are always fully readable. Enter confirms (blur) instead of adding a line, and line
// breaks in pasted text become spaces - a shop name is one line.
function NameField({ value, label, disabled, onChange, onBlur, className = "", lang, placeholder }) {
  const ref = useRef(null);
  const fit = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    // scrollHeight is the content + padding; the box is border-box, so its borders are added or the last line is clipped
    el.style.height = `${el.scrollHeight + (el.offsetHeight - el.clientHeight)}px`;
  };
  useLayoutEffect(fit, [value]);
  useEffect(() => {
    // re-fit when the column width changes (window resize, sidebar collapse, sheet tab switch)
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    let w = el.clientWidth;
    const ro = new ResizeObserver(() => {
      if (el.clientWidth !== w) {
        w = el.clientWidth;
        fit();
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <textarea
      ref={ref}
      rows={1}
      className={"name-input " + className}
      value={value ?? ""}
      disabled={disabled}
      aria-label={label}
      lang={lang}
      placeholder={placeholder}
      title={value || undefined}
      onChange={(e) => onChange(e.target.value.replace(/[\r\n]+/g, " "))}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          e.currentTarget.blur();
        }
      }}
      onBlur={onBlur}
    />
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

// Which shop-name line(s) the converted board shows. Tamil Only without a Tamil name prints the English name instead (the
// server never leaves the master's own Tamil name as the only name), and the option says so.
function LanguageSelect({ value, disabled, hasTamil, onChange }) {
  const v = LANGUAGES.some((l) => l.value === value) ? value : "both";
  return (
    <select value={v} disabled={disabled} aria-label="Language" onChange={(e) => onChange(e.target.value)}
      title={v === "ta" && !hasTamil ? "No Tamil name in this row - the English name will be printed" : "Which shop name(s) the board shows"}>
      {LANGUAGES.map((l) => (
        <option key={l.value} value={l.value}>{l.label}{l.value === "ta" && !hasTamil ? " (no Tamil name)" : ""}</option>
      ))}
    </select>
  );
}

// The row's master: its orientation (from its own width and height, the server's W:H >= 1.25 rule) and a dropdown of EVERY
// master of that orientation (any number). With one master it is shown and used automatically; with none, the row is
// flagged when it would be made from the other orientation's master.
function MasterCell({ shop, masters, locked, onChoose }) {
  const o = boardOrientation(shop);
  if (!o) return <span className="muted">{"—"}</span>;
  const list = mastersOf(masters, o);
  const tag = <span className={"orient-tag " + o} title={`${o === "landscape" ? "Landscape" : "Portrait"} board (W:H ${o === "landscape" ? "≥" : "<"} 1.25)`}>{o === "landscape" ? "L" : "P"}</span>;
  if (list.length < 2) {
    const only = list[0];
    const fallback = !only && masterFallback(shop, masters);
    if (fallback) {
      return (
        <span className="master-cell master-fallback" title={`No ${o} master is uploaded - this board would be made from the ${fallback} master and its layout will not fit. Upload a ${o} master.`}>
          {tag} <span className="fallback-warn">{"⚠"} {fallback} master</span>
        </span>
      );
    }
    return (
      <span className="master-cell" title={only ? `${only.name} - ${only.file_name || "master.cdr"}` : "No master uploaded yet"}>
        {tag} <span className="muted">{only ? only.name : "—"}</span>
      </span>
    );
  }
  const id = pickedMasterId(shop, masters) || "";
  const current = list.find((m) => m.id === id);
  return (
    <span className="master-cell">
      {tag}
      <select value={id} disabled={locked} aria-label={`${o} master`} onChange={(e) => onChoose(e.target.value || null)}
        title={current ? `${current.name} - ${current.file_name || "master.cdr"}` : "Auto: the best-fitting master for this board's size and type"}>
        <option value="">Auto (best match)</option>
        {list.map((m) => (
          <option key={m.id} value={m.id} title={m.file_name || undefined}>{masterLabel(m)}</option>
        ))}
      </select>
    </span>
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
      <td>
        <LanguageSelect value={form.language} hasTamil={!!(form.shop_name_local || "").trim()} onChange={(v) => setForm({ ...form, language: v })} />
      </td>
      <td colSpan={4}>
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
function ConvertCell({ shop, onConvert, onExport, onPreview, onIntelligence, intelCount = 0, stepEstimates }) {
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
        {shop.confidence && (
          <span className={`badge conf-badge conf-${shop.confidence.label}`} title={shop.confidence.reasons.join(" - ")}>
            {shop.confidence.label === "GOOD" ? "Exact size" : shop.confidence.label === "REVIEW" ? "Check" : "Draft"}
          </span>
        )}
        {learnedCount(shop.report?.layout?.intelligence) > 0 && (
          <span className="badge conf-badge conf-GOOD" title="Made with what designers corrected on this board size">
            Learned ({learnedCount(shop.report.layout.intelligence)})
          </span>
        )}
        <button className={"icon-btn row-dl-btn ci-row-btn" + (intelCount ? " ready" : "")} onClick={onIntelligence} aria-label={`Use Corel Intelligence on ${shop.name}`}
          title={sparkleTitle(intelCount)}>
          <Sparkles size={16} />
        </button>
        <button className="icon-btn row-dl-btn" onClick={onPreview} title="Preview the converted output" aria-label={`Preview the output of ${shop.name}`}>
          <Eye size={16} />
        </button>
        <button className="icon-btn row-dl-btn" onClick={onExport} title="Download Files (CDR, JPG, PNG, PDF)" aria-label={`Download files for ${shop.name}`}>
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
