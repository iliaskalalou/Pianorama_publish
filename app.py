#!/usr/bin/env python3
"""
Backend Flask pour Content Studio Pro
TikTok OAuth + Content Posting + Instagram OAuth + Content Publishing
Déployable sur Railway, Heroku, Render
"""

from flask import Flask, request, jsonify, redirect, send_file, abort
from flask_cors import CORS
import hashlib
import hmac
import io
import json
import requests
import os
import secrets
import threading
import time
from urllib.parse import urlencode

app = Flask(__name__)
# Render's free tier accepts up to ~100 MB request bodies; cap to 100 MB.
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
CORS(app, origins=[
    "https://contentstudiopro.com",
    "https://www.contentstudiopro.com",
    "https://iliaskalalou.github.io",
    "http://localhost:*",
])

# TikTok configuration. Read everything from environment variables; the
# secret must never be hardcoded into a file that gets committed.
CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://iliaskalalou.github.io/content-studio-pro")
BACKEND_URL = os.environ.get("BACKEND_URL", "https://content-studio-pro.onrender.com")
REDIRECT_URI = f"{BACKEND_URL}/callback"

if not CLIENT_KEY or not CLIENT_SECRET:
    raise RuntimeError(
        "TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET must be set in the environment "
        "(e.g. via Railway / Render / Heroku config or a local .env file)."
    )

# Instagram (Meta) configuration. Optional: only needed if Instagram OAuth
# is used. The app starts up fine without these — Instagram routes will return
# 503 if they are missing.
INSTAGRAM_APP_ID = os.environ.get("INSTAGRAM_APP_ID", "927504810318319")
INSTAGRAM_APP_SECRET = os.environ.get("INSTAGRAM_APP_SECRET", "")
INSTAGRAM_REDIRECT_URI = f"{BACKEND_URL}/instagram/callback"
INSTAGRAM_SCOPES = "instagram_business_basic,instagram_business_content_publish"
INSTAGRAM_GRAPH_VERSION = "v23.0"

VERSION = "v2.6-2026-06-14-refresh-token"


@app.route('/')
def home():
    """API home page."""
    return jsonify({
        "status": "online",
        "version": VERSION,
        "service": "Content Studio Pro Backend",
        "endpoints": {
            "/auth": "Initiate TikTok OAuth flow",
            "/callback": "TikTok OAuth callback",
            "/api/user-info": "Get TikTok user info",
            "/api/publish": "Publish video directly to TikTok (video.publish)",
            "/api/upload": "Upload video as TikTok draft (video.upload)",
            "/instagram/auth": "Initiate Instagram OAuth flow",
            "/instagram/callback": "Instagram OAuth callback",
            "/instagram/deauthorize": "Webhook called by Meta when a user revokes our app",
            "/instagram/data-deletion-callback": "Webhook called by Meta when a user requests deletion",
            "/api/instagram/user-info": "Get connected Instagram creator profile",
            "/api/instagram/publish": "Step 1: upload video + create Instagram container (returns container_id)",
            "/api/instagram/publish/status": "Step 2: poll container status_code until FINISHED",
            "/api/instagram/publish/finalize": "Step 3: publish the container (returns final media_id)",
        }
    })

@app.route('/auth')
def auth():
    """Start the TikTok OAuth flow.
    Both video.publish (direct posting) and video.upload (drafts) are
    requested so the app can demonstrate the two posting modes."""
    params = {
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": "user.info.basic,video.publish,video.upload",
        "redirect_uri": REDIRECT_URI,
        "state": request.args.get('state', 'state123')
    }

    auth_url = f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    """OAuth callback. Receives the auth code, exchanges it for an access
    token via TikTok's V2 token endpoint, then redirects back to the
    frontend with the token in the URL."""
    code = request.args.get('code')
    error = request.args.get('error')

    if error:
        return redirect(f"{FRONTEND_URL}?error={error}")
    if not code:
        return redirect(f"{FRONTEND_URL}?error=no_code")

    # V2 OAuth token endpoint. Must match the V2 auth URL used in /auth.
    token_url = "https://open.tiktokapis.com/v2/oauth/token/"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
    }
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }

    try:
        response = requests.post(token_url, data=data, headers=headers, timeout=10)
        result = response.json()
    except Exception as e:
        app.logger.error(f"Token exchange request failed: {e}")
        return redirect(f"{FRONTEND_URL}?error=server_error")

    # V2 response: access_token is at the top level, not under "data".
    access_token = result.get("access_token")
    if response.status_code == 200 and access_token:
        # Also forward the refresh_token + open_id so a pipeline can store the
        # refresh_token (valid ~365 days) and auto-renew the access_token
        # without a manual re-login. open_id identifies which account this is.
        from urllib.parse import urlencode as _ue
        params = {
            "token": access_token,
            "refresh_token": result.get("refresh_token", ""),
            "expires_in": result.get("expires_in", ""),
            "refresh_expires_in": result.get("refresh_expires_in", ""),
            "open_id": result.get("open_id", ""),
            "success": "true",
        }
        return redirect(f"{FRONTEND_URL}?{_ue(params)}")

    # Log the actual TikTok error to make future debugging possible.
    app.logger.error(
        f"Token exchange failed (status {response.status_code}): {result}"
    )
    err_code = result.get("error", "token_exchange_failed")
    return redirect(f"{FRONTEND_URL}?error={err_code}")

