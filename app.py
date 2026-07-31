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
from datetime import datetime, timedelta
import random
import os
import urllib.parse
import jwt
from datetime import timedelta
import pickle
import threading

TOKEN_CACHE = {}
app = Flask(__name__)

KEY_LIMIT = 500
tracker = defaultdict(lambda: [0, time.time()])

LIKED_DATA_FILE = "liked_data.pkl"
liked_cache = defaultdict(set)  # target_uid -> set of account_uids that successfully liked
like_timestamps = {}  # key: "account_uid:target_uid" -> timestamp

RESET_HOUR = 3
RESET_MINUTE = 0
RESET_SECOND = 0

# Rate limiting bypass
RATE_LIMIT_DELAYS = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]

def load_liked_data():
    global liked_cache, like_timestamps
    try:
        if os.path.exists(LIKED_DATA_FILE):
            with open(LIKED_DATA_FILE, 'rb') as f:
                data = pickle.load(f)
                liked_cache = data.get('liked_cache', defaultdict(set))
                like_timestamps = data.get('like_timestamps', {})
                print(f"✅ Loaded liked data: {len(liked_cache)} entries")
                print(f"📊 Total successful likes: {sum(len(v) for v in liked_cache.values())}")
    except Exception as e:
        print(f"❌ Error loading liked data: {e}")
        liked_cache = defaultdict(set)
        like_timestamps = {}

def save_liked_data():
    try:
        data = {
            'liked_cache': liked_cache,
            'like_timestamps': like_timestamps
        }
        with open(LIKED_DATA_FILE, 'wb') as f:
            pickle.dump(data, f)
    except Exception as e:
        print(f"❌ Error saving liked data: {e}")

def is_uid_liked_in_24hrs(target_uid, account_uid):
    """Check if account successfully liked this UID in last 24 hours"""
    key = f"{account_uid}:{target_uid}"
    if key in like_timestamps:
        last_liked = datetime.fromtimestamp(like_timestamps[key])
        if datetime.now() - last_liked < timedelta(hours=24):
            return True
    return False

def mark_as_liked(target_uid, account_uid):
    """ONLY mark if successfully liked (status 200)"""
    key = f"{account_uid}:{target_uid}"
    like_timestamps[key] = datetime.now().timestamp()
    liked_cache[target_uid].add(account_uid)
    save_liked_data()

def get_next_reset_time():
    now = datetime.now()
    reset_time = datetime(now.year, now.month, now.day, RESET_HOUR, RESET_MINUTE, RESET_SECOND)
    if now >= reset_time:
        reset_time += timedelta(days=1)
    return reset_time

def daily_reset_task():
    while True:
        try:
            next_reset = get_next_reset_time()
            wait_seconds = (next_reset - datetime.now()).total_seconds()
            if wait_seconds > 0:
                print(f"⏰ Next reset at: {next_reset.strftime('%Y-%m-%d %H:%M:%S')} IST")
                time.sleep(wait_seconds)
            print(f"🔄 Performing daily reset at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
            reset_liked_data()
        except Exception as e:
            print(f"❌ Reset task error: {e}")
            time.sleep(60)

def reset_liked_data():
    global liked_cache, like_timestamps
    liked_cache.clear()
    like_timestamps.clear()
    save_liked_data()
    print(f"✅ Reset complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")

load_liked_data()
reset_thread = threading.Thread(target=daily_reset_task, daemon=True)
reset_thread.start()
print("🚀 Background reset task started")

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
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict):
                        if 'jwt_token' in data:
                            return data['jwt_token']
                        elif 'token' in data:
                            return data['token']
                return None
    except:
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

