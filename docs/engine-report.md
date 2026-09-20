# Engine report: content check, geometric accuracy, calibration, wide-board rule, confidence label

Covers Steps 1-5 of the follow-up engine task (dalmia only; Agarpathi untouched throughout).
Full detail and derivations are in `CLAUDE.md`; this is the reviewable summary plus the
Step 3b calibration result and its safety check against Step 5.

## What was measured

- **Step 1 - content check** (`backend/app/content_check.py`): does the generated file's
  text actually say the requested shop name / phone / GST. Live result: **13/13 dalmia
  boards CONTENT_OK**, including a board with no real GST line correctly coming back
  `NOT_CHECKED` rather than a false pass or fail.
- **Step 2 - geometric accuracy in mm** (`metrics.geometric_accuracy`): per-cluster
  position/size error in millimetres (not %-of-page) plus % of the real file's content
  area matched within 2/5/10mm, for all 12 non-trivial dalmia boards.
- **Step 3 - calibration tooling** (`tools/build_label_page.py`,
  `tools/calibrate_threshold.py`): a page to mark boards OK/NOT OK by eye, and a search
  over candidate metrics for the threshold that best reproduces those labels. Never writes
  `metrics_config.json` itself.
- **Step 3b - calibration run** (this report): see below.
- **Step 4 - wide-board scale gap**: leave-one-out check on the `panel_sequence`
  `size_table` (`tools/wide_board_loo.py`). **Negative result - no rule changed**: a
  flat-average alternative for vertical centring didn't consistently beat the existing
  per-aspect interpolation, and the one lone `sequence_4` sample (11-216) showed 2-5x
  worse leave-one-out error than the three `sequence_3` boards - left as a documented
  REVIEW case, not forced into a rule the data doesn't support.
- **Step 5 - confidence label** (`app/confidence.py`): GOOD/REVIEW/MANUAL, computable with
  only the requested size and brand (content check and layout checks are optional and can
  only make the label worse, never better). See the agreement check below.

## Step 3b: calibration result on your labels

Labels: `backend/dataset_analysis/labels/dalmia_labels.json`, 10 OK / 2 NOT_OK
(AHMED TRADERS 240x60, TAMILNADU STEELS marked NOT_OK). Full labels reproduced at the
bottom of this file for the record.

| Metric | Direction | Best threshold | Accuracy | Misclassified |
|---|---|---|---|---|
| `unmatched_ours` (new candidate) | lower_is_better | ≤ 3.5 | **92% (11/12)** | AHMED 240x60 |
| `visual_combined` | higher_is_better | ≥ 0.695 | 83% (10/12) | AHMED 216x48, AHMED 240x60 |
| `geo_max_position_error_mm` | lower_is_better | ≤ 838.7 | 83% (10/12) | AHMED 240x60, TAMILNADU |
| `geo_max_size_error_mm` | lower_is_better | ≤ 1905.0 | 83% (10/12) | AHMED 216x48, TAMILNADU |
| `geo_area_matched_within_5mm_pct` | higher_is_better | ≥ 16.1% | 83% (10/12) | SRI KAVI 180x48, AHMED 216x48 |
| content check (reference) | - | - | 83% (10/12) | AHMED 240x60, TAMILNADU |

**`unmatched_ours` (added this step) was found by looking at exactly these 2 negative
labels and gets 11/12 - the report and `calibrate_threshold.py`'s own output both carry an
explicit warning that this rests on only 2 NOT_OK examples and is not a validated rule.**
Every other metric caps at 83% because AHMED 216x48 (labelled OK) scores *worse* than both
NOT_OK boards on visual/position/size/area, while AHMED 240x60 (labelled NOT_OK) scores
*better* than several OK boards - no single threshold on any one of these metrics can
separate them without also misclassifying something else. `metrics_config.json` is
unchanged; the threshold search is stopped here per instruction.

## What 12 labels can and cannot show

- **Can**: confirm a metric is *not* trivially wrong (all candidates here agree on 10 of
  12 boards), surface a candidate worth watching (`unmatched_ours`), and - the main use
  made of it in Step 5 below - sanity-check that the confidence label never gives a false
  "GOOD" on a board a human rejected.