@app.route('/api/user-info')
def get_user_info():
    """Fetch TikTok user info via the V2 endpoint. The fields parameter is
    mandatory in V2 — without it the API returns an error."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "No token provided"}), 401

    token = auth_header.split(' ')[1]

    url = "https://open.tiktokapis.com/v2/user/info/"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"fields": "open_id,union_id,avatar_url,display_name"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get("error", {}).get("code") == "ok":
                return jsonify(result.get("data", {}).get("user", {}))
            app.logger.error(f"User info API error: {result}")
            return jsonify({"error": result.get("error", {})}), 400
        app.logger.error(f"User info HTTP {response.status_code}: {response.text}")
        return jsonify({"error": "Failed to get user info"}), 400
    except Exception as e:
        app.logger.error(f"User info request failed: {e}")
        return jsonify({"error": str(e)}), 500

def _read_token():
    """Extract the OAuth bearer token from the request headers."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    return auth_header.split(' ', 1)[1]


def _read_video_file():
    """Return (filename, bytes) for the uploaded video, or None.
    The frontend can either send the file as multipart/form-data under the
    'video' key, or fall back to a JSON body during the demo."""
    if 'video' in request.files:
        f = request.files['video']
        return f.filename, f.read()
    return None, None


def _post_to_tiktok(init_url, init_body, file_bytes, token):
    """Run the TikTok V2 publish flow:
    1. POST to init_url to obtain an upload_url and publish_id.
    2. PUT the video bytes to that upload_url.
    Returns (status_code, response_payload).
    """
    init_resp = requests.post(
        init_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=init_body,
        timeout=15,
    )
    init_data = init_resp.json()
    if init_resp.status_code != 200 or init_data.get("error", {}).get("code") not in ("ok", None):
        app.logger.error(f"TikTok init failed (HTTP {init_resp.status_code}): {init_data}")
        return init_resp.status_code, init_data

    upload_url = init_data.get("data", {}).get("upload_url")
    publish_id = init_data.get("data", {}).get("publish_id")
    if not upload_url:
        app.logger.error(f"No upload_url returned by TikTok: {init_data}")
        return 500, {"error": {"code": "no_upload_url", "message": "TikTok did not return an upload URL"}}

    file_size = len(file_bytes)
    put_resp = requests.put(
        upload_url,
        data=file_bytes,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
        },
        timeout=60,
    )
    if put_resp.status_code not in (200, 201):
        app.logger.error(f"TikTok upload PUT failed (HTTP {put_resp.status_code}): {put_resp.text}")
        return put_resp.status_code, {"error": {"code": "upload_failed", "message": put_resp.text[:500]}}

    return 200, {"publish_id": publish_id}


def _read_bool_form(field: str, default: bool = False) -> bool:
    """Parse a 'true'/'false' string from the multipart form body."""
    raw = request.form.get(field)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


