import os, json, mimetypes
from datetime import datetime, timezone
from pathlib import Path
from email.message import EmailMessage

import aiosmtplib
import aiofiles
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
import uvicorn

# ── Config ──────────────────────────────────────────────────────────────────
UPLOAD_DIR   = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))
ADMIN_TOKEN  = os.environ.get("FILEDROPS_ADMIN_TOKEN", "changeme")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "alecneukirch@gmail.com")
SMTP_HOST    = os.environ.get("SMTP_HOST", "")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASS", "")
SMTP_FROM    = os.environ.get("SMTP_FROM", "")
MAX_BYTES    = int(os.environ.get("MAX_UPLOAD_MB", "150")) * 1024 * 1024

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(docs_url=None, redoc_url=None)

# ── Helpers ──────────────────────────────────────────────────────────────────
def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_-]", "_", s.strip().lower())[:48]

async def _send_email(subject: str, body: str):
    if not SMTP_HOST:
        return
    msg = EmailMessage()
    msg["From"]    = SMTP_FROM
    msg["To"]      = NOTIFY_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASS,
            start_tls=True,
        )
    except Exception as e:
        print(f"[email] send failed: {e}")

# ── HTML templates ────────────────────────────────────────────────────────────
_BASE_STYLE = """
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {
    --bg: #0d0d14; --surface: #14141f; --border: rgba(255,255,255,0.08);
    --text: #e8e8f0; --text2: #a0a0b8; --text3: #606078;
    --accent: #7c6af7; --green: #2ecc71; --red: #e74c3c;
    --mono: 'JetBrains Mono', 'Fira Mono', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif;
         min-height: 100vh; display: flex; flex-direction: column; align-items: center;
         padding: 48px 16px 64px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
          padding: 36px 40px; width: 100%; max-width: 560px; }
  h1 { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
  .sub { font-family: var(--mono); font-size: 12px; color: var(--text3); margin-bottom: 28px; }
  label { display: block; font-family: var(--mono); font-size: 11px; color: var(--text3);
          letter-spacing: .06em; text-transform: uppercase; margin-bottom: 5px; }
  input[type=text], input[type=email] {
    width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 5px;
    padding: 9px 12px; color: var(--text); font-size: 14px; outline: none;
    transition: border-color .15s; }
  input:focus { border-color: var(--accent); }
  .field { margin-bottom: 18px; }
  .drop-zone { border: 2px dashed var(--border); border-radius: 8px; padding: 32px 20px;
               text-align: center; cursor: pointer; transition: border-color .15s, background .15s;
               margin-bottom: 20px; }
  .drop-zone.over { border-color: var(--accent); background: rgba(124,106,247,0.07); }
  .drop-zone p { color: var(--text2); font-size: 14px; margin-bottom: 6px; }
  .drop-zone small { font-family: var(--mono); font-size: 11px; color: var(--text3); }
  #file-list { margin-top: 10px; font-family: var(--mono); font-size: 12px; color: var(--text2); }
  .btn { background: var(--accent); color: #fff; border: none; border-radius: 6px;
         padding: 11px 24px; font-size: 14px; font-weight: 600; cursor: pointer;
         width: 100%; transition: opacity .15s; }
  .btn:hover { opacity: .88; }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .msg { font-family: var(--mono); font-size: 13px; padding: 12px 16px; border-radius: 6px;
         margin-bottom: 20px; }
  .msg-ok  { background: rgba(46,204,113,.12); color: var(--green); border: 1px solid rgba(46,204,113,.3); }
  .msg-err { background: rgba(231,76,60,.12); color: var(--red);   border: 1px solid rgba(231,76,60,.3); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 20px; }
  th { font-family: var(--mono); font-size: 11px; color: var(--text3); text-align: left;
       padding: 6px 10px; border-bottom: 1px solid var(--border); }
  td { padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: top; }
  td.mono { font-family: var(--mono); font-size: 12px; color: var(--text2); }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .logo { font-family: var(--mono); font-size: 13px; color: var(--text3); margin-bottom: 32px; }
  .logo span { color: var(--accent); }
</style>
"""

UPLOAD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>{style}<title>File Drop · swu.guru</title></head>
<body>
<div class="logo"><span>swu</span>.guru / files</div>
<div class="card">
  <h1>Deck Sheet Drop</h1>
  <p class="sub">files.swu.guru · submit scanned registration sheets</p>
  {msg}
  <form id="f" method="post" action="/upload" enctype="multipart/form-data">
    <div class="field">
      <label>Your name / org</label>
      <input type="text" name="to_name" placeholder="e.g. Brute Squad Gaming" required>
    </div>
    <div class="field">
      <label>Event name</label>
      <input type="text" name="event_name" placeholder="e.g. LAW Planetary Qualifier 2026-05-10" required>
    </div>
    <div class="field">
      <label>Your email (optional — for confirmation)</label>
      <input type="email" name="sender_email" placeholder="you@example.com">
    </div>
    <div class="field">
      <label>Notes (optional)</label>
      <input type="text" name="notes" placeholder="double-sided, round count, etc.">
    </div>
    <div class="drop-zone" id="dz" onclick="document.getElementById('fi').click()">
      <p>Drop files here or click to browse</p>
      <small>PDF, PNG, JPG, TIFF &nbsp;·&nbsp; up to {max_mb} MB per file &nbsp;·&nbsp; multiple allowed</small>
      <div id="file-list"></div>
    </div>
    <input type="file" id="fi" name="files" multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"
           style="display:none" onchange="onFiles(this)">
    <button class="btn" type="submit" id="sub" disabled>Upload Files</button>
  </form>
