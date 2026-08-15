from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import requests
import re
from urllib.parse import urlparse, parse_qs, quote, unquote
import os

app = Flask(__name__)
CORS(app)

NDUS_COOKIE = os.environ.get("NDUS_COOKIE", "")

def extract_surl(url):
    parsed = urlparse(url)
    if 'surl' in parse_qs(parsed.query):
        return parse_qs(parsed.query)['surl'][0]
    match = re.search(r'/s/([a-zA-Z0-9_-]+)', parsed.path)
    if match:
        return match.group(1)
    return None

def get_terabox_data(url, custom_ndus=None):
    surl = extract_surl(url)
    if not surl:
        return {"error": "Invalid TeraBox URL or missing surl"}
    
    short_url = surl
    if surl.startswith("1"):
        short_url = surl[1:]

    cookie_to_use = custom_ndus if custom_ndus else NDUS_COOKIE

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Cookie": f"ndus={cookie_to_use}" if cookie_to_use else ""
    }

    first_url = f"https://dm.terabox.app/sharing/link?surl={surl}"
    try:
        res = requests.get(first_url, headers=headers, timeout=10)
        res.raise_for_status()
    except Exception as e:
        return {"error": f"Failed to fetch initial page: {e}"}

    match = re.search(r'fn%28%22(.*?)%22%29', res.text)
    if not match:
        return {"error": "Failed to extract jsToken. Verification might be required."}
    
    jsToken = match.group(1)

    api_url = "https://dm.terabox.app/share/list"
    params = {
        "app_id": "250528",
        "jsToken": jsToken,
        "site_referer": "https://www.terabox.app/",
        "shorturl": short_url,
        "root": "1"
    }

    api_headers = {
        "Host": "dm.terabox.app",
        "User-Agent": headers["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://dm.terabox.app/sharing/link?surl={short_url}&clearCache=1",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://dm.terabox.app",
        "Cookie": headers["Cookie"]
    }

    try:
        api_res = requests.get(api_url, params=params, headers=api_headers, timeout=10)
        api_res.raise_for_status()
        data = api_res.json()
        
        if data.get("errno") != 0:
            return {"error": f"API returned error: {data.get('errno')} - {data.get('errmsg', '')}"}
            
        return data
    except Exception as e:
        return {"error": f"Failed to fetch API data: {e}"}


@app.route('/api/terabox', methods=['GET'])
def get_terabox_link():
    url = request.args.get('url')
    custom_ndus = request.args.get('ndus')
    
    if not url:
        return jsonify({"error": "Please provide a 'url' query parameter"}), 400
        
    data = get_terabox_data(url, custom_ndus)
    if "error" in data:
        return jsonify({"status": "error", "message": data["error"]}), 400
        
    try:
        files = data.get("list", [])
        if not files:
            return jsonify({"status": "error", "message": "No files found in the shared link."}), 404
            
        item = files[0]
        size_bytes = int(item.get("size", 0))
        size_mb = f"{size_bytes / (1024 * 1024):.2f} MB"
        
        dlink = item.get("dlink")
        if not dlink:
             return jsonify({"status": "error", "message": "Download link not found in API response. Are you sure the cookie is valid?"}), 500
        
        host_url = request.host_url.rstrip('/')
        
        safe_dlink = quote(str(dlink))
        ndus_query = f"&ndus={quote(custom_ndus)}" if custom_ndus else ""

        # Provide Multiple Working Proxy Links!
        # Sab proxy hi hain kyunki direct dm-d wala link block ho jayega aapke phone pe.
        proxy_url_1 = f"{host_url}/api/download?dlink={safe_dlink}{ndus_query}"
        proxy_url_2 = f"{host_url}/api/download_v2?dlink={safe_dlink}{ndus_query}"

        result = {
            "status": "success",
            "filename": item.get("server_filename"),
            "size": size_mb,
            "thumbnail": item.get("thumbs", {}).get("url3", ""),
            "links": {
                "download_link_1": proxy_url_1,
                "download_link_2": proxy_url_2
            }
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": "Failed to parse API response.", "details": str(e)}), 500

@app.route('/api/download', methods=['GET', 'HEAD'])
def proxy_download():
    return handle_proxy_download(request)

@app.route('/api/download_v2', methods=['GET', 'HEAD'])
def proxy_download_v2():
    # Similar to download_v1 but with smaller chunk size as alternative approach
    return handle_proxy_download(request, chunk_size=8192)

def handle_proxy_download(req, chunk_size=1024*1024):
    dlink = req.args.get('dlink')
    custom_ndus = req.args.get('ndus')
    if not dlink:
        return "Missing dlink", 400

    cookie_to_use = custom_ndus if custom_ndus else NDUS_COOKIE
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cookie": f"ndus={cookie_to_use}" if cookie_to_use else "",
        "Referer": "https://www.terabox.app/"
    }

    try:
        if req.method == 'HEAD':
            upstream_req = requests.head(dlink, headers=headers, allow_redirects=True)
            return Response("", status=upstream_req.status_code, headers=dict(upstream_req.headers))

        if 'Range' in req.headers:
            headers['Range'] = req.headers['Range']

        upstream_req = requests.get(dlink, headers=headers, stream=True, allow_redirects=True)
        
        if upstream_req.status_code not in [200, 206]:
            return jsonify({"error": f"Failed to download from TeraBox. Status code: {upstream_req.status_code}"}), upstream_req.status_code

        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        resp_headers = [(name, value) for (name, value) in upstream_req.headers.items()
                        if name.lower() not in excluded_headers]

        if 'content-length' in upstream_req.headers:
            resp_headers.append(('Content-Length', upstream_req.headers['content-length']))

        return Response(stream_with_context(upstream_req.iter_content(chunk_size=chunk_size)), 
                        headers=resp_headers,
                        content_type=upstream_req.headers.get('content-type'),
                        status=upstream_req.status_code)
    except Exception as e:
        return str(e), 500

@app.route('/')
def index():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>TeraBox Downloader API</title>
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; margin: 20px; max-width: 800px; margin: auto; padding: 20px;}
            h1, h2, h3 { color: #333; }
            code { background: #f4f4f4; padding: 2px 6px; border-radius: 4px; font-family: monospace; color: #d63384;}
            pre { background: #f4f4f4; padding: 15px; border-radius: 8px; overflow-x: auto; font-family: monospace; }
        </style>
    </head>
    <body>
        <h1>TeraBox Downloader API</h1>
        <p>API Endpoint: <code>/api/terabox?url=YOUR_URL</code></p>
    </body>
    </html>
    """
    return html_content

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