@app.route('/api/publish', methods=['POST'])
def publish_video():
    """Direct Post: publish a video straight to the user's TikTok profile.
    Uses the video.publish scope. The privacy level chosen by the user is
    applied to the published content, along with the creator's commercial
    disclosure and interaction settings (Allow Comments / Duet / Stitch)."""
    token = _read_token()
    if not token:
        return jsonify({"error": "No token provided"}), 401

    title = request.form.get('title', 'Video from Content Studio Pro')
    privacy = request.form.get('privacy', 'SELF_ONLY')
    # UI sends commercial=true/false; UI sends disable_comment/duet/stitch as
    # "true" when the corresponding "Allow X" toggle is UNCHECKED.
    is_commercial = _read_bool_form('commercial', False)
    disable_comment = _read_bool_form('disable_comment', False)
    disable_duet = _read_bool_form('disable_duet', False)
    disable_stitch = _read_bool_form('disable_stitch', False)

    filename, file_bytes = _read_video_file()
    if not file_bytes:
        return jsonify({"success": False, "message": "No video file provided"}), 400

    # Branded content cannot be set to SELF_ONLY (TikTok policy).
    if is_commercial and privacy == "SELF_ONLY":
        return jsonify({
            "success": False,
            "error": {"code": "invalid_branded_content_privacy",
                      "message": "Branded content cannot be set to private (SELF_ONLY)."},
        }), 400

    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy,
            "disable_duet": disable_duet,
            "disable_comment": disable_comment,
            "disable_stitch": disable_stitch,
            "video_cover_timestamp_ms": 0,
            # Commercial-content disclosure flags forwarded to TikTok exactly
            # as the creator set them on the dashboard. Off by default.
            "brand_content_toggle": is_commercial,
            "brand_organic_toggle": is_commercial,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": len(file_bytes),
            "chunk_size": len(file_bytes),
            "total_chunk_count": 1,
        },
    }

    status, payload = _post_to_tiktok(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        init_body, file_bytes, token,
    )

    if status == 200:
        return jsonify({
            "success": True,
            "mode": "direct_post",
            "publish_id": payload.get("publish_id"),
            "filename": filename,
            "privacy": privacy,
            "message": f"Video '{title}' was sent to TikTok. In Sandbox the post is private to the connected creator.",
        })

    return jsonify({
        "success": False,
        "mode": "direct_post",
        "error": payload.get("error", {}),
        "message": payload.get("error", {}).get("message", "Direct post failed."),
    }), status if status != 200 else 500


@app.route('/api/upload', methods=['POST'])
def upload_draft():
    """Upload to TikTok as a draft. Uses the video.upload scope. The video
    lands in the user's TikTok inbox where they can review, edit and finalise
    the post inside the TikTok app."""
    token = _read_token()
    if not token:
        return jsonify({"error": "No token provided"}), 401

    title = request.form.get('title', 'Video from Pianorama Publish')
    filename, file_bytes = _read_video_file()
    if not file_bytes:
        return jsonify({"success": False, "message": "No video file provided"}), 400

    init_body = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": len(file_bytes),
            "chunk_size": len(file_bytes),
            "total_chunk_count": 1,
        },
    }

    status, payload = _post_to_tiktok(
        "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
        init_body, file_bytes, token,
    )

    if status == 200:
        return jsonify({
            "success": True,
            "mode": "upload_draft",
            "publish_id": payload.get("publish_id"),
            "filename": filename,
            "message": f"Video '{title}' uploaded as a draft. Open your TikTok inbox to finalise the post.",
        })

    return jsonify({
        "success": False,
        "mode": "upload_draft",
        "error": payload.get("error", {}),
        "message": payload.get("error", {}).get("message", "Draft upload failed."),
    }), status if status != 200 else 500

# --- Instagram OAuth + Content Publishing ---
# Implements the new "Instagram API with Instagram Login" flow (launched 2024).
# OAuth endpoints are on instagram.com, Graph API on graph.instagram.com.
# Content publishing on Instagram requires a publicly accessible video URL
# (pull-from-URL only), so we host the uploaded bytes in memory for ~1h.
_instagram_tmp_videos = {}  # tmp_id -> {bytes, expires_at}
_INSTAGRAM_TMP_TTL_SECONDS = 3600
_instagram_tmp_lock = threading.Lock()


def _instagram_configured():
    return bool(INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET)


def _instagram_store_tmp_video(file_bytes):
    """Store uploaded video bytes in memory, return the public URL."""
    tmp_id = secrets.token_urlsafe(24) + ".mp4"
    with _instagram_tmp_lock:
        # Opportunistic cleanup of expired entries
        now = time.time()
        for old_id in list(_instagram_tmp_videos.keys()):
            if _instagram_tmp_videos[old_id]["expires_at"] < now:
                _instagram_tmp_videos.pop(old_id, None)
        _instagram_tmp_videos[tmp_id] = {
            "bytes": file_bytes,
            "expires_at": now + _INSTAGRAM_TMP_TTL_SECONDS,
        }
    return f"{BACKEND_URL}/instagram/tmp-video/{tmp_id}"


