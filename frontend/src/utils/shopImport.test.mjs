import test from "node:test";
import assert from "node:assert/strict";
import * as XLSX from "xlsx";
import { cleanShopName, detectUnit, mapSheetRows, parseConvertFlag, parseShopFile, parseSize } from "./shopImport.js";

const summary = (r) => r.shops.map((s) => [s.name, s.width, s.height, s.unit]);
const fileOf = (aoa, name = "x.xlsx") => {
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), "Sheet1");
  return new File([XLSX.write(wb, { type: "array", bookType: "xlsx" })], name);
};

test("combined size cells in every notation", () => {
  const cases = [
    ["120 * 48", 120, 48, "in", "in"],
    ["10 x 4", 10, 4, "in", "in"],
    ["30X40", 30, 40, "in", "in"],
    ["10ft x 4ft", 10, 4, "ft", "ft"],
    ["12' * 4'", 12, 4, "ft", "ft"],
    ["10*4", 10, 4, "in", "in"],
    ["10.5 × 4.25", 10.5, 4.25, "in", "in"],
    ["10 by 4", 10, 4, "in", "in"],
    ["12-4", 12, 4, "in", "in"],
    ["10 x 4 feet", 10, 4, "ft", "ft"],
    ["10ft x 48in", 10, 48, "ft", "in"],
  ];
  for (const [cell, w, h, uw, uh] of cases) {
    assert.deepEqual(parseSize(cell), { width: w, height: h, width_unit: uw, height_unit: uh }, cell);
  }
  assert.equal(parseSize("no size here"), null);
  assert.equal(parseSize("0 x 4"), null);
});

test("unit detection: ft / feet / ' -> ft, default in", () => {
  assert.equal(detectUnit("Size (ft)"), "ft");
  assert.equal(detectUnit("FEET"), "ft");
  assert.equal(detectUnit("12'"), "ft");
  assert.equal(detectUnit("Width"), null);
  assert.equal(detectUnit("Board Size"), null);          // "in" inside a word must not count
  assert.equal(detectUnit("10 in"), "in");
});

test("single combined column ('10*4') under a Size header", () => {
  const r = mapSheetRows([["Shop Name", "Size"], ["A Stores", "10*4"], ["B Traders", "120 x 48"], ["C", "12' * 4'"]]);
  assert.deepEqual(r.missing, []);
  assert.deepEqual(summary(r), [["A Stores", 10, 4, "in"], ["B Traders", 120, 48, "in"], ["C", 12, 4, "ft"]]);
});

test("header unit applies to a combined column: 'Size (ft)'", () => {
  const r = mapSheetRows([["Store", "Size (ft)"], ["A", "10*4"], ["B", "8 x 3"]]);
  assert.deepEqual(summary(r), [["A", 10, 4, "ft"], ["B", 8, 3, "ft"]]);
});

test("separate Width / Height columns (also W/H, Breadth/Length, unit in the header or cell)", () => {
  let r = mapSheetRows([["Shop", "Width", "Height"], ["A", 12, 4], ["B", "30", "40"]]);
  assert.deepEqual(summary(r), [["A", 12, 4, "in"], ["B", 30, 40, "in"]]);
  r = mapSheetRows([["Outlet", "W", "H"], ["A", 12, 4]]);
  assert.deepEqual(summary(r), [["A", 12, 4, "in"]]);
  r = mapSheetRows([["Dealer", "Breadth (ft)", "Length (ft)"], ["A", 12, 4]]);
  assert.deepEqual(summary(r), [["A", 12, 4, "ft"]]);
  r = mapSheetRows([["Client", "Width", "Height"], ["A", "12 ft", "4 ft"], ["B", "12'", "48"]]);
  assert.deepEqual(summary(r), [["A", 12, 4, "ft"], ["B", 12, 48, "ft"]]);
});

