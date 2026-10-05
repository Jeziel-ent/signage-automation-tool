// English shop name -> Tamil script, offline and instant (no request leaves the browser). Shop names are proper nouns and
// English trade words, so this TRANSLITERATES (writes the sound in Tamil letters) - "SRI KANNIYAMMAN STORES" ->
// "ஸ்ரீ கன்னியம்மன் ஸ்டோர்ஸ்" - rather than translating meaning. A dictionary covers the common signage words (whose
// Tamil spelling is not phonetic), single letters and short initials are spelt as letter names ("NR" -> "என்ஆர்"), and
// everything else goes through phonetic rules. It is a first draft for the designer: the Tamil name stays editable.

const PULLI = "்";

// Common words on shop boards, with the spelling Tamil signs actually use.
const WORDS = {
  SRI: "ஸ்ரீ", SHRI: "ஸ்ரீ", SREE: "ஸ்ரீ", SHREE: "ஸ்ரீ", THIRU: "திரு",
  STORE: "ஸ்டோர்", STORES: "ஸ்டோர்ஸ்", SHOP: "ஷாப்", SHOPPE: "ஷாப்பி", MART: "மார்ட்", MARKET: "மார்க்கெட்",
  SUPER: "சூப்பர்", SUPERMARKET: "சூப்பர்மார்க்கெட்", BAZAAR: "பஜார்", BAZAR: "பஜார்", CENTER: "சென்டர்", CENTRE: "சென்டர்",
  TRADERS: "டிரேடர்ஸ்", TRADER: "டிரேடர்", TRADING: "டிரேடிங்", TRADE: "டிரேட்", AGENCY: "ஏஜென்சி", AGENCIES: "ஏஜென்சீஸ்",
  ENTERPRISES: "எண்டர்பிரைசஸ்", ENTERPRISE: "எண்டர்பிரைஸ்", CORPORATION: "கார்ப்பரேஷன்", COMPANY: "கம்பெனி", CO: "கோ",
  SONS: "சன்ஸ்", SON: "சன்", BROTHERS: "பிரதர்ஸ்", BROS: "பிரதர்ஸ்", AND: "அண்ட்", "&": "அண்ட்",
  HARDWARE: "ஹார்டுவேர்", HARDWARES: "ஹார்டுவேர்ஸ்", STEEL: "ஸ்டீல்", STEELS: "ஸ்டீல்ஸ்", CEMENT: "சிமெண்ட்",
  CEMENTS: "சிமெண்ட்ஸ்", BUILDING: "பில்டிங்", BUILDERS: "பில்டர்ஸ்", MATERIALS: "மெட்டீரியல்ஸ்", PAINTS: "பெயிண்ட்ஸ்",
  PAINT: "பெயிண்ட்", ELECTRICALS: "எலக்ட்ரிக்கல்ஸ்", ELECTRICAL: "எலக்ட்ரிக்கல்", ELECTRONICS: "எலக்ட்ரானிக்ஸ்",
  PLYWOOD: "பிளைவுட்", PLYWOODS: "பிளைவுட்ஸ்", TILES: "டைல்ஸ்", GLASS: "கிளாஸ்", PIPES: "பைப்ஸ்", TIMBER: "டிம்பர்",
  PROVISION: "புரொவிஷன்", PROVISIONS: "புரொவிஷன்ஸ்", PROVISON: "புரொவிஷன்", GENERAL: "ஜெனரல்", NEW: "நியூ",
  MEDICAL: "மெடிக்கல்", MEDICALS: "மெடிக்கல்ஸ்", PHARMACY: "பார்மசி", CLINIC: "கிளினிக்", HOSPITAL: "ஹாஸ்பிடல்",
  TEXTILES: "டெக்ஸ்டைல்ஸ்", TEXTILE: "டெக்ஸ்டைல்", SILKS: "சில்க்ஸ்", FANCY: "பேன்சி", READYMADES: "ரெடிமேட்ஸ்",
  JEWELLERY: "ஜுவல்லரி", JEWELLERS: "ஜுவல்லர்ஸ்", JEWELRY: "ஜுவல்லரி", HOTEL: "ஹோட்டல்", BAKERY: "பேக்கரி",
  SWEETS: "ஸ்வீட்ஸ்", RESTAURANT: "ரெஸ்டாரண்ட்", MOBILES: "மொபைல்ஸ்", MOBILE: "மொபைல்", FURNITURE: "பர்னிச்சர்",
  FURNITURES: "பர்னிச்சர்ஸ்", AUTO: "ஆட்டோ", MOTORS: "மோட்டார்ஸ்", GARAGE: "கேரேஜ்", TYRES: "டயர்ஸ்", WORKS: "ஒர்க்ஸ்",
  INDUSTRIES: "இண்டஸ்ட்ரீஸ்", PRIVATE: "பிரைவேட்", PVT: "பிரைவேட்", LIMITED: "லிமிடெட்", LTD: "லிமிடெட்",
  POOJA: "பூஜை", KADAI: "கடை", NATTU: "நாட்டு", MARUNTHU: "மருந்து", MARUNDHU: "மருந்து", AMMAN: "அம்மன்",
  MURUGAN: "முருகன்", GANESH: "கணேஷ்", GANESHA: "கணேஷா", VINAYAGA: "விநாயகா", VINAYAGAR: "விநாயகர்", LAKSHMI: "லட்சுமி",
  BALAJI: "பாலாஜி", KRISHNA: "கிருஷ்ணா", RAMA: "ராமா", SIVA: "சிவா", SHIVA: "சிவா", DURGA: "துர்கா", MEENAKSHI: "மீனாட்சி",
  ANNAI: "அன்னை", VELAN: "வேலன்", SELVAM: "செல்வம்", RAJ: "ராஜ்", KUMAR: "குமார்", AL: "அல்", MADEENA: "மதீனா",
  MADINA: "மதீனா", THE: "தி", OF: "ஆஃப்", DEALER: "டீலர்", DEALERS: "டீலர்ஸ்", AGRO: "அக்ரோ", FARM: "பார்ம்",
  FERTILIZERS: "பெர்டிலைசர்ஸ்", STATIONERY: "ஸ்டேஷனரி", STATIONERS: "ஸ்டேஷனர்ஸ்", BOOK: "புக்", BOOKS: "புக்ஸ்",
  // food & drink, cafes and other common board words (their Tamil spelling is not phonetic from the English letters)
  ASIAN: "ஏசியன்", JUICE: "ஜூஸ்", JUICES: "ஜூஸ்", BAR: "பார்", CAFE: "கஃபே", CAFÉ: "கஃபே", COFFEE: "காபி", TEA: "டீ",
  ICE: "ஐஸ்", CREAM: "கிரீம்", ICECREAM: "ஐஸ்கிரீம்", PARLOUR: "பார்லர்", PARLOR: "பார்லர்", FRESH: "ஃப்ரெஷ்",
  FRUITS: "ஃப்ரூட்ஸ்", FRUIT: "ஃப்ரூட்", SHAKE: "ஷேக்", SHAKES: "ஷேக்ஸ்", SNACKS: "ஸ்நாக்ஸ்", CHAT: "சாட்",
  FAST: "ஃபாஸ்ட்", FOOD: "ஃபுட்", FOODS: "ஃபுட்ஸ்", BAKES: "பேக்ஸ்", BAKERS: "பேக்கர்ஸ்", CAKES: "கேக்ஸ்", CAKE: "கேக்",
  STALL: "ஸ்டால்", CORNER: "கார்னர்", POINT: "பாயிண்ட்", ZONE: "ஜோன்", SPOT: "ஸ்பாட்", KITCHEN: "கிச்சன்",
  CHICKEN: "சிக்கன்", BIRYANI: "பிரியாணி", MESS: "மெஸ்", NAMMA: "நம்ம", MALIGAI: "மளிகை", JOTHI: "ஜோதி",
  SAI: "சாய்", SHENBAGAM: "செண்பகம்", SUPPLIERS: "சப்ளையர்ஸ்", DISTRIBUTORS: "டிஸ்ட்ரிபியூட்டர்ஸ்", SERVICES: "சர்வீசஸ்",
  SERVICE: "சர்வீஸ்", FASHION: "ஃபேஷன்", FASHIONS: "ஃபேஷன்ஸ்", COLLECTIONS: "கலெக்ஷன்ஸ்", GIFTS: "கிஃப்ட்ஸ்",
  STAR: "ஸ்டார்", TAMIL: "தமிழ்", TAMILNADU: "தமிழ்நாடு", NADU: "நாடு", MAHAL: "மஹால்", VILAS: "விலாஸ்", DEPOT: "டிப்போ", HALL: "ஹால்", PALACE: "பேலஸ்", WORLD: "வேர்ல்ட்", HOUSE: "ஹவுஸ்", HOME: "ஹோம்", CITY: "சிட்டி",
};

