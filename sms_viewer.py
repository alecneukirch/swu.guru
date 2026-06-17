#!/usr/bin/env python3
"""SMS Backup Viewer — run this then open http://localhost:7878"""

import json, time
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

SMS_FILE = Path(__file__).parent / "sms-20260616211940.xml"
PORT = 7878
PAGE = 80

# ── Parse ─────────────────────────────────────────────────────────────────────

def load(path):
    print(f"Parsing {path.name} …", flush=True)
    t0 = time.time()
    contacts = {}
    root = None

    for event, elem in ET.iterparse(str(path), events=("start", "end")):
        if event == "start":
            if root is None:
                root = elem
            continue

        if elem.tag not in ("sms", "mms"):
            continue

        address  = (elem.get("address") or "").strip() or "(Unknown)"
        name     = (elem.get("contact_name") or "").strip()
        if not name or name.lower() in ("(unknown)", "null"):
            name = address

        date_ms  = int(elem.get("date") or 0)
        readable = elem.get("readable_date") or ""
        sent     = int(elem.get("type") or elem.get("msg_box") or 1) == 2

        if elem.tag == "sms":
            body   = elem.get("body") or ""
            if body == "null":
                body = ""
            is_mms = False
        else:
            body = ""
            for p in elem.findall("./parts/part"):
                ct = p.get("ct") or ""
                if ct.startswith("text/"):
                    t = p.get("text") or ""
                    if t and t != "null":
                        body = t
                        break
            is_mms = True

        digits = "".join(c for c in address if c.isdigit())
        key    = digits[-10:] if len(digits) >= 7 else address

        if key not in contacts:
            contacts[key] = dict(
                key=key, name=name, address=address,
                messages=[], last_date=0, last_body=""
            )
        else:
            c = contacts[key]
            cur = c["name"]
            is_number = cur.replace("+", "").replace(" ", "").isdigit()
            if (cur in (c["address"], key) or is_number):
                if name not in (address, key) and not name.replace("+","").replace(" ","").isdigit():
                    c["name"] = name

        msg = {"d": date_ms, "r": readable, "b": body, "s": sent, "m": is_mms}
        c   = contacts[key]
        c["messages"].append(msg)
        if date_ms > c["last_date"]:
            c["last_date"] = date_ms
            c["last_body"] = body[:100] if body else ("[media]" if is_mms else "")

        root.clear()

    for c in contacts.values():
        c["messages"].sort(key=lambda m: m["d"])

    elapsed = time.time() - t0
    total   = sum(len(c["messages"]) for c in contacts.values())
    print(f"  {total:,} messages · {len(contacts):,} contacts · {elapsed:.1f}s", flush=True)
    return contacts


print("Starting SMS Viewer…")
contacts_db   = load(SMS_FILE)
contact_list  = sorted(
    [{"key":   c["key"],
      "name":  c["name"],
      "address": c["address"],
      "count": len(c["messages"]),
      "last_date": c["last_date"],
      "last_body": c["last_body"]}
     for c in contacts_db.values()],
    key=lambda x: -x["last_date"]
)

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Messages</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;1,400&family=JetBrains+Mono:wght@400;500&family=Crimson+Pro:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
<style>
:root {
  --bg:           #0e0c0a;
  --sidebar:      #111009;
  --hover:        #171510;
  --active:       #1e1b12;
  --border:       rgba(255,248,220,0.07);
  --border-med:   rgba(255,248,220,0.12);
  --text:         #d6d1c8;
  --text2:        #8a8479;
  --text3:        #4e4a45;
  --accent:       #c9a96f;
  --accent-dim:   rgba(201,169,111,0.12);
  --sent-bg:      #231c0d;
  --sent-border:  #3e3018;
  --sent-text:    #ddc9a2;
  --recv-bg:      #0e1521;
  --recv-border:  #1d2e48;
  --recv-text:    #b5c8da;
  --sep-line:     rgba(255,248,220,0.06);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; background: var(--bg); color: var(--text); }

::-webkit-scrollbar       { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #2c2820; border-radius: 2px; }
::-webkit-scrollbar-thumb:hover { background: #3e3830; }

/* ── Layout ── */
.layout { display: flex; height: 100vh; }

/* ── Sidebar ── */
.sidebar {
  width: 308px; flex-shrink: 0;
  background: var(--sidebar);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
}
.sidebar-head {
  padding: 22px 18px 14px;
  border-bottom: 1px solid var(--border);
}
.sidebar-title {
  font-family: 'Cormorant Garamond', serif;
  font-size: 22px; font-weight: 600; letter-spacing: 0.01em;
  color: var(--text); margin-bottom: 13px;
  display: flex; align-items: baseline; gap: 8px;
}
.sidebar-title-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; font-weight: 400;
  color: var(--text3);
}
.search-wrap { position: relative; }
.search-icon {
  position: absolute; left: 10px; top: 50%;
  transform: translateY(-50%);
  color: var(--text3); font-size: 14px; pointer-events: none;
  font-family: 'JetBrains Mono', monospace;
}
#search {
  width: 100%;
  background: rgba(255,248,220,0.04);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px 8px 30px;
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  outline: none;
  transition: border-color 0.15s;
}
#search::placeholder { color: var(--text3); }
#search:focus { border-color: var(--accent); }

