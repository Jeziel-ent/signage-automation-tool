"""Dump every shape in a .cdr to JSON via CorelDRAW COM, for studying real
designer files (see backend/tools/analyze_designs.py and the top-level
CLAUDE.md "Designer dataset analysis" section).

Read-only: opens the document, reads geometry/text, closes without saving.
Uses the same launch/timeout/prompt-suppression plumbing as CorelEngine
(see backend/app/corel_util.py) - SIGNAGE_REUSE_COREL=1, SIGNAGE_COREL_VISIBLE=1
and SIGNAGE_COREL_TIMEOUT_S all apply here too.

Usage:
    python dump_objects.py <path-to.cdr> [output.json]

If output.json is omitted, writes next to this script's caller as
<stem>.objects.json in the current directory.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import corel_util  # noqa: E402

SHAPE_TYPES = {
    0: "none", 1: "rectangle", 2: "ellipse", 3: "curve", 4: "polygon",
    5: "bitmap", 6: "text", 7: "group", 8: "selection", 9: "guideline",
    10: "blend_group", 11: "extrude_group", 12: "ole_object", 13: "contour_group",
    14: "linear_dimension", 15: "bevel_group", 16: "drop_shadow_group",
    17: "3d_object", 18: "artistic_media_group", 19: "connector",
    20: "mesh_fill", 21: "custom", 22: "custom_effect_group", 23: "symbol",
    24: "html_form_object", 25: "html_active_object", 26: "perfect_shape", 27: "eps",
}
CDR_MILLIMETER = 3


def _text_info(shape) -> dict:
    info: dict = {}
    try:
        story = shape.Text.Story
        info["text"] = story.Text
    except Exception as e:
        info["text_error"] = str(e)
    try:
        info["font"] = shape.Text.Story.Font
    except Exception:
        info["font"] = None  # mixed fonts within the shape, or not readable
    try:
        info["font_size"] = shape.Text.Story.Size
    except Exception:
        info["font_size"] = None
    return info


def _dump_shape(shape, layer_name: str, group_path: list[str], out: list[dict]):
    stype = SHAPE_TYPES.get(int(shape.Type), f"unknown_{shape.Type}")
    entry = {
        "name": shape.Name or "",
        "type": stype,
        "layer": layer_name,
        "group_path": list(group_path),
        "x": float(shape.LeftX),
        "y": float(shape.BottomY),
        "w": float(shape.SizeWidth),
        "h": float(shape.SizeHeight),
        "locked": bool(shape.Locked),
    }
    if stype == "text":
        entry.update(_text_info(shape))
    out.append(entry)

    if stype == "group":
        try:
            children = [shape.Shapes.Item(i) for i in range(1, shape.Shapes.Count + 1)]
        except Exception:
            children = []
        for child in children:
            _dump_shape(child, layer_name, group_path + [entry["name"]], out)


def dump(cdr_path: Path) -> dict:
    import pythoncom

    pythoncom.CoInitialize()
    app, we_launched_it, pid = corel_util.dispatch_corel()
    try:
        doc = corel_util.run_with_timeout(
            lambda: app.OpenDocument(str(cdr_path.resolve())), pid, "OpenDocument",
        )
        try:
            doc.Unit = CDR_MILLIMETER
            page = doc.ActivePage
            page_w, page_h = float(page.SizeWidth), float(page.SizeHeight)

            shapes: list[dict] = []
            for i in range(1, page.Layers.Count + 1):
                layer = page.Layers.Item(i)
                layer_name = layer.Name or f"layer_{i}"
                try:
                    top_shapes = [layer.Shapes.Item(j) for j in range(1, layer.Shapes.Count + 1)]
                except Exception:
                    top_shapes = []
                for s in top_shapes:
                    _dump_shape(s, layer_name, [], shapes)

            return {
                "file": cdr_path.name,
                "page_mm": {"w": page_w, "h": page_h},
                "shape_count": len(shapes),
                "shapes": shapes,
            }
        finally:
            # Best-effort: a cleanup failure (e.g. the COM server already died)
            # must not clobber a result we already successfully read.
            try:
                doc.Close()
            except Exception:
                pass
    finally:
        corel_util.quit_corel(app, we_launched_it, pid)
        pythoncom.CoUninitialize()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cdr_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(cdr_path.stem + ".objects.json")
    result = dump(cdr_path)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{result['shape_count']} shapes -> {out_path}")


if __name__ == "__main__":
    main()