test("custom client headers: Client / Particulars / Dealer / Outlet, Measurement / Recce / Dimension / Board", () => {
  for (const [nameH, sizeH] of [["Client Name", "Measurement"], ["Particulars", "Recce Size"], ["Dealer", "Dimensions"], ["Outlet", "Board Size"]]) {
    const r = mapSheetRows([["S.No", nameH, sizeH, "Phone"], [1, "Sri Kumar", "10 x 4", "9876543210"], [2, "Devi", "20*8", ""]]);
    assert.deepEqual(summary(r), [["Sri Kumar", 10, 4, "in"], ["Devi", 20, 8, "in"]], `${nameH}/${sizeH}`);
  }
});

test("shop-name preference: 'Shop Name' beats 'Contact Name' and a bare 'Name'; size headers are never a name", () => {
  const r = mapSheetRows([["Contact Name", "Name", "Shop Name", "Store Size"], ["Ravi", "x", "Real Shop", "10*4"]]);
  assert.equal(r.shops[0].name, "Real Shop");
  assert.deepEqual([r.shops[0].width, r.shops[0].height], [10, 4]);
});

test("no name header: falls back to the first text column (not the S.No, phone or size column)", () => {
  const r = mapSheetRows([["S.No", "Party", "Size", "Mobile"], [1, "Sri Kumar Stores", "10*4", "9876543210"], [2, "Devi Agencies", "12*4", "9123456780"]]);
  assert.deepEqual(r.missing, []);
  assert.deepEqual(summary(r), [["Sri Kumar Stores", 10, 4, "in"], ["Devi Agencies", 12, 4, "in"]]);
});

test("no size header: the column whose values look like sizes is used; headerless sheets work", () => {
  let r = mapSheetRows([["Shop Name", "Board"], ["A", "10x4"]]);
  assert.deepEqual(summary(r), [["A", 10, 4, "in"]]);
  r = mapSheetRows([["Party", "Details"], ["A", "10*4"], ["B", "12*4"]]);
  assert.deepEqual(summary(r), [["A", 10, 4, "in"], ["B", 12, 4, "in"]]);
  r = mapSheetRows([["Sri Kumar", "10*4"], ["Devi", "12*4"]]);                       // first row is DATA, not a header
  assert.deepEqual(summary(r), [["Sri Kumar", 10, 4, "in"], ["Devi", 12, 4, "in"]]);
});

test("a title above the header row is skipped; row numbers are spreadsheet rows; blank rows ignored", () => {
  const r = mapSheetRows([["ABC Signage - recce report", ""], ["", ""], ["Shop", "Size"], ["A", "10*4"], ["", ""], ["B", "oops"], ["", "3*3"]]);
  assert.equal(r.layout.headerRow, 3);
  assert.deepEqual(summary(r), [["A", 10, 4, "in"]]);
  assert.deepEqual(r.errors.map((e) => e.row), [6, 7]);
  assert.match(r.errors[0].reason, /could not read a size/);
  assert.match(r.errors[1].reason, /missing shop name/);
});

test("only name / width / height are read (phone, GST, address columns are ignored)", () => {
  const r = mapSheetRows([["Shop Name", "Size", "Phone", "GST", "Address"], ["A", "10*4", "9876543210", "G1", "Somewhere"]]);
  assert.deepEqual(Object.keys(r.shops[0]).sort((a, b) => a.localeCompare(b)), ["height", "name", "row", "unit", "unitSource", "width"]);
});

test("no usable columns -> missing is reported and nothing is imported", () => {
  assert.deepEqual(mapSheetRows([["Foo", "Bar"], ["1", "2"]]).missing.sort((a, b) => a.localeCompare(b)), ["name", "size"]);
  const onlyName = mapSheetRows([["Shop"], ["A"], ["B"]]);
  assert.deepEqual(onlyName.missing, ["size"]);
  assert.equal(onlyName.shops.length, 0);
});