@app.route('/instagram/tmp-video/<tmp_id>')
def instagram_serve_tmp_video(tmp_id):
    """Serve a temporary video so Instagram can pull it (push_by_url flow)."""
    with _instagram_tmp_lock:
        entry = _instagram_tmp_videos.get(tmp_id)
        if entry and entry["expires_at"] < time.time():
            _instagram_tmp_videos.pop(tmp_id, None)
            entry = None
    if not entry:
        return ("Not Found or Expired", 404)
    return send_file(
        io.BytesIO(entry["bytes"]),
        mimetype="video/mp4",
        as_attachment=False,
        download_name=tmp_id,
    )


@app.route('/instagram/auth')
def instagram_auth():
    """Start the Instagram OAuth flow (Business Login)."""
    if not _instagram_configured():
        return jsonify({"error": "Instagram not configured on this backend"}), 503
    params = {
        "client_id": INSTAGRAM_APP_ID,
        "response_type": "code",
        "scope": INSTAGRAM_SCOPES,
        "redirect_uri": INSTAGRAM_REDIRECT_URI,
        "state": request.args.get('state', secrets.token_urlsafe(12)),
    }
    auth_url = f"https://www.instagram.com/oauth/authorize?{urlencode(params)}"
    return redirect(auth_url)


@app.route('/instagram/callback')
def instagram_callback():
    """OAuth callback for Instagram. Exchanges code -> short-lived token,
    then upgrades to a long-lived token (~60 days)."""
    if not _instagram_configured():
        return jsonify({"error": "Instagram not configured on this backend"}), 503

    code = request.args.get('code')
    err = request.args.get('error') or request.args.get('error_description')
    if err:
        return redirect(f"{FRONTEND_URL}?ig_error={err}")
    if not code:
        return redirect(f"{FRONTEND_URL}?ig_error=no_code")

    # 1) Exchange code -> short-lived token (~1h)
    try:
        short_resp = requests.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": INSTAGRAM_APP_ID,
                "client_secret": INSTAGRAM_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": INSTAGRAM_REDIRECT_URI,
                "code": code,
            },
            timeout=15,
        )
        short_data = short_resp.json()
    except Exception as e:
        app.logger.error(f"Instagram short token exchange failed: {e}")
        return redirect(f"{FRONTEND_URL}?ig_error=server_error")

    short_token = short_data.get("access_token")
    if not short_token:
        app.logger.error(f"Instagram short token missing: {short_data}")
        return redirect(f"{FRONTEND_URL}?ig_error=token_exchange_failed")

    # 2) Upgrade to long-lived token (~60 days)
    try:
        long_resp = requests.get(
            f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": INSTAGRAM_APP_SECRET,
                "access_token": short_token,
            },
            timeout=15,
        )
        long_data = long_resp.json()
    except Exception as e:
        app.logger.error(f"Instagram long token exchange failed: {e}")
        long_data = {}

    long_token = long_data.get("access_token", short_token)
    expires_in = long_data.get("expires_in", 3600)
    return redirect(
        f"{FRONTEND_URL}?ig_token={long_token}&ig_expires_in={expires_in}&ig_success=true"
    )


