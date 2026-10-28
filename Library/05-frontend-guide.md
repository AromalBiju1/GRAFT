# 05 - Frontend guide

The frontend is a React 19 + Vite 7 single-page app. It renders a chat
interface with sessions, streaming messages, and file attachments. Attachments
are uploaded and indexed first, then referenced by server ID in the chat
request. Chapter
[06 - Frontend/backend integration](06-frontend-backend-integration.md) covers
that request sequence.

This chapter is about the code that is there: what each file does, how state
flows, and how to run or extend it.

---

## Stack

| Piece | Version | Role |
|---|---|---|
| React | ^19.0.0 | UI |
| React DOM | ^19.0.0 | Mounting |
| Vite | ^7.0.0 | Dev server and build |
| `@vitejs/plugin-react` | ^5.0.0 | JSX/Fast Refresh |
| `react-markdown` | ^10.1.0 | Renders assistant messages as Markdown |

There is no router, no state-management library, and no CSS framework. State is
local React state; styling is plain CSS.

---

## Run and build

Requires Node `^20.19.0` or `>=22.12.0` (the Vite 7 engine requirement).

```bash
cd frontend
npm install
npm run dev
```

- Dev server: Vite prints the URL, normally <http://localhost:5173>.
- Build: `npm run build` writes static assets to `frontend/dist/`.
- Preview the build: `npm run preview`.
- No lint or test script is defined in `package.json`; tests are run directly
  with Node's built-in test runner (see below).

`frontend/vite.config.js` proxies `/query`, `/api`, `/health`, and `/index` to
`http://127.0.0.1:8000`. `/analytics` is not proxied, but the mounted UI does
not call it. The `/api` proxy is what makes the two-phase chat flow work
without CORS in development.

---

## Entry point and component tree

```text
index.html
  └── src/main.jsx
        └── <StrictMode>
              └── <App />
                    ├── <Sidebar />
                    ├── <main class="chat">
                    │     ├── header (logo, title)
                    │     ├── <Feed />
                    │     ├── dropzone (when dragging)
                    │     └── <footer>
                    │           ├── notice
                    │           ├── <AttachmentTray />
                    │           └── <InputBar />
```

`main.jsx` does one thing: create the React root and render `App` inside
`StrictMode`. There is no provider, router, or global store.

---

## File-by-file

### `frontend/index.html`

The Vite HTML entry. Sets the language, viewport, title, `#root`, and loads
`/src/main.jsx` as a module. The logo is served from `public/graft-logo.png`
and referenced as `/graft-logo.png`.

### `frontend/src/main.jsx`

Creates the root with `createRoot(document.getElementById("root"))` and renders
`<App />` in `<StrictMode>`. No other setup.

### `frontend/src/App.jsx`

**Role:** The application shell and the only stateful component that matters.

**State:**

| State | Type | Purpose |
|---|---|---|
| `sessions` | array | Chat sessions, each with `{id, title, messages, files, fileIds}` |
| `activeId` | string | Which session is shown |
| `sidebar` | boolean | Sidebar collapsed/open |
| `text` | string | Current input |
| `queue` | array | Attachments `{id, file, state, progress, fid, error}` |
| `busy` | boolean | A chat request is in flight |
| `notice` | string | Error/validation message |
| `drag` | boolean | Drag-over overlay |

**Refs:** `cancel` holds the abort function returned by `sendChat`; `depth`
tracks nested dragenter/dragleave events.

**Key functions:**
- `newSession()` creates
  `{id, title:"New chat", messages:[GREETING], files:[], fileIds:[]}`.
- `patch(fn)` updates only the active session.
- `addFiles(list)` validates files through `validate()`, uploads each accepted
  file to `/api/files`, polls `/api/files/{id}` until `ready`, and records the
  server ID in the session's `fileIds`.
- `send()`:
  1. trims the prompt and collects ready `fid`s from the queue plus the
     session's existing `fileIds`;
  2. appends a user message and an empty assistant message;
  3. renames the session on the first message;
  4. clears the input and sets busy;
  5. calls `sendChat({prompt, fileIds, ...})`;
  6. stores the abort function for the Stop button.