async def send_like_with_retry(encrypted_uid, token, url, max_retries=3):
    """Send like with retry and bypass rate limiting"""
    edata = bytes.fromhex(encrypted_uid)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB54"
    }
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=edata, headers=headers, timeout=5) as response:
                    if response.status == 200:
                        return True
                    elif response.status == 429:  # Rate limited - wait and retry
                        await asyncio.sleep(random.choice(RATE_LIMIT_DELAYS) * 2)
                        continue
                    elif response.status in [401, 403]:  # Token expired
                        return False
                    else:
                        # Other error - small delay then retry
                        await asyncio.sleep(random.choice(RATE_LIMIT_DELAYS))
                        continue
        except:
            await asyncio.sleep(random.choice(RATE_LIMIT_DELAYS))
            continue
    
    return False

async def send_likes_ultra_fast(target_uid, server_name, url, limit):
    """ULTRA FAST - Send likes concurrently with bypass"""
    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0, 'limit_requested': limit, 'skipped_24hr': 0, 'rate_limited': 0}
    
    # Filter accounts that already successfully liked in 24 hours
    fresh_accounts = []
    skipped_24hr = 0
    
    for acc in accounts:
        if is_uid_liked_in_24hrs(target_uid, acc['uid']):
            skipped_24hr += 1
        else:
            fresh_accounts.append(acc)
    
    print(f"📊 Total: {len(accounts)}, Fresh: {len(fresh_accounts)}, Skipped (24hr): {skipped_24hr}")
    
    if not fresh_accounts:
        return {
            'success': 0, 
            'failed': 0, 
            'total': len(accounts),
            'limit_requested': limit,
            'skipped_24hr': skipped_24hr,
            'rate_limited': 0
        }
    
    random.shuffle(fresh_accounts)
    
    # Use all fresh accounts up to limit
    accounts_to_use = fresh_accounts[:min(limit, len(fresh_accounts))]
    
    # Prepare encrypted message once
    protobuf_message = create_protobuf_message(target_uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)
    
    # HIGH CONCURRENCY for speed
    semaphore = asyncio.Semaphore(50)  # 50 concurrent!
    
    tasks = []
    for acc in accounts_to_use:
        tasks.append(send_like_ultra_fast(target_uid, encrypted_uid, acc, url, semaphore, server_name))
    
    # Run all tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    successful = 0
    failed = 0
    rate_limited = 0
    
    for r in results:
        if isinstance(r, dict):
            if r.get('status') == 'success':
                successful += 1
                # ONLY mark as liked if successful!
                mark_as_liked(target_uid, r['uid'])
            elif r.get('status') == 'rate_limited':
                rate_limited += 1
                failed += 1
            else:
                failed += 1
        else:
            failed += 1
    
    # If we hit rate limit, wait and retry failed ones
    if rate_limited > 0 and failed > 0:
        print(f"⚠️ Rate limited: {rate_limited}, retrying...")
        await asyncio.sleep(2)
        
        # Retry failed accounts
        retry_tasks = []
        for r in results:
            if isinstance(r, dict) and r.get('status') == 'rate_limited':
                # Re-add failed accounts for retry
                retry_tasks.append(send_like_ultra_fast(target_uid, encrypted_uid, r['account'], url, semaphore, server_name))
        
        if retry_tasks:
            retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
            for r in retry_results:
                if isinstance(r, dict) and r.get('status') == 'success':
                    successful += 1
                    mark_as_liked(target_uid, r['uid'])
                else:
                    failed += 1
    
    return {
        'success': successful,
        'failed': failed,
        'total': len(accounts),
        'limit_requested': limit,
        'skipped_24hr': skipped_24hr,
        'rate_limited': rate_limited,
        'accounts_used': len(accounts_to_use)
    }

