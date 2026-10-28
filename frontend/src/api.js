const BASE_URL = import.meta.env.VITE_API_URL ?? "";

export const ACCEPT = ".pdf,.docx,.txt";
export const MAX_MB = 10;
const OK = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
];
const ALLOWED_EXTS = [".pdf", ".docx", ".txt"];

export function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const body = new FormData();
    body.append("file", file);

    xhr.open("POST", `${BASE_URL}/api/files`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          reject(new Error("Invalid JSON response from server"));
        }
      } else {
        let msg = `Upload failed (${xhr.status}).`;
        try {
          msg = JSON.parse(xhr.responseText).detail ?? msg;
        } catch {}
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error("Can't reach the server. Check your connection."));
    xhr.send(body);
  });
}

export async function pollFileStatus(fid, maxAttempts = 60, intervalMs = 500) {
  for (let i = 0; i < maxAttempts; i++) {
    const res = await fetch(`${BASE_URL}/api/files/${fid}`);
    if (!res.ok) {
      throw new Error(`Failed to check file status (${res.status})`);
    }
    const data = await res.json();
    if (data.status === "ready") return data;
    if (data.status === "failed") {
      throw new Error(data.error || "Document processing failed");
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("Document processing timed out");
}

export function sendChat({ prompt, fileIds = [], onEvent, onError, onDone }) {
  const xhr = new XMLHttpRequest();
  let seen = 0;
  let buf = "";

  const parse = () => {
    buf += xhr.responseText.slice(seen);
    seen = xhr.responseText.length;
    const parts = buf.split("\n\n");
    buf = parts.pop();
    for (const p of parts) {
      const ev = /^event: (.+)$/m.exec(p)?.[1];
      const dataStr = /^data: (.+)$/m.exec(p)?.[1];
      if (ev && dataStr) {
        try {
          onEvent(ev, JSON.parse(dataStr));
        } catch {
          // ignore malformed frame
        }
      }
    }
  };

  xhr.open("POST", `${BASE_URL}/api/chat`);
  xhr.setRequestHeader("Content-Type", "application/json");
  xhr.onprogress = parse;
  xhr.onload = () => {
    if (xhr.status >= 400) {
      let msg = `Request failed (${xhr.status}).`;
      try {
        msg = JSON.parse(xhr.responseText).detail ?? msg;
      } catch {}
      onError(msg);
    } else {
      parse();
    }
    onDone();
  };
  xhr.onerror = () => {
    onError("Can't reach the server. Check your connection and try again.");
    onDone();
  };
  xhr.onabort = onDone;
  xhr.send(JSON.stringify({ prompt, file_ids: fileIds }));

  return () => xhr.abort();
}

// Returns { accepted, errors } so the UI can show why a file was refused.
export function validate(list, existing) {
  const accepted = [];
  const errors = [];
  for (const file of list) {
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    const isAllowedType = OK.includes(file.type) || ALLOWED_EXTS.includes(ext);

    if (!isAllowedType) {
      errors.push(`${file.name}: use PDF, DOCX or TXT.`);
    } else if (file.size > MAX_MB * 2 ** 20) {
      errors.push(`${file.name}: larger than ${MAX_MB} MB.`);
    } else if (existing.some((e) => e.file?.name === file.name && e.file?.size === file.size)) {
      continue;
    } else {
      accepted.push({ id: crypto.randomUUID(), file, state: "queued" });
    }
  }
  return { accepted, errors };
}

export const fmtSize = (b) =>
  b < 1048576
    ? `${Math.max(1, Math.round(b / 1024))} KB`
    : `${(b / 1048576).toFixed(1)} MB`;