const LETTERS = {
  A: "ஏ", B: "பி", C: "சி", D: "டி", E: "ஈ", F: "எஃப்", G: "ஜி", H: "எச்", I: "ஐ", J: "ஜே", K: "கே", L: "எல்", M: "எம்",
  N: "என்", O: "ஓ", P: "பி", Q: "க்யூ", R: "ஆர்", S: "எஸ்", T: "டி", U: "யூ", V: "வி", W: "டபிள்யூ", X: "எக்ஸ்", Y: "வை", Z: "இசட்",
};

// vowel -> [independent letter, vowel sign after a consonant ("" = the consonant's own inherent a)]
const VOWELS = [
  ["aa", "ஆ", "ா"], ["ai", "ஐ", "ை"], ["au", "ஔ", "ௌ"], ["ee", "ஈ", "ீ"], ["ii", "ஈ", "ீ"], ["oo", "ஊ", "ூ"],
  ["uu", "ஊ", "ூ"], ["ou", "ஔ", "ௌ"], ["a", "அ", ""], ["i", "இ", "ி"], ["u", "உ", "ு"], ["e", "எ", "ெ"], ["o", "ஓ", "ோ"],
];
// consonant spellings, longest first; the value is the Tamil consonant (a cluster for ksh / x)
const CONS = [
  ["ksh", "க்ஷ"], ["sh", "ஷ"], ["ch", "ச"], ["th", "த"], ["dh", "த"], ["zh", "ழ"], ["ph", "ப"], ["bh", "ப"], ["gh", "க"],
  ["kh", "க"], ["k", "க"], ["g", "க"], ["j", "ஜ"], ["t", "ட"], ["d", "ட"], ["m", "ம"], ["p", "ப"], ["b", "ப"], ["r", "ர"],
  ["l", "ல"], ["v", "வ"], ["w", "வ"], ["h", "ஹ"], ["f", "ப"], ["z", "ஜ"], ["q", "க"], ["x", "க்ஸ"], ["y", "ய"],
];
const isVowelAt = (w, i) => /[aeiou]/.test(w[i] || "");