- Drag handlers implement a nest-safe drag overlay.

**Event handling:**
- `token` appends `d.text || d.t` to the assistant message.
- `meta` stores routing and evidence on the assistant message.
- `status` updates a queued file's state using `d.state || d.status`.
- `error` replaces the assistant message with the error and shows a notice.
- `onDone` clears busy and drops the abort handle. It does **not** clear the
  queue; ready attachments remain available for the next message.

The frontend and backend contracts now line up: JSON
`{prompt, file_ids}` to `/api/chat`, and both legacy and current SSE keys are
accepted. See chapter 06 for the full flow.

**Session behavior:** Sessions are in memory only. A page refresh loses all
chats and files. There is no localStorage or backend persistence.

### `frontend/src/api.js`

**Role:** Chat transport and file validation.

**Exports:**
- `uploadFile(file, onProgress)`: POSTs multipart field `file` to
  `/api/files`; resolves the upload record.
- `pollFileStatus(fid, maxAttempts=60, intervalMs=500)`: polls
  `/api/files/{fid}` until `ready`, rejects on `failed` or timeout.
- `sendChat({prompt, fileIds, onEvent, onError, onDone})` -> abort function.
- `ACCEPT = ".pdf,.docx,.txt"`.
- `MAX_MB = 10`.
- `validate(list, existing)` -> `{accepted, errors}`.
- `fmtSize(bytes)` -> human-readable size.

**How `sendChat` works:**
- Uses `XMLHttpRequest`, not `fetch`, so response streaming
  (`xhr.onprogress`) and cancellation (`xhr.abort()`) share one mechanism.
- Sets `Content-Type: application/json` and POSTs
  `` `${import.meta.env.VITE_API_URL ?? ""}/api/chat` `` with
  `{prompt, file_ids: fileIds}`.
- Parses SSE incrementally: it appends new `responseText` to a buffer, splits
  on blank lines, reads `event:` and `data:` lines, and calls
  `onEvent(event, JSON.parse(data))`; malformed frames are ignored.
- On HTTP >= 400, tries to read `detail` from a JSON body and calls `onError`.
- On network failure, calls a user-friendly "Can't reach the server" error.
- `onDone` runs on load, error, and abort.

**Validation rules:**
- Accepts known MIME types or the PDF/DOCX/TXT extension (some browsers report
  an empty MIME type).
- Max 10 MB per file.
- Duplicate files (same name and size) are silently skipped.
- Accepted files get `{id: crypto.randomUUID(), file, state:"queued"}`.

**Note:** `crypto.randomUUID()` requires a secure context. `localhost` counts
as secure; a plain-HTTP LAN address may not. Use HTTPS or a polyfill if you
deploy over plain HTTP to another host.

### `frontend/src/query.js`

**Role:** The clean, tested client for the working `POST /query` endpoint.

**Export:** `submitQuery(query)`.

**Behavior:**
- `fetch("/query", {method:"POST", headers:{"Content-Type":"application/json"},
  body: JSON.stringify({query})})`.
- On network failure: "Could not reach the API..."
- Parses JSON, extracting errors from `detail` strings, FastAPI validation
  arrays, or `error.message`.
- Rejects non-object/array responses as invalid.
- Returns the parsed response unchanged.

**Usage status:** `submitQuery` is covered by tests but is **not imported by
`App.jsx` or any mounted component**. It is a clean client for a simple
question/answer view if you add one.

### `frontend/src/components/Sidebar.jsx`

Collapsible sidebar with:
- a toggle button;
- a "New chat" button;
- a session history list (titles, active highlight);
- a "Files in this chat" list (names, or "No files yet").

It is purely presentational; all state and callbacks come from `App`.

### `frontend/src/components/Feed.jsx`

The message list:
- renders user messages as plain text plus attached filenames;
- renders assistant messages through `react-markdown`;
- adds an `err` class for errors;
- auto-scrolls to the bottom unless the user has scrolled up more than 120 px;
- uses `role="log"` and `aria-live="polite"` for screen readers.