test("real files: a single-column-size .xlsx, a Width/Height .xlsx, a custom-header .xlsx and a .csv", async () => {
  let r = await parseShopFile(fileOf([["Shop Name", "Size"], ["A", "10*4"], ["B", "120 x 48"], ["C", "30X40"], ["D", "10ft x 4ft"], ["E", "12' * 4'"]]));
  assert.equal(r.shops.length, 5);
  assert.deepEqual(r.shops.map((s) => [s.width, s.height, s.unit]), [[10, 4, "in"], [120, 48, "in"], [30, 40, "in"], [10, 4, "ft"], [12, 4, "ft"]]);

  r = await parseShopFile(fileOf([["Shop", "Width", "Height"], ["A", 12, 4], ["B", 30, 40], ["C", 120, 48]]));
  assert.deepEqual(summary(r), [["A", 12, 4, "in"], ["B", 30, 40, "in"], ["C", 120, 48, "in"]]);

  r = await parseShopFile(fileOf([["Sl", "Client", "Recce Size", "Remarks"], [1, "Kumar Traders", "10 x 4", "ok"], [2, "Devi Agencies", "8*3", ""]]));
  assert.deepEqual(summary(r), [["Kumar Traders", 10, 4, "in"], ["Devi Agencies", 8, 3, "in"]]);

  r = await parseShopFile(new File(["Store,Dimensions\nCSV Shop,10*4\nOther,12 x 4\n"], "s.csv"));
  assert.deepEqual(summary(r), [["CSV Shop", 10, 4, "in"], ["Other", 12, 4, "in"]]);
});

test("one shared unit: mixed or metric units are converted to inches, a single explicit unit applies to both", () => {
  const r = mapSheetRows([["Shop", "Size"], ["A", "10ft x 48in"], ["B", "300 cm x 100 cm"], ["C", "10 x 4 ft"], ["D", "10*4"], ["E", "1000mm x 500mm"]]);
  assert.deepEqual(summary(r), [["A", 120, 48, "in"], ["B", 118.11, 39.37, "in"], ["C", 10, 4, "ft"], ["D", 10, 4, "in"], ["E", 39.37, 19.69, "in"]]);
});

test("reads a local / Tamil shop-name column into shop_name_local, never as the English name", async () => {
  for (const header of ["Shop Name (Local)", "Shop Name Local", "Local Name", "Tamil Name", "Shop Name (Tamil)"]) {
    const r = mapSheetRows([[header, "Shop Name", "Size"], ["அனிஷ் ஸ்டோர்ஸ்", "ANISH STORES", "8x4 ft"], ["", "KALKEE", "6x6 ft"]]);
    assert.deepEqual(r.shops.map((s) => [s.name, s.shop_name_local]), [["ANISH STORES", "அனிஷ் ஸ்டோர்ஸ்"], ["KALKEE", undefined]], header);
  }
  const f = await parseShopFile(fileOf([["Store Name", "Tamil Name", "Width", "Height"], ["A", "அ", "11 ft", "6 ft"], ["B", "ஆ", "60", "75"]]));
  assert.deepEqual(f.shops.map((s) => [s.name, s.shop_name_local, s.width, s.height, s.unit]),
    [["A", "அ", 11, 6, "ft"], ["B", "ஆ", 60, 75, "in"]]);
});

test("a designer file name in the name column prints as just the shop name", () => {
  assert.equal(cleanShopName("73 - 60 X 75 Inch - Nonlit - SRI AMBIRAMI PROVISON STORES.cdr"), "SRI AMBIRAMI PROVISON STORES");
  assert.equal(cleanShopName("66 - 8 X 4 Feet - Nonlit - VASANTHAM ENTERPRISES - Copy.cdr"), "VASANTHAM ENTERPRISES");
  assert.equal(cleanShopName("Anish - Stores"), "Anish - Stores");
  const r = mapSheetRows([["Shop Name", "Size"], ["67 - 6 X 6 Feet - Nonlit - KALKEE POOJA STORES.cdr", "6x6 ft"]]);
  assert.equal(r.shops[0].name, "KALKEE POOJA STORES");
});

test("Default Unit: a sheet with no unit anywhere takes it, and the rows are tagged as following it", () => {
  const rows = [["Shop Name", "Size"], ["A", "10*4"], ["B", "12 x 5"]];
  const ft = mapSheetRows(rows, { defaultUnit: "ft" });
  assert.deepEqual(summary(ft), [["A", 10, 4, "ft"], ["B", 12, 5, "ft"]]);
  assert.deepEqual(ft.shops.map((s) => s.unitSource), ["default", "default"]);
  // no option -> inches, exactly as before
  assert.deepEqual(summary(mapSheetRows(rows)), [["A", 10, 4, "in"], ["B", 12, 5, "in"]]);
  // an invalid default falls back to inches
  assert.equal(mapSheetRows(rows, { defaultUnit: "cm" }).shops[0].unit, "in");
});