function readVowel(w, i) {
  for (const [spell, ind, sign] of VOWELS) if (w.startsWith(spell, i)) return { spell, ind, sign };
  return null;
}

/** One word of Latin letters (lower case) -> Tamil, by sound. */
export function phonetic(word) {
  const w = word.toLowerCase().replace(/[^a-z]/g, "");
  let out = "";
  let i = 0;
  let pending = false; // the last consonant still has no vowel (it gets a pulli unless a vowel follows)
  const addCons = (tamil) => {
    if (pending) out += PULLI;
    out += tamil;
    pending = true;
  };
  while (i < w.length) {
    const ch = w[i];
    // y between consonants or at the end after a consonant sounds like i ("SHANTHY" -> சாந்தி)
    if (ch === "y" && pending && !isVowelAt(w, i + 1)) {
      out += "ி";
      pending = false;
      i += 1;
      continue;
    }
    const v = readVowel(w, i);
    if (v) {
      const end = i + v.spell.length >= w.length;
      if (pending) {
        // a final "a" is long in Tamil spellings of names (MEENA -> மீனா); a final silent "e" after a consonant is dropped
        if (end && v.spell === "a") out += "ா";
        else if (end && v.spell === "e" && w.length > 3) out += PULLI;
        else out += v.sign;
        pending = false;
      } else {
        out += v.ind;
      }
      i += v.spell.length;
      continue;
    }
    // doubled consonant: the first takes a pulli ("tt" -> ட்ட, "nn" -> ன்ன, "mm" -> ம்ம)
    if (ch === "n") {
      const nx = w[i + 1] || "";
      if (i === 0) addCons("ந");
      else if (nx === "n") { addCons("ன"); addCons("ன"); i += 2; continue; }
      else if (nx === "d" || nx === "t") { addCons("ந"); addCons("த"); i += w.startsWith("th", i + 1) || w.startsWith("dh", i + 1) ? 3 : 2; continue; }
      else if (nx === "k" || nx === "g") addCons("ங");
      else if (nx === "j" || w.startsWith("ch", i + 1)) addCons("ஞ");
      else addCons("ன");
      i += 1;
      continue;
    }
    if (ch === "s" && w[i + 1] === "h") { addCons("ஷ"); i += 2; continue; }
    if (ch === "s") {
      // s before a vowel is ச in Tamil names (SELVAM, SARAVANA); before a consonant or at the end it is ஸ (STEEL, -S)
      addCons(isVowelAt(w, i + 1) ? "ச" : "ஸ");
      i += 1;
      continue;
    }
    if (ch === "c") {
      if (w[i + 1] === "h") { addCons("ச"); i += 2; continue; }
      addCons(/[eiy]/.test(w[i + 1] || "") ? "ச" : "க");
      i += w[i + 1] === "k" ? 2 : 1;
      continue;
    }
    const c = CONS.find(([spell]) => w.startsWith(spell, i));
    if (!c) { i += 1; continue; }
    if (c[0].length === 1 && w[i + 1] === ch) { addCons(c[1]); addCons(c[1]); i += 2; continue; }
    addCons(c[1]);
    i += c[0].length;
  }
  if (pending) out += PULLI;
  return out;
}

