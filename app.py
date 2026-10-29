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
    guide_template = """
=====================================================
        YOUTUBE APP / AUTOMATION - USER GUIDE
=====================================================
Aapka User API Token: TOKEN_PLACEHOLDER

Ye guide aapko batayegi ki aap is API se apna frontend/app kaise bana sakte hain.
Aap isse YouTube videos load, unhe play aur apne subscribed channels check kar sakte hain.

-----------------------------------------------------
1. VIDEO SEARCH & INFINITE LOAD KAISE KAREIN?
-----------------------------------------------------
URL ENDPOINT:
https://no-fun-only-money.onrender.com/api?token=TOKEN_PLACEHOLDER&action=search&q=YOUR_SEARCH_QUERY&pageToken=NEXT_PAGE_TOKEN

- q: Aap kya search karna chahte hain (e.g. trending india, latest songs).
- pageToken: (Optional) Infinite scrolling ke liye. API response me 'nextPageToken' aayega, usko agli request me bhej kar aur aage ke videos la sakte hain (unlimited scrolling).

-----------------------------------------------------
2. VIDEOS KO WEBSITE / APP ME PLAY KAISE KAREIN BINA LOGO KE?
-----------------------------------------------------
Ab aap direct MP4 stream le sakte hain taaki 'Watch on YouTube' logo na aaye:
ENDPOINT: https://no-fun-only-money.onrender.com/api?token=TOKEN_PLACEHOLDER&action=get_stream&video_id=VIDEO_ID

HTML5 Video Tag me lagayein:
<video width="100%" controls autoplay src="API_SE_MILA_HUA_URL"></video>

-----------------------------------------------------
3. APNE SUBSCRIBED CHANNELS KAISE CHECK KAREIN?
-----------------------------------------------------
URL ENDPOINT:
https://no-fun-only-money.onrender.com/api?token=TOKEN_PLACEHOLDER&action=subscriptions
=====================================================
"""
    guide_text = guide_template.replace("TOKEN_PLACEHOLDER", token)
    return Response(guide_text, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename=user_guide_{token}.txt"})

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
        # User Action Handlers
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

        # --- NAYA FEATURE: NO LOGO RAW STREAM / AUTO DOWNLOAD VIA PUBLIC API ---
        elif action == 'get_stream':
            video_id = request.args.get('video_id')
            if not video_id: return jsonify({'status': 'error', 'message': 'Missing video_id'}), 400
            
            # FIXED: 4 different API servers added! Agar ek block ho jaaye toh code crash nahi karega
            instances = [
                "https://pipedapi.kavin.rocks",
                "https://pipedapi.tokhmi.xyz",
                "https://pipedapi.smnz.de",
                "https://piped-api.garudalinux.org"
            ]
            
            stream_url = None
            for base_url in instances:
                try:
                    res = requests.get(f"{base_url}/streams/{video_id}", timeout=8)
                    if res.status_code == 200:
                        data = res.json()
                        for stream in data.get('videoStreams', []):
                            if not stream.get('videoOnly'):
                                stream_url = stream.get('url')
                                if stream.get('quality') == '720p':
                                    break
                        if stream_url:
                            break # Found working stream!
                except:
                    continue # Try next instance if current one fails
                    
            if stream_url:
                return jsonify({'status': 'success', 'stream_url': stream_url})
            else:
                return jsonify({'status': 'failed', 'message': 'All raw stream servers failed.'}), 404

        # Load / Read Handlers
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
                items.append({
                    'title': snippet.get('title', 'Unknown Channel'),
                    'channelId': snippet.get('resourceId', {}).get('channelId'),
                    'thumbnail': thumb_url,
                    'channelUrl': f"https://youtube.com/channel/{snippet.get('resourceId', {}).get('channelId')}"
                })
            
            return jsonify({
                'status': 'success', 
                'results': items, 
                'nextPageToken': res.get('nextPageToken', None)
            })

        elif action == 'search':
            query = request.args.get('q', 'trending')
            page_token = request.args.get('pageToken')
            
            kwargs = {"part": "snippet", "q": query, "maxResults": 50, "type": "video"}
            if page_token: kwargs["pageToken"] = page_token
            
            res = youtube.search().list(**kwargs).execute()
            items = []
            for item in res.get('items', []):
                if item['id'].get('videoId'):
                    ch_id = item['snippet']['channelId']
                    items.append({
                        'title': item['snippet']['title'],
                        'channelTitle': item['snippet']['channelTitle'],
                        'channelId': ch_id,
                        'videoId': item['id']['videoId'],
                        'thumbnail': item['snippet']['thumbnails']['high']['url'],
                        'channelUrl': f"https://youtube.com/channel/{ch_id}"
                    })
            return jsonify({
                'status': 'success', 
                'results': items,
                'nextPageToken': res.get('nextPageToken', None) # Paging ke liye bhejna
            })

        return jsonify({'status': 'error', 'message': 'Invalid action!'}), 400
    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
