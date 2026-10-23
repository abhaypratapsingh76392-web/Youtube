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
app.secret_key = "super_permanent_clean_key_9988_yt"

# Firebase Database URL
FIREBASE_URL = "https://luckymall-23b3f-default-rtdb.firebaseio.com"

# Render Redirect URI
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

# --- YOUTUBE URL EXTRACTORS ---
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
    # Direct UC... ID (24 characters)
    if url_or_id.startswith('UC') and len(url_or_id) == 24:
        return url_or_id

    # Handle jaise @channelName
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

    # Search ke through channel ID nikalna
    clean_query = url_or_id.replace('https://', '').replace('http://', '').replace('www.youtube.com/', '').replace('/', '')
    try:
        res = youtube.search().list(part="id", type="channel", q=clean_query, maxResults=1).execute()
        items = res.get('items', [])
        if items:
            return items[0]['id']['channelId']
    except Exception:
        pass

    return url_or_id

# --- ROUTES ---
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

    # User ka YouTube Profile/Channel Info fetch karein
    creds = Credentials(
        token=token_data.get('access_token'),
        refresh_token=token_data.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=['https://www.googleapis.com/auth/youtube.force-ssl']
    )
    youtube = build('youtube', 'v3', credentials=creds)

    user_info = {'title': 'Connected User', 'picture': ''}
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

# --- DOWNLOAD SETUP TXT ROUTE ---
@app.route('/download-setup')
def download_setup():
    token = request.args.get('token')
    if not token:
        return "Token parameter missing", 400

    base_url = "https://no-fun-only-money.onrender.com/api"

    content = f"""==================================================================
              YOUTUBE BOT & API AUTOMATION SETUP GUIDE
==================================================================

Aapka Permanent Token: {token}
Base Server API URL: {base_url}

------------------------------------------------------------------
1. KISI CHANNEL KO SUBSCRIBE KARNE KA SYSTEM:
------------------------------------------------------------------
Aap direct YouTube Channel ka koi bhi URL/Link daal sakte hain!

URL Format:
{base_url}?token={token}&action=subscribe&channel_url=[CHANNEL_URL]

Ready Example:
{base_url}?token={token}&action=subscribe&channel_url=https://www.youtube.com/@MrBeast

Kaise use karein aur kahan kya replace karein:
1. Upar wale link me "[CHANNEL_URL]" ki jagah jis channel ko subscribe karwana hai uska link paste karein (Example: https://youtube.com/@TSeries).
2. Is pura URL ko kisi bhi phone browser, Chrome me enter karein ya background me hit karein.
3. Channel instantly aapke account se subscribe ho jayega!

------------------------------------------------------------------
2. KISI VIDEO KO LIKE KARNE KA SYSTEM:
------------------------------------------------------------------
Aap direct YouTube Video ka link daal sakte hain!

URL Format:
{base_url}?token={token}&action=like&video_url=[VIDEO_URL]

Ready Example:
{base_url}?token={token}&action=like&video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ

Kaise use karein:
1. "[VIDEO_URL]" ki jagah target YouTube video ka link dalein.
2. Link ko hit ya open karte hi video automatically like ho jayegi.

------------------------------------------------------------------
3. YOUTUBE SEARCH KARNE KA SYSTEM:
------------------------------------------------------------------
URL Format:
{base_url}?token={token}&action=search&q=[SEARCH_KEYWORD]

Ready Example:
{base_url}?token={token}&action=search&q=tech+gadgets

------------------------------------------------------------------
4. APNE PYTHON / PHP YA WEBSITE ME CODE SE KAISE CHALAYEIN:
------------------------------------------------------------------
[Python Code Example]
import requests
url = "{base_url}?token={token}&action=subscribe&channel_url=https://www.youtube.com/@MrBeast"
response = requests.get(url)
print(response.json())

[HTML / Redirect Button Example]
<a href="{base_url}?token={token}&action=subscribe&channel_url=https://www.youtube.com/@MrBeast">
   Subscribe MrBeast
</a>
==================================================================
"""
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename=setup_instructions_{token[:6]}.txt"}
    )

# --- DIRECT URL API HANDLER ---
@app.route('/api', methods=['GET', 'POST'])
def api_handler():
    token = request.args.get('token')
    action = request.args.get('action')

    if not token or not action:
        return jsonify({'status': 'error', 'message': 'token aur action parameter zaroori hain!'}), 400

    user_creds = firebase_read(f"youtube_tokens/{token}")
    if not user_creds:
        return jsonify({'status': 'error', 'message': 'Token invalid hai ya database me nahi mila!'}), 404

    creds = Credentials(
        token=user_creds.get('token'),
        refresh_token=user_creds.get('refresh_token'),
        token_uri=user_creds.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=user_creds.get('client_id'),
        client_secret=user_creds.get('client_secret'),
        scopes=user_creds.get('scopes')
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        user_creds['token'] = creds.token
        firebase_write(f"youtube_tokens/{token}", user_creds)

    youtube = build('youtube', 'v3', credentials=creds)

    try:
        # Subscribe Action (Direct URL ya Channel ID dono support karta hai)
        if action == 'subscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            if not channel_input:
                return jsonify({'status': 'error', 'message': 'channel_url ya channel_id zaroori hai!'}), 400

            target_channel_id = resolve_channel_id(youtube, channel_input)
            if not target_channel_id:
                return jsonify({'status': 'error', 'message': 'Channel ID resolve nahi ho payi!'}), 400

            youtube.subscriptions().insert(
                part="snippet",
                body={
                    'snippet': {
                        'resourceId': {
                            'kind': 'youtube#channel',
                            'channelId': target_channel_id
                        }
                    }
                }
            ).execute()
            return jsonify({'status': 'success', 'message': f'Channel ({channel_input}) subscribe ho gaya!', 'channel_id': target_channel_id})

        # Like Action (Direct Video Link ya Video ID dono support karta hai)
        elif action == 'like':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            if not video_input:
                return jsonify({'status': 'error', 'message': 'video_url ya video_id parameter zaroori hai!'}), 400

            target_video_id = extract_video_id(video_input)
            youtube.videos().rate(id=target_video_id, rating='like').execute()
            return jsonify({'status': 'success', 'message': f'Video ({video_input}) like ho gayi!', 'video_id': target_video_id})

        # Search Action
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
