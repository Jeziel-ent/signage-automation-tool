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

## 6. Portrait or square boards need their own master — don't rely on the
   landscape one being auto-adapted

If you'll ever need this design on a **portrait or square** board (height
equal to or greater than width), prepare a **separate master `.cdr` for
that orientation** and upload it as its own brand/master, rather than
expecting the tool to reflow your landscape master automatically.

**Why**: this was checked against 9 real portrait/square boards from an
existing product line, comparing each one's actual designer file against
what this landscape master would have produced. Every single one of those
9 real files did two things a landscape master's own content cannot supply
on its own:

1. **Dropped the product-photo bitmap entirely.** None of the 9 kept it —
   there was simply no room for it once the page went portrait/square.
2. **Completely rearranged the remaining pieces** — the shop-name text
   (both languages) moved to the *top* of the page, and the logo/graphic
   groups moved to the *bottom*, stacked one above another instead of
   spread left-to-right. This is a genuine redesign, not a rotated or
   rescaled copy of the landscape layout — there's no automatic rule that
   can "figure out" a rearrangement like this from the landscape master's
   geometry alone.

Because of this, the tool has no way to produce a good portrait/square
board from a landscape-only master, no matter how the objects are grouped
or named — the missing bitmap and the top/bottom rearrangement are content
decisions, not something a resize rule can invent. A request for a
portrait or square size against a landscape-only master is flagged
**MANUAL** (needs a human to lay it out) rather than silently generating
something wrong.

**What to do**: build a second master specifically for portrait/square use
— same brand, same logos, same text, but laid out (and, if needed, missing
the product photo) the way you actually want a tall or square board to
look — and follow steps 1–5 above for it independently. If your product
line only ever ships landscape boards, you can skip this entirely.

## 7. Grouping rules, restated for logos, text and bitmaps specifically

These follow directly from steps 2–3 above, called out here because they
are the three asset types most often prepared inconsistently:

- **Logos and graphics**: always one group per logo (step 2), even if the
  logo is small or simple. An ungrouped multi-curve logo scales into
  visibly shattered fragments the moment the board size changes — this is
  not a rare edge case, it happens on *every* resize, not just extreme
  ones.
- **Text placeholders** (shop name, phone/GST, any other per-shop text):
  each one should be its **own** text object, tagged with the right prefix
  (`shopname` for the shop name — including a *second*, separately tagged
  `shopname` object if the design shows the name in two languages/scripts
  side by side, which is normal for bilingual signage). Don't merge two
  different pieces of text (e.g. the shop name and a tagline) into one text
  box — the tool replaces a whole text object's content at once, so mixing
  concerns in one box means one can't be updated without disturbing the
  other.
- **Bitmap assets** (product photos): keep as a single embedded bitmap,
  never split into multiple overlapping images — a master with more than
  one product-photo bitmap on the same page has no single, well-defined
  place for the tool to put a *replacement* photo (this exact ambiguity
  shows up as a real, currently-unhandled composition difference between
  otherwise-similar boards in production data). If a size genuinely needs a
  different photo treatment (e.g. a second, smaller product shot added only
  on very large boards), that is itself a reason to prepare a size-specific
  master rather than expecting one bitmap-based master to cover every case.

---

**Why this matters**: the tool reads each *top-level* object on the page
(it does not look inside groups) and moves/scales it as a rigid unit. A
group of 40 curves that make up a logo moves as one clean unit; 40
*ungrouped* curves each move toward their own individual center and the
logo visibly shatters. This was confirmed against real production masters —
see the top-level `CLAUDE.md` "Designer dataset analysis" section for
details.