- **Cannot**: pick a production threshold. 12 boards, and only 2 of them negative, is too
  small a sample to fix a cutoff with any confidence - a single relabelled board could
  change the "best" threshold, none of it generalises to Agarpathi or to sizes/aspect
  ratios outside this exact set, and (shown directly above) the 2 negative examples
  actively contradict several metrics' rankings, which no amount of threshold-searching
  can paper over. More labels - especially more *NOT_OK* ones, and ones that don't invert
  the metrics' own ranking - would be needed before any number here should be trusted.

## Safety rule: never GOOD when the human said NOT OK

This is the property that actually matters for production use of the confidence label: a
board a human would reject must never be told "GOOD" (a false "MANUAL" or "REVIEW" costs a
manual look; a false "GOOD" ships a bad board unreviewed). Checked directly against your
12 labels:

| Shop | Target | Confidence label | Your label |
|---|---|---|---|
| SRI KAVI STEELS | 180x48in | REVIEW | OK |
| SEETHARAMAN TRADERS | 180x60in | REVIEW | OK |
| NR TRADERS | 120x48in | GOOD | OK |
| M Pandi | 120x48in | GOOD | OK |
| SAFI STEEL TRADERS | 144x60in | GOOD | OK |
| KRS STEELS | 144x60in | GOOD | OK |
| AHMED TRADERS | 216x48in | REVIEW | OK |
| **AHMED TRADERS** | **240x60in** | **REVIEW** | **NOT_OK** |
| **TAMILNADU STEELS** | **120x60in** | **MANUAL** | **NOT_OK** |
| AMAL TRADERS (x2) | 144x60in | GOOD | OK |
| A 1 SEVAN STAR ENTERPRISES | 120x48in | GOOD | OK |

- **GOOD-but-NOT_OK boards: 0.** The rule holds on all 12 labelled boards - both boards
  you rejected come back REVIEW and MANUAL, never GOOD.
- **MANUAL/REVIEW-but-OK boards: 3** (SRI KAVI 180x48, SEETHARAMAN, AHMED 216x48) - all
  three are tiled/wide boards you accepted that the system is nonetheless cautious about,
  because every real tiled sample so far (including these) has had large historical
  geometric error. This is the label being conservative in the safe direction, not an
  error to fix.

Label logic is unchanged from Step 5 (`REVIEW_ERROR_MM = 200.0`, aspect-range lookup) -
this result doesn't call for any change to it, since the one property that matters
(never GOOD when rejected) already holds.

## Labels used (for the record)

```json
{
  "02_-_180_X_48_Inch_-_GSB_-_SRI_KAVI_STEELS": "OK",
  "06_-_180_X_60_Inch_-_2_Nos_Double_Side_GSB_-_SEETHARAMAN_TRADERS": "OK",
  "03_-_120_X_48_Inch_-_2_Nos_Double_Side_GSB_-_NR_TRADERS": "OK",
  "05_-_120_X_48_Inch_-_Nonlit_-_M_Pandi": "OK",
  "09_-_144_X_60_Inch_-_GSB_-_SAFI_STEEL_TRADERS_PRIVATE_LIMITED": "OK",
  "10_-_144_X_60_Inch_-_2_Nos_Double_Side_GSB_-_KRS_STEELS": "OK",
  "11_-_216_X_48_Inch_-_GSB_-_AHMED_TRADERS": "OK",
  "11_-_240_X_60_Inch_-_2_Nos_Double_Side_GSB_-_AHMED_TRADERS": "NOT_OK",
  "12_-_120_X_60_Inch_-_2_Nos_Double_Side_GSB_-_TAMILNADU_STEELS": "NOT_OK",
  "13_-_144_X_60_Inch_-_2_Nos_Double_Side_GSB_-_AMAL_TRADERS": "OK",
  "13_-_144_X_60_Inch_-_GSB_-_AMAL_TRADERS": "OK",
  "14_-_120_X_48_Inch_-_GSB_-_A_1_SEVAN_STAR_ENTERPRISES": "OK"
}
```

Source file: `backend/dataset_analysis/labels/dalmia_labels.json`. Calibration output:
`backend/dataset_analysis/calibration/dalmia/proposal.{json,md}`.
