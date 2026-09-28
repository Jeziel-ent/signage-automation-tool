// "Generate ZIP" modal helpers: the WeTransfer upload's status line (steps are wetransfer_uploader.py's `at(...)` names).
const STEP_TEXT = {
  queued: "Initiating WeTransfer upload...",
  launch: "Initiating WeTransfer upload...",
  open: "Opening WeTransfer...",
  consent: "Accepting the cookie and terms screens...",
  add_file: "Adding the ZIP...",
  link_mode: "Choosing a link transfer...",
  email: "Entering the sender e-mail...",
  submit: "Starting the transfer...",
  requires_otp: "Waiting for the verification code...",
  verifying: "Checking the verification code...",
  uploading: "Uploading ZIP to WeTransfer...",
  done: "Link ready",
};

export function wtStatusText(job) {
  if (!job) return "Uploading ZIP to WeTransfer...";
  const base = STEP_TEXT[job.step] || "Uploading ZIP to WeTransfer...";
  return job.step === "uploading" && job.progress ? `${base} ${Math.round(job.progress)}%` : base;
}

/** Overall bar value: the setup steps fill the first 10 %, the upload's own % the rest. */
export function wtProgress(job) {
  if (!job) return 0;
  if (job.status === "success") return 100;
  const setup = ["queued", "launch", "open", "consent", "add_file", "link_mode", "email", "submit", "requires_otp", "verifying"];
  const i = setup.indexOf(job.step);
  if (i >= 0) return Math.round(((i + 1) / setup.length) * 10);
  if (job.step === "uploading") return 10 + Math.round((job.progress || 0) * 0.9);
  return 0;
}

// WeTransfer's e-mailed codes mix letters and digits (e.g. "953GYV"): keep A-Z / 0-9, upper-case, at most OTP_MAX -
// the same rule for typing and pasting (a pasted "953-gyv " becomes "953GYV"). The server accepts 4-10 characters.
export const OTP_MIN = 4;
export const OTP_MAX = 8;
export function normalizeOtp(value) {
  return String(value || "").replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, OTP_MAX);
}
