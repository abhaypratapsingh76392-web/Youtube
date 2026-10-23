import os
import json
import secrets
import requests
from flask import Flask, redirect, request, session, url_for, render_template, jsonify
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
app.secret_key = "secret_youtube_key_9988"

# Firebase Realtime Database URL
FIREBASE_URL = "https://luckymall-23b3f-default-rtdb.firebaseio.com"

# Render Live Callback URL
REDIRECT_URI = "https://no-fun-only-money.onrender.com/oauth2callback"
CLIENT_SECRETS_FILE = "client_secret.json"
SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

# --- FIREBASE HELPER FUNCTIONS ---
def firebase_write(path, data):
    url = f"{FIREBASE_URL}/{path.lstrip('/')}.json"
    requests.put(url, json=data, timeout=5)

def firebase_read(path):
    url = f"{FIREBASE_URL}/{path.lstrip('/')}.json"
    res = requests.get(url, timeout=5)
    return res.json() if res.status_code == 200 else None

# --- WEB UI ROUTES ---
@app.route('/')
def index():
    user_token = session.get('my_custom_token')
    return render_template('index.html', token=user_token)

@app.route('/login')
def login():
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI

    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['state'] = state
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
    flow.redirect_uri = REDIRECT_URI

    auth_resp = request.url
    if auth_resp.startswith('http://'):
        auth_resp = auth_resp.replace('http://', 'https://', 1)

    flow.fetch_token(authorization_response=auth_resp)
    creds = flow.credentials

    # Har user ke liye ek permanent unique token create karein
    custom_token = secrets.token_hex(10)

    # Google credentials ko Firebase me store karein
    user_data = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }
    firebase_write(f"youtube_tokens/{custom_token}", user_data)

    session['my_custom_token'] = custom_token
    return redirect(url_for('index'))

# --- CUSTOM URL API ENDPOINT ---
# Is route par URL se direct subscribe, like aur search chalega
@app.route('/api', methods=['GET', 'POST'])
def api_handler():
    token = request.args.get('token')
    action = request.args.get('action')  # 'subscribe', 'like', ya 'search'

    if not token or not action:
        return jsonify({'status': 'error', 'message': 'token aur action parameter zaroori hain!'}), 400

    # Firebase se user credentials retrieve karein
    stored_creds = firebase_read(f"youtube_tokens/{token}")
    if not stored_creds:
        return jsonify({'status': 'error', 'message': 'Invalid token ya user nahi mila!'}), 404

    creds = Credentials(
        token=stored_creds.get('token'),
        refresh_token=stored_creds.get('refresh_token'),
        token_uri=stored_creds.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=stored_creds.get('client_id'),
        client_secret=stored_creds.get('client_secret'),
        scopes=stored_creds.get('scopes')
    )

    # Agar token expire ho gaya ho to refresh karein
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        stored_creds['token'] = creds.token
        firebase_write(f"youtube_tokens/{token}", stored_creds)

    youtube = build('youtube', 'v3', credentials=creds)

    try:
        # 1. Channel Subscribe Action
        if action == 'subscribe':
            channel_id = request.args.get('channel_id')
            if not channel_id:
                return jsonify({'status': 'error', 'message': 'channel_id parameter missing!'}), 400

            res = youtube.subscriptions().insert(
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
            return jsonify({'status': 'success', 'message': f'Channel {channel_id} subscribed successfully!'})

        # 2. Video Like Action
        elif action == 'like':
            video_id = request.args.get('video_id')
            if not video_id:
                return jsonify({'status': 'error', 'message': 'video_id parameter missing!'}), 400

            youtube.videos().rate(id=video_id, rating='like').execute()
            return jsonify({'status': 'success', 'message': f'Video {video_id} liked successfully!'})

        # 3. Search Action
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
