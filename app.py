import os
import re
import json
import secrets
import urllib.parse
import requests
from flask import Flask, redirect, request, session, url_for, render_template, jsonify, Response
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_permanent_clean_key_9988_yt")

# Admin Password
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "abhay12345")

# Firebase Database URL
FIREBASE_URL = "https://luckymall-23b3f-default-rtdb.firebaseio.com"

# Render Live Callback URL
REDIRECT_URI = "https://no-fun-only-money.onrender.com/oauth2callback"

# client_secret.json auto-load
with open("client_secret.json") as f:
    secret_data = json.load(f)
cfg = secret_data.get("web") or secret_data.get("installed")
CLIENT_ID = cfg["client_id"]
CLIENT_SECRET = cfg["client_secret"]

# --- FIREBASE HELPERS ---
def firebase_write(path, data):
    try:
        url = f"{FIREBASE_URL}/{path.lstrip('/')}.json"
        requests.put(url, json=data, timeout=10)
    except Exception as e:
        print("Firebase Write Error:", e)

def firebase_read(path):
    try:
        url = f"{FIREBASE_URL}/{path.lstrip('/')}.json"
        res = requests.get(url, timeout=10)
        return res.json() if res.status_code == 200 else None
    except Exception as e:
        print("Firebase Read Error:", e)
        return None

# --- YOUTUBE HELPERS ---
def extract_video_id(url_or_id):
    if not url_or_id: return None
    url_or_id = url_or_id.strip()
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id): return url_or_id
    patterns = [
        r'(?:v=|\/embed\/|\/watch\?v=|\/shorts\/|^youtu\.be\/|watch\?.*v=)([a-zA-Z0-9_-]{11})',
        r'youtu\.be\/([a-zA-Z0-9_-]{11})',
    ]
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
    clean_query = url_or_id.replace('https://', '').replace('http://', '').replace('www.youtube.com/', '').replace('/', '')
    try:
        res = youtube.search().list(part="id", type="channel", q=clean_query, maxResults=1).execute()
        if res.get('items'): return res['items'][0]['id']['channelId']
    except Exception: pass
    return url_or_id

def get_authenticated_service(token):
    user_creds = firebase_read(f"youtube_tokens/{token}")
    if not user_creds: return None, "Token invalid hai ya database me nahi mila!"
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
        except Exception as e: return None, f"Token refresh fail hua: {str(e)}"
    youtube = build('youtube', 'v3', credentials=creds)
    return youtube, None

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
    params = {
        'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI, 'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/youtube.force-ssl',
        'access_type': 'offline', 'prompt': 'consent select_account'
    }
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}")

@app.route('/oauth2callback')
def oauth2callback():
    code = request.args.get('code')
    if not code: return redirect(url_for('index'))

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        'code': code, 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'redirect_uri': REDIRECT_URI, 'grant_type': 'authorization_code'
    }
    resp = requests.post(token_url, data=payload, timeout=15)
    token_data = resp.json()

    if 'access_token' not in token_data:
        return f"<h3>Authentication Error:</h3><pre>{json.dumps(token_data)}</pre><a href='/login'>Try Again</a>", 400

    creds = Credentials(
        token=token_data.get('access_token'), refresh_token=token_data.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token', client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET, scopes=['https://www.googleapis.com/auth/youtube.force-ssl']
    )
    youtube = build('youtube', 'v3', credentials=creds)

    user_info = {'title': 'Connected YouTube Account', 'picture': ''}
    channel_id = None
    try:
        ch_resp = youtube.channels().list(part="snippet,id", mine=True).execute()
        if ch_resp.get('items'):
            channel_id = ch_resp['items'][0]['id']
            snippet = ch_resp['items'][0]['snippet']
            user_info['title'] = snippet.get('title', 'YouTube User')
            user_info['picture'] = snippet.get('thumbnails', {}).get('default', {}).get('url', '')
    except Exception:
        pass

    # ✅ TOKEN PERSISTENCE FIX: Check if this channel_id already has a token
    existing_token = None
    all_tokens = firebase_read("youtube_tokens") or {}
    for t_key, t_val in all_tokens.items():
        if t_val.get('channel_id') == channel_id and channel_id is not None:
            existing_token = t_key
            break

    custom_token = existing_token if existing_token else secrets.token_hex(8)

    save_payload = {
        'channel_id': channel_id,
        'token': token_data.get('access_token'),
        'refresh_token': token_data.get('refresh_token'),
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'scopes': ['https://www.googleapis.com/auth/youtube.force-ssl'],
        'user_info': user_info
    }

    firebase_write(f"youtube_tokens/{custom_token}", save_payload)
    session['my_custom_token'] = custom_token

    return redirect(url_for('index', token=custom_token))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- DOWNLOAD GUIDE ROUTE ---
