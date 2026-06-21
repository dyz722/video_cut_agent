"""Local web viewer for project run events and tool logs."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import urllib.parse
import webbrowser

from . import config, log_store
from .events import EVENTS


_SERVER = None
_SERVER_PORT = None
_SERVER_ROOT = None


def _json(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


def _int_param(query: dict, name: str, default: int, maximum: int = 5000) -> int:
    raw = query.get(name, [str(default)])[0]
    try:
        return min(max(int(raw), 1), maximum)
    except ValueError:
        return default


def _html() -> str:
    project = str(config.PROJECT_DIR)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>veoai logs</title>
  <style>
    :root {{ color-scheme: dark; --bg:#0b0d12; --panel:#151923; --line:#2a3040;
      --text:#f4f7fb; --muted:#9aa4b2; --brand:#7c9cff; --issue:#ff7a7a; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Noto Sans SC",sans-serif;
      background:var(--bg); color:var(--text); }}
    header {{ position:sticky; top:0; z-index:5; padding:14px 18px; background:#0f131c;
      border-bottom:1px solid var(--line); display:flex; justify-content:space-between;
      gap:12px; align-items:center; }}
    h1 {{ font-size:18px; margin:0; }}
    main {{ max-width:1500px; margin:auto; padding:18px; display:grid; gap:14px; }}
    .muted {{ color:var(--muted); font-size:12px; }}
    .toolbar {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
    button, select, input {{ background:#151923; color:var(--text); border:1px solid var(--line);
      border-radius:8px; padding:8px 10px; font:inherit; }}
    button.active {{ background:var(--brand); color:#07101f; font-weight:700; }}
    section {{ border:1px solid var(--line); background:var(--panel); border-radius:8px; overflow:hidden; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ border-bottom:1px solid var(--line); padding:8px; vertical-align:top; }}
    th {{ text-align:left; color:var(--muted); font-weight:600; background:#10141d; }}
    tr:hover {{ background:#1b2130; }}
    code, pre {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    pre {{ white-space:pre-wrap; margin:0; max-height:260px; overflow:auto; color:#d7deea; }}
    .kind-issue {{ color:var(--issue); font-weight:700; }}
    .summary {{ max-width:680px; }}
    .hidden {{ display:none; }}
  </style>
</head>
<body>
<header>
  <div>
    <h1>veoai logs</h1>
    <div class="muted">{project}</div>
  </div>
  <div class="toolbar">
    <button id="eventsBtn" class="active" onclick="setTab('events')">Events</button>
    <button id="toolsBtn" onclick="setTab('tools')">Tools</button>
    <select id="kind" onchange="render()">
      <option value="">all kinds</option>
      <option>status</option><option>plan</option><option>tool</option>
      <option>obs</option><option>issue</option><option>bg</option>
    </select>
    <input id="q" placeholder="filter text" oninput="render()">
    <button onclick="load()">Refresh</button>
  </div>
</header>
<main>
  <div id="status" class="muted"></div>
  <section id="eventsSec"><table id="events"></table></section>
  <section id="toolsSec" class="hidden"><table id="tools"></table></section>
</main>
<script>
let tab = 'events';
let events = [];
let tools = [];

function esc(v) {{
  return String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function setTab(next) {{
  tab = next;
  document.getElementById('eventsBtn').classList.toggle('active', tab === 'events');
  document.getElementById('toolsBtn').classList.toggle('active', tab === 'tools');
  document.getElementById('eventsSec').classList.toggle('hidden', tab !== 'events');
  document.getElementById('toolsSec').classList.toggle('hidden', tab !== 'tools');
  document.getElementById('kind').disabled = tab !== 'events';
  render();
}}
function matches(row) {{
  const q = document.getElementById('q').value.trim().toLowerCase();
  if (!q) return true;
  return JSON.stringify(row).toLowerCase().includes(q);
}}
async function load() {{
  const [e, t, s] = await Promise.all([
    fetch('/api/events?limit=1000').then(r => r.json()),
    fetch('/api/tools?limit=500').then(r => r.json()),
    fetch('/api/status').then(r => r.json()),
  ]);
  events = e.events || [];
  tools = t.tools || [];
  document.getElementById('status').textContent =
    `${{events.length}} events · ${{tools.length}} tool calls · ${{s.log_dir || ''}}`;
  render();
}}
function renderEvents() {{
  const kind = document.getElementById('kind').value;
  const rows = events.filter(e => (!kind || e.kind === kind) && matches(e)).reverse();
  document.getElementById('events').innerHTML =
    '<tr><th>time</th><th>run</th><th>kind</th><th>name</th><th>summary</th></tr>' +
    rows.map(e => `<tr>
      <td>${{esc(e.created_at || e.ts || '')}}</td>
      <td><code>${{esc(e.run_id || '')}}</code></td>
      <td class="kind-${{esc(e.kind)}}">${{esc(e.kind || '')}}</td>
      <td>${{esc(e.name || '')}}</td>
      <td class="summary">${{esc(e.summary || '')}}</td>
    </tr>`).join('');
}}
function renderTools() {{
  const rows = tools.filter(matches).reverse();
  document.getElementById('tools').innerHTML =
    '<tr><th>time</th><th>run</th><th>tool</th><th>summary</th><th>input</th><th>output</th></tr>' +
    rows.map(t => `<tr>
      <td>${{esc(t.created_at || t.ts || '')}}</td>
      <td><code>${{esc(t.run_id || '')}}</code></td>
      <td>${{esc(t.name || '')}}</td>
      <td class="summary">${{esc(t.summary || '')}}</td>
      <td><pre>${{esc(JSON.stringify(t.input ?? '', null, 2))}}</pre></td>
      <td><pre>${{esc(t.output || '')}}</pre></td>
    </tr>`).join('');
}}
function render() {{
  if (tab === 'events') renderEvents();
  else renderTools();
}}
load();
setInterval(load, 5000);
</script>
</body>
</html>"""


def _make_handler():
    class LogHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _send_json(self, data: dict):
            body = _json(data)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path in ("", "/"):
                body = _html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/events":
                limit = _int_param(query, "limit", 1000)
                self._send_json({"events": log_store.read_jsonl(log_store.EVENT_LOG, limit)})
                return
            if parsed.path == "/api/tools":
                limit = _int_param(query, "limit", 500)
                self._send_json({"tools": log_store.read_jsonl(log_store.TOOL_LOG, limit)})
                return
            if parsed.path == "/api/status":
                self._send_json({
                    "project": str(config.PROJECT_DIR),
                    "log_dir": str(log_store.log_dir()),
                    "current_run": EVENTS.current_run,
                })
                return
            self.send_error(404)

    return LogHandler


def ensure_log_server() -> int:
    global _SERVER, _SERVER_PORT, _SERVER_ROOT
    root = config.PROJECT_DIR.resolve()
    if _SERVER and _SERVER_ROOT == root:
        return _SERVER_PORT
    _SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler())
    _SERVER_PORT = _SERVER.server_address[1]
    _SERVER_ROOT = root
    thread = threading.Thread(target=_SERVER.serve_forever, daemon=True)
    thread.start()
    return _SERVER_PORT


def open_log_view(open_browser: bool = True) -> str:
    port = ensure_log_server()
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        webbrowser.open(url)
    return f"Log viewer ready: {url}\n{log_store.log_summary()}"
