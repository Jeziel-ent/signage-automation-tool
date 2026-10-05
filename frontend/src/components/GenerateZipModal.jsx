import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Copy, Download, ExternalLink, Link2, Loader2, RotateCw, X } from "lucide-react";
import { zipIssuesText, zipRequest } from "../utils/assetZip.js";
import { OTP_MAX, OTP_MIN, normalizeOtp, wtProgress, wtStatusText } from "../utils/generateZip.js";
import { fmtBytes } from "../utils/fileSize.js";
import { motion } from "framer-motion";
import { BACKDROP_MOTION, CARD_MOTION } from "./modalMotion.js";
import "./ExportModal.css";

/**
 * "Generate ZIP": builds Signage_Assets_Export.zip on the server as soon as it opens (JPGs at the root, CDR&PDF/cdr and
 * CDR&PDF/pdf), then offers "Download ZIP File" and "Generate WeTransfer Link" for that same archive. Closing the modal
 * deletes the archive on the server (after any upload still reading it). `shops`: the queue's converted shops with `no`.
 */
export default function GenerateZipModal({ shops: shopsProp, onClose }) {
  // the shops at the moment the modal opened: the page re-renders (status polls) with a new array each time, and the
  // archive must not be rebuilt for that
  const [shops] = useState(shopsProp);
  const [zip, setZip] = useState(null); // {token, download, summary}
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const tokenRef = useRef(null);
  const mounted = useRef(false);
  const started = useRef(-1); // the attempt whose build was started (StrictMode runs effects twice in dev)

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      discard(tokenRef.current); // the archive is removed when the modal goes away (after any upload reading it)
    };
  }, []);

  useEffect(() => {
    if (started.current === attempt) return; // one build per attempt - never a second multi-GB packaging job
    started.current = attempt;
    setZip(null);
    setError("");
    (async () => {
      try {
        const r = await fetch("/api/export-zip", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(zipRequest(shops)),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${r.status}`);
        if (!mounted.current) {
          discard(body.token); // closed while it was being built
          return;
        }
        tokenRef.current = body.token;
        setZip(body);
      } catch (e) {
        if (mounted.current) setError(e.message);
      }
    })();
  }, [shops, attempt]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const s = zip && zip.summary;
  return (
    <motion.div className="xm-back" {...BACKDROP_MOTION} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <motion.div {...CARD_MOTION} className="xm gz" role="dialog" aria-modal="true" aria-labelledby="gz-title">
        <header className="xm-head">
          <div>
            <h2 id="gz-title">ZIP Archive Ready</h2>
            <p className="xm-sub">Signage_Assets_Export.zip &middot; {shops.length} converted shop{shops.length === 1 ? "" : "s"}</p>
          </div>
          <button className="xm-x" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <X size={18} />
          </button>
        </header>

        <div className="xm-body">
          {!zip && !error && (
            <div className="gz-state" role="status">
              <Loader2 className="xm-spin" size={34} />
              <p>Packaging JPGs, CDR and PDF files into the CDR&amp;PDF folder...</p>
            </div>
          )}

          {error && (
            <div className="gz-state">
              <AlertTriangle size={30} className="gz-warn-ico" />
              <p className="xm-err">Could not build the ZIP: {error}</p>
              <button className="xm-btn" onClick={() => setAttempt((n) => n + 1)}><RotateCw size={14} /> Try again</button>
            </div>
          )}

          {zip && (
            <>
              <div className="gz-state">
                <SuccessTick />
                <p className="gz-done">Your signage package has been generated successfully!</p>
                <p className="xm-note">
                  {s.shops} shop{s.shops === 1 ? "" : "s"} &middot; {s.files} file{s.files === 1 ? "" : "s"}
                  {s.bytes ? ` · ${fmtBytes(s.bytes)}` : ""}
                </p>
              </div>
              {zipIssuesText(s) && <div className="xm-warn">{zipIssuesText(s)}</div>}

              <div className="gz-actions">
                <a className="gz-action gz-download" href={zip.download} download="Signage_Assets_Export.zip">
                  <Download size={20} />
                  <span><strong>Download ZIP File</strong><small>Save it to this computer</small></span>
                </a>
                <WeTransferCard token={zip.token} />
              </div>
            </>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}

function discard(token) {
  if (!token) return;
  fetch(`/api/export-zip/${token}`, { method: "DELETE", keepalive: true }).catch(() => {});
}

function SuccessTick() {
  return (
    <svg className="gz-tick" viewBox="0 0 96 96" aria-hidden="true">
      <circle className="gz-tick-ring" cx="48" cy="48" r="40" />
      <path className="gz-tick-mark" d="M30 49.5 L43 62 L67 36" />
    </svg>
  );
}

const EMAIL_KEY = "signage.wetransferEmail";
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function savedEmail() {
  try {
    return localStorage.getItem(EMAIL_KEY) || "";
  } catch {
    return "";
  }
}

/**
 * The WeTransfer link, in stages: the sender e-mail WeTransfer requires -> the upload (browser on the server) -> if
 * WeTransfer e-mails a code, the person types it here and it is handed to the waiting upload -> the we.tl link, or the
 * server-link fallback when WeTransfer cannot be used.
 */
function WeTransferCard({ token }) {
  const [stage, setStage] = useState("idle"); // idle | email | job
  const [email, setEmail] = useState(savedEmail);
  const [emailErr, setEmailErr] = useState("");
  const [job, setJob] = useState(null); // the upload job's last status
  const [otp, setOtp] = useState("");
  const [otpBusy, setOtpBusy] = useState(false);
  const [otpErr, setOtpErr] = useState("");
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(Date.now());
  const inputRef = useRef(null);
  const polling = !!job && job.status !== "success" && job.status !== "failed";
  const needsOtp = !!job && job.status === "requires_otp";
  const url = job && job.status === "success" ? job.wetransfer_url : "";
  const fallback = !!(job && job.fallback_used);

  useEffect(() => {
    if (!polling) return undefined;
    const t = setInterval(async () => {
      try {
        const r = await fetch(`/api/export-wetransfer/${job.job_id}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const s = await r.json();
        setJob(s);
        if (s.status === "requires_otp") setOtpBusy(false);
        if (s.status === "failed") setErr(s.error || "The upload failed");
      } catch { /* transient - keep polling */ }
    }, 1000);
    return () => clearInterval(t);
  }, [polling, job]);

  useEffect(() => { // the code's countdown
    if (!needsOtp) return undefined;
    setNow(Date.now()); // not the stale time from when the card mounted
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [needsOtp]);

  async function start(e) {
    e?.preventDefault();
    const address = email.trim();
    if (!EMAIL_RE.test(address)) {
      setEmailErr("Enter a valid e-mail address, like user@company.com");
      return;
    }
    try {
      localStorage.setItem(EMAIL_KEY, address);
    } catch { /* private mode - just not remembered */ }
    setEmailErr("");
    setErr("");
    setCopied(false);
    setOtp("");
    setOtpErr("");
    setStage("job");
    setJob({ status: "queued", step: "queued", progress: 0 });
    try {
      const r = await fetch("/api/export-wetransfer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, sender_email: address }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${r.status}`);
      setJob({ job_id: body.job_id, status: "queued", step: "queued", progress: 0 });
    } catch (error_) {
      setErr(error_.message);
      setJob(null);
    }
  }

  async function submitOtp(e) {
    e.preventDefault();
    const code = normalizeOtp(otp);
    if (code.length < OTP_MIN) {
      setOtpErr("Enter the whole code from the WeTransfer e-mail (letters and numbers)");
      return;
    }
    setOtpErr("");
    setOtpBusy(true);
    try {
      const r = await fetch("/api/export-wetransfer/verify-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: job.job_id, otp_code: code }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${r.status}`);
      setOtp("");
      setJob((j) => ({ ...j, status: "running", step: "verifying", otp_error: null }));
    } catch (error_) {
      setOtpBusy(false);
      setOtpErr(error_.message);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      inputRef.current?.select();
      document.execCommand("copy"); // older browsers / non-secure origins
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const secondsLeft = needsOtp && job.otp_deadline ? Math.max(0, Math.round(job.otp_deadline - now / 1000)) : null;

  return (
    <div className={`gz-action gz-link${url ? " ready" : ""}`}>
      {stage === "idle" && !err && (
        <button type="button" className="gz-link-start" onClick={() => setStage("email")}>
          <Link2 size={20} />
          <span><strong>Generate WeTransfer Link</strong><small>Upload the ZIP and get a share link</small></span>
        </button>
      )}

      {stage === "email" && (
        <form className="gz-link-form" onSubmit={start} noValidate>
          <label className="xm-field-label" htmlFor="gz-email">Enter Sender Email</label>
          <p className="xm-note">WeTransfer requires the sender&apos;s address for a link transfer and may e-mail it a verification code.</p>
          <input
            id="gz-email" className="pf-input" type="email" required autoFocus autoComplete="email"
            placeholder="user@company.com" value={email} onChange={(e) => setEmail(e.target.value)}
          />
          {emailErr && <p className="xm-err">{emailErr}</p>}
          <div className="gz-form-row">
            <button type="button" className="xm-btn" onClick={() => setStage("idle")}>Cancel</button>
            <button type="submit" className="xm-primary"><Link2 size={15} /> Proceed with Upload</button>
          </div>
        </form>
      )}

      {stage === "job" && polling && !needsOtp && (
        <div className="gz-link-busy" role="status">
          <div className="gz-link-line"><Loader2 size={16} className="xm-spin" /> {wtStatusText(job)}</div>
          <div className="progress-bar small"><div className="progress-fill" style={{ width: `${wtProgress(job)}%` }} /></div>
        </div>
      )}

      {stage === "job" && needsOtp && (
        <form className="gz-link-form" onSubmit={submitOtp} noValidate>
          <label className="xm-field-label" htmlFor="gz-otp">Verification code</label>
          <p className="gz-otp-msg">WeTransfer sent a verification code to <strong>{email.trim()}</strong> - letters and numbers, e.g. 953GYV.</p>
          {job.otp_error && <p className="xm-err">{job.otp_error}</p>}
          <input
            id="gz-otp" className="pf-input gz-otp" type="text" inputMode="text" autoComplete="one-time-code" autoFocus
            autoCapitalize="characters" autoCorrect="off" spellCheck={false}
            placeholder="e.g. 953GYV" maxLength={OTP_MAX} value={otp} disabled={otpBusy}
            onChange={(e) => setOtp(normalizeOtp(e.target.value))}
          />
          {otpErr && <p className="xm-err">{otpErr}</p>}
          <div className="gz-form-row">
            {secondsLeft !== null && (
              <span className="xm-note">{secondsLeft > 0 ? `Waiting ${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")} - after that you get a server link instead.` : "Time is up..."}</span>
            )}
            <button type="submit" className="xm-primary" disabled={otpBusy || !otp}>
              {otpBusy ? <Loader2 size={15} className="xm-spin-white" /> : <Check size={15} />} Submit OTP &amp; Generate Link
            </button>
          </div>
        </form>
      )}

      {url && (
        <div className="gz-link-done">
          <span className="xm-field-label">{fallback ? "Download link (this server)" : "WeTransfer link"}</span>
          {fallback && (
            <div className={`gz-fallback${job.local_only ? " local" : ""}`} role="note">
              <strong>Generated direct server link (WeTransfer upload fallback).</strong>{" "}
              {job.local_only
                ? "It opens only on this computer - the backend listens on 127.0.0.1. Start it with --host 0.0.0.0 to share it on the office network."
                : "It works for anyone who can reach this server (office network or VPN), not the open internet."}
              {job.expires_at ? ` Available until ${new Date(job.expires_at * 1000).toLocaleString()}.` : ""}
              {job.wetransfer_error && <details><summary>Why WeTransfer failed</summary><span className="gz-why">{job.wetransfer_error}</span></details>}
            </div>
          )}
          <div className="gz-url-row">
            <input ref={inputRef} className="pf-input gz-url" value={url} readOnly onFocus={(e) => e.target.select()} aria-label={fallback ? "Download link" : "WeTransfer link"} />
            <button type="button" className="xm-btn" onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />} Copy Link</button>
            <a className="xm-btn" href={url} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> Open Link</a>
          </div>
          {copied && <span className="gz-toast" role="status">Copied!</span>}
        </div>
      )}

      {err && (
        <div className="gz-link-err">
          <p className="xm-err">Could not get a WeTransfer link: {err}</p>
          <button type="button" className="xm-btn" onClick={() => { setErr(""); setStage("email"); }}><RotateCw size={14} /> Try again</button>
        </div>
      )}
    </div>
  );
}
