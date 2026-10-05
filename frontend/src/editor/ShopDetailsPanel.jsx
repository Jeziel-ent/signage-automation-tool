import { useEffect, useMemo, useState } from "react";
import { buildIndex } from "./ops.js";
import { ancestry } from "./model.js";
import { contactIds, findShopNameNode, textLabel, textNodes, toBoardText, toFieldText } from "./shopDetails.js";

const storeKey = (shopId) => `signage.shopNameNode.${shopId}`;
const readChoice = (shopId) => {
  try {
    return localStorage.getItem(storeKey(shopId));
  } catch {
    return null;
  }
};
const writeChoice = (shopId, id) => {
  try {
    if (id) localStorage.setItem(storeKey(shopId), id);
    else localStorage.removeItem(storeKey(shopId));
  } catch {
    /* storage unavailable: the choice lasts for this tab only */
  }
};

/**
 * A text field bound to one text object on the board. Every keystroke is previewed live on the canvas (`onPreview`, never an op);
 * the edit becomes a normal `text` op (undoable, replayed by Save and Generate) on blur or Enter. Shift+Enter adds a line; Esc
 * reverts. CorelDRAW's \r line breaks are kept.
 */
function BoardTextField({ node, locked, placeholder, onApply, onFocusNode, onPreview }) {
  const content = node.text.content ?? "";
  const [draft, setDraft] = useState(toFieldText(content));
  useEffect(() => setDraft(toFieldText(content)), [node.id, content]); // undo/redo or a canvas edit changed it

  const apply = () => {
    onPreview(null);
    const next = toBoardText(draft, content);
    if (next !== content) onApply(node.id, next);
  };
  return (
    <textarea
      className="ed-shop-input"
      rows={Math.min(3, Math.max(1, draft.split("\n").length))}
      value={draft}
      disabled={locked}
      title={locked ? "This text is locked" : "Enter to apply, Shift+Enter for a new line, Esc to revert"}
      placeholder={placeholder}
      onFocus={onFocusNode}
      onChange={(e) => {
        setDraft(e.target.value);
        onPreview({ id: node.id, content: toBoardText(e.target.value, content), font: node.text.font });
      }}
      onBlur={apply}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          e.currentTarget.blur();
        } else if (e.key === "Escape") {
          setDraft(toFieldText(content));
          onPreview(null);
        }
      }}
    />
  );
}

/**
 * Right panel: the shop's name and contact details as they appear ON THE BOARD. The shop record's name (from the Automation page)
 * is shown for reference; the board's shop-name text is found by tag / content match, or chosen once from the board's text objects
 * (remembered per shop) - a v2 conversion keeps the master's own shop-name text, so it usually has to be chosen. The Phone/GST text
 * is found by its label, like product_engine's contact slot.
 */
export default function ShopDetailsPanel({ scene, shop, shopId, onSelect, onCommit, onToast, onTextPreview }) {
  const preview = (p) => onTextPreview && onTextPreview(p);
  const idx = useMemo(() => buildIndex(scene), [scene]);
  const texts = useMemo(() => textNodes(scene), [scene]);
  const contacts = useMemo(() => contactIds(scene), [scene]);
  const [choice, setChoice] = useState(() => readChoice(shopId));
  const found = findShopNameNode(scene, shop && shop.name, choice);
  const candidates = texts.filter((t) => !contacts.includes(t.id));

  const entry = (id) => idx.get(id);
  const isLocked = (id) => {
    const e = entry(id);
    return !e || !!e.node.locked || !!(e.layer && e.layer.locked) || ancestry(idx, id).some((a) => a.locked);
  };
  const focusOnCanvas = (id) => {
    const chain = ancestry(idx, id);
    onSelect([chain.length ? chain.at(-1).id : id], null);
  };
  const applyText = (id, content) => {
    try {
      onCommit({ op: "text", id, content });
    } catch (e) {
      onToast(e.message);
    }
  };
  const choose = (id) => {
    setChoice(id || null);
    writeChoice(shopId, id || null);
    if (id) focusOnCanvas(id);
  };

  const nameNode = found && entry(found.id) ? entry(found.id).node : null;
  const recordName = shop && shop.name ? shop.name : "";

  return (
    <section className="ed-panel ed-shop">
      <h3>Shop details</h3>

      <div className="ed-shop-field">
        <label className="ed-shop-label" htmlFor="ed-shop-pick">Shop name</label>
        {recordName && <div className="ed-shop-record" title="The shop's name on the Automation page">Shop record: <strong>{recordName}</strong></div>}
        {candidates.length > 0 ? (
          <select
            id="ed-shop-pick"
            className="ed-shop-pick"
            value={found ? found.id : ""}
            onChange={(e) => choose(e.target.value)}
            title="Which text object on the board is the shop name"
          >
            <option value="">{found ? "" : "Choose the shop-name text on the board…"}</option>
            {candidates.map((t) => (
              <option key={t.id} value={t.id}>{textLabel(t)}</option>
            ))}
          </select>
        ) : (
          <p className="ed-shop-hint">This board has no text objects to edit.</p>
        )}
        {nameNode ? (
          <>
            <BoardTextField
              node={nameNode}
              locked={isLocked(nameNode.id)}
              placeholder="Enter shop name…"
              onApply={applyText}
              onPreview={preview}
              onFocusNode={() => focusOnCanvas(nameNode.id)}
            />
            <div className="ed-shop-row">
              {found.source === "tag" && <span className="ed-shop-hint">Tagged "shopname" in the master</span>}
              {found.source === "match" && <span className="ed-shop-hint">Matched by the shop's name</span>}
              {recordName && toFieldText(nameNode.text.content).trim() !== recordName.trim() && !isLocked(nameNode.id) && (
                <button className="ed-btn ed-shop-use" onClick={() => applyText(nameNode.id, toBoardText(recordName, nameNode.text.content))}>
                  Use “{recordName.length > 22 ? `${recordName.slice(0, 21)}…` : recordName}”
                </button>
              )}
            </div>
          </>
        ) : (
          candidates.length > 0 && <p className="ed-shop-hint">Pick the text that shows the shop name - the choice is remembered for this shop.</p>
        )}
      </div>

      <div className="ed-shop-field">
        <span className="ed-shop-label">Contact &amp; GST info</span>
        {contacts.length ? (
          contacts.map((id) => {
            const e = entry(id);
            return e ? (
              <BoardTextField key={id} node={e.node} locked={isLocked(id)} placeholder="Phone No. / GST NO." onApply={applyText} onPreview={preview} onFocusNode={() => focusOnCanvas(id)} />
            ) : null;
          })
        ) : (
          <p className="ed-shop-hint">No "Phone No." / "GST NO." text found on this board.</p>
        )}
      </div>
    </section>
  );
}
