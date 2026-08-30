from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import requests
import re
from urllib.parse import urlparse, parse_qs, quote
import os

app = Flask(__name__)
CORS(app)

NDUS_COOKIE = os.environ.get("NDUS_COOKIE", "")
API_KEY = os.environ.get("API_KEY", "")  # optional: set karne par ?key= zaroori hoga

# FIX #1: Ek hi UA sab jagah — dlink UA-bound hota hai, mismatch = 403/download fail
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

# FIX #4: Fallback domains
SHARE_DOMAINS = ["www.terabox.app", "dm.terabox.app", "www.terabox.com",
                 "www.teraboxapp.com", "www.1024tera.com"]

# FIX #2: Open-proxy band — sirf ye hosts allow
ALLOWED_DL_HOSTS = (".terabox.com", ".terabox.app", ".teraboxapp.com",
                    ".terabox.hk", ".terasharelink.com", ".teraboxlink.com",
                    ".1024tera.com", ".mirrobox.com", ".momerybox.com",
                    ".freeterabox.com", ".terabox.fun", ".terabox.club", ".teraboxshare.com")

ERRNO_MAP = {
    -9:  "Invalid/expired NDUS cookie. Render env var NDUS_COOKIE update karo.",
    -20: "Verification required (jsToken block). Thodi der baad retry karo ya naya cookie lagao.",
    -12: "Share link cancelled hai.",
    -7:  "Share link invalid hai ya expired ho gaya.",
    -2:  "File/link expired.",
    -6:  "Login required — cookie galat hai.",
    2:   "Invalid parameters — URL check karo.",
    130: "Share link expired hai.",
}

def extract_surl(url):
    parsed = urlparse(url)
    surl = parse_qs(parsed.query).get('surl', [None])[0]
    if not surl:
        m = re.search(r'/s/([a-zA-Z0-9_-]+)', parsed.path)
        surl = m.group(1) if m else None
    return surl

def check_api_key():
    if not API_KEY:
        return True
    return (request.args.get('key') == API_KEY
            or request.headers.get('X-API-Key') == API_KEY)

def get_terabox_data(url, custom_ndus=None):
    surl = extract_surl(url)
    if not surl:
        return {"error": "Invalid TeraBox URL ya 'surl' missing hai."}

    short_url = surl[1:] if surl.startswith("1") else surl
    cookie = custom_ndus if custom_ndus else NDUS_COOKIE
    cookie_header = f"ndus={cookie}" if cookie else ""

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Cookie": cookie_header})

    # FIX #3/#4: jsToken multiple patterns + multiple domains
    js_token, used_domain = None, None
    for domain in SHARE_DOMAINS:
        try:
            res = session.get(f"https://{domain}/sharing/link?surl={surl}", timeout=15)
            if res.status_code != 200:
                continue
            m = re.search(r'fn%28%22(.*?)%22%29', res.text)
            if not m:
                m = re.search(r'fn\("%22(.*?)%22%29', res.text)  # alt encoding
            if not m:
                m = re.search(r'fn\("(.+?)"\)', res.text)        # decoded page
            if m:
                js_token, used_domain = m.group(1), domain
                break
        except requests.RequestException:
            continue

    if not js_token:
        return {"error": "jsToken nahi mila — TeraBox verification mang raha hai. Kuch der baad retry karo."}

    api_headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://{used_domain}/sharing/link?surl={surl}",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie_header,
    }

    last_errno = None
    for short in (short_url, surl):  # FIX: dono format try karo
        params = {"app_id": "250528", "web": "1", "channel": "dubox", "clienttype": "0",
                  "jsToken": js_token, "shorturl": short, "root": "1"}
        try:
            res = session.get(f"https://{used_domain}/share/list",
                              params=params, headers=api_headers, timeout=15)
            data = res.json()
        except Exception as e:
            return {"error": f"API request failed: {e}"}

        if data.get("errno") == 0:
            return data
        last_errno = data.get("errno")
        if last_errno == -9:
            break  # cookie problem — retry pointless

    msg = ERRNO_MAP.get(last_errno, f"TeraBox API error errno={last_errno}")
    return {"error": msg, "errno": last_errno}

def human_size(b):
    b = float(b)
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.2f} {u}"
        b /= 1024
    return f"{b:.2f} PB"

