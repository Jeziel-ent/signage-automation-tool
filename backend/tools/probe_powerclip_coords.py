"""Live probe: what coordinate system does CorelDRAW use for a PowerClip's children?

Opens a scratch COPY of an already-generated board (never the original, never
anything in signage_dataset/; the copy is closed unsaved), finds a PowerClip and
one of its children, and answers three questions with real COM calls:

  1. ABSOLUTE vs CONTAINER-RELATIVE: move the container by (dx, dy) and see
     whether the child's LeftX/BottomY shift by (dx, dy) (absolute page
     coordinates - contents follow the frame) or stay put (container-relative).
     Every PowerClip found so far sits at (0, 0), where the two are
     indistinguishable, so this moves the frame first to tell them apart.
  2. READ/WRITE SYMMETRY: does assigning LeftX/BottomY/SetSize on a child read
     back exactly what was assigned (what export_replay._set_bbox relies on)?
  3. VISIBLE EFFECT: does moving the child change the container's rendered
     image (a PNG export of the container, before vs after)?

Usage (one CorelDRAW job; refuses below the RAM floor):
    python tools/probe_powerclip_coords.py <generated.cdr> <container_id> <child_id> [--min-ram-gb 1.5]
e.g. python tools/probe_powerclip_coords.py data/jobs_v2/433ed15354d7/out/ca12d4768466/Shop_2.cdr s46 s48
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_util, export_replay  # noqa: E402

CDR_MM, CDR_PNG, CDR_SELECTION, CDR_RGB = 3, 802, 2, 4


def box(s):
    return [round(float(s.LeftX), 3), round(float(s.BottomY), 3), round(float(s.SizeWidth), 3), round(float(s.SizeHeight), 3)]


def render(doc, shape, path: Path) -> None:
    doc.ClearSelection()
    shape.AddToSelection()
    w = max(64, min(1600, round(float(shape.SizeWidth) / 25.4 * 96)))
    h = max(64, min(1600, round(float(shape.SizeHeight) / 25.4 * 96)))
    doc.ExportBitmap(str(path), CDR_PNG, CDR_SELECTION, CDR_RGB, w, h, 96, 96, 1, False, True, True, False, 0, None, None).Finish()
    doc.ClearSelection()


def replay_check(doc, container, shapes, a) -> int:
    """End to end: the ids in the editor's scene.json must resolve to the same shapes in a freshly
    opened document, and a replayed move + resize of one PowerClip child must verify cleanly."""
    import json
    from app import scene_ops
    scene = json.loads(Path(a.scene).read_text(encoding="utf-8"))
    node = scene_ops.find_node(scene, a.child_id)
    frm = {k: node[k] for k in "xywh"}
    to = {"x": frm["x"] + 30, "y": frm["y"] + 20, "w": frm["w"] * 0.8, "h": frm["h"] * 0.8}
    ops = [{"op": "move", "ids": [a.child_id], "dx": 12.0, "dy": -8.0},
           {"op": "resize", "ids": [a.child_id], "from": {"x": frm["x"] + 12, "y": frm["y"] - 8, "w": frm["w"], "h": frm["h"]}, "to": to}]
    print(f"scene child {a.child_id}: {node['type']} {[round(v, 2) for v in frm.values()]}  (COM resolves it to {box(shapes[a.child_id])})")
    render(doc, container, work_dir / "before.png")
    r = export_replay.Replayer(doc, scene, ops)          # raises ReplayError if scene ids and document disagree
    r.run()
    expected = scene_ops.apply_ops(scene, ops)
    v = export_replay.verify(doc.ActivePage, expected)
    print("after replay, child box in CorelDRAW:", box(shapes[a.child_id]), " expected:", [round(to[k], 3) for k in "xywh"])
    print("verify():", {"ok": v["ok"], "compared": v["compared"], "mismatches": v["mismatches"][:3]}, "warnings:", r.warnings)
    render(doc, container, work_dir / "after.png")
    from PIL import Image, ImageChops
    d = ImageChops.difference(Image.open(work_dir / "before.png").convert("RGB"), Image.open(work_dir / "after.png").convert("RGB")).convert("L")
    print(f"container render differs in {sum(1 for x in d.tobytes() if x > 8) / (d.width * d.height):.2%} of pixels")
    return 0 if v["ok"] else 2


work_dir: Path = Path(".")


def main() -> int:
    global work_dir
    ap = argparse.ArgumentParser()
    ap.add_argument("cdr")
    ap.add_argument("container_id")
    ap.add_argument("child_id")
    ap.add_argument("--min-ram-gb", type=float, default=1.5)
    ap.add_argument("--scene", help="the board's scene.json: replay a real move+resize of child_id from it through export_replay.Replayer and verify()")
    a = ap.parse_args()

    free = corel_util.check_memory()
    if free < a.min_ram_gb:
        print(f"REFUSED: {free:.2f} GB free < {a.min_ram_gb} GB floor")
        return 1
    corel_util.cleanup_orphaned_instances()

    work = Path(tempfile.mkdtemp(prefix="pc_probe_"))
    work_dir = work
    copy = work / "probe_copy.cdr"
    shutil.copyfile(a.cdr, copy)                      # never touch the original

    import pythoncom
    pythoncom.CoInitialize()
    app, launched, pid = corel_util.dispatch_corel()
    doc = None
    try:
        doc = corel_util.run_with_timeout(lambda: app.OpenDocument(str(copy.resolve())), pid, "open")
        doc.Unit = CDR_MM
        shapes, _ = export_replay.index_doc(doc.ActivePage)
        container, child = shapes[a.container_id], shapes[a.child_id]
        if a.scene:
            return replay_check(doc, container, shapes, a)
        print("container", a.container_id, box(container))
        print("child    ", a.child_id, box(child))
        print("PowerClip.Shapes of the container (id, type, box):")
        for c in export_replay._children(container.PowerClip):
            print("   ", f"s{int(c.StaticID)}", int(c.Type), box(c))

        render(doc, container, work / "before.png")

        # 1. absolute vs relative: move the frame, watch the child
        c0, k0 = box(container), box(child)
        container.Move(200.0, 100.0)
        c1, k1 = box(container), box(child)
        print(f"\n[1] container moved (+200,+100): frame {c0[:2]} -> {c1[:2]}; child {k0[:2]} -> {k1[:2]}")
        moved_with = abs((k1[0] - k0[0]) - 200) < 0.01 and abs((k1[1] - k0[1]) - 100) < 0.01
        stayed = abs(k1[0] - k0[0]) < 0.01 and abs(k1[1] - k0[1]) < 0.01
        print("    ->", "child coordinates are ABSOLUTE page coordinates (they follow the frame)" if moved_with else
              "child coordinates did NOT follow the frame: container-relative" if stayed else "inconclusive")
        container.Move(-200.0, -100.0)
        assert box(container) == c0, "frame not restored"

        # 2. write/read symmetry through the exact calls export_replay uses
        before = box(child)
        child.Move(60.0, 40.0)
        print(f"\n[2a] child.Move(60,40): {before[:2]} -> {box(child)[:2]}")
        want = {"x": before[0] + 25.0, "y": before[1] - 15.0, "w": before[2] * 0.5, "h": before[3] * 0.5}
        export_replay._set_bbox(child, want)
        got = box(child)
        print(f"[2b] _set_bbox to {[round(v, 3) for v in want.values()]} -> read back {got}")
        print("    ->", "SYMMETRIC" if all(abs(g - w) < 0.05 for g, w in zip(got, want.values())) else "NOT symmetric")

        # 3. visible effect on the rendered container
        render(doc, container, work / "after.png")
        from PIL import Image, ImageChops
        A, B = Image.open(work / "before.png").convert("RGB"), Image.open(work / "after.png").convert("RGB")
        diff = ImageChops.difference(A, B).convert("L")
        changed = sum(1 for v in diff.tobytes() if v > 8) / (diff.width * diff.height)
        print(f"\n[3] container render before vs after moving/resizing the child: {changed:.2%} of pixels differ")
        print("    ->", "VISIBLE" if changed > 0.001 else "NO visible change")
        return 0
    finally:
        try:
            if doc is not None:
                doc.Dirty = False
                doc.Close()
        except Exception:
            pass
        corel_util.quit_corel(app, launched, pid)
        pythoncom.CoUninitialize()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