.contact-list { flex: 1; overflow-y: auto; }

.contact-item {
  display: flex; align-items: center; gap: 11px;
  padding: 10px 14px 10px 16px;
  cursor: pointer;
  border-left: 2px solid transparent;
  transition: background 0.1s, border-color 0.1s;
  position: relative;
}
.contact-item + .contact-item { border-top: 1px solid var(--border); }
.contact-item:hover { background: var(--hover); }
.contact-item.active {
  background: var(--active);
  border-left-color: var(--accent);
}
.contact-item.active::after {
  content: '';
  position: absolute; inset: 0;
  background: var(--accent-dim);
  pointer-events: none;
}

.avatar {
  width: 40px; height: 40px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-family: 'Cormorant Garamond', serif;
  font-size: 15px; font-weight: 600;
  flex-shrink: 0; user-select: none;
}
.ci-body { flex: 1; min-width: 0; }
.ci-name {
  font-family: 'Cormorant Garamond', serif;
  font-size: 15.5px; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  line-height: 1.2; color: var(--text);
}
.ci-preview {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; color: var(--text2);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  margin-top: 3px;
}
.ci-right {
  flex-shrink: 0;
  display: flex; flex-direction: column; align-items: flex-end; gap: 4px;
}
.ci-date { font-family: 'JetBrains Mono', monospace; font-size: 9.5px; color: var(--text3); }
.ci-count { font-family: 'JetBrains Mono', monospace; font-size: 9px; color: var(--text3); }

/* ── Main pane ── */
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }

.chat-header {
  padding: 16px 28px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 14px;
  flex-shrink: 0;
}
.ch-avatar {
  width: 44px; height: 44px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-family: 'Cormorant Garamond', serif;
  font-size: 18px; font-weight: 600; flex-shrink: 0;
}
.ch-info { flex: 1; }
.ch-name {
  font-family: 'Cormorant Garamond', serif;
  font-size: 21px; font-weight: 600; line-height: 1.2; color: var(--text);
}
.ch-addr {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; color: var(--text2); margin-top: 1px;
}
.ch-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; color: var(--text3);
}

.msgs-wrap {
  flex: 1; overflow-y: auto;
  padding: 24px 32px 24px;
  display: flex; flex-direction: column;
  scroll-behavior: auto;
}

.load-earlier {
  display: flex; justify-content: center;
  padding: 0 0 20px;
}
.load-earlier-btn {
  background: transparent;
  border: 1px solid var(--border-med);
  color: var(--text2);
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  padding: 7px 20px;
  border-radius: 4px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
  letter-spacing: 0.02em;
}
.load-earlier-btn:hover { border-color: var(--accent); color: var(--accent); }
.load-earlier-btn:disabled { opacity: 0.4; cursor: default; pointer-events: none; }

.date-sep {
  display: flex; align-items: center; gap: 12px;
  padding: 16px 0 8px;
}
.date-sep-line { flex: 1; height: 1px; background: var(--sep-line); }
.date-sep-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; color: var(--text3);
  white-space: nowrap; letter-spacing: 0.05em;
}

.msg-row { display: flex; padding: 2px 0; }
.msg-row.sent { justify-content: flex-end; }
.msg-row.recv { justify-content: flex-start; }

.bubble {
  max-width: 66%;
  padding: 9px 14px 8px;
  border-radius: 14px;
  font-family: 'Crimson Pro', Georgia, serif;
  font-size: 16px; line-height: 1.55;
  word-break: break-word;
}
.bubble.sent {
  background: var(--sent-bg); border: 1px solid var(--sent-border);
  color: var(--sent-text); border-bottom-right-radius: 3px;
}
.bubble.recv {
  background: var(--recv-bg); border: 1px solid var(--recv-border);
  color: var(--recv-text); border-bottom-left-radius: 3px;
}
.bubble-meta {
  display: flex; align-items: center; gap: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9.5px; color: var(--text3);
  margin-top: 5px;
}
.msg-row.sent .bubble-meta { justify-content: flex-end; }
.mms-badge {
  font-size: 8.5px; color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 3px; padding: 0 4px; line-height: 1.6;
  letter-spacing: 0.04em;
}
.media-placeholder {
  font-style: italic;
  font-size: 14px;
  color: var(--text3);
}