async def send_like_ultra_fast(target_uid, encrypted_uid, account, url, semaphore, server_name):
    """Ultra fast individual like sender with bypass"""
    async with semaphore:
        try:
            # Random small delay to bypass rate limiting
            await asyncio.sleep(random.uniform(0.01, 0.05))
            
            token = await get_valid_token(account['uid'], account['password'])
            if not token:
                return {'status': 'failed', 'uid': account['uid']}
            
            success = await send_like_with_retry(encrypted_uid, token, url)
            
            if success:
                return {'status': 'success', 'uid': account['uid']}
            else:
                # Check if it was rate limiting
                return {'status': 'rate_limited', 'uid': account['uid'], 'account': account}
        except Exception as e:
            return {'status': 'failed', 'uid': account['uid']}

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
    except:
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
    except:
        return jsonify({"error": "Data parsing failed", "status": 0}), 200

    if server_name == "IND":
        like_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        like_url = "https://client.us.freefiremobile.com/LikeProfile"
    elif server_name == "MENA":
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"
    else:
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"

    # Use ULTRA FAST mode
    if requested_likes and requested_likes > 0:
        result = asyncio.run(send_likes_ultra_fast(uid, server_name, like_url, requested_likes))
        success_count = result['success']
    else:
        # Default to 50 if no limit specified
        result = asyncio.run(send_likes_ultra_fast(uid, server_name, like_url, 50))
        success_count = result['success']

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
        next_reset = get_next_reset_time()

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
            "limit_requested": requested_likes if requested_likes else "50 (default)",
            "skipped_24hr_successful": result.get('skipped_24hr', 0),
            "rate_limited_retried": result.get('rate_limited', 0),
            "accounts_used": result.get('accounts_used', 0),
            "failed": result.get('failed', 0),
            "next_reset_at": next_reset.strftime('%Y-%m-%d %H:%M:%S IST'),
            "rule_24hr": "✅ Only successful likes count towards 24hr rule"
        })
    except Exception as e:
        return jsonify({"error": str(e), "status": 0}), 500

@app.route('/reset-cache', methods=['GET'])
def reset_cache():
    key = request.args.get("key")
    if key != "JMLB":
        return jsonify({"error": "Invalid key"}), 403
    
    global liked_cache, like_timestamps
    liked_cache.clear()
    like_timestamps.clear()
    save_liked_data()
    return jsonify({"message": "Cache cleared - all accounts can like again", "credit": "@minister_69"})

@app.route('/stats', methods=['GET'])
def get_stats():
    key = request.args.get("key")
    if key != "JMLB":
        return jsonify({"error": "Invalid key"}), 403
    
    total_likes = sum(len(v) for v in liked_cache.values())
    total_uids = len(liked_cache)
    next_reset = get_next_reset_time()
    
    return jsonify({
        "total_uids_liked": total_uids,
        "total_successful_likes": total_likes,
        "next_reset_at": next_reset.strftime('%Y-%m-%d %H:%M:%S IST'),
        "reset_time": "3:00 AM IST Daily",
        "rule": "Only successful likes count for 24hr rule"
    })

@app.route('/health', methods=['GET'])
def health_check():
    accounts = load_accounts("IND")
    return jsonify({
        "status": "healthy",
        "accounts_loaded": len(accounts),
        "token_cache": len(TOKEN_CACHE),
        "server": "Railway",
        "24hr_rule": "Active (successful likes only)",
        "reset_time": "3:00 AM IST Daily"
    })

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "message": "✅ SUPER FAST API is running!",
        "endpoints": {
            "/like": "Send likes (ULTRA FAST - 50 concurrent)",
            "/health": "Check API health",
            "/reset-cache": "Reset liked cache",
            "/stats": "View statistics"
        },
        "usage": "/like?uid=TARGET_UID&server_name=IND&key=JMLB&likes=10",
        "24hr_rule": "✅ ONLY SUCCESSFUL LIKES count for 24hr rule",
        "reset_time": "3:00 AM IST Daily",
        "speed": "🚀 ULTRA FAST - 50 concurrent likes with bypass",
        "credit": "@minister_69"
    })

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    print("🚀 SUPER FAST Server started on Railway!")
    print("📁 Account files loaded")
    print("⏰ 24-hour rule: ONLY SUCCESSFUL LIKES count")
    print("⚡ ULTRA FAST: 50 concurrent likes with bypass!")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)