The `busy` prop shows an ellipsis for an empty assistant message while waiting.

### `frontend/src/components/InputBar.jsx`

The composer:
- hidden multi-file picker with `accept` and `multiple`;
- paperclip button;
- auto-expanding textarea (up to 200 px);
- Enter sends, Shift+Enter inserts a newline;
- send button becomes a Stop button while busy;
- disabled state when there is no text and no attachment.

### `frontend/src/components/AttachmentTray.jsx`

The attachment chips:
- kind label (PDF/DOCX/TXT) from the extension or MIME type;
- filename and size;
- state-specific UI: `queued` (remove button), `uploading` (progress bar and
  percentage), `processing` ("Indexing document in ChromaDB…"), `ready`
  ("Ready in ChromaDB"), `failed` (stored error message);
- remove button for `queued`, `ready`, and `failed`.

### `frontend/src/QueryForm.jsx`

A simple labelled textarea + submit button. Calls `onSubmit(query.trim())` and
disables itself while `loading`. It does not call the API itself.

**Status:** Not mounted by `App.jsx`. Kept as a reusable, accessible query
form.

### `frontend/src/AnswerPanel.jsx`

Renders an answer string in a labelled section, with a fallback message for
empty/non-string answers.

**Status:** Not mounted.

### `frontend/src/EvidenceList.jsx`

Renders an evidence array:
- filters to objects;
- shows source, page, level, and score (3 decimals) when present;
- shows text;
- tolerates missing fields with a placeholder.

**Status:** Mounted by `Feed` inside the "Inspect Evidence" accordion.

### `frontend/src/RoutingBadges.jsx`

Renders routing metadata as badges:
- complexity (green/yellow/red for simple/moderate/complex and low/medium/high);
- retrieval depth;
- each activated module (underscores replaced with spaces);
- "No modules activated" when the list is empty.

**Status:** Mounted by `Feed` when an assistant message has routing data.

### `frontend/src/chat.css`

The stylesheet actually imported by `App.jsx`. It defines the chat layout,
sidebar, feed bubbles, input bar, attachment tray, dropzone, badges, and
responsive behavior. If you change the chat look, this is the file.

### `frontend/src/styles.css`

A stylesheet for the unused single-query components (`panel`, `answer`,
`evidence-list`, `badges`). It is **not imported anywhere** in the current
entry path. If you mount those components, import this file or copy the needed
rules into `chat.css`.

### `frontend/public/graft-logo.png`

Static logo referenced by the header (`/graft-logo.png`).

### `frontend/tests/query.test.js`

Node's built-in test runner (`node:test`) with `node:assert/strict`. It tests
`submitQuery`:

1. Uses the relative `/query` path, POSTs JSON, and preserves the backend
   response.
2. Surfaces `detail` strings, FastAPI validation arrays, and
   `error.message`.
3. Handles network failures, HTTP errors, non-JSON bodies, and `null` bodies.

Run it from `frontend/`:

```bash
node --test tests/query.test.js
```

There is no `npm test` script; add one if you want CI to run it.

### `frontend/vite.config.js`

```js
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/query": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/index": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
```

`/api` is required for both file upload/polling and `/api/chat`. `/analytics`
is not proxied because the mounted UI does not call it.

### `frontend/vite.local.config.js`

Some developer machines have an untracked local override (for example, an API
on port 8001 because 8000 is occupied). It is not part of the committed repo.
Do not depend on it; use `vite.config.js` or `VITE_API_URL`.

---

## State flow in one picture

