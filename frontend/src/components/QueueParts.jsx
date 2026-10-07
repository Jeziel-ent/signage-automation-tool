// Pieces of the Automation page's Shops Queue, kept apart so the page component stays readable (same markup, same behaviour).
import { AnimatePresence } from "framer-motion";
import BrandSelect from "./BrandSelect.jsx";
import AnimatedCount from "./AnimatedCount.jsx";
import { Building2, Eye, Layers, Store, FileCode2, FileSpreadsheet, Plus, FolderArchive, Play, Printer, RectangleHorizontal, RectangleVertical, Sparkles, Trash2 } from "lucide-react";
import UploadDropzone from "./UploadDropzone.jsx";
import ExportModal from "./ExportModal.jsx";
import PrintFileModal from "./PrintFileModal.jsx";
import GenerateZipModal from "./GenerateZipModal.jsx";
import ShopDownloadModal from "./ShopDownloadModal.jsx";
import DownloadAllModal from "./DownloadAllModal.jsx";
import PreviewGalleryModal from "./PreviewGalleryModal.jsx";
import QueueGalleryModal from "./QueueGalleryModal.jsx";
import MasterManagementModal from "./MasterManagementModal.jsx";
import { intelAllTitle } from "../utils/correctionsView.js";
import { mastersOf } from "../utils/masters.js";
import { fmtBytes } from "../utils/fileSize.js";

const plural = (n) => (n === 1 ? "" : "s");

/** The header badge text for the uploaded masters. */
export function masterBadgeText(masters, bothMasters, count) {
  if (bothMasters) return `${count} Masters Ready (${masters.landscape.length} L / ${masters.portrait.length} P)`;
  if (masters.landscape.length) return `Landscape only (${masters.landscape.length})`;
  if (masters.portrait.length) return `Portrait only (${masters.portrait.length})`;
  return "No master yet";
}

function unitNote(report) {
  return report.defaulted === report.added ? "No unit found in the sheet" : `No unit found for ${report.defaulted} row${plural(report.defaulted)}`;
}

/** What the last Excel import did: how many shops, the skipped rows, notes. */
export function ImportReport({ report, defaultUnit }) {
  const errors = report.errors;
  return (
    <output className="import-report">
      {report.added > 0 && <div>Successfully imported {report.added} shop{plural(report.added)} from {report.file}</div>}
      {errors.length > 0 && <div>{errors.length} row{plural(errors.length)} skipped:</div>}
      {report.sheets?.length > 0 && <div>Sheets: {report.sheets.join(", ")} - one tab each below.</div>}
      {report.translated > 0 && (
        <div>Tamil names were written automatically for {report.translated} shop{plural(report.translated)} (marked "auto") - please check them.</div>
      )}
      {report.defaulted > 0 && (
        <div>
          {unitNote(report)}
          {" - using the default unit ("}{defaultUnit}{"). The Unit dropdown in the table header changes them."}
        </div>
      )}
      {report.note && <div className="err">{report.note}</div>}
      {errors.length > 0 && (
        <ul className="err">
          {errors.map((e) => (
            <li key={`${e.sheet || ""}:${e.row}:${e.reason}`}>{e.sheet ? `${e.sheet}, row` : "Row"} {e.row}: {e.reason}</li>
          ))}
        </ul>
      )}
    </output>
  );
}

function tabLabel(t, noSheet) {
  if (t === "") return "All sheets";
  return t === noSheet ? "Added manually" : t;
}

/** One tab per Excel sheet the rows came from (+ "All sheets"). */
export function SheetTabs({ tabs, shops, current, noSheet, sheetOf, onPick }) {
  const real = tabs.filter((t) => t !== noSheet).length;
  return (
    <div className="sheet-tabs" role="tablist" aria-label="Excel sheets">
      <span className="sheet-tabs-label">{real} sheet{plural(real)}</span>
      {["", ...tabs].map((t) => {
        const n = t ? shops.filter((x) => sheetOf(x) === t).length : shops.length;
        const named = t && t !== noSheet;
        return (
          <button key={t || "all"} role="tab" aria-selected={current === t} className={"sheet-tab" + (current === t ? " active" : "")}
            onClick={() => onPick(t)} title={named ? `Sheet "${t}" of the imported workbook` : undefined}>
            {tabLabel(t, noSheet)} <span className="sheet-count">{n}</span>
          </button>
        );
      })}
    </div>
  );
}

const NEED_ONE = "Convert at least one shop first";

