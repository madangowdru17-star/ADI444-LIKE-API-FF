# MINISTER LIKE API SRC UID PASSWORD 
# POWERED BY : @minister_69
# CHANNEL : @minister_6T9
from flask import Flask, request, jsonify
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson
import binascii
import aiohttp
import requests
import json
import like_pb2
import like_count_pb2
import uid_generator_pb2
import time
from collections import defaultdict
from datetime import datetime
import random
import os
import urllib.parse
import jwt
from datetime import timedelta

TOKEN_CACHE = {}
app = Flask(__name__)

KEY_LIMIT = 500
tracker = defaultdict(lambda: [0, time.time()])
liked_cache = defaultdict(set)

def get_today_midnight_timestamp():
    now = datetime.now()
    midnight = datetime(now.year, now.month, now.day)
    return midnight.timestamp()

def load_accounts(server_name):
    try:
        if server_name == "IND":
            filename = "account_ind.txt"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            filename = "account_br.txt"
        elif server_name == "MENA":
            filename = "account_mena.txt"
        else:
            filename = "account_bd.txt"
        
        if not os.path.exists(filename):
            print(f"⚠️ {filename} not found, trying account_ind.txt")
            filename = "account_ind.txt"
            if not os.path.exists(filename):
                print(f"❌ No account file found")
                return []
        
        accounts = []
        print(f"📂 Loading from: {filename} for server {server_name}")
        
        with open(filename, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                if ':' in line:
                    parts = line.split(':', 1)
                    uid = parts[0].strip()
                    password = parts[1].strip()
                    
                    if uid and password:
                        accounts.append({
                            "uid": uid,
                            "password": password
                        })
        
        print(f"✅ Total {len(accounts)} accounts loaded for {server_name}")
        return accounts
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return []

async def generate_jwt_token(uid, password):
    try:
        encoded_password = urllib.parse.quote(password)
        url = f"https://ff-jwt-gen-api.lovable.app/api/public/token?uid={uid}&password={encoded_password}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=24) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if isinstance(data, dict):
                        if 'jwt_token' in data:
                            return data['jwt_token']
                        elif 'token' in data:
                            return data['token']
                return None
    except Exception as e:
        print(f"JWT error: {e}")
        return None

async def get_valid_token(uid, password):
    if uid in TOKEN_CACHE:
        cached = TOKEN_CACHE[uid]
        remaining = (cached["expires_at"] - datetime.utcnow()).total_seconds()
        if remaining > 1800:
            return cached["token"]

    token = await generate_jwt_token(uid, password)
    if not token:
        return None

    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        TOKEN_CACHE[uid] = {
            "token": token,
            "expires_at": datetime.utcfromtimestamp(exp)
        }
    except:
        TOKEN_CACHE[uid] = {
            "token": token,
            "expires_at": datetime.utcnow() + timedelta(hours=24)
        }

    return token

def encrypt_message(plaintext):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded_message = pad(plaintext, AES.block_size)
    return binascii.hexlify(cipher.encrypt(padded_message)).decode('utf-8')

def create_protobuf_message(user_id, region):
    message = like_pb2.like()
    message.uid = int(user_id)
    message.region = region
    return message.SerializeToString()

async def send_like(encrypted_uid, token, url):
    try:
        edata = bytes.fromhex(encrypted_uid)
        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
            'Authorization': f"Bearer {token}",
            'Content-Type': "application/x-www-form-urlencoded",
            'X-GA': "v1 1",
            'ReleaseVersion': "OB54"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=headers, timeout=8) as response:
                return response.status
    except Exception as e:
        print(f"Send like error: {e}")
        return 500