test("Default Unit never overrides a unit the file gives: in the cell, a header hint or a Unit column", () => {
  const cell = mapSheetRows([["Shop Name", "Size"], ["A", "120 x 48 in"], ["B", "10*4"]], { defaultUnit: "ft" });
  assert.deepEqual(summary(cell), [["A", 120, 48, "in"], ["B", 10, 4, "ft"]]);
  assert.deepEqual(cell.shops.map((s) => s.unitSource), ["excel", "default"]);
  const header = mapSheetRows([["Shop Name", "Size (inches)"], ["A", "120*48"]], { defaultUnit: "ft" });
  assert.deepEqual(summary(header), [["A", 120, 48, "in"]]);
  assert.equal(header.shops[0].unitSource, "excel");
  const wh = mapSheetRows([["Shop", "Width (ft)", "Height (ft)"], ["A", "10", "4"]], { defaultUnit: "in" });
  assert.deepEqual(summary(wh), [["A", 10, 4, "ft"]]);
});

test("a separate Unit / UOM column sets each row's unit; a blank unit cell falls back to the default", () => {
  const r = mapSheetRows([
    ["S.No", "Shop Name", "Width", "Height", "Unit"],
    [1, "A", "10", "4", "Feet"],
    [2, "B", "120", "48", "Inches"],
    [3, "C", "8", "3", ""],
    [4, "D", "96", "36", "in"],
  ], { defaultUnit: "ft" });
  assert.equal(r.layout.unitCol, 4);
  assert.deepEqual(summary(r), [["A", 10, 4, "ft"], ["B", 120, 48, "in"], ["C", 8, 3, "ft"], ["D", 96, 36, "in"]]);
  assert.deepEqual(r.shops.map((s) => s.unitSource), ["excel", "excel", "default", "excel"]);
  // with a combined Size column too, and a unit written in the cell beating the Unit column
  const c = mapSheetRows([["Shop Name", "Size", "UOM"], ["A", "10*4", "ft"], ["B", "120*48 in", "ft"]], { defaultUnit: "in" });
  assert.deepEqual(summary(c), [["A", 10, 4, "ft"], ["B", 120, 48, "in"]]);
});

test("a 'Units' column of quantities is not mistaken for a unit column", () => {
  const r = mapSheetRows([["Shop Name", "Size", "Units"], ["A", "10*4", "2"], ["B", "12*5", "1"]], { defaultUnit: "ft" });
  assert.equal(r.layout.unitCol, -1);
  assert.deepEqual(summary(r), [["A", 10, 4, "ft"], ["B", 12, 5, "ft"]]);
});

test("combined sizes: every common spelling, thousands separators, comma only for a bare size", async () => {
  const { parseSize } = await import("./shopImport.js");
  const p = (s) => { const r = parseSize(s, null); return r && [r.width, r.height, r.width_unit, r.height_unit]; };
  assert.deepEqual(p("10*4"), [10, 4, null, null]);
  assert.deepEqual(p("10x4"), [10, 4, null, null]);
  assert.deepEqual(p("10 X 4 FT"), [10, 4, "ft", "ft"]);
  assert.deepEqual(p("120x48 IN"), [120, 48, "in", "in"]);
  assert.deepEqual(p("10' x 4'"), [10, 4, "ft", "ft"]);
  assert.deepEqual(p('120" x 48"'), [120, 48, "in", "in"]);
  assert.deepEqual(p("10.5 × 4.2"), [10.5, 4.2, null, null]);
  assert.deepEqual(p("3,000 x 1,200 mm"), [3000, 1200, "mm", "mm"]);
  assert.deepEqual(p("10, 4"), [10, 4, null, null]);
  assert.equal(p("No 5, 3rd Cross"), null);
});