/** The Shops Queue footer: Print File / ZIP / Download All / Corel Intelligence / Convert All (or the batch banner while converting). */
export function QueueFooter({ banner, summary, printableCount, intelCount, convertibleCount, on }) {
  if (banner) return <div className="ws-card-foot">{banner}</div>;
  const has = printableCount > 0;
  return (
    <div className="ws-card-foot">
      {summary && <span className="ws-summary">{summary}</span>}
      <button className="btn-outline-red" onClick={on.printFile} disabled={!has}
        title={has ? "Create the Print Details summary sheet for converted shops" : NEED_ONE}>
        <Printer size={15} /> Create Print File
      </button>
      <button className="btn-outline-dark" onClick={on.zip} disabled={!has}
        title={has ? "JPG, CDR and PDF of every converted shop in one ZIP - download it or get a WeTransfer link" : NEED_ONE}>
        <FolderArchive size={15} /> Generate ZIP
      </button>
      <button className="btn-outline-dark foot-split" onClick={on.downloadAll} disabled={!has}
        title={has ? "Every converted shop's CDR, PDF, JPG or PNG in one ZIP - choose the format" : NEED_ONE}>
        <FileCode2 size={15} /> Download All
      </button>
      <button className="btn-outline-red" onClick={on.intelligence} disabled={!intelCount} title={intelAllTitle(intelCount)}>
        <Sparkles size={15} /> Corel Intelligence{intelCount ? ` (${intelCount})` : ""}
      </button>
      {convertibleCount > 0 && (
        <button className="btn-gradient" onClick={on.convertAll}>
          <Play size={15} /> Convert All ({convertibleCount})
        </button>
      )}
    </div>
  );
}

function MasterThumb({ m }) {
  if (m.preview_url) {
    return (
      <a className="mt-thumb" href={m.preview_url} target="_blank" rel="noreferrer" title="Open the preview">
        <img src={m.preview_url} alt={`${m.name} preview`} />
      </a>
    );
  }
  return <span className="mt-thumb empty" title={m.preview_error || "Preview not available"}>no preview</span>;
}

function MasterRow({ m, isDefault, onRemove }) {
  return (
    <li className="mt-row" data-master-id={m.id}>
      <MasterThumb m={m} />
      <div className="mt-info">
        <div className="mt-name" title={m.name}>
          {m.name}
          {isDefault && <span className="mt-default">default</span>}
        </div>
        <div className="mt-file" title={m.file_name}>{m.file_name || "master.cdr"} <span>{fmtBytes(m.file_size)}</span></div>
      </div>
      <button className="icon-btn mt-del" onClick={() => onRemove(m)} title={`Delete ${m.name}`} aria-label={`Delete ${m.name}`}>
        <Trash2 size={15} />
      </button>
    </li>
  );
}

/** One orientation's panel: a header (icon, name, size rule, count), one compact row per uploaded master, then a slim drop strip.
 *  Any number of masters per orientation; the first is that orientation's default. */
export function MasterPanel({ orientation: o, masters, brand, onRemove, onAdded }) {
  const list = mastersOf(masters, o);
  const land = o === "landscape";
  const Icon = land ? RectangleHorizontal : RectangleVertical;
  return (
    <div className={`mt-panel ${o}`} data-orientation={o}>
      <div className="mt-head">
        <span className="mt-icon"><Icon size={18} /></span>
        <div className="mt-head-text">
          <h3>{land ? "Landscape" : "Portrait"}</h3>
          <span className="mt-rule">{land ? "wider than tall · W:H ≥ 1.25" : "taller or square · W:H < 1.25"}</span>
        </div>
        <span className="mt-count" title={`${list.length} ${o} master${plural(list.length)}`}>{list.length}</span>
      </div>
      {list.length > 0 && (
        <ul className="mt-list">
          {list.map((m, i) => <MasterRow key={m.id} m={m} isDefault={i === 0 && list.length > 1} onRemove={onRemove} />)}
        </ul>
      )}
      <div className="mt-slot" data-add={o}>
        <UploadDropzone
          key={`${o}-${list.length}`}
          strip={{ empty: list.length === 0, text: list.length ? `Add another ${o} master` : `Drop a ${o} master (.cdr) here` }}
          disabled={!brand}
          brand={brand}
          orientation={o}
          label={`${o} master`}
          onUploaded={(body) => onAdded(body)}
        />
      </div>
    </div>
  );
}

