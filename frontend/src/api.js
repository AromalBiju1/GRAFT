// One XHR does upload progress (upload.onprogress), SSE parsing (onprogress on
// the response) and cancellation (abort) with a single mechanism.
/**
 * Send a multipart chat request and report upload progress, SSE events, and errors.
 * Call onDone when the request finishes or is aborted; return a cancellation function.
 */
export function sendChat({ prompt, files, onUpload, onEvent, onError, onDone }) {
  const xhr = new XMLHttpRequest();
  const body = new FormData();
  body.append("prompt", prompt);
  files.forEach((f) => body.append("files", f.file));

  let seen = 0, buf = "";
  /** Dispatch complete SSE frames while retaining a partial frame for the next update. */
  const parse = () => {
    buf += xhr.responseText.slice(seen);
    seen = xhr.responseText.length;
    const parts = buf.split("\n\n");
    buf = parts.pop();
    for (const p of parts) {
      const ev = /^event: (.+)$/m.exec(p)?.[1];
      const data = /^data: (.+)$/m.exec(p)?.[1];
      if (ev && data) onEvent(ev, JSON.parse(data));
    }
  };

  xhr.open("POST", `${import.meta.env.VITE_API_URL ?? ""}/chat`);
  xhr.upload.onprogress = (e) => e.lengthComputable && onUpload(Math.round((e.loaded / e.total) * 100));
  xhr.onprogress = parse;
  xhr.onload = () => {
    if (xhr.status >= 400) {
      let msg = `Request failed (${xhr.status}).`;
      try { msg = JSON.parse(xhr.responseText).detail ?? msg; } catch {}
      onError(msg);
    } else parse();
    onDone();
  };
  xhr.onerror = () => { onError("Can't reach the server. Check your connection and try again."); onDone(); };
  xhr.onabort = onDone;
  xhr.send(body);
  return () => xhr.abort();
}

export const ACCEPT = ".pdf,.docx,.txt";
export const MAX_MB = 10;
const OK = ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/plain"];

/**
 * Return queued file records and validation errors for supported files up to MAX_MB.
 * Skip files whose name and size match an existing attachment.
 */
export function validate(list, existing) {
  const accepted = [], errors = [];
  for (const file of list) {
    if (!OK.includes(file.type)) errors.push(`${file.name}: use PDF, DOCX or TXT.`);
    else if (file.size > MAX_MB * 2 ** 20) errors.push(`${file.name}: larger than ${MAX_MB} MB.`);
    else if (existing.some((e) => e.file.name === file.name && e.file.size === file.size)) continue;
    else accepted.push({ id: crypto.randomUUID(), file, state: "queued" });
  }
  return { accepted, errors };
}
/** Format bytes as rounded KB (at least 1 KB) or MB with one decimal place. */
export const fmtSize = (b) => (b < 1048576 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1048576).toFixed(1)} MB`);
