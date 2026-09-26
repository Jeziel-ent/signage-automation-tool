"""
Modular CorelDRAW signage layout engine.

Reads a JSON config describing a canvas size and a list of elements
(rectangle placeholders, text blocks, or images fitted into a bounding box)
and builds them in an active CorelDRAW document via win32com.client COM
automation.

Requires: pywin32 (`pip install pywin32`), CorelDRAW installed.

Usage:
    python generate_layout.py --config config.json --output-dir output
"""

import argparse
import json
import sys
from pathlib import Path

import pythoncom
import win32com.client

CDR_INCHES = 1  # cdrInch unit code

# cdrParagraphTextAlignment values.
_ALIGNMENTS = {
    "left": 1,
    "center": 2,
    "right": 3,
    "full": 4,
}


class CorelDrawEngine:
    """Builds a signage layout in CorelDRAW from a JSON config."""

    # CorelDRAW COM constants (verified against the generated typelib and
    # already-proven usage in backend/app/engines.py).
    CDR_PNG = 802
    CDR_CURRENT_PAGE = 1  # cdrExportRange.cdrCurrentPage (2 is cdrSelection)
    CDR_RGB_IMAGE = 4  # cdrImageType.cdrRGBColorImage

    def __init__(self, config_path, output_dir_override=None):
        config = self._load_config(Path(config_path))
        self._configure(config, output_dir_override, config_name=Path(config_path).stem)

    @classmethod
    def from_config_dict(cls, config_dict, output_dir_override=None, config_name="config"):
        """Build an engine from an already-loaded (and possibly per-record
        overridden) config dict, skipping the file read entirely."""
        engine = cls.__new__(cls)
        engine._configure(config_dict, output_dir_override, config_name=config_name)
        return engine

    def _configure(self, config, output_dir_override, config_name):
        self.config_path = Path(config_name)
        self.config = config

        self.canvas_w_in = float(self.config["canvas"]["width_in"])
        self.canvas_h_in = float(self.config["canvas"]["height_in"])

        output_dir = output_dir_override or self.config.get("output_dir", "output")
        self.output_dir = Path(output_dir).resolve()

        self.app = None
        self.doc = None

    # -- setup -----------------------------------------------------------

    @staticmethod
    def _load_config(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            print(f"ERROR: Could not read config file '{config_path}': {exc}")
            sys.exit(1)

    def connect(self):
        """Connect to a running CorelDRAW instance, with a Dispatch fallback."""
        try:
            self.app = win32com.client.GetActiveObject("CorelDRAW.Application")
        except Exception:
            try:
                self.app = win32com.client.Dispatch("CorelDRAW.Application")
                self.app.Visible = True
            except Exception as exc:
                print(f"ERROR: Could not connect to or launch CorelDRAW via COM: {exc}")
                sys.exit(1)

    def get_or_create_document(self):
        """Reuse the active document if one exists, otherwise create a fresh one
        sized to the configured canvas."""
        try:
            doc = self.app.ActiveDocument
            if doc is None:
                raise RuntimeError("No active document.")
        except Exception as exc:
            print(f"No active document found ({exc}); creating a new one...")
            try:
                doc = self.app.CreateDocument()
            except Exception as create_exc:
                print(f"ERROR: Could not create a new CorelDRAW document: {create_exc}")
                sys.exit(1)

        try:
            doc.Unit = CDR_INCHES
            doc.ActivePage.SetSize(self.canvas_w_in, self.canvas_h_in)
        except Exception as exc:
            print(f"WARNING: Could not set document unit/page size: {exc}")

        self.doc = doc
        return doc

    # -- coordinates -------------------------------------------------------

    def _box_to_coords(self, norm_box):
        """Convert a top-left-origin normalized [x, y, w, h] (0..1) box into
        CorelDRAW's bottom-left-origin page coordinates (in inches)."""
        norm_x, norm_y, norm_w, norm_h = norm_box
        abs_width = norm_w * self.canvas_w_in
        abs_height = norm_h * self.canvas_h_in
        abs_left = norm_x * self.canvas_w_in
        abs_top = self.canvas_h_in - (norm_y * self.canvas_h_in)
        abs_bottom = abs_top - abs_height
        abs_right = abs_left + abs_width
        return abs_left, abs_top, abs_right, abs_bottom

    # -- document hygiene ----------------------------------------------------

    def _remove_existing(self, element_id):
        """Delete any shape already named `element_id` on ANY layer, so
        re-running the engine (including after an element's `layer` was
        reassigned in the config) replaces it instead of stacking a
        duplicate on top of it."""
        try:
            for layer in self.doc.ActivePage.Layers:
                for shape in list(layer.Shapes):
                    if shape.Name == element_id:
                        shape.Delete()
        except Exception as exc:
            print(f"WARNING: Could not clean up existing shape '{element_id}': {exc}")

    def _ensure_layer(self, layer_name):
        """Return the named layer, creating it on the active page if it
        doesn't exist yet, and make it the active layer for shape creation."""
        try:
            for layer in self.doc.ActivePage.Layers:
                if layer.Name == layer_name:
                    layer.Activate()
                    return layer
        except Exception as exc:
            print(f"WARNING: Could not enumerate existing layers ({exc}); attempting to create '{layer_name}' anyway.")

        try:
            layer = self.doc.ActivePage.CreateLayer(layer_name)
            layer.Activate()
            return layer
        except Exception as exc:
            print(f"WARNING: Could not create layer '{layer_name}' ({exc}); falling back to the active layer.")
            return self.doc.ActiveLayer

    def _cleanup_empty_layers(self, keep_names):
        """Delete any layer that ended up with zero shapes and isn't one of
        the layers this config actually uses (e.g. CorelDRAW's default
        'Layer 1' on a freshly created document)."""
        try:
            layers = list(self.doc.ActivePage.Layers)
        except Exception as exc:
            print(f"WARNING: Could not enumerate layers for cleanup: {exc}")
            return

        if len(layers) <= 1:
            # CorelDRAW requires at least one layer on a page; never remove
            # the last one even if it happens to be empty.
            return

        for layer in layers:
            try:
                if layer.Name in keep_names:
                    continue
                # CorelDRAW's Guides layer (and similar special layers) can't
                # be deleted and isn't a "default" layer to clean up anyway.
                if layer.IsSpecialLayer:
                    continue
                if layer.Shapes.Count == 0:
                    print(f"Removing empty default layer '{layer.Name}'.")
                    layer.Delete()
            except Exception as exc:
                print(f"WARNING: Could not clean up layer '{getattr(layer, 'Name', '?')}': {exc}")

    # -- element builders --------------------------------------------------

    def _create_rectangle(self, element, layer):
        element_id = element["id"]
        left, top, right, bottom = self._box_to_coords(element["box"])
        style = self.config.get("defaults", {}).get("placeholder", {})

        try:
            shape = layer.CreateRectangle(left, top, right, bottom)
        except Exception as exc:
            print(f"WARNING: Failed to create rectangle for '{element_id}': {exc}")
            return None

        try:
            shape.Name = element_id
        except Exception as exc:
            print(f"WARNING: Failed to set name for '{element_id}': {exc}")

        try:
            r, g, b = style.get("outline_rgb", [255, 0, 0])
            shape.Outline.Color.RGBAssign(r, g, b)
            shape.Outline.Width = style.get("outline_width_in", 0.01)
        except Exception as exc:
            print(f"WARNING: Failed to set outline for '{element_id}': {exc}")

        try:
            r, g, b = style.get("fill_rgb", [200, 230, 255])
            shape.Fill.UniformColor.RGBAssign(r, g, b)
        except Exception as exc:
            print(f"WARNING: Failed to set fill for '{element_id}': {exc}")

        return shape

    def _create_text(self, element, layer):
        element_id = element["id"]
        left, top, right, bottom = self._box_to_coords(element["box"])
        defaults = self.config.get("defaults", {}).get("text", {})

        try:
            shape = layer.CreateParagraphText(left, top, right, bottom, element["text"])
        except Exception as exc:
            print(f"WARNING: Failed to create text shape for '{element_id}': {exc}")
            return None

        try:
            shape.Name = element_id
        except Exception as exc:
            print(f"WARNING: Failed to set name for '{element_id}': {exc}")

        font = element.get("font")
        size_pt = element.get("size_pt")
        alignment = element.get("alignment", defaults.get("alignment", "center"))

        try:
            if font:
                shape.Text.Story.Font = font
            if size_pt:
                shape.Text.Story.Size = size_pt
            shape.Text.Story.Alignment = _ALIGNMENTS.get(alignment, _ALIGNMENTS["center"])

            # CorelDRAW silently no-ops on an unrecognized font name rather
            # than raising, so read it back and warn instead of trusting it.
            if font:
                applied_font = shape.Text.Story.Font
                if applied_font != font:
                    print(
                        f"WARNING: Font '{font}' did not stick for '{element_id}' "
                        f"(reads back as '{applied_font}') - it may not be installed."
                    )
        except Exception as exc:
            print(f"WARNING: Failed to set font/size/alignment for '{element_id}': {exc}")

        try:
            r, g, b = element.get("color_rgb", defaults.get("color_rgb", [0, 0, 0]))
            shape.Fill.UniformColor.RGBAssign(r, g, b)
        except Exception as exc:
            print(f"WARNING: Failed to set text color for '{element_id}': {exc}")

        return shape

    def _create_image(self, element, layer):
        element_id = element["id"]
        asset_path = element.get("asset_path")

        if not asset_path:
            print(f"INFO: No asset_path configured for '{element_id}'; using a placeholder rectangle.")
            return self._create_rectangle(element, layer)

        left, top, right, bottom = self._box_to_coords(element["box"])

        try:
            abs_path = str(Path(asset_path).resolve())
            # Import's Options parameter is typed VT_DISPATCH; it must be
            # passed None explicitly, not Python's default of 0/omitted.
            shape = layer.Import(abs_path, 0, None)
            # Layer.Import's own return type is VT_VOID (verified against the
            # generated typelib) -- it does not hand back the new shape, but
            # the import leaves it as the document's active selection.
            if shape is None:
                shape = self.doc.ActiveShape
            if shape is None:
                raise RuntimeError("Import produced no shape (ActiveShape is None).")
        except Exception as exc:
            print(
                f"WARNING: Failed to import image for '{element_id}' from "
                f"'{asset_path}' ({exc}); using a placeholder rectangle instead."
            )
            return self._create_rectangle(element, layer)

        try:
            shape.Name = element_id
        except Exception as exc:
            print(f"WARNING: Failed to set name for '{element_id}': {exc}")

        box_w, box_h = right - left, top - bottom
        try:
            img_w, img_h = shape.SizeWidth, shape.SizeHeight
            if img_w and img_h:
                scale = min(box_w / img_w, box_h / img_h)
                new_w, new_h = img_w * scale, img_h * scale
                shape.SetSize(new_w, new_h)
                # Center the fitted image inside its bounding box.
                shape.LeftX = left + (box_w - new_w) / 2
                shape.BottomY = bottom + (box_h - new_h) / 2
        except Exception as exc:
            print(f"WARNING: Failed to fit image for '{element_id}' into its box: {exc}")

        return shape

    _BUILDERS = {
        "rectangle": _create_rectangle,
        "text": _create_text,
        "image": _create_image,
    }

    # -- run -----------------------------------------------------------------

    def _export_pdf(self, pdf_path):
        pdf_path = Path(pdf_path).resolve()
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # PublishToPDF takes only FileName in this CorelDRAW install's
            # typelib (verified; no color-mode/DPI params on this overload).
            # Color mode therefore follows the document's own fill colors,
            # not a forced CMYK conversion here - see the run() caveat note.
            self.doc.PublishToPDF(str(pdf_path))
            print(f"  Exported PDF to '{pdf_path}'.")
            return True
        except Exception as exc:
            print(f"  WARNING: Failed to export PDF to '{pdf_path}': {exc}")
            return False

    def _export_png(self, png_path, dpi):
        png_path = Path(png_path).resolve()
        png_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            export_filter = self.doc.ExportBitmap(
                str(png_path), self.CDR_PNG, self.CDR_CURRENT_PAGE, self.CDR_RGB_IMAGE,
                0, 0, dpi, dpi, 1, False, False, True, False, 0, None, None,
            )
            export_filter.Finish()
            print(f"  Exported PNG ({dpi} dpi, RGB) to '{png_path}'.")
            return True
        except Exception as exc:
            print(f"  WARNING: Failed to export PNG to '{png_path}': {exc}")
            return False

    def run(self, save_path=None, pdf_path=None, png_path=None, png_dpi=300):
        """Build the configured layout and save/export it.

        `save_path` overrides where the .cdr is saved (defaults to
        `<output_dir>/<config_name>_output.cdr`, the original single-run
        behaviour). `pdf_path`/`png_path` are optional and only exported
        when given, so plain single-run usage is unaffected.
        """
        pythoncom.CoInitialize()
        try:
            self.connect()
            self.get_or_create_document()

            created, failed = 0, 0
            used_layer_names = set()
            for element in self.config["elements"]:
                element_id = element["id"]
                element_type = element.get("type", "rectangle")
                builder = self._BUILDERS.get(element_type)
                if builder is None:
                    print(f"WARNING: Unknown element type '{element_type}' for '{element_id}'; skipping.")
                    failed += 1
                    continue

                layer_name = element.get("layer", "Default")
                used_layer_names.add(layer_name)
                layer = self._ensure_layer(layer_name)

                self._remove_existing(element_id)
                shape = builder(self, element, layer)
                if shape is not None:
                    created += 1
                else:
                    failed += 1

            self._cleanup_empty_layers(keep_names=used_layer_names)

            try:
                self.doc.ActiveWindow.Refresh()
            except Exception:
                pass

            if save_path is None:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                save_path = self.output_dir / f"{self.config_path.stem}_output.cdr"
            save_path = Path(save_path).resolve()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            saved_ok = False
            try:
                # SaveAs's Options parameter is typed VT_DISPATCH; it must be
                # passed None explicitly, not Python's default of 0/omitted.
                self.doc.SaveAs(str(save_path), None)
                print(f"Saved document to '{save_path}'.")
                saved_ok = True
            except Exception as exc:
                print(f"WARNING: Failed to save document to '{save_path}': {exc}")

            pdf_ok = self._export_pdf(pdf_path) if pdf_path else None
            png_ok = self._export_png(png_path, png_dpi) if png_path else None

            print(f"Done. Created {created} shape(s), {failed} failed.")
            return {"saved": saved_ok, "pdf": pdf_ok, "png": png_ok}

        finally:
            pythoncom.CoUninitialize()


def parse_args():
    parser = argparse.ArgumentParser(description="Build a signage layout in CorelDRAW from a JSON config.")
    parser.add_argument("--config", default="config.json", help="Path to the layout config JSON file.")
    parser.add_argument("--output-dir", default=None, help="Overrides the config's output_dir.")
    return parser.parse_args()


def main():
    args = parse_args()
    engine = CorelDrawEngine(args.config, output_dir_override=args.output_dir)
    engine.run()


if __name__ == "__main__":
    main()