test("vendor / actual width headers, a title block and a two-row Size > Width | Height header", async () => {
  const { mapSheetRows } = await import("./shopImport.js");
  const a = mapSheetRows([["Project X"], [], ["Vendor Name", "Actual Width", "Actual Height"], ["A", "10", "4"]]);
  assert.deepEqual(a.missing, []);
  assert.deepEqual(a.shops.map((s) => [s.name, s.width, s.height]), [["A", 10, 4]]);
  const b = mapSheetRows([
    ["Signage list"], [""],
    ["S.No", "Shop Name", "Size", "", "Unit"],
    ["", "", "Width", "Height", ""],
    [1, "Sri Kumar", "12", "5", "ft"],
    [2, "Anish", "120", "48", "in"],
  ]);
  assert.deepEqual(b.missing, []);
  assert.deepEqual(b.shops.map((s) => [s.name, s.width, s.height, s.unit]), [["Sri Kumar", 12, 5, "ft"], ["Anish", 120, 48, "in"]]);
  const c = mapSheetRows([["Shop", "Ht", "Wd"], ["X", "4", "10"]]);
  assert.deepEqual(c.shops.map((s) => [s.width, s.height]), [[10, 4]]);
});

test("parseShopFile skips a cover sheet and reads the sheet that holds the shops", async () => {
  const XLSX = await import("xlsx");
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([["Summary"], ["Total boards", 2]]), "Cover");
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([["Shop Name", "Board Size"], ["A", "10 X 4 FT"], ["B", "120x48 IN"]]), "Shops");
  const { parseShopFile } = await import("./shopImport.js");
  const r = await parseShopFile(new File([XLSX.write(wb, { type: "array", bookType: "xlsx" })], "w.xlsx"));
  assert.equal(r.sheet, "Shops");
  assert.deepEqual(r.shops.map((s) => [s.name, s.width, s.height, s.unit]), [["A", 10, 4, "ft"], ["B", 120, 48, "in"]]);
});

test("board type from a Type column or from a designer file name in the name column", async () => {
  const { mapSheetRows } = await import("./shopImport.js");
  const a = mapSheetRows([["Shop Name", "Size", "Type of Board"], ["A", "10x4", "front lit"], ["B", "10x4", ""]]);
  assert.deepEqual(a.shops.map((s) => [s.name, s.board_type]), [["A", "Frontlit"], ["B", undefined]]);
  const b = mapSheetRows([["Shop Name", "Size"], ["76 - 125 X 48 Inch - Nonlit - SRI KANNIYAMMAN.cdr", "125x48"]]);
  assert.deepEqual(b.shops.map((s) => [s.name, s.board_type]), [["SRI KANNIYAMMAN", "Nonlit"]]);
});

test("parseShopWorkbook: every sheet with shops, each row tagged with its sheet; a cover sheet reported as missing", async () => {
  const XLSX = await import("xlsx");
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([["Summary"], ["Boards", 3]]), "Cover");
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([["Shop Name", "Size"], ["A", "10x4 ft"], ["B", "12x4 ft"]]), "Chennai");
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([["Shop Name", "Width", "Height"], ["C", "120", "48"]]), "Madurai");
  const { parseShopWorkbook } = await import("./shopImport.js");
  const { sheets } = await parseShopWorkbook(new File([XLSX.write(wb, { type: "array", bookType: "xlsx" })], "w.xlsx"));
  assert.deepEqual(sheets.map((s) => [s.name, s.shops.length, s.missing.length > 0]), [["Cover", 0, true], ["Chennai", 2, false], ["Madurai", 1, false]]);
  assert.deepEqual(sheets[1].shops.map((s) => [s.name, s.sheet_name]), [["A", "Chennai"], ["B", "Chennai"]]);
  // a one-sheet workbook / CSV: no sheet names (no tabs)
  const one = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(one, XLSX.utils.aoa_to_sheet([["Shop Name", "Size"], ["A", "10x4"]]), "Sheet1");
  const r = await parseShopWorkbook(new File([XLSX.write(one, { type: "array", bookType: "xlsx" })], "o.xlsx"));
  assert.equal(r.sheets[0].shops[0].sheet_name, undefined);
});