/** The Shops Queue header buttons that show once there are shops: Gallery, Import Excel, Add Shop. */
export function QueueActions({ printableCount, hasJob, importing, onGallery, onImport, onAdd }) {
  const has = printableCount > 0;
  return (
    <>
      <button className="btn ghost gallery-btn" disabled={!has} onClick={onGallery}
        title={has ? "See every converted board in one gallery" : NEED_ONE}
        aria-label={`Open the gallery of ${printableCount} converted boards`}>
        <Eye size={15} /> Gallery <span className="count-badge">{printableCount}</span>
      </button>
      <button className="btn-gradient" disabled={!hasJob || importing} onClick={onImport}>
        <FileSpreadsheet size={16} /> {importing ? "Importing..." : "Import Excel (.xlsx / .csv)"}
      </button>
      <button className="btn ghost" disabled={!hasJob} onClick={onAdd}>
        <Plus size={15} /> Add Shop
      </button>
    </>
  );
}

/** Every modal of the page, each in its own AnimatePresence: closing plays its exit animation before it unmounts. */
export function QueueModals({ brand, masters, printable, job, show, close, galleryShop, downloadShop, exportShop, onAdded, onDelete, onMoreOptions }) {
  return (
    <>
      <AnimatePresence>{show.zip && <GenerateZipModal key="zip" shops={printable} onClose={close.zip} />}</AnimatePresence>
      <AnimatePresence>
        {show.manage && <MasterManagementModal key="masters" brand={brand} masters={masters} onAdded={onAdded} onDelete={onDelete} onClose={close.manage} />}
      </AnimatePresence>
      <AnimatePresence>{show.print && <PrintFileModal key="print" shops={printable} brand={brand} onClose={close.print} />}</AnimatePresence>
      <AnimatePresence>{show.gallery && <QueueGalleryModal key="queue-gallery" shops={printable} onClose={close.gallery} />}</AnimatePresence>
      <AnimatePresence>{galleryShop && <PreviewGalleryModal key={`pg-${galleryShop.id}`} shop={galleryShop} onClose={close.galleryShop} />}</AnimatePresence>
      <AnimatePresence>{show.downloadAll && <DownloadAllModal key="dl-all" shops={printable} onClose={close.downloadAll} />}</AnimatePresence>
      <AnimatePresence>
        {downloadShop && <ShopDownloadModal key={`dl-${downloadShop.id}`} shop={downloadShop} onClose={close.downloadShop} onMoreOptions={onMoreOptions} />}
      </AnimatePresence>
      <AnimatePresence>
        {exportShop && (
          <ExportModal key={exportShop.id} jobId={exportShop.job_id || job?.id} shopId={exportShop.id} shopName={exportShop.name} onClose={close.exportShop} />
        )}
      </AnimatePresence>
    </>
  );
}

/** Left side of the brand bar: the brand picker, "+ New Brand" (or its input) and Manage Masters. */
export function BrandControls({ brand, brands, setBrand, adding, setAdding, newBrand, setNewBrand, onAddBrand, nMasters, onManage }) {
  return (
    <div className="ws-bar-left">
      <span className="ws-bar-title">
        <Building2 size={14} /> Brand
      </span>
      <BrandSelect value={brand} options={brands} onChange={setBrand} />
      {adding ? (
        <>
          <input className="ws-input" autoFocus placeholder="New brand name" value={newBrand} onChange={(e) => setNewBrand(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onAddBrand()} />
          <button className="ws-cta" onClick={onAddBrand} disabled={!newBrand.trim()}>Add</button>
          <button className="ws-cta-ghost" onClick={() => setAdding(false)}>Cancel</button>
        </>
      ) : (
        <button className="ws-cta" onClick={() => setAdding(true)}>
          <Plus size={14} /> New Brand
        </button>
      )}
      <button className="ws-cta-ghost manage-masters-btn" onClick={onManage} disabled={!brand}
        title={brand ? `View, upload and delete ${brand}'s master templates` : "Pick a brand first"}>
        <Layers size={14} /> Manage Masters <span className="count-badge">{nMasters}</span>
      </button>
    </div>
  );
}

/** Right side of the brand bar: shops loaded and the masters badge. */
export function StatusBadges({ shopCount, bothMasters, masterBadge }) {
  return (
    <div className="ws-badges">
      <span className="ws-badge">
        <Store size={13} /> <AnimatedCount value={shopCount} /> Shop{plural(shopCount)} Loaded
      </span>
      <span className={"ws-badge" + (bothMasters ? " ok" : "")}>
        <span className={"ws-dot" + (bothMasters ? " live" : "")} aria-hidden="true" /> {masterBadge}
      </span>
    </div>
  );
}