@app.route('/download-guide')
def download_guide():
    token = request.args.get('token', 'YOUR_TOKEN_HERE')
    guide_text = f"""
=====================================================
        YOUTUBE AUTOMATION API - USER GUIDE
=====================================================
Aapka API Token: {token}

Is token ka use karke aap apni khud ki Vidmate jaisi website bana sakte hain.

-----------------------------------------------------
1. VIDEO SEARCH KARNA (API CALL)
-----------------------------------------------------
URL: https://no-fun-only-money.onrender.com/api?token={token}&action=search&q=technology

Response: Ye aapko JSON format me videos ki list dega (Title, Video ID, Channel Name).

-----------------------------------------------------
2. VIDEO LIKE / DISLIKE KARNA
-----------------------------------------------------
Like: https://no-fun-only-money.onrender.com/api?token={token}&action=like&video_url=https://www.youtube.com/watch?v=VIDEO_ID
Dislike: https://no-fun-only-money.onrender.com/api?token={token}&action=dislike&video_url=https://www.youtube.com/watch?v=VIDEO_ID

-----------------------------------------------------
3. CHANNEL SUBSCRIBE / UNSUBSCRIBE KARNA
-----------------------------------------------------
Subscribe: https://no-fun-only-money.onrender.com/api?token={token}&action=subscribe&channel_url=https://www.youtube.com/@ChannelName
Unsubscribe: https://no-fun-only-money.onrender.com/api?token={token}&action=unsubscribe&channel_url=https://www.youtube.com/@ChannelName

-----------------------------------------------------
4. APNI WEBSITE ME KAISE LAGAYEIN?
-----------------------------------------------------
Aap apne HTML/Javascript code me in URLs ko fetch() ya axios ke zariye call kar sakte hain.

Example (Javascript):
fetch('https://no-fun-only-money.onrender.com/api?token={token}&action=search&q=song')
  .then(response => response.json())
  .then(data => console.log(data));

NOTE: Ye API URL sirf aapke token ke sath kaam karega. Isko kisi ke sath share na karein.
"""
    return Response(guide_text, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename=guide_{token}.txt"})

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
            error = "Galat Password! Dubara koshish karein."

    if not session.get('is_admin'):
        return render_template('admin.html', authenticated=False, error=error)

    all_data = firebase_read("youtube_tokens") or {}
    token_list = []
    for t_key, t_val in all_data.items():
        if isinstance(t_val, dict):
            u_info = t_val.get('user_info', {})
            token_list.append({
                'token': t_key, 'name': u_info.get('title', 'Unknown Account'),
                'picture': u_info.get('picture', '')
            })

    return render_template('admin.html', authenticated=True, tokens=token_list)

@app.route('/admin-logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_panel'))