// brackets, commas and quotes stuck to a word ("(Jothi", "maligai)") are kept around its Tamil form, not fed into it
const WRAP_RE = /^([()[\]{},;:!?"'“”‘’]*)(.*?)([()[\]{},;:!?"'“”‘’]*)$/;  // NOSONAR - bounded, human-entered strings (file names / emails); no ReDoS exposure, rewrite would risk parsing changes

function word(token) {
  const [, lead, core, trail] = WRAP_RE.exec(token);
  if ((lead || trail) && core) return lead + word(core) + trail;
  const up = token.toUpperCase();
  if (WORDS[up]) return WORDS[up];
  if (!/[A-Z]/.test(up)) return token; // digits, punctuation, anything already Tamil
  const letters = up.replace(/[^A-Z]/g, "");
  if (letters.length > 1 && letters.endsWith("S") && WORDS[letters.slice(0, -1)]) return WORDS[letters.slice(0, -1)] + "ஸ்";
  // "A", "NR", "SKM", "A.K." - initials are read out as letter names
  if (letters.length === 1 || (letters.length <= 3 && !/[AEIOU]/.test(letters)) || /^([A-Z]\.)+[A-Z]?$/.test(up)) {
    return [...letters].map((l) => LETTERS[l]).join("");
  }
  return phonetic(letters);
}

/** "SRI KANNIYAMMAN NATTU MARUNTHU KADAI" -> "ஸ்ரீ கன்னியம்மன் நாட்டு மருந்து கடை". Spacing and digits are kept. */
export function toTamil(name) {
  const s = String(name ?? "").trim();
  if (!s) return "";
  return s.split(/(\s+)/).map((t) => (/^\s+$/.test(t) ? " " : word(t))).join("").trim();
}
