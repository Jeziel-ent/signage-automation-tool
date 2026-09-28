import { test } from "node:test";
import assert from "node:assert/strict";
import { wtProgress, wtStatusText } from "./generateZip.js";

test("status text follows the uploader's steps", () => {
  assert.equal(wtStatusText(null), "Uploading ZIP to WeTransfer...");
  assert.equal(wtStatusText({ step: "queued" }), "Initiating WeTransfer upload...");
  assert.equal(wtStatusText({ step: "verifying" }), "Checking the verification code...");
  assert.equal(wtStatusText({ step: "consent" }), "Accepting the cookie and terms screens...");
  assert.equal(wtStatusText({ step: "uploading", progress: 42.4 }), "Uploading ZIP to WeTransfer... 42%");
  assert.equal(wtStatusText({ step: "uploading", progress: 0 }), "Uploading ZIP to WeTransfer...");
});

test("progress: setup steps then the upload's own percentage", () => {
  assert.equal(wtProgress(null), 0);
  assert.ok(wtProgress({ step: "launch" }) > 0 && wtProgress({ step: "verifying" }) === 10);
  assert.ok(wtProgress({ step: "email" }) < wtProgress({ step: "submit" }));
  assert.equal(wtProgress({ step: "uploading", progress: 50 }), 55);
  assert.equal(wtProgress({ status: "success", step: "done" }), 100);
});

test("verification codes: letters and digits, upper-cased, typed or pasted", async () => {
  const { normalizeOtp, OTP_MAX } = await import("./generateZip.js");
  assert.equal(normalizeOtp("953gyv"), "953GYV");
  assert.equal(normalizeOtp(" 953-GyV\n"), "953GYV");
  assert.equal(normalizeOtp("123456"), "123456");
  assert.equal(normalizeOtp("abcdefghijk").length, OTP_MAX);
  assert.equal(normalizeOtp(null), "");
});