def build_result(data, full_list=False):
    files = data.get("list", [])
    if not files:
        return None
    host = request.host_url.rstrip('/')
    out = []
    for item in files:
        dlink = item.get("dlink")
        if not dlink:
            continue
        name = item.get("server_filename", "file")
        size_b = int(item.get("size", 0))
        ndus_q = ""
        if request.args.get('ndus'):
            ndus_q = f"&ndus={quote(request.args.get('ndus'))}"
        base = (f"{host}/api/download?dlink={quote(dlink)}"
                f"&filename={quote(name)}{ndus_q}")
        entry = {
            "filename": name,
            "size": human_size(size_b),
            "size_bytes": size_b,
            "is_folder": item.get("isdir", 0) == 1,
            "thumbnail": (item.get("thumbs") or {}).get("url3", ""),
            "download_link": base,
            "download_link_v2": base.replace("/api/download?", "/api/download_v2?"),
        }
        if not full_list:
            entry["direct_dlink"] = dlink  # server-side use ke liye
        out.append(entry)
    return out

@app.route('/api/terabox', methods=['GET'])
def get_terabox_link():
    if not check_api_key():
        return jsonify({"status": "error", "message": "Invalid API key"}), 401
    url = request.args.get('url')
    if not url:
        return jsonify({"status": "error", "message": "'url' query parameter zaroori hai"}), 400

    data = get_terabox_data(url, request.args.get('ndus'))
    if "error" in data:
        return jsonify({"status": "error", "message": data["error"]}), 400

    files = build_result(data)
    if not files:
        return jsonify({"status": "error", "message": "Link mein koi downloadable file nahi mili."}), 404

    return jsonify({"status": "success", "count": len(files), "files": files})

@app.route('/api/download', methods=['GET', 'HEAD'])
def proxy_download():
    return handle_proxy(request, chunk_size=1024 * 512)

@app.route('/api/download_v2', methods=['GET', 'HEAD'])
def proxy_download_v2():
    return handle_proxy(request, chunk_size=8192)