/* ── Empty / loading states ── */
.center-state {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  gap: 14px; color: var(--text3);
}
.center-icon {
  font-size: 44px; opacity: 0.25; user-select: none;
}
.center-text {
  font-family: 'Cormorant Garamond', serif;
  font-size: 18px; color: var(--text3);
}
.spinner {
  width: 26px; height: 26px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.no-results {
  display: flex; justify-content: center;
  padding: 40px 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; color: var(--text3);
  letter-spacing: 0.05em;
}
</style>
</head>
<body>
<div class="layout">

  <aside class="sidebar">
    <div class="sidebar-head">
      <div class="sidebar-title">
        Messages
        <span class="sidebar-title-count" id="contact-count"></span>
      </div>
      <div class="search-wrap">
        <span class="search-icon">⌕</span>
        <input id="search" type="text" placeholder="search contacts…" autocomplete="off" spellcheck="false">
      </div>
    </div>
    <div class="contact-list" id="contact-list">
      <div class="center-state" style="height:200px"><div class="spinner"></div></div>
    </div>
  </aside>

  <main class="main" id="main">
    <div class="center-state">
      <div class="center-icon">✉</div>
      <div class="center-text">Select a contact</div>
    </div>
  </main>

</div>
<script>
// ── Avatar ────────────────────────────────────────────────────────────────────
const PALETTE = [
  ['#261c0a','#c9a96f'], ['#0c1a2a','#6aa0c8'], ['#0c2618','#6ec88c'],
  ['#260c1a','#c86e90'], ['#190c26','#8e6ec8'], ['#26190c','#c8916e'],
  ['#0c2626','#6ec8c8'], ['#201c0c','#c8c06e'], ['#1a0c0c','#c87070'],
  ['#0c1a1a','#70b8b8'],
];
function avatarFor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff;
  return PALETTE[h % PALETTE.length];
}
function initials(name) {
  const p = name.trim().split(/\s+/);
  if (p.length === 1) return p[0].slice(0, 2).toUpperCase();
  return (p[0][0] + p[p.length - 1][0]).toUpperCase();
}

// ── Date formatting ────────────────────────────────────────────────────────────
function fmtSidebar(ms) {
  if (!ms) return '';
  const d = new Date(ms), now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  if (now - d < 7 * 86400000) return d.toLocaleDateString([], {weekday:'short'});
  if (d.getFullYear() === now.getFullYear()) return d.toLocaleDateString([], {month:'short', day:'numeric'});
  return d.toLocaleDateString([], {month:'short', day:'numeric', year:'2-digit'});
}
function dateSep(readable) {
  if (!readable) return '';
  const m = readable.match(/^(\w+ \d+, \d{4})/);
  return m ? m[1] : readable.split(' ').slice(0,3).join(' ');
}
function msgTime(readable) {
  if (!readable) return '';
  const m = readable.match(/(\d+:\d+(?::\d+)?\s*(?:AM|PM))/i);
  return m ? m[1] : '';
}

// ── Escape ────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) { return String(s).replace(/"/g,'&quot;'); }

// ── State ─────────────────────────────────────────────────────────────────────
let allContacts = [];
let activeKey   = null;
let loadedMsgs  = [];
let curTotal    = 0;
let curOffset   = 0;
let loading     = false;

// ── Bootstrap ─────────────────────────────────────────────────────────────────
(async () => {
  const res = await fetch('/api/contacts');
  allContacts = await res.json();
  document.getElementById('contact-count').textContent = allContacts.length.toLocaleString() + ' contacts';
  renderList(allContacts);
})();

// ── Render contact list ────────────────────────────────────────────────────────
function renderList(list) {
  const el = document.getElementById('contact-list');
  if (!list.length) {
    el.innerHTML = '<div class="no-results">no results</div>';
    return;
  }
  el.innerHTML = list.map(c => {
    const [bg, fg] = avatarFor(c.name);
    const active   = c.key === activeKey ? ' active' : '';
    return `<div class="contact-item${active}" data-key="${escAttr(c.key)}" onclick="pickContact(this)">
  <div class="avatar" style="background:${bg};color:${fg}">${esc(initials(c.name))}</div>
  <div class="ci-body">
    <div class="ci-name">${esc(c.name)}</div>
    <div class="ci-preview">${esc(c.last_body)}</div>
  </div>
  <div class="ci-right">
    <div class="ci-date">${esc(fmtSidebar(c.last_date))}</div>
    <div class="ci-count">${c.count.toLocaleString()}</div>
  </div>
</div>`;
  }).join('');
}

// ── Search ────────────────────────────────────────────────────────────────────
document.getElementById('search').addEventListener('input', function() {
  const q = this.value.trim().toLowerCase();
  renderList(q ? allContacts.filter(c =>
    c.name.toLowerCase().includes(q) || c.address.includes(q)
  ) : allContacts);
});