test("the unit in a designer file name used as the shop name wins over the default for a unitless size", async () => {
  const { unitFromName } = await import("./shopImport.js");
  assert.equal(unitFromName("75 - 6 X 4 Feet - Nonlit - SRI RAM NADUMARUTHU KADAI.cdr"), "ft");
  assert.equal(unitFromName("73 - 60 X 75 Inch - Nonlit - SRI AMBIRAMI"), "in");
  assert.equal(unitFromName("Anish Stores"), null);
  const r = mapSheetRows([["Shop Name", "Size"], ["75 - 6 X 4 Feet - Nonlit - SRI RAM", "6 X 4"], ["Plain", "8 x 4"],
    ["68 - 7 X 5 Feet - Nonlit - M.S POOJA STORES", "7 X 5 in"]], { defaultUnit: "in" });
  assert.deepEqual(r.shops.map((s) => [s.name, s.width, s.height, s.unit, s.unitSource]),
    [["SRI RAM", 6, 4, "ft", "excel"], ["Plain", 8, 4, "in", "default"], ["M.S POOJA STORES", 7, 5, "in", "excel"]]);
});

test("an explicit S.No column is kept per row; blank cells and sheets without one fall back to position", async () => {
  const { rowSno } = await import("./shopPayload.js");
  for (const h of ["S.No", "S. No.", "Sl.No", "SNo", "SLNO", "Serial No", "Sr. No", "#"]) {
    const r = mapSheetRows([[h, "Shop Name", "Size"], ["76", "Kalkee", "6x6 ft"], ["", "Anish", "8x4 ft"], ["SL-01", "Devi", "5x5 ft"]]);
    assert.equal(r.layout.snoCol, 0, h);
    assert.deepEqual(r.shops.map((s, i) => [s.name, rowSno(s, i)]), [["Kalkee", "76"], ["Anish", 2], ["Devi", "SL-01"]], h);
  }
  const none = mapSheetRows([["Shop Name", "Size"], ["Kalkee", "6x6 ft"], ["Anish", "8x4 ft"]]);
  assert.equal(none.layout.snoCol, -1);
  assert.deepEqual(none.shops.map((s, i) => rowSno(s, i)), [1, 2]);
  // "Shop No" / "Phone No" are not serial columns
  assert.equal(mapSheetRows([["Shop No", "Shop Name", "Size"], ["A1", "Kalkee", "6x6"]]).layout.snoCol, -1);
});

test("a Language column pre-fills each row's Language (whole words, Both when blank or unclear)", async () => {
  const { parseShopRowLanguage: lang } = await import("./shopImport.js");
  const { toDraftRow } = await import("./shopPayload.js");
  for (const v of ["English", "English Only", "EN", "eng", "ஆங்கிலம்"]) assert.equal(lang(v), "en", v);
  for (const v of ["Tamil", "Tamil Only", "TA", "tam", "தமிழ்"]) assert.equal(lang(v), "ta", v);
  for (const v of ["Both", "English & Tamil", "EN/TA", "en & ta", "Bilingual", "", "status", "xyz", null]) assert.equal(lang(v), "both", String(v));
  for (const h of ["Language", "Lang", "Language Mode", "Mode", "Text Language"]) {
    const r = mapSheetRows([["S.No", "Shop Name", "Size", h], [1, "A", "8x4 ft", "English Only"], [2, "B", "6x6 ft", "Tamil"], [3, "C", "10x4 ft", ""]]);
    assert.equal(r.layout.langCol, 3, h);
    assert.deepEqual(r.shops.map((s) => toDraftRow(s).language ?? "both"), ["en", "ta", "both"], h);
  }
  assert.equal(mapSheetRows([["Shop Name", "Size"], ["A", "8x4"]]).layout.langCol, -1);
  const f = await parseShopFile(fileOf([["Shop Name", "Width", "Height", "Lang"], ["A", "8 ft", "4 ft", "ENG"], ["B", "6", "6", "Both"]]));
  assert.deepEqual(f.shops.map((s) => s.language ?? "both"), ["en", "both"]);
});