def _parse_signed_request(signed_request: str):
    """Parse Meta's signed_request payload, verify HMAC-SHA256 signature.
    Returns the decoded payload dict, or None if signature is invalid."""
    import base64
    if not signed_request or '.' not in signed_request:
        return None
    try:
        encoded_sig, payload = signed_request.split('.', 1)
        # Add padding for base64url
        def _b64decode(s):
            s = s + '=' * (-len(s) % 4)
            return base64.urlsafe_b64decode(s)
        sig = _b64decode(encoded_sig)
        data = json.loads(_b64decode(payload).decode('utf-8'))
        expected = hmac.new(
            INSTAGRAM_APP_SECRET.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(sig, expected):
            app.logger.warning("Instagram signed_request: signature mismatch")
            return None
        return data
    except Exception as e:
        app.logger.error(f"Instagram signed_request parse failed: {e}")
        return None


@app.route('/instagram/deauthorize', methods=['POST'])
def instagram_deauthorize():
    """Webhook called by Meta when a user removes our app from their Instagram
    authorized apps. We do not store user data persistently, so there is
    nothing to delete server-side; we just acknowledge the notification."""
    if not _instagram_configured():
        return ("Instagram not configured", 503)
    signed_request = request.form.get("signed_request", "")
    payload = _parse_signed_request(signed_request)
    if payload is None:
        return ("Invalid signature", 400)
    app.logger.info(f"Instagram deauthorization received for user_id={payload.get('user_id')}")
    return ("OK", 200)


@app.route('/instagram/data-deletion-callback', methods=['POST'])
def instagram_data_deletion_callback():
    """Webhook called by Meta when a user requests deletion of their data
    associated with our app. We must respond with a JSON containing a status
    URL and a confirmation_code, per Meta's specification."""
    if not _instagram_configured():
        return ("Instagram not configured", 503)
    signed_request = request.form.get("signed_request", "")
    payload = _parse_signed_request(signed_request)
    if payload is None:
        return ("Invalid signature", 400)
    user_id = payload.get('user_id', 'unknown')
    confirmation_code = hashlib.sha256(
        f"{user_id}-{INSTAGRAM_APP_SECRET}".encode('utf-8')
    ).hexdigest()[:16]
    app.logger.info(f"Instagram data deletion request for user_id={user_id}, code={confirmation_code}")
    # We do not store user data persistently, so the deletion is immediate.
    return jsonify({
        "url": f"{FRONTEND_URL}/data-deletion?ig_user_id={user_id}&code={confirmation_code}",
        "confirmation_code": confirmation_code,
    })


@app.route('/api/instagram/user-info')
def instagram_user_info():
    """Return the connected Instagram creator profile (id, username, etc.)."""
    if not _instagram_configured():
        return jsonify({"error": "Instagram not configured"}), 503
    token = _read_token()
    if not token:
        return jsonify({"error": "No token provided"}), 401
    try:
        resp = requests.get(
            f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/me",
            params={
                "fields": "id,username,account_type,profile_picture_url",
                "access_token": token,
            },
            timeout=10,
        )
        data = resp.json()
        if resp.status_code != 200 or "id" not in data:
            app.logger.error(f"Instagram user-info error: {data}")
            return jsonify({"error": data}), 400
        return jsonify(data)
    except Exception as e:
        app.logger.error(f"Instagram user-info request failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/instagram/publish', methods=['POST'])
def instagram_publish():
    """Step 1 of the Instagram publish flow.
    Uploads the video bytes, hosts them temporarily, creates an Instagram
    media container, and returns the container_id immediately.

    Render's free tier has a request timeout (~30-100s) so we cannot wait
    for Instagram to finish processing the container in the same call.
    The client must then poll /api/instagram/publish/status and call
    /api/instagram/publish/finalize when the container is FINISHED.
    """
    if not _instagram_configured():
        return jsonify({"error": "Instagram not configured"}), 503
    token = _read_token()
    if not token:
        return jsonify({"error": "No token provided"}), 401

    caption = request.form.get('caption', '')
    share_to_feed = request.form.get('share_to_feed', 'true').lower() == 'true'
    filename, file_bytes = _read_video_file()
    if not file_bytes:
        return jsonify({"success": False, "message": "No video file provided"}), 400

    # 1) Host the video temporarily so Instagram can pull it
    video_url = _instagram_store_tmp_video(file_bytes)

    # 2) Resolve the Instagram user id
    try:
        me_resp = requests.get(
            f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/me",
            params={"fields": "id,username", "access_token": token},
            timeout=10,
        )
        me_data = me_resp.json()
    except Exception as e:
        return jsonify({"error": f"Could not resolve Instagram user: {e}"}), 500
    ig_user_id = me_data.get("id")
    if not ig_user_id:
        app.logger.error(f"Instagram /me failed: {me_data}")
        return jsonify({"error": "Could not resolve Instagram user id", "details": me_data}), 400

    # 3) Create the media container (returns immediately)
    try:
        container_resp = requests.post(
            f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/{ig_user_id}/media",
            params={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true" if share_to_feed else "false",
                "access_token": token,
            },
            timeout=20,
        )
        container_data = container_resp.json()
    except Exception as e:
        return jsonify({"error": f"Container creation failed: {e}"}), 500
    container_id = container_data.get("id")
    if not container_id:
        app.logger.error(f"Instagram container creation failed: {container_data}")
        return jsonify({"error": "Container creation failed", "details": container_data}), 400

    return jsonify({
        "success": True,
        "stage": "container_created",
        "container_id": container_id,
        "ig_user_id": ig_user_id,
        "filename": filename,
        "share_to_feed": share_to_feed,
        "video_url": video_url,
        "next_step": "Poll GET /api/instagram/publish/status?container_id=<id> until status_code == FINISHED, then POST /api/instagram/publish/finalize with container_id and ig_user_id.",
    })


@app.route('/api/instagram/publish/status')
def instagram_publish_status():
    """Step 2 of the Instagram publish flow.
    Returns the current status_code for a media container. The client should
    poll this every 3-5 seconds until status_code == FINISHED (or ERROR).
    """
    if not _instagram_configured():
        return jsonify({"error": "Instagram not configured"}), 503
    token = _read_token()
    if not token:
        return jsonify({"error": "No token provided"}), 401
    container_id = request.args.get('container_id', '').strip()
    if not container_id:
        return jsonify({"error": "Missing container_id"}), 400
    try:
        status_resp = requests.get(
            f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/{container_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=10,
        )
        status_data = status_resp.json()
    except Exception as e:
        return jsonify({"error": f"Status check failed: {e}"}), 502
    if status_resp.status_code != 200:
        return jsonify({"error": "Instagram returned non-200", "details": status_data}), status_resp.status_code
    return jsonify({
        "container_id": container_id,
        "status_code": status_data.get("status_code"),
        "status": status_data.get("status"),
    })


@app.route('/api/instagram/publish/finalize', methods=['POST'])
def instagram_publish_finalize():
    """Step 3 of the Instagram publish flow.
    Publishes a media container that has reached status_code == FINISHED.
    Returns the final media_id.
    """
    if not _instagram_configured():
        return jsonify({"error": "Instagram not configured"}), 503
    token = _read_token()
    if not token:
        return jsonify({"error": "No token provided"}), 401
    container_id = (request.form.get('container_id') or request.json and request.json.get('container_id') or '').strip()
    ig_user_id = (request.form.get('ig_user_id') or request.json and request.json.get('ig_user_id') or '').strip()
    if not container_id or not ig_user_id:
        return jsonify({"error": "Missing container_id or ig_user_id"}), 400
    try:
        publish_resp = requests.post(
            f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/{ig_user_id}/media_publish",
            params={"creation_id": container_id, "access_token": token},
            timeout=20,
        )
        publish_data = publish_resp.json()
    except Exception as e:
        return jsonify({"error": f"Publish call failed: {e}"}), 502
    media_id = publish_data.get("id")
    if not media_id:
        app.logger.error(f"Instagram publish failed: {publish_data}")
        return jsonify({"error": "Publish failed", "details": publish_data}), 400
    return jsonify({
        "success": True,
        "stage": "published",
        "media_id": media_id,
        "container_id": container_id,
        "message": f"Reel published on Instagram (media_id={media_id}).",
    })


# --- TikTok URL verification files ---
# TikTok asks for a static .txt file at the URL we declared as Redirect URI.
# Since Render does not serve static files, we expose the file content
# through a Flask route that matches several common verification paths.
TIKTOK_VERIFICATIONS = {
    "tiktokhgNUP2jOBL4lC10ioiEatBEgMxy0ow8L.txt":
        "tiktok-developers-site-verification=hgNUP2jOBL4lC10ioiEatBEgMxy0ow8L",
}


def _serve_tiktok_verification(filename):
    body = TIKTOK_VERIFICATIONS.get(filename)
    if body is None:
        return "Not Found", 404
    return body, 200, {"Content-Type": "text/plain"}


@app.route('/<path:filename>.txt')
def tiktok_verification_root(filename):
    """Serve TikTok verification files at the domain root, e.g.
    https://pianorama-publish.onrender.com/tiktokXXX.txt"""
    return _serve_tiktok_verification(f"{filename}.txt")


@app.route('/callback/<path:filename>.txt')
def tiktok_verification_under_callback(filename):
    """Serve TikTok verification files directly under /callback, e.g.
    https://pianorama-publish.onrender.com/callback/tiktokXXX.txt"""
    return _serve_tiktok_verification(f"{filename}.txt")


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
