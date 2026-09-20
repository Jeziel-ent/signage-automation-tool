# Preparing a master file for the signage tool

The tool works far better on a master `.cdr` that is grouped and named
deliberately, instead of the hundreds of loose curves a typical export
leaves behind. This is a one-time, ~10 minute job per master. Steps below
are exact for **CorelDRAW 2019** (Object Manager docker + Ctrl+G).

## 1. Open the Object Manager

`Window → Dockers → Object Manager` (or `Ctrl+F3`). Keep it open for the
rest of this process — it's how you'll select, group and rename shapes.

## 2. Group every logo and product image into one object

Any logo, product photo, or decorative graphic that's currently made of
several separate curves/paths must become **one** object, or the tool will
scale and move each fragment independently and the logo will visually fall
apart when resized.

For each logo/graphic:

1. In the Object Manager, or on the canvas, click one piece of it, then
   **Shift+click** every other piece that belongs to the same logo/image
   (or rubber-band select the whole area with the pick tool if nothing
   else overlaps it).
2. Press **Ctrl+G** to group them.
3. Check the Object Manager: you should now see one `Group` entry where
   there used to be many separate shapes.

Repeat for every logo, badge, and product photo on the page. A bitmap
(photo) that's already a single object needs no grouping — just naming
(next step).

## 3. Name every object by its role

Double-click a shape's name in the Object Manager (or press F2) and rename
it with a prefix — lower case, no spaces needed after the prefix. The tool
matches by **prefix**, so `logo_main`, `logo_2`, `logo` are all treated the
same way; the suffix is just for your own reference.

| Prefix | Use it for | Behaviour when resized |
|---|---|---|
| `bg` | The full-page background rectangle/fill | Always stretched to exactly fill the new page |
| `frame` | A border that should keep its margin to the page edges | Margins to each edge kept fixed; the frame itself stretches between them |
| `fixed_` | Something that must stay its original size (e.g. a QR code, a certification mark) | Never resized; kept the same distance from whichever edge it started closest to |
| `shopname` | The shop name text (and its regional-language sibling, if there's a separate text box for it) | Content gets replaced per shop; repositioned into the gap when the design is tiled for a very wide/tall board |
| `logo_` / *(anything else)* | Product images, brand logos, decorative graphics | Scaled uniformly with the design; repeated ("tiled") automatically for boards much wider/taller than the master |

Rename **every** top-level object — don't leave loose, unnamed shapes. An
unnamed text object is guessed as `text`; an unnamed graphic is guessed as
`logo`; but a guess is a guess, and an untagged raw curve fragment (not
inside a group) will be treated as its own separate logo and moved
independently, which is the exact problem step 2 fixes.

## 4. Double-check nothing is left ungrouped

Select all (`Ctrl+A`) and look at the Object Manager's shape count for the
page. If you see far more entries than the number of logos + text boxes +
background you expect, something didn't get grouped in step 2 — scroll the
list and look for stray `Curve`/`Path` entries sitting outside any group.

## 5. Save

`File → Save As`, keep it as `.cdr`, and use it as the master upload for
this tool. You do not need to flatten layers or merge groups further — one
group per logo/image, correctly named, is exactly what the tool expects.

---

**Why this matters**: the tool reads each *top-level* object on the page
(it does not look inside groups) and moves/scales it as a rigid unit. A
group of 40 curves that make up a logo moves as one clean unit; 40
*ungrouped* curves each move toward their own individual center and the
logo visibly shatters. This was confirmed against real production masters —
see the top-level `CLAUDE.md` "Designer dataset analysis" section for
details.