async def send_likes_sequential(target_uid, server_name, url, limit):
    """Send likes one by one until limit reached"""
    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0, 'limit_requested': limit}
    
    random.shuffle(accounts)
    success_count = 0
    failed_count = 0
    
    protobuf_message = create_protobuf_message(target_uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)
    
    for acc in accounts:
        if success_count >= limit:
            break
        
        token = await get_valid_token(acc['uid'], acc['password'])
        if not token:
            failed_count += 1
            continue
        
        status = await send_like(encrypted_uid, token, url)
        if status == 200:
            success_count += 1
            liked_cache[target_uid].add(acc['uid'])
            print(f"✅ Like {success_count}/{limit} from {acc['uid']}")
        else:
            failed_count += 1
        
        await asyncio.sleep(0.3)
    
    return {
        'success': success_count,
        'failed': failed_count,
        'total': len(accounts),
        'limit_requested': limit
    }

async def send_all_likes(target_uid, server_name, url):
    region = server_name
    protobuf_message = create_protobuf_message(target_uid, region)
    encrypted_uid = encrypt_message(protobuf_message)
    
    accounts = load_accounts(server_name)
    if not accounts: 
        return {'success': 0, 'failed': 0, 'total': 0, 'already_liked': 0}
    
    already_liked = liked_cache.get(target_uid, set())
    fresh_accounts = [acc for acc in accounts if acc['uid'] not in already_liked]
    
    print(f"📊 Total accounts: {len(accounts)}")
    print(f"✅ Fresh accounts: {len(fresh_accounts)}")
    
    if not fresh_accounts:
        return {
            'success': 0, 
            'failed': 0, 
            'total': len(accounts),
            'already_liked': len(already_liked),
            'fresh_used': 0
        }
    
    random.shuffle(fresh_accounts)
    
    semaphore = asyncio.Semaphore(15)
    tasks = []
    for acc in fresh_accounts[:1000]:
        tasks.append(process_account(target_uid, encrypted_uid, acc, url, semaphore, server_name))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    successful = 0
    failed = 0
    for r in results:
        if isinstance(r, tuple):
            status, uid = r
            if status == 200:
                successful += 1
            else:
                failed += 1
    
    return {
        'success': successful,
        'failed': failed,
        'total': len(accounts),
        'already_liked': len(already_liked),
        'fresh_used': len(fresh_accounts[:1000])
    }

async def process_account(target_uid, encrypted_uid, account, url, semaphore, server_name):
    async with semaphore:
        token = await get_valid_token(account['uid'], account['password'])
        if not token:
            return 500, account['uid']
        
        status = await send_like(encrypted_uid, token, url)
        if status == 200:
            liked_cache[target_uid].add(account['uid'])
            return status, account['uid']
        
        return status, account['uid']

def enc(uid):
    message = uid_generator_pb2.uid_generator()
    message.krishna_ = int(uid)
    message.teamXdarks = 1
    return encrypt_message(message.SerializeToString())

def decode_protobuf(binary):
    try:
        items = like_count_pb2.Info()
        items.ParseFromString(binary)
        return items
    except:
        return None

def get_player_info(encrypted_uid, server_name, token):
    if server_name == "IND":
        url = "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        url = "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
    elif server_name == "MENA":
        url = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"
    else:
        url = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"

    edata = bytes.fromhex(encrypted_uid)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB54"
    }

    try:
        response = requests.post(url, data=edata, headers=headers, verify=False, timeout=10)
        return decode_protobuf(response.content)
    except Exception as e:
        print(f"Get player info error: {e}")
        return None