def handle_proxy(req, chunk_size):
    dlink = req.args.get('dlink')
    custom_ndus = req.args.get('ndus')
    filename = req.args.get('filename', 'download')

    if not dlink:
        return jsonify({"error": "dlink missing"}), 400

    # FIX #2: open-proxy band
    host = urlparse(dlink).hostname or ""
    if not any(host == s[1:] or host.endswith(s) for s in ALLOWED_DL_HOSTS):
        return jsonify({"error": "Sirf TeraBox links allowed hain"}), 403

    cookie = custom_ndus if custom_ndus else NDUS_COOKIE
    headers = {
        "User-Agent": UA,  # FIX #1: same UA jo link banate waqt use hua
        "Cookie": f"ndus={cookie}" if cookie else "",
        "Referer": "https://www.terabox.app/",
    }

    try:
        if req.method == 'HEAD':
            upstream = requests.head(dlink, headers=headers,
                                     allow_redirects=True, timeout=20)
            return Response("", status=upstream.status_code,
                            headers=dict((k, v) for k, v in upstream.headers.items()
                                         if k.lower() not in
                                         ('transfer-encoding', 'connection')))

        if 'Range' in req.headers:
            headers['Range'] = req.headers['Range']

        upstream = requests.get(dlink, headers=headers, stream=True,
                                allow_redirects=True, timeout=(10, 60))

        if upstream.status_code not in (200, 206, 416):
            return jsonify({"error": f"TeraBox se download fail — HTTP {upstream.status_code}. "
                                     "Cookie expire ho sakti hai."}), upstream.status_code

        hop = {'content-encoding', 'transfer-encoding', 'connection', 'keep-alive'}
        resp_headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in hop]

        # FIX #6: sahi filename — browser me proper naam se save hoga
        if not any(k.lower() == 'content-disposition' for k, _ in resp_headers):
            resp_headers.append(('Content-Disposition',
                                 f"attachment; filename*=UTF-8''{quote(filename)}"))

        return Response(stream_with_context(upstream.iter_content(chunk_size=chunk_size)),
                        headers=resp_headers,
                        content_type=upstream.headers.get('content-type', 'application/octet-stream'),
                        status=upstream.status_code)
    except requests.Timeout:
        return jsonify({"error": "TeraBox timeout — dobara try karo."}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TeraBox Downloader</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:'Segoe UI',system-ui,sans-serif; background:linear-gradient(135deg,#0f172a,#1e293b);
         min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; color:#e2e8f0; }
  .card { background:#1e293b; border:1px solid #334155; border-radius:16px; padding:32px;
          max-width:520px; width:100%; box-shadow:0 20px 60px rgba(0,0,0,.5); }
  h1 { font-size:1.5rem; margin-bottom:6px; }
  h1 span { color:#38bdf8; }
  p.sub { color:#94a3b8; font-size:.9rem; margin-bottom:20px; }
  .row { display:flex; gap:10px; }
  input { flex:1; padding:12px 14px; border-radius:10px; border:1px solid #334155;
          background:#0f172a; color:#e2e8f0; font-size:.95rem; outline:none; }
  input:focus { border-color:#38bdf8; outline:none; }
  button { padding:12px 20px; border:none; border-radius:10px; cursor:pointer;
           background:#0ea5e9; color:#fff; font-weight:600; font-size:.95rem; }
  button:hover { background:#0284c7; }
  button:disabled { opacity:.5; }
  .result { margin-top:20px; display:none; }
  .file { background:#0f172a; border:1px solid #334155; border-radius:12px; padding:16px;
          display:flex; gap:14px; align-items:center; }
  .file img { width:64px; height:64px; border-radius:8px; object-fit:cover; background:#334155; }
  .file .info { flex:1; min-width:0; }
  .file .name { font-weight:600; word-break:break-all; }
  .file .size { color:#94a3b8; font-size:.85rem; margin-top:2px; }
  .btn-dl { display:inline-block; margin-top:8px; padding:8px 16px; background:#22c55e;
            border-radius:8px; color:#fff; text-decoration:none; font-size:.85rem; font-weight:600; }
  .err { margin-top:16px; background:#450a0a; border:1px solid #b91c1c; color:#fca5a5;
         padding:12px 14px; border-radius:10px; display:none; font-size:.9rem; }
  .spin { display:inline-block; width:16px; height:16px; border:2px solid #fff;
          border-top-color:transparent; border-radius:50%; animation:r .7s linear infinite;
          vertical-align:-3px; margin-right:6px; }
  @keyframes r { to { transform:rotate(360deg); } }
</style>
</head>
<body>
  <div class="card">
    <h1>TeraBox <span>Downloader</span></h1>
    <p class="sub">TeraBox link paste karo — direct download link milega.</p>
    <div class="row">
      <input id="url" type="text" placeholder="https://terabox.com/s/1..." autocomplete="off">
      <button id="go" onclick="fetchLink()">Get Link</button>
    </div>
    <div class="err" id="err"></div>
    <div class="result" id="result"></div>
  </div>
<script>
async function fetchLink() {
  const btn = document.getElementById('go'), err = document.getElementById('err'),
        res = document.getElementById('result'), url = document.getElementById('url').value.trim();
  err.style.display = 'none'; res.style.display = 'none';
  if (!url) { err.textContent = 'Pehle link to daalo.'; err.style.display = 'block'; return; }
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Working...';
  try {
    const r = await fetch('/api/terabox?url=' + encodeURIComponent(url));
    const j = await r.json();
    if (j.status !== 'success') throw new Error(j.message || 'Failed');
    let html = '';
    j.files.forEach(f => {
      html += `<div class="file" style="margin-bottom:12px">
        <img src="${f.thumbnail || ''}" onerror="this.style.visibility='hidden'">
        <div class="info">
          <div class="name">${f.filename}</div>
          <div class="size">${f.size}</div>
          <a class="btn-dl" href="${f.download_link}" target="_blank" rel="noopener">Download</a>
        </div></div>`;
    });
    document.getElementById('result').innerHTML = html;
    res.style.display = 'block';
  } catch (e) {
    err.textContent = e.message; err.style.display = 'block';
  }
  btn.disabled = false; btn.textContent = 'Get Link';
}
document.getElementById('url').addEventListener('keydown', e => { if (e.key === 'Enter') fetchLink(); });
</script>
</body>
</html>"""

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