test("a master's shop name comes from its file name without Windows copy suffixes", async () => {
  const { shopNameFromFileName } = await import("./shopImport.js");
  assert.equal(shopNameFromFileName("02 - 3 X 6 Feet - Double Side GSB - Sri Sai cafe (1).cdr"), "Sri Sai cafe");
  assert.equal(shopNameFromFileName("05 - 6 X 3 Feet - Double Side GSB - Namma T Zone (1).cdr"), "Namma T Zone");
  assert.equal(shopNameFromFileName("66 - 8 X 4 Feet - Nonlit - VASANTHAM ENTERPRISES - Copy.cdr"), "VASANTHAM ENTERPRISES");
  assert.equal(shopNameFromFileName("master.cdr"), null);
  assert.equal(cleanShopName("02 - 3 X 6 Feet - Double Side GSB - Sri Sai cafe (1).cdr"), "Sri Sai cafe");
});

test("a UTF-8 CSV keeps its Tamil names (no byte-order mark needed)", async () => {
  const csv = "S.No,Shop Name,Shop Name (Tamil),Size\n1,Wide Shop,விட் ஷாப்,12 x 4 ft\n";
  const r = await parseShopFile(new File([new TextEncoder().encode(csv)], "shops.csv"));
  assert.equal(r.shops[0].shop_name_local, "விட் ஷாப்");
  const bom = await parseShopFile(new File([new TextEncoder().encode("﻿" + csv)], "shops.csv"));
  assert.equal(bom.shops[0].name, "Wide Shop");
  assert.equal(bom.shops[0].shop_name_local, "விட் ஷாப்");
});

test("the Shops Queue header row: S.NO | SHOP NAME (EN) | SHOP NAME (TA) | WIDTH | HEIGHT | UNIT | TYPE OF BOARD | LANGUAGE | MASTER | CONVERT", () => {
  const rows = [
    ["S.NO", "SHOP NAME (EN)", "SHOP NAME (TA)", "WIDTH", "HEIGHT", "UNIT", "TYPE OF BOARD", "LANGUAGE", "MASTER", "CONVERT"],
    [1, "Asian Juice bar", "ஏசியன் ஜூஸ் பார்", 16, 3, "ft", "Frontlit", "Both", "Master 8", "Yes"],
    [2, "Sri Sai cafe", "ஸ்ரீ சாய் கஃபே", 3, 6, "ft", "Double Side GSB", "Tamil Only", "L: Master 7", "No"],
    [3, "Lakshmi Store", "", 10, 3, "ft", "Nonlit", "English Only", "6 X 3.cdr", ""],
  ];
  const r = mapSheetRows(rows);
  assert.deepEqual(r.missing, []);
  assert.equal(r.shops.length, 3);
  const [a, b, c] = r.shops;
  assert.equal(a.name, "Asian Juice bar"); assert.equal(a.sno, "1"); assert.equal(a.shop_name_local, "ஏசியன் ஜூஸ் பார்");
  assert.equal(a.width, 16); assert.equal(a.height, 3); assert.equal(a.unit, "ft"); assert.equal(a.board_type, "Frontlit");
  assert.equal(a.master, "Master 8"); assert.equal(a.convert, undefined);          // Yes: converts as usual
  assert.equal(b.language, "ta"); assert.equal(b.convert, false); assert.equal(b.master, "L: Master 7");   // No: Convert All skips it
  assert.equal(c.language, "en"); assert.equal(c.master, "6 X 3.cdr"); assert.equal(c.convert, undefined);  // blank: converts
  assert.equal(r.layout.masterCol, 8); assert.equal(r.layout.convertCol, 9);
});

test("parseConvertFlag: explicit no / skip / hold only", () => {
  for (const v of ["No", "n", "FALSE", "0", "skip", "Hold"]) assert.equal(parseConvertFlag(v), false);
  for (const v of ["Yes", "y", "✓", "convert", "", null, "later?"]) assert.equal(parseConvertFlag(v), true);
});

test("a header that merely contains the letters 'ta' is not the Tamil column", () => {
  const r = mapSheetRows([["Shop Name", "Status", "Quantity", "Width", "Height"], ["A Store", "ok", 2, 10, 4]]);
  assert.equal(r.shops.length, 1);
  assert.equal(r.shops[0].shop_name_local, undefined);
});
