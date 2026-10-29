import os
import re
import json
import secrets
import urllib.parse
import requests
import io
import zipfile
from flask import Flask, redirect, request, session, url_for, render_template, jsonify, Response, send_file
from flask_cors import CORS
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.http import MediaFileUpload
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app) 
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_permanent_clean_key_9988_yt")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "abhay12345")
FIREBASE_URL = "https://luckymall-23b3f-default-rtdb.firebaseio.com"
REDIRECT_URI = "https://no-fun-only-money.onrender.com/oauth2callback"

try:
    with open("client_secret.json") as f:
        secret_data = json.load(f)
    cfg = secret_data.get("web") or secret_data.get("installed")
    CLIENT_ID = cfg["client_id"]
    CLIENT_SECRET = cfg["client_secret"]
except:
    CLIENT_ID = "YOUR_CLIENT_ID"
    CLIENT_SECRET = "YOUR_CLIENT_SECRET"

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
    # यह तुम्हारा पुराना index.html खोलेगा (जहाँ Google Login है)
    token = request.args.get('token') or session.get('my_custom_token')
    user_info = None
    if token:
        user_data = firebase_read(f"youtube_tokens/{token}")
        if user_data: user_info = user_data.get('user_info', {})
    return render_template('index.html', token=token, user=user_info)

# ✅ NAYA ROUTE: सिर्फ टोकन से लॉगिन करने वाला पेज
@app.route('/token-login')
def token_login_page():
    return render_template('token-login.html')

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

