#!/usr/bin/env python3
"""
Publie une video comme Reel sur Instagram via la Content Publishing API
(Instagram Graph API + Instagram Business Login).

Utilise le backend Content Studio Pro deja deploye sur api.contentstudiopro.com,
qui se charge de :
  1. Heberger temporairement la video (Instagram exige une URL publique)
  2. Creer le media container via Graph API
  3. Polling jusqu'a status FINISHED
  4. Publier le container

Usage:
    python publish_to_instagram.py <video.mp4> [--caption "..."] [--no-share-to-feed]

Le token peut etre fourni de 3 manieres (par ordre de priorite) :
  1. Variable d'env INSTAGRAM_ACCESS_TOKEN
  2. Flag CLI --token "..."
  3. Saisie interactive (le script ouvre Chrome sur /instagram/auth)

Validite du token : ~60 jours (Instagram long-lived token).
Cache local : ~/.instagram_token.json
"""

import json
import os
import sys
import time
import webbrowser
from pathlib import Path

import requests

# ------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------
SITE_URL = "https://contentstudiopro.com"
AUTH_URL = "https://api.contentstudiopro.com/instagram/auth"
PUBLISH_URL = "https://api.contentstudiopro.com/api/instagram/publish"
PUBLISH_STATUS_URL = "https://api.contentstudiopro.com/api/instagram/publish/status"
PUBLISH_FINALIZE_URL = "https://api.contentstudiopro.com/api/instagram/publish/finalize"
USERINFO_URL = "https://api.contentstudiopro.com/api/instagram/user-info"

TOKEN_CACHE = Path.home() / ".instagram_token.json"


# ------------------------------------------------------------------
# Token handling
# ------------------------------------------------------------------
def _load_cached_token() -> str | None:
    if not TOKEN_CACHE.exists():
        return None
    try:
        cached = json.loads(TOKEN_CACHE.read_text())
    except json.JSONDecodeError:
        return None
    if cached.get("expires_at", 0) <= time.time() + 60:
        return None
    return cached.get("access_token")


def _save_token(token: str, lifetime_seconds: int = 60 * 24 * 60 * 60) -> None:
    """Default lifetime = 60 days (Instagram long-lived token)."""
    payload = {
        "access_token": token,
        "expires_at": time.time() + lifetime_seconds - 60,
    }
    TOKEN_CACHE.write_text(json.dumps(payload, indent=2))
    print(f"  Token Instagram mis en cache dans {TOKEN_CACHE} (valide ~60 jours)")


def _prompt_for_token() -> str:
    print()
    print("=" * 70)
    print("AUTHENTIFICATION INSTAGRAM NECESSAIRE")
    print("=" * 70)
    print()
    print("Etape 1 : J'ouvre Chrome sur la page OAuth Instagram dans 3 secondes.")
    print(f"          Si rien ne s'ouvre, va manuellement sur :")
    print(f"          {AUTH_URL}")
    print()
    print("Etape 2 : Connecte-toi avec ton compte Instagram Business/Creator")
    print("          (test user) et autorise l'app Content Studio Pro.")
    print()
    print("Etape 3 : Apres autorisation, tu es redirige vers contentstudiopro.com.")
    print("          L'URL contiendra ?ig_token=XXX&ig_expires_in=YYY&ig_success=true")
    print("          Le frontend peut nettoyer l'URL apres quelques ms, donc :")
    print()
    print("          Option A - Copie depuis la barre d'adresse RAPIDEMENT")
    print("                     (la valeur entre 'ig_token=' et '&').")
    print()
    print("          Option B - Plus fiable : ouvre la console (Cmd+Option+J)")
    print("                     et tape :")
    print()
    print("             copy(sessionStorage.getItem('instagram_access_token'))")
    print()
    print("             (si pas dispo : tape sessionStorage et regarde toutes les cles)")
    print()
    print("Etape 4 : Reviens ici et colle le token.")
    print()
    time.sleep(3)
    webbrowser.open(AUTH_URL)
    token = input("Colle le token Instagram ici : ").strip()
    if not token:
        raise SystemExit("Token vide, abandon.")
    if token.startswith("ig_token="):
        token = token[len("ig_token="):]
    if "&" in token:
        token = token.split("&", 1)[0]
    _save_token(token)
    return token


def get_access_token(cli_token: str | None) -> str:
    # 1. CLI flag
    if cli_token:
        _save_token(cli_token)
        return cli_token
    # 2. Env var
    env_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if env_token:
        _save_token(env_token)
        return env_token
    # 3. Cache
    cached = _load_cached_token()
    if cached:
        print("  Token Instagram trouve en cache, valide.")
        return cached
    # 4. Prompt interactif
    return _prompt_for_token()


# ------------------------------------------------------------------
# Pre-publish check
# ------------------------------------------------------------------
def query_user_info(token: str) -> dict:
    """Recupere id, username, account_type du compte Instagram connecte."""
    resp = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"user-info HTTP {resp.status_code} : {resp.text[:500]}")
    return resp.json()