```text
User drops files
        |
        v
addFiles -> validate -> uploadFile -> POST /api/files
                              |
                              v
                    pollFileStatus -> GET /api/files/{id}
                              |
                              v
                    queue state: uploading -> processing -> ready
                    session.fileIds += server id

User types in InputBar
        |
        v
App.text --- send() ---> prompt + ready fileIds
        |                       |
        |                       v
        |             append user + assistant messages
        |             set busy
        |                       |
        |                       v
        |             sendChat(JSON) -> POST /api/chat
        |                       |
        |         SSE meta/token  -> onEvent -> message updates
        |         SSE status      -> onEvent -> attachment state
        |         SSE error       -> onEvent -> error bubble
        |         HTTP error      -> onError -> error bubble + notice
        |         load/error/abort-> onDone  -> busy=false
        v
React re-renders Feed / Sidebar / AttachmentTray
```

The browser holds sessions, messages, and file IDs. The backend keeps only the
in-memory `FILES` records for uploads; it receives no conversation history.
Each chat request is stateless apart from the file lookup and the metadata
filter.

---

## Using the working `/query` client today

If you want a functional question/answer demo without touching the chat
transport, mount the existing single-query pieces. For example, replace the
`<Feed />` and footer in `App.jsx` with a small container, or create a
`QueryView.jsx`:

```jsx
import { useState } from "react";
import QueryForm from "./QueryForm";
import AnswerPanel from "./AnswerPanel";
import EvidenceList from "./EvidenceList";
import RoutingBadges from "./RoutingBadges";
import { submitQuery } from "./query";
import "./styles.css";

export default function QueryView() {
  const [answer, setAnswer] = useState("");
  const [evidence, setEvidence] = useState([]);
  const [routing, setRouting] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function ask(query) {
    setLoading(true);
    setError("");
    try {
      const data = await submitQuery(query);
      setAnswer(data.answer);
      setEvidence(data.evidence ?? []);
      setRouting(data.routing ?? null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="panel-stack">
      <QueryForm onSubmit={ask} loading={loading} />
      {error && <p className="notice" role="alert">{error}</p>}
      <RoutingBadges routing={routing} />
      <AnswerPanel answer={answer} />
      <EvidenceList evidence={evidence} />
    </main>
  );
}
```

Prerequisites for that view:

1. The API is running on port 8000.
2. `vite.config.js` still proxies `/query` (it does).
3. `styles.css` is imported (as above) or its rules are merged into
   `chat.css`.

This is the fastest way to see the real GRAFT pipeline in a browser.

---

## Extending the UI

### Add a new component

1. Put it in `frontend/src/components/`.
2. Keep it presentational; pass data and callbacks from `App` or a container.
3. Use the existing CSS conventions (`panel`, `muted`, `badge`, `notice`).
4. Add `aria-label` / `role` where the existing components do.
5. If it needs new colors/layout, add rules to `chat.css` (not `styles.css`)
   unless you are reviving the old query view.

### Add a new API call

- For JSON request/response, follow `query.js`: `fetch`, explicit content type,
  JSON parse, normalize errors.
- For streaming, follow `api.js`: XHR for upload progress + incremental
  `responseText` parsing.
- Keep error messages user-facing; do not surface raw stack traces.
- Add a Node test if the function is pure enough (mock `fetch` as
  `query.test.js` does).

### Add a new session field

`sessions` entries are `{id, title, messages, files, fileIds}`. If you add a
field, initialize it in `newSession()` and preserve it in every `patch()`
callback. Because `patch` spreads the old session (`...s`), most additions are
backward-compatible.

---

## Known limitations

| Limitation | Impact | Where to fix |
|---|---|---|
| Upload records are in memory | Refresh or API restart loses file IDs | Add a persistent store behind `FILES` |
| No session persistence | Refresh loses chats | localStorage or a backend store |
| No conversation memory | Each query is independent | Extend the request schema intentionally |
| No upload/chat tests | Regressions in the two-phase flow are not caught | Add tests for `uploadFile`, `pollFileStatus`, `sendChat` |
| No frontend lint/build step in CI | Regressions can slip through | `.github/workflows/ci.yml` |
| `styles.css` unused | Old query components look unstyled if mounted | Import it or merge rules |

The two-phase upload/chat flow itself is implemented; chapter 06 explains the
request sequence and failure handling.

---

Next: [06 - Frontend/backend integration](06-frontend-backend-integration.md)
