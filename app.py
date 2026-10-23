import os
import json
import secrets
import urllib.parse
import requests
from flask import Flask, redirect, request, session, url_for, render_template, jsonify
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

app = Flask(__name__)
app.secret_key = "super_permanent_clean_key_9988"

# Firebase Database URL
FIREBASE_URL = "https://luckymall-23b3f-default-rtdb.firebaseio.com"

# Render Redirect URL
REDIRECT_URI = "https://no-fun-only-money.onrender.com/oauth2callback"

# client_secret.json se client_id aur client_secret auto-load
with open("client_secret.json") as f:
    secret_data = json.load(f)
cfg = secret_data.get("web") or secret_data.get("installed")
CLIENT_ID = cfg["client_id"]
CLIENT_SECRET = cfg["client_secret"]

# Firebase Helpers
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

@app.route('/')
def index():
    token = request.args.get('token') or session.get('my_custom_token')
    return render_template('index.html', token=token)

@app.route('/login')
def login():
    # Direct Google Authentication URL (No PKCE / No Verifier Loss)
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
        err = request.args.get('error', 'Google ne authorization cancel kar diya')
        return f"<h3>Authentication Cancelled:</h3><p>{err}</p><a href='/login'>Dobara Login Karein</a>", 400

    # Direct Google Token API Request (No library bugs)
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
        return f"<h3>Token Exchange Failed:</h3><pre>{json.dumps(token_data, indent=2)}</pre><a href='/login'>Try Again</a>", 400

    # Permanent custom secret token
    custom_token = secrets.token_hex(8)

    save_payload = {
        'token': token_data.get('access_token'),
        'refresh_token': token_data.get('refresh_token'),
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'scopes': ['https://www.googleapis.com/auth/youtube.force-ssl']
    }

    # Firebase me credentials save karein
    firebase_write(f"youtube_tokens/{custom_token}", save_payload)
    session['my_custom_token'] = custom_token

    # Token ko direct URL me pass karke index par bhej dein
    return redirect(url_for('index', token=custom_token))

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

    # Expire hone par auto refresh
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        user_creds['token'] = creds.token
        firebase_write(f"youtube_tokens/{token}", user_creds)

    youtube = build('youtube', 'v3', credentials=creds)

    try:
        # 1. Subscribe Channel
        if action == 'subscribe':
            channel_id = request.args.get('channel_id')
            if not channel_id:
                return jsonify({'status': 'error', 'message': 'channel_id parameter missing hai!'}), 400

            youtube.subscriptions().insert(
                part="snippet",
                body={
                    'snippet': {
                        'resourceId': {
                            'kind': 'youtube#channel',
                            'channelId': channel_id
                        }
                    }
                }
            ).execute()
            return jsonify({'status': 'success', 'message': f'Channel {channel_id} subscribe ho gaya!'})

        # 2. Like Video
        elif action == 'like':
            video_id = request.args.get('video_id')
            if not video_id:
                return jsonify({'status': 'error', 'message': 'video_id parameter missing hai!'}), 400

            youtube.videos().rate(id=video_id, rating='like').execute()
            return jsonify({'status': 'success', 'message': f'Video {video_id} liked!'})

        # 3. Search Query
        elif action == 'search':
            query = request.args.get('q', '')
            res = youtube.search().list(part="snippet", q=query, maxResults=5).execute()
            items = [{'title': item['snippet']['title'], 'channelId': item['snippet']['channelId']} for item in res.get('items', [])]
            return jsonify({'status': 'success', 'results': items})

        else:
            return jsonify({'status': 'error', 'message': 'Invalid action!'}), 400

    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
