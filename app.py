#!/usr/bin/env python3
"""
Render-ready Flask API for Free Fire Likes & Player Info (Credit: Lucky)
Supports:
- GET /like?uid=<uid>&region=<region>&key=<api_key>
- GET /info?uid=<uid>&region=<region>&key=<api_key>
- GET /health
- Uses uid:pass accounts (from uid_pass.txt / accounts_ind.json) + JWT token API
"""

import asyncio
import binascii
import json
import os
import time
from flask import Flask, jsonify, request
import aiohttp
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

try:
    import like_pb2
    import uid_generator_pb2
    import visit_count_pb2
    PROTOBUF_OK = True
except Exception:
    PROTOBUF_OK = False

requests.packages.urllib3.disable_warnings()

app = Flask(__name__)

JWT_API_URL = os.environ.get("JWT_TOKEN_API_URL", "https://jwt-token-api-beta.vercel.app/token")
API_KEYS_ENV = os.environ.get("API_KEYS", "LUCKY_API_KEY,FREE_LIKE_KEY")
VALID_API_KEYS = {k.strip() for k in API_KEYS_ENV.split(",") if k.strip()}
UID_PASS_FILE = os.environ.get("UID_PASS_FILE", "uid_pass.txt")
ACCOUNTS_JSON_FILE = os.environ.get("ACCOUNTS_JSON_FILE", "accounts_ind.json")
TOKENS_CACHE_FILE = os.environ.get("TOKENS_CACHE_FILE", "token_ind.json")
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "14400"))  # 4 hours

_TOKEN_CACHE = {"ts": 0.0, "tokens": []}


def _check_api_key() -> bool:
    if not VALID_API_KEYS:
        return True
    key = (request.args.get("key") or request.args.get("api_key") or request.headers.get("X-API-Key") or "").strip()
    return key in VALID_API_KEYS


def load_uid_pass_accounts() -> list[dict]:
    accounts = []
    seen = set()

    if os.path.exists(UID_PASS_FILE):
        try:
            with open(UID_PASS_FILE, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or ":" not in line:
                        continue
                    uid, pwd = line.split(":", 1)
                    uid, pwd = uid.strip(), pwd.strip()
                    if uid and pwd and uid not in seen:
                        seen.add(uid)
                        accounts.append({"uid": uid, "password": pwd})
        except Exception:
            pass

    if os.path.exists(ACCOUNTS_JSON_FILE):
        try:
            with open(ACCOUNTS_JSON_FILE, "r", encoding="utf-8") as f:
                items = json.load(f)
            if isinstance(items, list):
                for it in items:
                    uid = str(it.get("uid", "")).strip()
                    pwd = str(it.get("password", "")).strip()
                    if uid and pwd and uid not in seen:
                        seen.add(uid)
                        accounts.append({"uid": uid, "password": pwd})
        except Exception:
            pass

    return accounts


def refresh_tokens(force: bool = False) -> list[str]:
    now = time.time()
    if not force and _TOKEN_CACHE["tokens"] and (now - _TOKEN_CACHE["ts"] < TOKEN_TTL_SECONDS):
        return _TOKEN_CACHE["tokens"]

    if not force and os.path.exists(TOKENS_CACHE_FILE):
        try:
            mtime = os.path.getmtime(TOKENS_CACHE_FILE)
            if now - mtime < TOKEN_TTL_SECONDS:
                with open(TOKENS_CACHE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                toks = [x["token"] if isinstance(x, dict) else str(x) for x in raw if (isinstance(x, dict) and x.get("token")) or isinstance(x, str)]
                if toks:
                    _TOKEN_CACHE["ts"] = mtime
                    _TOKEN_CACHE["tokens"] = toks
                    return toks
        except Exception:
            pass

    accounts = load_uid_pass_accounts()
    tokens = []
    for acc in accounts:
        try:
            resp = requests.get(JWT_API_URL, params={"uid": acc["uid"], "password": acc["password"]}, timeout=10)
            if resp.status_code == 200:
                j = resp.json()
                tok = j.get("token") or j.get("access_token") or j.get("data")
                if tok:
                    tokens.append(str(tok))
        except Exception:
            continue

    if tokens:
        _TOKEN_CACHE["ts"] = now
        _TOKEN_CACHE["tokens"] = tokens
        try:
            with open(TOKENS_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump([{"token": t} for t in tokens], f, indent=2)
        except Exception:
            pass

    return tokens


def encrypt_message(plaintext: bytes) -> str | None:
    try:
        key = b"Yg&tc%DEuh6%Zc^8"
        iv = b"6oyZDr22E3ychjM%"
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(plaintext, AES.block_size))
        return binascii.hexlify(encrypted).decode("utf-8")
    except Exception:
        return None


def create_uid_protobuf(uid: str) -> bytes | None:
    if not PROTOBUF_OK:
        return None
    try:
        msg = uid_generator_pb2.uid_generator()
        msg.saturn_ = int(uid)
        msg.garena = 1
        return msg.SerializeToString()
    except Exception:
        return None


def create_like_protobuf(uid: str, region: str) -> bytes | None:
    if not PROTOBUF_OK:
        return None
    try:
        msg = like_pb2.like()
        msg.uid = int(uid)
        msg.region = region
        return msg.SerializeToString()
    except Exception:
        return None


def _base_url(region: str) -> str:
    r = region.upper()
    if r == "IND":
        return "https://client.ind.freefiremobile.com"
    if r in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com"
    return "https://clientbp.ggpolarbear.com"


def fetch_info(uid: str, region: str, token: str):
    pb = create_uid_protobuf(uid)
    if not pb:
        return None
    enc = encrypt_message(pb)
    if not enc:
        return None
    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "X-Unity-Version": "2022.3.47f1",
        "X-GA": "v1 1",
        "ReleaseVersion": "OB55",
    }
    resp = requests.post(f"{_base_url(region)}/GetPlayerPersonalShow", data=bytes.fromhex(enc), headers=headers, verify=False, timeout=12)
    decoded = visit_count_pb2.Info()
    decoded.ParseFromString(resp.content)
    return decoded


async def _send_likes_async(uid: str, region: str, tokens: list[str], limit: int | None = None):
    pb = create_like_protobuf(uid, region)
    if not pb:
        return 0
    enc = encrypt_message(pb)
    if not enc:
        return 0
    edata = bytes.fromhex(enc)
    url = f"{_base_url(region)}/LikeProfile"
    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "X-Unity-Version": "2022.3.47f1",
        "X-GA": "v1 1",
        "ReleaseVersion": "OB55",
    }
    use_tokens = tokens[:limit] if limit else tokens

    async def _one(sess, tok):
        h = dict(headers)
        h["Authorization"] = f"Bearer {tok}"
        try:
            async with sess.post(url, data=edata, headers=h, timeout=10) as r:
                return r.status
        except Exception:
            return None

    async with aiohttp.ClientSession() as session:
        res = await asyncio.gather(*[_one(session, t) for t in use_tokens], return_exceptions=True)
    return sum(1 for x in res if x == 200)


