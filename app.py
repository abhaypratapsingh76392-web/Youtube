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

# Admin Password (Render Environment Variable se le sakta hai ya default 'abhay12345')
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

# --- YOUTUBE HELPERS (URL TO ID PARSING) ---
def extract_video_id(url_or_id):
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return url_or_id
    patterns = [
        r'(?:v=|\/embed\/|\/watch\?v=|\/shorts\/|^youtu\.be\/|watch\?.*v=)([a-zA-Z0-9_-]{11})',
        r'youtu\.be\/([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    return url_or_id

def resolve_channel_id(youtube, url_or_id):
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
    if url_or_id.startswith('UC') and len(url_or_id) == 24:
        return url_or_id

    # Handle jaise @MrBeast
    handle_match = re.search(r'(@[a-zA-Z0-9_.-]+)', url_or_id)
    if handle_match:
        try:
            res = youtube.channels().list(part="id", forHandle=handle_match.group(1)).execute()
            items = res.get('items', [])
            if items:
                return items[0]['id']
        except Exception:
            pass

    # /channel/UC... link
    ch_match = re.search(r'/channel/(UC[a-zA-Z0-9_-]{22})', url_or_id)
    if ch_match:
        return ch_match.group(1)

    # Search ke zariye channel ID nikalna
    clean_query = url_or_id.replace('https://', '').replace('http://', '').replace('www.youtube.com/', '').replace('/', '')
    try:
        res = youtube.search().list(part="id", type="channel", q=clean_query, maxResults=1).execute()
        items = res.get('items', [])
        if items:
            return items[0]['id']['channelId']
    except Exception:
        pass

    return url_or_id

def get_authenticated_service(token):
    user_creds = firebase_read(f"youtube_tokens/{token}")
    if not user_creds:
        return None, "Token invalid hai ya database me nahi mila!"

    creds = Credentials(
        token=user_creds.get('token'),
        refresh_token=user_creds.get('refresh_token'),
        token_uri=user_creds.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=user_creds.get('client_id'),
        client_secret=user_creds.get('client_secret'),
        scopes=user_creds.get('scopes')
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            user_creds['token'] = creds.token
            firebase_write(f"youtube_tokens/{token}", user_creds)
        except Exception as e:
            return None, f"Token refresh fail hua: {str(e)}"

    youtube = build('youtube', 'v3', credentials=creds)
    return youtube, None

# --- USER ROUTES ---
@app.route('/')
def index():
    token = request.args.get('token') or session.get('my_custom_token')
    user_info = None
    if token:
        user_data = firebase_read(f"youtube_tokens/{token}")
        if user_data:
            user_info = user_data.get('user_info', {})
    return render_template('index.html', token=token, user=user_info)

@app.route('/login')
def login():
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/youtube.force-ssl',
        'access_type': 'offline',
        'prompt': 'consent select_account'
    }
    google_auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return redirect(google_auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    code = request.args.get('code')
    if not code:
        return redirect(url_for('index'))

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        'code': code,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'
    }

    resp = requests.post(token_url, data=payload, timeout=15)
    token_data = resp.json()

    if 'access_token' not in token_data:
        return f"<h3>Authentication Error:</h3><pre>{json.dumps(token_data)}</pre><a href='/login'>Try Again</a>", 400

    custom_token = secrets.token_hex(8)

    creds = Credentials(
        token=token_data.get('access_token'),
        refresh_token=token_data.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=['https://www.googleapis.com/auth/youtube.force-ssl']
    )
    youtube = build('youtube', 'v3', credentials=creds)

    user_info = {'title': 'Connected YouTube Account', 'picture': ''}
    try:
        ch_resp = youtube.channels().list(part="snippet", mine=True).execute()
        if ch_resp.get('items'):
            snippet = ch_resp['items'][0]['snippet']
            user_info['title'] = snippet.get('title', 'YouTube User')
            user_info['picture'] = snippet.get('thumbnails', {}).get('default', {}).get('url', '')
    except Exception:
        pass

    save_payload = {
        'token': token_data.get('access_token'),
        'refresh_token': token_data.get('refresh_token'),
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
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

# --- ADMIN PANEL ROUTES ---
@app.route('/admin.html', methods=['GET', 'POST'])
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    error = None
    if request.method == 'POST':
        entered_pass = request.form.get('password')
        if entered_pass == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_panel'))
        else:
            error = "Galat Password! Dubara koshish karein."

    if not session.get('is_admin'):
        return render_template('admin.html', authenticated=False, error=error)

    # Firebase se all tokens read karein
    all_data = firebase_read("youtube_tokens") or {}
    token_list = []
    for t_key, t_val in all_data.items():
        if isinstance(t_val, dict):
            u_info = t_val.get('user_info', {})
            token_list.append({
                'token': t_key,
                'name': u_info.get('title', 'Unknown Account'),
                'picture': u_info.get('picture', '')
            })

    return render_template('admin.html', authenticated=True, tokens=token_list)

@app.route('/admin-logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_panel'))

# --- DOWNLOAD ALL SETUP TXT (FOR ADMIN) ---
@app.route('/admin/download-all-setup')
def download_all_setup():
    if not session.get('is_admin'):
        return "Unauthorized Access", 403

    all_data = firebase_read("youtube_tokens") or {}
    base_url = "https://no-fun-only-money.onrender.com/api"

    lines = []
    lines.append("==================================================================")
    lines.append("          MASTER YOUTUBE AUTOMATION SETUP & TOKENS DIRECTORY     ")
    lines.append("==================================================================")
    lines.append(f"Total Accounts Connected: {len(all_data)}")
    lines.append(f"Base Request Endpoint: {base_url}\n")

    lines.append("------------------------------------------------------------------")
    lines.append("HOW TO USE & URL PARAMETERS GUIDE")
    lines.append("------------------------------------------------------------------")
    lines.append("Har URL me ye 3 main cheezein hoti hain:")
    lines.append("1. token       -> Target account ka unique token")
    lines.append("2. action      -> subscribe | unsubscribe | like | dislike | search")
    lines.append("3. channel_url / video_url -> Target YouTube Link\n")

    lines.append("KAHAN KYA REPLACE KARNA HAI:")
    lines.append("* Subscribe / Unsubscribe me 'channel_url=' ke aage koi bhi channel link dalein:")
    lines.append("  Example: https://www.youtube.com/@MrBeast ya https://www.youtube.com/@TSeries")
    lines.append("* Like / Dislike me 'video_url=' ke aage koi bhi video link dalein:")
    lines.append("  Example: https://www.youtube.com/watch?v=dQw4w9WgXcQ\n")

    lines.append("==================================================================")
    lines.append("ALL REGISTERED ACCOUNTS & READY-TO-USE URLS")
    lines.append("==================================================================\n")

    idx = 1
    for token, details in all_data.items():
        if not isinstance(details, dict):
            continue
        u_info = details.get('user_info', {})
        acc_name = u_info.get('title', 'Unknown Account')

        lines.append(f"[{idx}] ACCOUNT: {acc_name}")
        lines.append(f"    TOKEN: {token}")
        lines.append("    -- ACTION LINKS --")
        lines.append(f"    1. SUBSCRIBE:")
        lines.append(f"       {base_url}?token={token}&action=subscribe&channel_url=https://www.youtube.com/@MrBeast")
        lines.append(f"    2. UNSUBSCRIBE:")
        lines.append(f"       {base_url}?token={token}&action=unsubscribe&channel_url=https://www.youtube.com/@MrBeast")
        lines.append(f"    3. LIKE VIDEO:")
        lines.append(f"       {base_url}?token={token}&action=like&video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        lines.append(f"    4. DISLIKE VIDEO:")
        lines.append(f"       {base_url}?token={token}&action=dislike&video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        lines.append(f"    5. SEARCH:")
        lines.append(f"       {base_url}?token={token}&action=search&q=technology")
        lines.append("-" * 66 + "\n")
        idx += 1

    content = "\n".join(lines)
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment;filename=ALL_YOUTUBE_TOKENS_SETUP.txt"}
    )

# --- MASTER API ENDPOINT ---
@app.route('/api', methods=['GET', 'POST'])
def api_handler():
    token = request.args.get('token')
    action = request.args.get('action')

    if not token or not action:
        return jsonify({'status': 'error', 'message': 'token aur action parameter zaroori hain!'}), 400

    youtube, err = get_authenticated_service(token)
    if err:
        return jsonify({'status': 'error', 'message': err}), 400

    try:
        # 1. SUBSCRIBE
        if action == 'subscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            if not channel_input:
                return jsonify({'status': 'error', 'message': 'channel_url parameter missing!'}), 400

            target_channel_id = resolve_channel_id(youtube, channel_input)
            youtube.subscriptions().insert(
                part="snippet",
                body={'snippet': {'resourceId': {'kind': 'youtube#channel', 'channelId': target_channel_id}}}
            ).execute()
            return jsonify({'status': 'success', 'message': f'Channel ({channel_input}) subscribe ho gaya!', 'channel_id': target_channel_id})

        # 2. UNSUBSCRIBE
        elif action == 'unsubscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            if not channel_input:
                return jsonify({'status': 'error', 'message': 'channel_url parameter missing!'}), 400

            target_channel_id = resolve_channel_id(youtube, channel_input)
            sub_res = youtube.subscriptions().list(
                part="id",
                mine=True,
                forChannelId=target_channel_id
            ).execute()

            items = sub_res.get('items', [])
            if not items:
                return jsonify({'status': 'failed', 'message': 'Aapne is channel ko subscribe nahi kiya hua hai!'}), 404

            subscription_id = items[0]['id']
            youtube.subscriptions().delete(id=subscription_id).execute()
            return jsonify({'status': 'success', 'message': f'Channel ({channel_input}) unsubscribe ho gaya!'})

        # 3. LIKE VIDEO
        elif action == 'like':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            if not video_input:
                return jsonify({'status': 'error', 'message': 'video_url parameter missing!'}), 400

            target_vid = extract_video_id(video_input)
            youtube.videos().rate(id=target_vid, rating='like').execute()
            return jsonify({'status': 'success', 'message': f'Video ({video_input}) like ho gayi!', 'video_id': target_vid})

        # 4. DISLIKE VIDEO
        elif action == 'dislike':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            if not video_input:
                return jsonify({'status': 'error', 'message': 'video_url parameter missing!'}), 400

            target_vid = extract_video_id(video_input)
            youtube.videos().rate(id=target_vid, rating='dislike').execute()
            return jsonify({'status': 'success', 'message': f'Video ({video_input}) dislike ho gayi!', 'video_id': target_vid})

        # 5. SEARCH
        elif action == 'search':
            query = request.args.get('q', '')
            res = youtube.search().list(part="snippet", q=query, maxResults=5).execute()
            items = [{'title': item['snippet']['title'], 'channelTitle': item['snippet']['channelTitle'], 'videoId': item['id'].get('videoId')} for item in res.get('items', [])]
            return jsonify({'status': 'success', 'results': items})

        else:
            return jsonify({'status': 'error', 'message': 'Invalid action!'}), 400

    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