</div>
<script>
const dz = document.getElementById('dz');
const fl = document.getElementById('file-list');
const sub = document.getElementById('sub');
const fi = document.getElementById('fi');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
dz.addEventListener('dragleave', () => dz.classList.remove('over'));
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('over');
  fi.files = e.dataTransfer.files; onFiles(fi);
});
function onFiles(inp) {
  const names = Array.from(inp.files).map(f => f.name);
  fl.textContent = names.length ? names.join(', ') : '';
  sub.disabled = !names.length;
}
</script>
</body></html>
"""

SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>{style}<title>Uploaded · swu.guru</title></head>
<body>
<div class="logo"><span>swu</span>.guru / files</div>
<div class="card">
  <h1>Files received</h1>
  <p class="sub">files.swu.guru · submit scanned registration sheets</p>
  <div class="msg msg-ok">✓ {count} file(s) uploaded successfully. Thanks!</div>
  <a href="/" style="font-family:var(--mono);font-size:13px">← Submit more</a>
</div>
</body></html>
"""

ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>{style}<title>Admin · files.swu.guru</title></head>
<body>
<div class="logo"><span>swu</span>.guru / files / admin</div>
<div class="card" style="max-width:900px">
  <h1>Uploads</h1>
  <p class="sub">{count} submission(s)</p>
  <table>
    <thead><tr><th>Received</th><th>Event</th><th>From</th><th>Notes</th><th>Files</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
</body></html>
"""

# ── Routes ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    max_mb = MAX_BYTES // (1024 * 1024)
    return UPLOAD_HTML.format(style=_BASE_STYLE, msg="", max_mb=max_mb)


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    to_name:      str = Form(...),
    event_name:   str = Form(...),
    sender_email: str = Form(""),
    notes:        str = Form(""),
    files:        list[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(400, "No files provided")

    ts      = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dirname = f"{ts}_{_slug(to_name)}_{_slug(event_name)}"
    dest    = UPLOAD_DIR / dirname
    dest.mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"File type not allowed: {ext}")
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(400, f"{f.filename} exceeds size limit")
        safe_name = Path(f.filename).name
        async with aiofiles.open(dest / safe_name, "wb") as out:
            await out.write(data)
        saved.append(safe_name)

    meta = {
        "received":     ts,
        "to_name":      to_name,
        "event_name":   event_name,
        "sender_email": sender_email,
        "notes":        notes,
        "files":        saved,
    }
    async with aiofiles.open(dest / "meta.json", "w") as out:
        await out.write(json.dumps(meta, indent=2))

    body = (
        f"New deck sheet submission received.\n\n"
        f"From:   {to_name}\n"
        f"Email:  {sender_email or '(not provided)'}\n"
        f"Event:  {event_name}\n"
        f"Notes:  {notes or '(none)'}\n"
        f"Files:  {', '.join(saved)}\n"
        f"Folder: {dirname}\n"
    )
    await _send_email(f"[files.swu.guru] {event_name} — {to_name}", body)

    max_mb = MAX_BYTES // (1024 * 1024)
    return SUCCESS_HTML.format(style=_BASE_STYLE, count=len(saved))


@app.get("/admin", response_class=HTMLResponse)
async def admin(token: str = ""):
    if token != ADMIN_TOKEN:
        raise HTTPException(403, "Forbidden")

    submissions = sorted(UPLOAD_DIR.iterdir(), reverse=True) if UPLOAD_DIR.exists() else []
    rows = ""
    count = 0
    for d in submissions:
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        count += 1
        file_links = " &nbsp; ".join(
            f'<a href="/admin/file/{d.name}/{fn}?token={token}">{fn}</a>'
            for fn in meta.get("files", [])
        )
        rows += (
            f"<tr>"
            f"<td class='mono'>{meta.get('received','')}</td>"
            f"<td>{meta.get('event_name','')}</td>"
            f"<td>{meta.get('to_name','')} {('<br><small style=\"color:var(--text3)\">' + meta['sender_email'] + '</small>') if meta.get('sender_email') else ''}</td>"
            f"<td class='mono' style='font-size:11px'>{meta.get('notes','')}</td>"
            f"<td>{file_links}</td>"
            f"</tr>"
        )

    return ADMIN_HTML.format(style=_BASE_STYLE, count=count, rows=rows or "<tr><td colspan='5' style='color:var(--text3);font-family:var(--mono);padding:24px'>no submissions yet</td></tr>")


@app.get("/admin/file/{dirname}/{filename}")
async def serve_file(dirname: str, filename: str, token: str = ""):
    if token != ADMIN_TOKEN:
        raise HTTPException(403, "Forbidden")
    path = UPLOAD_DIR / dirname / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404)
    mime, _ = mimetypes.guess_type(str(path))
    return FileResponse(str(path), media_type=mime or "application/octet-stream",
                        filename=filename)


@app.get("/health")
async def health():
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
