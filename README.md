# signage-automation-tool

Designer flow: upload master `.cdr` → pick brand → add shops with W × H → **Generate** →
per-shop `.cdr`, print PDF, PNG preview and an object position/size report.

## Structure

- `backend/` FastAPI. `app/layout.py` = pure layout rules (tested). `app/engines.py` = `CorelEngine` (COM, Windows) and `MockEngine` (dev).
- `frontend/` React + Vite.

## Run (dev)

```
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api)
```

Engine: `SIGNAGE_ENGINE=auto|corel|mock` (auto = Corel on Windows, mock elsewhere).
The mock engine ignores your real file and uses a demo scene, so you can test the UI anywhere.

## Real output (Windows + CorelDRAW)

Run the backend on a Windows PC with CorelDRAW installed and `pip install pywin32`.
Jobs run one at a time because CorelDRAW is a single desktop instance.
The COM calls in `CorelEngine` are written from the CorelDRAW object model but have NOT been run
against a real CorelDRAW yet. Test with one real master file first. Likely tweaks: the enum values
(`CDR_PNG`, `CDR_CURRENT_PAGE`), versions of CorelDRAW that require `doc.SaveAs` options, and
shapes inside multiple layers/pages.

## Layout rules

Name objects in CorelDRAW (Object Manager) with a prefix to control behaviour:

| Prefix | Behaviour |
|---|---|
| `bg` | stretches to fill the whole page |
| `frame` | keeps its margins, stretches between them |
| `fixed` | keeps size, keeps distance to nearest edge |
| `text`, `logo` | scaled uniformly, centre stays proportional |

Untagged objects: ≥90% of the page → `bg`; text → `text`; otherwise `logo`.
Warnings are flagged in the report (clamped, tiny scale, big aspect change).

## Next ideas

Multi-page masters, per-brand rule presets, Excel/CSV import of shops, auth, job history in a DB.

## Standalone CorelDRAW layout tools (`generate_layout.py`, `batch_runner.py`)

Separate from the `backend/app` FastAPI pipeline above: a small, ad-hoc set of
scripts at the project root for building a signage layout directly in
CorelDRAW from a JSON config, and batch-rendering it across a product
dataset. Windows + CorelDRAW + `pywin32` only, same as the main engine.

### `generate_layout.py` / `CorelDrawEngine`

Reads `config.json` (canvas size + a list of elements, each a `rectangle`
placeholder, `text` block, or `image` fitted into a bounding box) and builds
it in CorelDRAW.

```
python generate_layout.py --config config.json --output-dir output
```

- Reuses the active CorelDRAW document if one exists, otherwise creates one
  sized to the config's canvas.
- Elements are organized onto named layers (`"layer"` field per element in
  `config.json`); `_ensure_layer()` creates a layer if it doesn't exist yet.
  Empty non-configured layers (e.g. CorelDRAW's default `Layer 1`) are
  cleaned up after the run; the special `Guides` layer is never touched.
- Re-running against the same document replaces shapes by name instead of
  duplicating them (`_remove_existing()` searches every layer, not just the
  target one, so reassigning an element's `layer` in the config still
  cleans up its old copy).
- `CorelDrawEngine.from_config_dict(config_dict, config_name=...)` builds an
  engine from an already-loaded (and possibly per-record overridden) config
  dict instead of reading a file — this is what `batch_runner.py` uses to
  feed one modified copy of the base config per product without writing a
  temp file to disk.
- `engine.run(save_path=None, pdf_path=None, png_path=None, png_dpi=300)` —
  `save_path` overrides the default `<output_dir>/<config_name>_output.cdr`;
  `pdf_path`/`png_path` are optional and only exported when given (used by
  `batch_runner.py` for the CDR+PDF+PNG triple per product).

### `batch_runner.py`

Renders one signage variant per record in a product dataset, reusing the
same base `config.json` layout with the text/image fields substituted in.

```
python batch_runner.py --products data/products.json --config config.json --output-dir output
```

- `data/products.json`: a list of records (`product_id`, `title_en`,
  `title_ta`, `price`, `hero_image_path`). `build_product_config()` deep
  -copies the base config and substitutes these into the
  `footer_text_en`/`footer_text_ta`/`hero_product_box` elements.
- Output subfolders (`output/cdr`, `output/pdf`, `output/png`) are created
  up front and re-checked before each product, in case something removes
  one mid-run.
- Each product's build+export is wrapped in its own `try/except`: a failure
  on one product (bad asset path, a CorelDRAW COM error, a malformed
  record) is logged and the batch continues with the remaining products
  rather than aborting the whole run. The final summary lists every
  incomplete product with its recorded error.

### COM type-marshalling gotchas found building these (Windows + real CorelDRAW, verified against the generated typelib in `%TEMP%\gen_py\3.10\<guid>x0x21x2\`)

These are specific to this standalone tool but the same class of bug already
documented for the main `CorelEngine` in `CLAUDE.md` ("CorelEngine COM
notes") — worth checking there too if a new COM call starts throwing
`TypeError: The Python instance can not be converted to a COM object`.

- **`Document.SaveAs`'s `Options` parameter is `VT_DISPATCH`** (type code 9)
  — it must be passed `None` explicitly, not omitted/left at Python's
  default of `0`. Omitting it raises the `TypeError` above rather than a
  COM error, which is easy to misdiagnose as "the whole call is wrong."
- **`Layer.Import`'s `Options` parameter has the same `VT_DISPATCH` issue**,
  *and* separately, **`Layer.Import` returns nothing** (`VT_VOID` in the
  typelib) — unlike `CreateRectangle`/`CreateParagraphText`, which hand
  back the new `Shape` directly. The import still happens as a side
  effect, but the new shape has to be recovered via `doc.ActiveShape`
  (CorelDRAW leaves a just-imported object as the active selection)
  instead of the call's own return value.
- **`Document.ActiveLayer` is a read-only property** — there is no setter
  in the typelib. Switching which layer new shapes land on has to go
  through `Layer.Activate()` on the target layer object, not
  `doc.ActiveLayer = layer`.
- CorelDRAW's `Guides` layer (and other special layers) refuse `Delete()`
  (`Cannot delete a special layer`) — check `Layer.IsSpecialLayer` and skip
  it up front rather than relying on catching the COM exception.
- `Document.PublishToPDF` in this CorelDRAW install's typelib takes **only**
  a `FileName` argument — no color-mode (CMYK/RGB) or DPI parameter is
  exposed on this overload. PDF color mode follows whatever color model the
  shapes were actually filled in, not a forced conversion at export time.
