import test from "node:test";
import assert from "node:assert/strict";
import { phonetic, toTamil } from "./tamilTranslit.js";

test("real shop names come out as their Tamil spellings (EN/TA pairs from the DARSHAN and AL MADEENA boards)", () => {
  assert.equal(toTamil("SRI KANNIYAMMAN NATTU MARUNTHU KADAI"), "ஸ்ரீ கன்னியம்மன் நாட்டு மருந்து கடை");
  assert.equal(toTamil("AL MADEENA POOJA STORE"), "அல் மதீனா பூஜை ஸ்டோர்");
});

test("dictionary words, plurals, initials and digits", () => {
  assert.equal(toTamil("NR TRADERS"), "என்ஆர் டிரேடர்ஸ்");
  assert.equal(toTamil("A 1 STAR ENTERPRISES"), "ஏ 1 ஸ்டார் எண்டர்பிரைசஸ்");
  assert.equal(toTamil("SAFI STEEL TRADERS PRIVATE LIMITED"), "சபி ஸ்டீல் டிரேடர்ஸ் பிரைவேட் லிமிடெட்");
  assert.equal(toTamil("TAMILNADU STEELS"), "தமிழ்நாடு ஸ்டீல்ஸ்");
  assert.equal(toTamil("  "), "");
  assert.equal(toTamil("ஸ்ரீ கடை"), "ஸ்ரீ கடை"); // already Tamil: unchanged
});

test("phonetic rules: doubled consonants, nasal assimilation, sh, final a / y", () => {
  assert.equal(phonetic("kanniyamman"), "கன்னியம்மன்");
  assert.equal(phonetic("santhosh"), "சந்தோஷ்");
  assert.equal(phonetic("kavitha"), "கவிதா");
  assert.equal(phonetic("anish"), "அனிஷ்");
  assert.equal(phonetic("ranga"), "ரங்கா");
});

test("board words from the current masters and punctuation around words", async () => {
  const { toTamil } = await import("./tamilTranslit.js");
  assert.equal(toTamil("Asian Juice bar"), "ஏசியன் ஜூஸ் பார்");
  assert.equal(toTamil("Sri Sai cafe"), "ஸ்ரீ சாய் கஃபே");
  assert.equal(toTamil("Hangyo Ice Cream").endsWith("ஐஸ் கிரீம்"), true);
  assert.equal(toTamil("Shenbagam Enterprises (Jothi maligai)"), "செண்பகம் எண்டர்பிரைசஸ் (ஜோதி மளிகை)");
});