// ── Pick contact ──────────────────────────────────────────────────────────────
async function pickContact(el) {
  const key = el.dataset.key;
  if (key === activeKey) return;
  activeKey   = key;
  loadedMsgs  = [];
  curOffset   = 0;
  document.querySelectorAll('.contact-item').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('main').innerHTML =
    '<div class="center-state"><div class="spinner"></div></div>';
  await fetchMsgs(key, 0, true);
}

// ── Fetch messages ────────────────────────────────────────────────────────────
async function fetchMsgs(key, offset, replace) {
  if (loading) return;
  loading = true;
  const res  = await fetch(`/api/messages?key=${encodeURIComponent(key)}&offset=${offset}`);
  const data = await res.json();
  loading    = false;

  if (replace) {
    loadedMsgs = data.messages;
  } else {
    loadedMsgs = [...data.messages, ...loadedMsgs];
  }
  curTotal  = data.total;
  curOffset = offset + data.messages.length;

  renderChat(data, replace);
}

// ── Render chat ────────────────────────────────────────────────────────────────
function renderChat(data, scrollBottom) {
  const ci     = allContacts.find(c => c.key === activeKey) || {};
  const name   = data.name || ci.name || activeKey;
  const addr   = data.address || ci.address || '';
  const [bg, fg] = avatarFor(name);

  let html = `<div class="chat-header">
    <div class="ch-avatar" style="background:${bg};color:${fg}">${esc(initials(name))}</div>
    <div class="ch-info">
      <div class="ch-name">${esc(name)}</div>
      <div class="ch-addr">${esc(addr)}</div>
    </div>
    <div class="ch-count">${data.total.toLocaleString()} messages</div>
  </div>
  <div class="msgs-wrap" id="msgs-wrap">`;

  if (data.has_more) {
    html += `<div class="load-earlier">
      <button class="load-earlier-btn" id="load-btn" onclick="loadEarlier()">↑ load earlier</button>
    </div>`;
  }

  let lastSep = '';
  for (const msg of loadedMsgs) {
    const sep = dateSep(msg.r);
    if (sep !== lastSep) {
      html += `<div class="date-sep">
        <div class="date-sep-line"></div>
        <div class="date-sep-text">${esc(sep)}</div>
        <div class="date-sep-line"></div>
      </div>`;
      lastSep = sep;
    }

    const dir     = msg.s ? 'sent' : 'recv';
    const bodyHtml = msg.b
      ? esc(msg.b).replace(/\n/g, '<br>')
      : (msg.m ? '<span class="media-placeholder">[media attachment]</span>' : '');
    const mmsBadge = msg.m ? '<span class="mms-badge">MMS</span>' : '';
    const t        = msgTime(msg.r);

    html += `<div class="msg-row ${dir}">
      <div class="bubble ${dir}">
        ${bodyHtml}
        <div class="bubble-meta">${mmsBadge}<span>${esc(t)}</span></div>
      </div>
    </div>`;
  }

  html += '</div>';
  document.getElementById('main').innerHTML = html;

  const wrap = document.getElementById('msgs-wrap');
  if (scrollBottom) {
    wrap.scrollTop = wrap.scrollHeight;
  }
}

// ── Load earlier ──────────────────────────────────────────────────────────────
async function loadEarlier() {
  const btn  = document.getElementById('load-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'loading…'; }
  const wrap = document.getElementById('msgs-wrap');
  const prev = wrap.scrollHeight;
  await fetchMsgs(activeKey, curOffset, false);
  // Restore scroll position so new messages appear above viewport
  const after = document.getElementById('msgs-wrap');
  after.scrollTop = after.scrollHeight - prev;
}
</script>
</body>
</html>"""

# ── Handler ───────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass  # silence per-request logs

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)

        if parsed.path == "/":
            self._html(HTML)

        elif parsed.path == "/api/contacts":
            self._json(contact_list)

        elif parsed.path == "/api/messages":
            key    = (qs.get("key")    or [""])[0]
            offset = int((qs.get("offset") or ["0"])[0])
            c      = contacts_db.get(key)
            if not c:
                self._json({"error": "not found"}, 404)
                return
            msgs  = c["messages"]
            total = len(msgs)
            start = max(0, total - PAGE - offset)
            end   = max(0, total - offset)
            self._json({
                "key":      key,
                "name":     c["name"],
                "address":  c["address"],
                "total":    total,
                "offset":   offset,
                "has_more": start > 0,
                "messages": msgs[start:end],
            })

        else:
            self.send_response(404)
            self.end_headers()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Open http://localhost:{PORT}", flush=True)
    try:
        HTTPServer(("", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