@app.route('/like', methods=['GET'])
def handle_requests():
    uid = request.args.get("uid")
    server_name = request.args.get("server_name", "").upper()
    key = request.args.get("key")
    client_ip = request.remote_addr
    
    likes_param = request.args.get("likes")
    requested_likes = int(likes_param) if likes_param and likes_param.isdigit() else None

    if key != "JMLB":
        return jsonify({"error": "Invalid or missing API key 🔑"}), 403

    if not uid or not server_name:
        return jsonify({"error": "UID and server_name are required"}), 400

    valid_servers = ["IND", "BR", "US", "SAC", "NA", "BD", "RU", "MENA"]
    if server_name not in valid_servers:
        return jsonify({"error": f"Invalid server. Use: {valid_servers}"}), 400

    accounts = load_accounts(server_name)
    if not accounts:
        accounts = load_accounts("IND")
        if not accounts:
            return jsonify({"error": f"No accounts found for server {server_name}"}), 500
        print(f"⚠️ Using IND accounts as fallback for {server_name}")
    
    today_midnight = get_today_midnight_timestamp()
    count, last_reset = tracker[client_ip]

    if last_reset < today_midnight:
        tracker[client_ip] = [0, time.time()]
        count = 0

    if count >= KEY_LIMIT:
        return jsonify({"error": "Daily limit reached", "remains": f"(0/{KEY_LIMIT})"}), 429

    check_token = None
    for account in accounts[:5]:
        check_token = asyncio.run(get_valid_token(account['uid'], account['password']))
        if check_token:
            print(f"✅ Token generated with UID: {account['uid']}")
            break
    
    if not check_token:
        return jsonify({"error": "Token generation failed - no valid accounts"}), 500
    
    encrypted_uid = enc(uid)

    before = get_player_info(encrypted_uid, server_name, check_token)
    if before is None:
        return jsonify({"error": "Invalid UID or server", "status": 0}), 200

    try:
        before_data = json.loads(MessageToJson(before))
        before_like = int(before_data['AccountInfo'].get('Likes', 0))
    except Exception as e:
        return jsonify({"error": f"Data parsing failed: {e}", "status": 0}), 200

    if server_name == "IND":
        like_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        like_url = "https://client.us.freefiremobile.com/LikeProfile"
    elif server_name == "MENA":
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"
    else:
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"

    if requested_likes and requested_likes > 0:
        result = asyncio.run(send_likes_sequential(uid, server_name, like_url, requested_likes))
        success_count = result['success']
        total_sent_info = result
    else:
        result = asyncio.run(send_all_likes(uid, server_name, like_url))
        success_count = result['success']
        total_sent_info = result

    after = get_player_info(encrypted_uid, server_name, check_token)
    if after is None:
        return jsonify({"error": "Could not verify likes after command", "status": 0}), 200

    try:
        after_data = json.loads(MessageToJson(after))
        after_like = int(after_data['AccountInfo']['Likes'])
        player_id = int(after_data['AccountInfo']['UID'])
        player_name = str(after_data['AccountInfo']['PlayerNickname'])
        
        like_given = after_like - before_like
        status = 1 if success_count > 0 else 2
        
        if success_count > 0:
            tracker[client_ip][0] += 1
            count += 1
        
        remains = KEY_LIMIT - count

        return jsonify({
            "LikesGivenByAPI": success_count,
            "VerifiedLikesAdded": like_given,
            "LikesafterCommand": after_like,
            "LikesbeforeCommand": before_like,
            "PlayerNickname": player_name,
            "UID": player_id,
            "status": status,
            "remains": f"({remains}/{KEY_LIMIT})",
            "total_accounts": len(accounts),
            "limit_requested": requested_likes if requested_likes else "all"
        })
    except Exception as e:
        return jsonify({"error": str(e), "status": 0}), 500

@app.route('/reset-cache', methods=['GET'])
def reset_cache():
    key = request.args.get("key")
    if key != "JMLB":
        return jsonify({"error": "Invalid key"}), 403
    
    global liked_cache
    liked_cache.clear()
    return jsonify({"message": "Cache cleared", "credit": "@minister_69"})

@app.route('/health', methods=['GET'])
def health_check():
    accounts = load_accounts("IND")
    return jsonify({
        "status": "healthy",
        "accounts_loaded": len(accounts),
        "token_cache": len(TOKEN_CACHE),
        "server": "Railway"
    })

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "message": "✅ API is running!",
        "endpoints": {
            "/like": "Send likes to a UID",
            "/health": "Check API health",
            "/reset-cache": "Reset liked cache"
        },
        "usage": "/like?uid=TARGET_UID&server_name=IND&key=JMLB&likes=10",
        "credit": "@minister_69"
    })

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    print("🚀 Server started on Railway!")
    print("📁 Account files loaded")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)