# ------------------------------------------------------------------
# Publish (3-step async flow to stay below Render's HTTP timeout)
#   1. POST /api/instagram/publish        -> creates container, returns container_id
#   2. GET  /api/instagram/publish/status -> poll status_code until FINISHED
#   3. POST /api/instagram/publish/finalize -> publishes the container
# ------------------------------------------------------------------
def _publish_step1_create_container(token, video_path, caption, share_to_feed):
    print(f"  [Step 1/3] Upload {video_path.name} + create Instagram container...")
    file_size = video_path.stat().st_size
    print(f"  Taille : {file_size / 1024 / 1024:.1f} MB")

    with video_path.open("rb") as f:
        files = {"video": (video_path.name, f, "video/mp4")}
        data = {
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
        }
        resp = requests.post(
            PUBLISH_URL,
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data=data,
            timeout=60,  # upload + create container, should be < 20 sec usually
        )

    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError(
            f"Step 1 : reponse non-JSON du backend (HTTP {resp.status_code}) : {resp.text[:500]}"
        )

    if resp.status_code != 200 or not payload.get("success"):
        raise RuntimeError(f"Step 1 echouee (HTTP {resp.status_code}) : {payload}")

    container_id = payload["container_id"]
    ig_user_id = payload["ig_user_id"]
    print(f"  Container cree : {container_id}")
    return container_id, ig_user_id


def _publish_step2_poll_until_finished(token, container_id, max_wait_seconds=300):
    print("  [Step 2/3] Polling container status (Instagram processe la video)...")
    deadline = time.time() + max_wait_seconds
    last_status = None
    while time.time() < deadline:
        try:
            resp = requests.get(
                PUBLISH_STATUS_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"container_id": container_id},
                timeout=15,
            )
            payload = resp.json()
        except Exception as e:
            print(f"    status check failed (on retente) : {e}")
            time.sleep(5)
            continue
        status_code = payload.get("status_code")
        if status_code != last_status:
            print(f"    status_code = {status_code}")
            last_status = status_code
        if status_code == "FINISHED":
            return
        if status_code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Step 2 : Instagram a rejete la video : {payload}")
        time.sleep(5)
    raise TimeoutError(
        f"Step 2 : timeout apres {max_wait_seconds}s. Dernier status : {last_status}"
    )


def _publish_step3_finalize(token, container_id, ig_user_id):
    print("  [Step 3/3] Publication du Reel...")
    resp = requests.post(
        PUBLISH_FINALIZE_URL,
        headers={"Authorization": f"Bearer {token}"},
        data={"container_id": container_id, "ig_user_id": ig_user_id},
        timeout=30,
    )
    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError(
            f"Step 3 : reponse non-JSON (HTTP {resp.status_code}) : {resp.text[:500]}"
        )
    if resp.status_code != 200 or not payload.get("success"):
        raise RuntimeError(f"Step 3 echouee (HTTP {resp.status_code}) : {payload}")
    return payload


def publish_reel(token: str, video_path: Path, caption: str, share_to_feed: bool) -> dict:
    """Publication 3-etapes async pour rester sous le timeout Render."""
    container_id, ig_user_id = _publish_step1_create_container(
        token, video_path, caption, share_to_feed
    )
    _publish_step2_poll_until_finished(token, container_id)
    result = _publish_step3_finalize(token, container_id, ig_user_id)
    result.setdefault("filename", video_path.name)
    result.setdefault("share_to_feed", share_to_feed)
    return result


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Publie une video comme Reel sur Instagram via Content Studio Pro."
    )
    parser.add_argument("video", help="Chemin du fichier video (MP4)")
    parser.add_argument(
        "--caption", default="", help="Caption du Reel (peut etre vide)"
    )
    parser.add_argument(
        "--no-share-to-feed",
        action="store_true",
        help="Si fourni, le Reel n'apparait que dans l'onglet Reels (pas le feed principal)",
    )
    parser.add_argument(
        "--token", default=None, help="Access token Instagram (alternative a INSTAGRAM_ACCESS_TOKEN)"
    )
    args = parser.parse_args()

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        print(f"ERREUR : fichier introuvable : {video_path}", file=sys.stderr)
        sys.exit(1)

    print("1. Recuperation du token d'acces Instagram")
    token = get_access_token(args.token)

    print("\n2. Verification du compte Instagram connecte")
    try:
        info = query_user_info(token)
        nick = info.get("username", "?")
        kind = info.get("account_type", "?")
        ig_id = info.get("id", "?")
        print(f"  Compte connecte : @{nick}  (type: {kind}, id: {ig_id})")
        if kind not in ("BUSINESS", "MEDIA_CREATOR"):
            print(
                f"  ATTENTION : le compte n'est pas Business/Creator (type={kind}). "
                "L'API Instagram va probablement rejeter la publication.",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"  Impossible de querier user-info (on continue quand meme) : {e}")

    share_to_feed = not args.no_share_to_feed
    print(f"\n3. Publication du Reel (share_to_feed = {share_to_feed})")
    print("   Le backend va heberger la video, creer un container Instagram,")
    print("   attendre que Instagram processe le media (peut prendre 1-3 min),")
    print("   puis publier le Reel.")
    print()

    result = publish_reel(token, video_path, args.caption, share_to_feed)

    print("\nPUBLIE :")
    print(f"  media_id     : {result.get('media_id')}")
    print(f"  container_id : {result.get('container_id')}")
    print(f"  filename     : {result.get('filename')}")
    print(f"  share_to_feed: {result.get('share_to_feed')}")
    print()
    print("  -> Ouvre l'app Instagram sur @{} pour voir ta Reel.".format(
        info.get("username", "ton_compte")
    ))


if __name__ == "__main__":
    main()
