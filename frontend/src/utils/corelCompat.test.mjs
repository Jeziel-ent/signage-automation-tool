import test from "node:test";
import assert from "node:assert/strict";
import { masterNeeds, maxCorelMajor } from "./corelCompat.js";

test("the newest install of the agent on this PC counts", () => {
  assert.equal(maxCorelMajor({ corel: [{ major: 21 }, { major: 27 }] }, []), 27);
});

test("without a local agent the online agents of the account are used, offline ones are not", () => {
  const agents = [{ online: false, corel: [{ major: 27 }] }, { online: true, corel: [{ major: 21 }] }];
  assert.equal(maxCorelMajor(null, agents), 21);
});

test("nothing known is 0", () => {
  assert.equal(maxCorelMajor(null, []), 0);
  assert.equal(maxCorelMajor({ corel: [] }, []), 0);
  assert.equal(maxCorelMajor(null, undefined), 0);
});

test("a master newer than the PC's CorelDRAW says what it needs", () => {
  assert.equal(masterNeeds({ core_major: 25 }, 21), 25);
});

test("a master the PC can open, or an unreadable / unknown case, needs nothing", () => {
  assert.equal(masterNeeds({ core_major: 25 }, 27), 0);
  assert.equal(masterNeeds({ core_major: 25 }, 25), 0);
  assert.equal(masterNeeds({ core_major: null }, 21), 0);       // a pre-X4 file: no stamp to compare
  assert.equal(masterNeeds({ core_major: 25 }, 0), 0);          // the PC's version is not known yet: no warning
  assert.equal(masterNeeds(undefined, 21), 0);
});