@app.route('/admin/download-all-setup')
def download_all_setup():
    if not session.get('is_admin'): return "Unauthorized Access", 403

    all_data = firebase_read("youtube_tokens") or {}
    base_url = "https://no-fun-only-money.onrender.com/api"

    lines = [
        "==================================================================",
        "          MASTER YOUTUBE AUTOMATION SETUP & TOKENS DIRECTORY     ",
        "==================================================================",
        f"Total Accounts Connected: {len(all_data)}\n",
        "ALL REGISTERED ACCOUNTS & READY-TO-USE URLS",
        "==================================================================\n"
    ]

    idx = 1
    for token, details in all_data.items():
        if not isinstance(details, dict): continue
        acc_name = details.get('user_info', {}).get('title', 'Unknown Account')
        lines.append(f"[{idx}] ACCOUNT: {acc_name} | TOKEN: {token}")
        lines.append(f"    SUBSCRIBE:   {base_url}?token={token}&action=subscribe&channel_url=https://www.youtube.com/@MrBeast")
        lines.append(f"    UNSUBSCRIBE: {base_url}?token={token}&action=unsubscribe&channel_url=https://www.youtube.com/@MrBeast")
        lines.append(f"    LIKE:        {base_url}?token={token}&action=like&video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        lines.append(f"    DISLIKE:     {base_url}?token={token}&action=dislike&video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        lines.append("-" * 66 + "\n")
        idx += 1

    return Response("\n".join(lines), mimetype="text/plain", headers={"Content-Disposition": "attachment;filename=MASTER_SETUP.txt"})

# --- MASTER API ENDPOINT ---
@app.route('/api', methods=['GET', 'POST'])
def api_handler():
    token = request.args.get('token')
    action = request.args.get('action')

    if not token or not action:
        return jsonify({'status': 'error', 'message': 'token aur action parameter zaroori hain!'}), 400

    youtube, err = get_authenticated_service(token)
    if err: return jsonify({'status': 'error', 'message': err}), 400

    try:
        if action == 'subscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            if not channel_input: return jsonify({'status': 'error', 'message': 'channel_url parameter missing!'}), 400
            target_channel_id = resolve_channel_id(youtube, channel_input)
            youtube.subscriptions().insert(part="snippet", body={'snippet': {'resourceId': {'kind': 'youtube#channel', 'channelId': target_channel_id}}}).execute()
            return jsonify({'status': 'success', 'message': f'Channel ({channel_input}) subscribe ho gaya!'})

        elif action == 'unsubscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            if not channel_input: return jsonify({'status': 'error', 'message': 'channel_url parameter missing!'}), 400
            target_channel_id = resolve_channel_id(youtube, channel_input)
            sub_res = youtube.subscriptions().list(part="id", mine=True, forChannelId=target_channel_id).execute()
            if not sub_res.get('items'): return jsonify({'status': 'failed', 'message': 'Aapne is channel ko subscribe nahi kiya hua hai!'}), 404
            youtube.subscriptions().delete(id=sub_res['items'][0]['id']).execute()
            return jsonify({'status': 'success', 'message': f'Channel unsubscribe ho gaya!'})

        elif action == 'like':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            if not video_input: return jsonify({'status': 'error', 'message': 'video_url parameter missing!'}), 400
            youtube.videos().rate(id=extract_video_id(video_input), rating='like').execute()
            return jsonify({'status': 'success', 'message': f'Video like ho gayi!'})

        elif action == 'dislike':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            if not video_input: return jsonify({'status': 'error', 'message': 'video_url parameter missing!'}), 400
            youtube.videos().rate(id=extract_video_id(video_input), rating='dislike').execute()
            return jsonify({'status': 'success', 'message': f'Video dislike ho gayi!'})

        elif action == 'search':
            query = request.args.get('q', '')
            res = youtube.search().list(part="snippet", q=query, maxResults=10).execute()
            items = [{'title': item['snippet']['title'], 'channelTitle': item['snippet']['channelTitle'], 'videoId': item['id'].get('videoId'), 'thumbnail': item['snippet']['thumbnails']['default']['url']} for item in res.get('items', []) if item['id'].get('videoId')]
            return jsonify({'status': 'success', 'results': items})

        return jsonify({'status': 'error', 'message': 'Invalid action!'}), 400
    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
