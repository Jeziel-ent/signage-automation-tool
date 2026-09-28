import { test } from "node:test";
import assert from "node:assert/strict";
import { defaultReplacement, nextMissingFont, substituteFor } from "./fontSubs.js";

test("asks about each missing font once: substituted or dismissed ones are skipped", () => {
  const missing = ["Copperplate Gothic Bold", "AvantGarde-Demi"];
  assert.equal(nextMissingFont(missing, {}, []), "Copperplate Gothic Bold");
  assert.equal(nextMissingFont(missing, { "copperplate gothic bold": { font: "Arial" } }, []), "AvantGarde-Demi");
  assert.equal(nextMissingFont(missing, { "Copperplate Gothic Bold": { font: "Arial" } }, ["avantgarde-demi"]), null);
  assert.equal(nextMissingFont([], {}, []), null);
});

test("a text node is drawn in its substitute, matched case-insensitively", () => {
  const subs = { "Copperplate Gothic Bold": { font: "Arial", permanent: true } };
  assert.equal(substituteFor("copperplate gothic bold", subs), "Arial");
  assert.equal(substituteFor("Arial", subs), null);
  assert.equal(substituteFor(null, subs), null);
});

test("default replacement: the current choice, else a common sans, else the first installed font", () => {
  assert.equal(defaultReplacement(["Impact", "arial"], null), "arial");
  assert.equal(defaultReplacement(["Impact", "Nirmala UI"], null), "Impact");
  assert.equal(defaultReplacement(["Impact", "Arial"], "Nirmala UI"), "Nirmala UI");
  assert.equal(defaultReplacement([], null), "");
});