# --- MASTER API ---
@app.route('/api', methods=['GET', 'POST'])
def api_handler():
    token = request.args.get('token')
    action = request.args.get('action')
    if not token or not action: return jsonify({'status': 'error', 'message': 'Missing params'}), 400

    youtube, err = get_authenticated_service(token)
    if err: return jsonify({'status': 'error', 'message': err}), 400

    try:
        # 1. SUBSCRIBE / UNSUBSCRIBE / LIKE / DISLIKE
        if action == 'subscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            target_channel_id = resolve_channel_id(youtube, channel_input)
            youtube.subscriptions().insert(part="snippet", body={'snippet': {'resourceId': {'kind': 'youtube#channel', 'channelId': target_channel_id}}}).execute()
            return jsonify({'status': 'success', 'message': 'Subscribed successfully!'})

        elif action == 'unsubscribe':
            channel_input = request.args.get('channel_url') or request.args.get('channel_id')
            target_channel_id = resolve_channel_id(youtube, channel_input)
            sub_res = youtube.subscriptions().list(part="id", mine=True, forChannelId=target_channel_id).execute()
            if not sub_res.get('items'): return jsonify({'status': 'failed', 'message': 'Not subscribed!'}), 404
            youtube.subscriptions().delete(id=sub_res['items'][0]['id']).execute()
            return jsonify({'status': 'success', 'message': 'Unsubscribed successfully!'})

        elif action == 'like':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            youtube.videos().rate(id=extract_video_id(video_input), rating='like').execute()
            return jsonify({'status': 'success', 'message': 'Video Liked!'})

        elif action == 'dislike':
            video_input = request.args.get('video_url') or request.args.get('video_id')
            youtube.videos().rate(id=extract_video_id(video_input), rating='dislike').execute()
            return jsonify({'status': 'success', 'message': 'Video Disliked!'})

        # 2. SEARCH & SUBSCRIPTIONS
        elif action == 'subscriptions':
            page_token = request.args.get('pageToken')
            kwargs = {"part": "snippet", "mine": True, "maxResults": 50}
            if page_token: kwargs["pageToken"] = page_token
            res = youtube.subscriptions().list(**kwargs).execute()
            items = []
            for item in res.get('items', []):
                snippet = item.get('snippet', {})
                thumbnails = snippet.get('thumbnails', {})
                thumb_url = thumbnails.get('high', {}).get('url') or thumbnails.get('default', {}).get('url', '')
                items.append({'title': snippet.get('title', 'Unknown'), 'channelId': snippet.get('resourceId', {}).get('channelId'), 'thumbnail': thumb_url, 'channelUrl': f"https://youtube.com/channel/{snippet.get('resourceId', {}).get('channelId')}"})
            return jsonify({'status': 'success', 'results': items, 'nextPageToken': res.get('nextPageToken', None)})

        elif action == 'search':
            query = request.args.get('q', 'trending')
            page_token = request.args.get('pageToken')
            kwargs = {"part": "snippet", "q": query, "maxResults": 50, "type": "video"}
            if page_token: kwargs["pageToken"] = page_token
            res = youtube.search().list(**kwargs).execute()
            items = []
            for item in res.get('items', []):
                if item['id'].get('videoId'):
                    items.append({'title': item['snippet']['title'], 'channelTitle': item['snippet']['channelTitle'], 'channelId': item['snippet']['channelId'], 'videoId': item['id']['videoId'], 'thumbnail': item['snippet']['thumbnails']['high']['url'], 'channelUrl': f"https://youtube.com/channel/{item['snippet']['channelId']}"})
            return jsonify({'status': 'success', 'results': items, 'nextPageToken': res.get('nextPageToken', None)})

        # 3. NEW: TOKEN LOGIN DASHBOARD APIS
        elif action == 'check_token':
            ch_resp = youtube.channels().list(part="snippet,statistics", mine=True).execute()
            if not ch_resp.get('items'): return jsonify({'status': 'error', 'message': 'No channel found for this token.'}), 404
            ch = ch_resp['items'][0]
            return jsonify({
                'status': 'success',
                'channel': {
                    'id': ch['id'],
                    'title': ch['snippet']['title'],
                    'picture': ch['snippet']['thumbnails']['high']['url'],
                    'subscribers': ch['statistics'].get('subscriberCount', 'Hidden'),
                    'videos': ch['statistics'].get('videoCount', '0')
                }
            })

        elif action == 'get_videos':
            search_res = youtube.search().list(part="snippet", forMine=True, type="video", maxResults=50).execute()
            items = []
            for item in search_res.get('items', []):
                items.append({
                    'videoId': item['id']['videoId'],
                    'title': item['snippet']['title'],
                    'description': item['snippet']['description'],
                    'thumbnail': item['snippet']['thumbnails']['default']['url']
                })
            return jsonify({'status': 'success', 'results': items})

        elif action == 'delete_video':
            video_id = request.args.get('video_id')
            if not video_id: return jsonify({'status': 'error', 'message': 'Missing video_id'}), 400
            youtube.videos().delete(id=video_id).execute()
            return jsonify({'status': 'success', 'message': 'Video deleted successfully!'})

        elif action == 'edit_video':
            if request.method == 'POST':
                data = request.get_json()
                video_id = data.get('video_id')
                title = data.get('title')
                description = data.get('description')
                if not video_id or not title: return jsonify({'status': 'error', 'message': 'Missing data'}), 400
                
                vid_res = youtube.videos().list(part="snippet", id=video_id).execute()
                if not vid_res.get('items'): return jsonify({'status': 'error', 'message': 'Video not found'}), 404
                
                snippet = vid_res['items'][0]['snippet']
                snippet['title'] = title
                snippet['description'] = description
                snippet['categoryId'] = snippet.get('categoryId', '22')
                
                youtube.videos().update(part="snippet", body={'id': video_id, 'snippet': snippet}).execute()
                return jsonify({'status': 'success', 'message': 'Video updated successfully!'})
            return jsonify({'status': 'error', 'message': 'POST required'}), 405

        elif action == 'upload_video':
            if request.method == 'POST':
                if 'video' not in request.files: return jsonify({'status': 'error', 'message': 'No video file provided'}), 400
                file = request.files['video']
                title = request.form.get('title', 'Untitled Video')
                description = request.form.get('description', '')
                
                if file.filename == '': return jsonify({'status': 'error', 'message': 'Empty filename'}), 400

                filename = secure_filename(file.filename)
                filepath = os.path.join("/tmp", filename)
                file.save(filepath)

                try:
                    body = {
                        'snippet': {'title': title, 'description': description, 'categoryId': '22'},
                        'status': {'privacyStatus': 'public'}
                    }
                    media = MediaFileUpload(filepath, chunksize=-1, resumable=True)
                    youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
                    os.remove(filepath)
                    return jsonify({'status': 'success', 'message': 'Video uploaded successfully!'})
                except Exception as e:
                    if os.path.exists(filepath): os.remove(filepath)
                    return jsonify({'status': 'failed', 'error': str(e)}), 500
            return jsonify({'status': 'error', 'message': 'POST required'}), 405

        return jsonify({'status': 'error', 'message': 'Invalid action!'}), 400
    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
