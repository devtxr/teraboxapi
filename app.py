from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import re
from urllib.parse import urlparse, parse_qs
import os

app = Flask(__name__)
CORS(app)

# You can set your ndus cookie as an environment variable or via .env
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
        return {"error": "Failed to extract jsToken. Verification might be required or Cloudflare blocked the request."}
    
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
    custom_ndus = request.args.get('ndus')  # allow users to pass their own cookie
    
    if not url:
        return jsonify({"error": "Please provide a 'url' query parameter"}), 400
        
    data = get_terabox_data(url, custom_ndus)
    if "error" in data:
        return jsonify({"status": "error", "message": data["error"]}), 400
        
    try:
        files = data.get("list", [])
        if not files:
            return jsonify({"status": "error", "message": "No files found in the shared link."}), 404
            
        # Return the first file for simplicity, or handle multiple files if needed
        item = files[0]
        
        # Format size logically
        size_bytes = int(item.get("size", 0))
        size_mb = f"{size_bytes / (1024 * 1024):.2f} MB"
        
        result = {
            "status": "success",
            "filename": item.get("server_filename"),
            "size": size_mb,
            "download_link": item.get("dlink"),
            "thumbnail": item.get("thumbs", {}).get("url3", "")
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": "Failed to parse API response or no files found."}), 500

@app.route('/')
def index():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>TeraBox Downloader API Integration Guide</title>
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; margin: 20px; max-width: 800px; margin: auto; padding: 20px;}
            h1, h2, h3 { color: #333; }
            code { background: #f4f4f4; padding: 2px 6px; border-radius: 4px; font-family: monospace; color: #d63384;}
            pre { background: #f4f4f4; padding: 15px; border-radius: 8px; overflow-x: auto; font-family: monospace; }
            .step { margin-bottom: 20px; }
            .important { background-color: #fffae6; border-left: 4px solid #ffcc00; padding: 10px; margin: 15px 0; }
        </style>
    </head>
    <body>
        <h1>TeraBox Downloader API</h1>
        <p>Welcome to the TeraBox API endpoint. Below is the integration guide and instructions on how to use it for multiple users.</p>

        <h2>API Integration</h2>
        <p><strong>Endpoint:</strong> <code>/api/terabox</code></p>
        <p><strong>Method:</strong> <code>GET</code></p>
        
        <h3>Parameters</h3>
        <ul>
            <li><code>url</code> <strong>(Required)</strong>: The TeraBox share URL (e.g., <code>https://terabox.app/s/...</code>)</li>
            <li><code>ndus</code> <strong>(Optional)</strong>: Your personal TeraBox NDUS cookie. If multiple users use this API, passing their own <code>ndus</code> cookie ensures they bypass rate limits or login requirements.</li>
        </ul>

        <h3>Example Request</h3>
        <pre><code>GET /api/terabox?url=https://terabox.app/s/1HSEb8PZRUE7Z1Tvd3ZtT0g&ndus=YOUR_COOKIE_HERE</code></pre>
        
        <h3>Example Response</h3>
        <pre><code>{
  "status": "success",
  "filename": "amazing_video.mp4",
  "size": "500.00 MB",
  "download_link": "https://d.terabox.app/...",
  "thumbnail": "https://data.terabox.app/..."
}</code></pre>

        <hr>

        <h2>How to get NDUS Cookie from Phone (Hindi/English Guide)</h2>
        <p>Agar aap phone se NDUS cookie nikalna chahte hain, toh follow these steps:</p>
        
        <div class="step">
            <h3>Step 1: Browser Install Karein</h3>
            <p>Apne Android phone mein <strong>Kiwi Browser</strong> ya <strong>Lemur Browser</strong> install karein playstore se. (Kyunki inme extensions support karta hai).</p>
        </div>

        <div class="step">
            <h3>Step 2: Extension Add Karein</h3>
            <p>Kiwi browser open karein aur search karein: <strong>"Cookie Editor extension chrome"</strong>. Pela link open karke <em>"Add to Chrome"</em> pe click karein.</p>
        </div>

        <div class="step">
            <h3>Step 3: TeraBox Login Karein</h3>
            <p>Browser me naya tab khol kar <code>terabox.com</code> par jayen aur apne account se login karein.</p>
        </div>

        <div class="step">
            <h3>Step 4: Cookie Copy Karein</h3>
            <p>Login hone ke baad, browser ke 3 dots (menu) par click karein aur sabse niche <strong>Cookie Editor</strong> par click karein. Wahan list me ek <code>ndus</code> naam ka option hoga. Us par click karein aur uski <strong>value</strong> copy kar lein.</p>
            <div class="important">
                <strong>Note:</strong> Yeh cookie lambi hogi, ise bina kisi space ke copy karke API ke URL me <code>&ndus=...</code> parameter mein daalein.
            </div>
        </div>
        
    </body>
    </html>
    """
    return html_content

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