@app.get("/health")
def health():
    accs = load_uid_pass_accounts()
    toks = refresh_tokens(force=False)
    return jsonify({
        "status": "ok",
        "protobuf_loaded": PROTOBUF_OK,
        "uid_pass_accounts_count": len(accs),
        "cached_tokens_count": len(toks),
    })


@app.get("/info")
def api_info():
    if not _check_api_key():
        return jsonify({"status": "error", "message": "Invalid or missing API key"}), 401
    uid = (request.args.get("uid") or "").strip()
    region = (request.args.get("region") or "IND").strip().upper()
    if not uid.isdigit():
        return jsonify({"status": "error", "message": "Valid numeric uid is required"}), 400

    tokens = refresh_tokens(force=False)
    if not tokens:
        return jsonify({"status": "error", "message": "No tokens available"}), 503

    info = fetch_info(uid, region, tokens[0])
    if not info or not getattr(info, "AccountInfo", None):
        return jsonify({"status": "error", "message": "Failed to fetch player info"}), 502

    acc = info.AccountInfo
    return jsonify({
        "status": "success",
        "uid": uid,
        "region": region,
        "nickname": getattr(acc, "PlayerNickname", "Unknown"),
        "likes": int(getattr(acc, "Likes", 0)),
        "level": int(getattr(acc, "Level", 0)),
    })


@app.get("/like")
def api_like():
    if not _check_api_key():
        return jsonify({"status": "error", "message": "Invalid or missing API key"}), 401
    uid = (request.args.get("uid") or "").strip()
    region = (request.args.get("region") or "IND").strip().upper()
    limit_raw = request.args.get("limit")
    limit = int(limit_raw) if (limit_raw and limit_raw.isdigit()) else None

    if not uid.isdigit():
        return jsonify({"status": "error", "message": "Valid numeric uid is required"}), 400
    if not PROTOBUF_OK:
        return jsonify({"status": "error", "message": "Protobuf modules missing"}), 500

    tokens = refresh_tokens(force=False)
    if not tokens:
        return jsonify({"status": "error", "message": "No tokens available"}), 503

    before_info = fetch_info(uid, region, tokens[0])
    before_likes = int(getattr(before_info.AccountInfo, "Likes", 0)) if (before_info and getattr(before_info, "AccountInfo", None)) else 0
    nickname = getattr(before_info.AccountInfo, "PlayerNickname", "Unknown") if (before_info and getattr(before_info, "AccountInfo", None)) else "Unknown"

    sent_ok = asyncio.run(_send_likes_async(uid, region, tokens, limit=limit))
    time.sleep(1.0)

    after_info = fetch_info(uid, region, tokens[0])
    after_likes = int(getattr(after_info.AccountInfo, "Likes", before_likes)) if (after_info and getattr(after_info, "AccountInfo", None)) else before_likes
    likes_given = max(0, after_likes - before_likes)

    return jsonify({
        "status": "success",
        "uid": uid,
        "region": region,
        "nickname": nickname,
        "before_likes": before_likes,
        "after_likes": after_likes,
        "likes_given": likes_given,
        "requests_200": sent_ok,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
