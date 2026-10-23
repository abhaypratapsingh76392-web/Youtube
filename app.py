import os
import json
import secrets
import requests
from flask import Flask, redirect, request, session, url_for, render_template, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# OAuthLib environment settings (Proxy aur Scopes ke errors se bachne ke liye)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

app = Flask(__name__)
app.secret_key = "super_unique_secure_key_youtube_99"

# Render reverse-proxy support (HTTPS detect karne ke liye)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Session cookie settings taaki redirect hone par state delete na ho
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_HTTPONLY=True
)

# Firebase Realtime Database URL
FIREBASE_URL = "https://luckymall-23b3f-default-rtdb.firebaseio.com"

# Render Live Callback URL
REDIRECT_URI = "https://no-fun-only-money.onrender.com/oauth2callback"
CLIENT_SECRETS_FILE = "client_secret.json"
SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

# --- FIREBASE HELPERS ---
def firebase_write(path, data):
    try:
        url = f"{FIREBASE_URL}/{path.lstrip('/')}.json"
        requests.put(url, json=data, timeout=5)
    except Exception as e:
        print(f"Firebase Write Error: {e}")

def firebase_read(path):
    try:
        url = f"{FIREBASE_URL}/{path.lstrip('/')}.json"
        res = requests.get(url, timeout=5)
        return res.json() if res.status_code == 200 else None
    except Exception as e:
        print(f"Firebase Read Error: {e}")
        return None

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
        prompt='consent select_account'
    )
    session['state'] = state
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    try:
        # Agar browser cookie drop bhi kar de, to Google ke response se state match karein
        state = session.get('state') or request.args.get('state')

        flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
        flow.redirect_uri = REDIRECT_URI

        auth_resp = request.url
        if auth_resp.startswith('http://'):
            auth_resp = auth_resp.replace('http://', 'https://', 1)

        flow.fetch_token(authorization_response=auth_resp)
        creds = flow.credentials

        # Unique token generate karein
        custom_token = secrets.token_hex(10)

        user_data = {
            'token': creds.token,
            'refresh_token': getattr(creds, 'refresh_token', None),
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        
        # Firebase me store karein
        firebase_write(f"youtube_tokens/{custom_token}", user_data)
        session['my_custom_token'] = custom_token

        return redirect(url_for('index'))

    except Exception as e:
        # Agar koi bhi error aati hai to direct screen par show hogi, 500 error page nahi aayega
        return f"<h3>Authentication Setup Issue:</h3><p>{str(e)}</p><a href='/login'>Try Again</a>", 400

# --- API ENDPOINT ---
@app.route('/api', methods=['GET', 'POST'])
def api_handler():
    token = request.args.get('token')
    action = request.args.get('action')

    if not token or not action:
        return jsonify({'status': 'error', 'message': 'token aur action dono zaroori hain!'}), 400

    stored_creds = firebase_read(f"youtube_tokens/{token}")
    if not stored_creds:
        return jsonify({'status': 'error', 'message': 'Token invalid hai ya database me nahi mila!'}), 404

    creds = Credentials(
        token=stored_creds.get('token'),
        refresh_token=stored_creds.get('refresh_token'),
        token_uri=stored_creds.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=stored_creds.get('client_id'),
        client_secret=stored_creds.get('client_secret'),
        scopes=stored_creds.get('scopes')
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        stored_creds['token'] = creds.token
        firebase_write(f"youtube_tokens/{token}", stored_creds)

    youtube = build('youtube', 'v3', credentials=creds)

    try:
        # Subscribe Channel
        if action == 'subscribe':
            channel_id = request.args.get('channel_id')
            if not channel_id:
                return jsonify({'status': 'error', 'message': 'channel_id parameter missing!'}), 400

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
            return jsonify({'status': 'success', 'message': f'Channel {channel_id} successfully subscribed!'})

        # Like Video
        elif action == 'like':
            video_id = request.args.get('video_id')
            if not video_id:
                return jsonify({'status': 'error', 'message': 'video_id parameter missing!'}), 400

            youtube.videos().rate(id=video_id, rating='like').execute()
            return jsonify({'status': 'success', 'message': f'Video {video_id} liked!'})

        # Search Query
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
