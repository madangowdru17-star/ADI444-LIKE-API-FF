# MINISTER LIKE API SRC UID PASSWORD 
# POWERED BY : @minister_69
# CHANNEL : @minister_6T9
from flask import Flask, request, jsonify, render_template_string
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
liked_cache = defaultdict(set)
like_timestamps = {}

ACCOUNT_STATUS_FILE = "account_status.pkl"
account_status = {}

RESET_HOUR = 3
RESET_MINUTE = 0
RESET_SECOND = 0

RATE_LIMIT_DELAYS = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]

# TARGET UIDS TO AUTO-LIKE - EDIT THESE!
AUTO_LIKE_TARGETS = [
    "3997461446",
    "4675544032",
    # Add more target UIDs here
]

def load_account_status():
    global account_status
    try:
        if os.path.exists(ACCOUNT_STATUS_FILE):
            with open(ACCOUNT_STATUS_FILE, 'rb') as f:
                account_status = pickle.load(f)
                print(f"✅ Loaded account status: {len(account_status)} accounts")
    except Exception as e:
        print(f"❌ Error loading account status: {e}")
        account_status = {}

def save_account_status():
    try:
        with open(ACCOUNT_STATUS_FILE, 'wb') as f:
            pickle.dump(account_status, f)
    except Exception as e:
        print(f"❌ Error saving account status: {e}")

def load_liked_data():
    global liked_cache, like_timestamps
    try:
        if os.path.exists(LIKED_DATA_FILE):
            with open(LIKED_DATA_FILE, 'rb') as f:
                data = pickle.load(f)
                liked_cache = data.get('liked_cache', defaultdict(set))
                like_timestamps = data.get('like_timestamps', {})
                print(f"✅ Loaded liked data: {len(liked_cache)} entries")
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
    key = f"{account_uid}:{target_uid}"
    if key in like_timestamps:
        last_liked = datetime.fromtimestamp(like_timestamps[key])
        if datetime.now() - last_liked < timedelta(hours=24):
            return True
    return False

def mark_as_liked(target_uid, account_uid):
    key = f"{account_uid}:{target_uid}"
    like_timestamps[key] = datetime.now().timestamp()
    liked_cache[target_uid].add(account_uid)
    save_liked_data()

def log_error(account_uid, error_type, details):
    if account_uid not in account_status:
        account_status[account_uid] = {}
    account_status[account_uid]['last_error'] = error_type
    account_status[account_uid]['last_error_time'] = datetime.now().isoformat()
    account_status[account_uid]['details'] = details
    save_account_status()

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
            reset_all_data()
        except Exception as e:
            print(f"❌ Reset task error: {e}")
            time.sleep(60)

def reset_all_data():
    global liked_cache, like_timestamps, account_status
    liked_cache.clear()
    like_timestamps.clear()
    for uid in account_status:
        account_status[uid]['status'] = 'reset'
        account_status[uid]['reset_time'] = datetime.now().isoformat()
    save_liked_data()
    save_account_status()
    print(f"✅ Reset complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")

load_liked_data()
load_account_status()
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
        else:
            filename = "account_bd.txt"
        
        if not os.path.exists(filename):
            print(f"⚠️ {filename} not found")
            return []
        
        accounts = []
        print(f"📂 Loading from: {filename}")
        
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
        
        print(f"✅ Total {len(accounts)} accounts loaded")
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
    except Exception as e:
        print(f"JWT Error for {uid}: {e}")
        return None

async def get_valid_token(uid, password):
    if uid in TOKEN_CACHE:
        cached = TOKEN_CACHE[uid]
        remaining = (cached["expires_at"] - datetime.utcnow()).total_seconds()
        if remaining > 1800:
            return cached["token"]

    token = await generate_jwt_token(uid, password)
    if not token:
        log_error(uid, "TOKEN_GENERATION_FAILED", "Could not generate JWT token")
        return None

    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        TOKEN_CACHE[uid] = {
            "token": token,
            "expires_at": datetime.utcfromtimestamp(exp)
        }
    except Exception as e:
        log_error(uid, "TOKEN_DECODE_ERROR", str(e))
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

async def send_like_with_retry(encrypted_uid, token, url, account_uid, max_retries=3):
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
                    response_text = await response.text()
                    
                    if response.status == 200:
                        if account_uid in account_status:
                            account_status[account_uid]['status'] = 'working'
                            account_status[account_uid]['last_check'] = datetime.now().isoformat()
                            save_account_status()
                        return True, None
                    elif "LIMIT" in response_text:
                        log_error(account_uid, "DAILY_LIMIT", response_text)
                        if account_uid in account_status:
                            account_status[account_uid]['status'] = 'timeout'
                            account_status[account_uid]['reset_time'] = get_next_reset_time().isoformat()
                            save_account_status()
                        return False, "limit_reached"
                    elif response.status == 429:
                        log_error(account_uid, "RATE_LIMITED", f"Attempt {attempt+1}")
                        await asyncio.sleep(random.choice(RATE_LIMIT_DELAYS) * 2)
                        continue
                    else:
                        log_error(account_uid, f"HTTP_{response.status}", response_text[:100])
                        await asyncio.sleep(random.choice(RATE_LIMIT_DELAYS))
                        continue
        except Exception as e:
            log_error(account_uid, "CONNECTION_ERROR", str(e))
            continue
    
    return False, "max_retries_exceeded"

async def check_account(account, target_uid="3997461446"):
    """Check if account can like"""
    token = await get_valid_token(account['uid'], account['password'])
    if not token:
        return False, "no_token"
    
    protobuf_message = create_protobuf_message(target_uid, "IND")
    encrypted_uid = encrypt_message(protobuf_message)
    url = "https://client.ind.freefiremobile.com/LikeProfile"
    
    return await send_like_with_retry(encrypted_uid, token, url, account['uid'])

async def send_likes_ultra_fast(target_uid, server_name, url, limit):
    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0, 'limit_requested': limit}
    
    fresh_accounts = []
    skipped_24hr = 0
    
    for acc in accounts:
        if is_uid_liked_in_24hrs(target_uid, acc['uid']):
            skipped_24hr += 1
        else:
            fresh_accounts.append(acc)
    
    if not fresh_accounts:
        return {'success': 0, 'failed': 0, 'total': len(accounts), 'limit_requested': limit, 'skipped_24hr': skipped_24hr}
    
    random.shuffle(fresh_accounts)
    accounts_to_use = fresh_accounts[:min(limit, len(fresh_accounts))]
    
    protobuf_message = create_protobuf_message(target_uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)
    
    semaphore = asyncio.Semaphore(50)
    tasks = []
    for acc in accounts_to_use:
        tasks.append(send_single_like(target_uid, encrypted_uid, acc, url, semaphore, server_name))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    successful = 0
    failed = 0
    
    for r in results:
        if isinstance(r, dict):
            if r.get('status') == 'success':
                successful += 1
                mark_as_liked(target_uid, r['uid'])
            else:
                failed += 1
        else:
            failed += 1
    
    return {
        'success': successful,
        'failed': failed,
        'total': len(accounts),
        'limit_requested': limit,
        'skipped_24hr': skipped_24hr,
        'accounts_used': len(accounts_to_use)
    }

async def send_single_like(target_uid, encrypted_uid, account, url, semaphore, server_name):
    async with semaphore:
        try:
            await asyncio.sleep(random.uniform(0.01, 0.05))
            
            token = await get_valid_token(account['uid'], account['password'])
            if not token:
                return {'status': 'failed', 'uid': account['uid']}
            
            success, error = await send_like_with_retry(encrypted_uid, token, url, account['uid'])
            
            if success:
                return {'status': 'success', 'uid': account['uid']}
            else:
                return {'status': 'failed', 'uid': account['uid']}
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

# HTML Dashboard
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>🤖 Auto-Like Dashboard</title>
    <meta http-equiv="refresh" content="10">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #0a0e1a; color: #fff; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 25px; border-radius: 15px; margin-bottom: 25px; text-align: center; }
        .header h1 { font-size: 2.5em; }
        .header p { opacity: 0.9; margin-top: 5px; }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 25px; }
        .status-card { background: #141928; padding: 20px; border-radius: 12px; text-align: center; border: 1px solid #1e2a4a; }
        .status-card .number { font-size: 2.5em; font-weight: bold; }
        .status-card .label { color: #8899bb; font-size: 0.9em; margin-top: 5px; }
        .green { color: #00ff88; }
        .red { color: #ff6b6b; }
        .yellow { color: #ffd93d; }
        .blue { color: #4fc3f7; }
        .purple { color: #b388ff; }
        table { width: 100%; border-collapse: collapse; background: #141928; border-radius: 12px; overflow: hidden; margin-top: 20px; }
        th { background: #1e2a4a; padding: 12px 15px; text-align: left; font-weight: 600; color: #8899bb; }
        td { padding: 12px 15px; border-bottom: 1px solid #1a2240; }
        .badge { padding: 4px 12px; border-radius: 20px; font-size: 0.8em; font-weight: bold; display: inline-block; }
        .badge-working { background: #00ff8822; color: #00ff88; border: 1px solid #00ff88; }
        .badge-timeout { background: #ff6b6b22; color: #ff6b6b; border: 1px solid #ff6b6b; }
        .badge-reset { background: #ffd93d22; color: #ffd93d; border: 1px solid #ffd93d; }
        .badge-unknown { background: #8899bb22; color: #8899bb; border: 1px solid #8899bb; }
        .section-title { font-size: 1.5em; margin-top: 30px; margin-bottom: 15px; color: #fff; }
        .target-list { background: #141928; padding: 15px; border-radius: 12px; border: 1px solid #1e2a4a; }
        .target-item { padding: 8px 0; border-bottom: 1px solid #1a2240; }
        .target-item:last-child { border-bottom: none; }
        .auto-status { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 0.8em; }
        .auto-running { background: #00ff8822; color: #00ff88; border: 1px solid #00ff88; }
        .auto-stopped { background: #ff6b6b22; color: #ff6b6b; border: 1px solid #ff6b6b; }
        .refresh-btn { background: #667eea; color: #fff; border: none; padding: 10px 25px; border-radius: 8px; cursor: pointer; font-size: 1em; }
        .refresh-btn:hover { background: #764ba2; }
        .next-reset { color: #ffd93d; font-weight: bold; }
        .log-area { background: #0a0e1a; padding: 15px; border-radius: 12px; max-height: 200px; overflow-y: auto; font-family: monospace; font-size: 0.85em; border: 1px solid #1e2a4a; margin-top: 20px; }
        .log-entry { padding: 4px 0; border-bottom: 1px solid #141928; }
        .log-time { color: #667eea; }
        .log-success { color: #00ff88; }
        .log-error { color: #ff6b6b; }
        .log-info { color: #ffd93d; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Auto-Like Dashboard</h1>
            <p>Real-time monitoring & control | Resets daily at 3:00 AM IST</p>
            <div style="margin-top: 15px;">
                <button class="refresh-btn" onclick="location.reload()">🔄 Refresh</button>
                <span class="auto-status auto-running" style="margin-left: 15px;">▶ Auto-Like Running</span>
                <span style="margin-left: 15px;">Next Reset: <span class="next-reset">{{ next_reset }}</span></span>
            </div>
        </div>

        <div class="status-grid">
            <div class="status-card">
                <div class="number blue">{{ total_accounts }}</div>
                <div class="label">📊 Total Accounts</div>
            </div>
            <div class="status-card">
                <div class="number green">{{ working_count }}</div>
                <div class="label">✅ Working Now</div>
            </div>
            <div class="status-card">
                <div class="number red">{{ timeout_count }}</div>
                <div class="label">⏰ Limit Reached</div>
            </div>
            <div class="status-card">
                <div class="number purple">{{ total_likes }}</div>
                <div class="label">❤️ Total Likes Sent</div>
            </div>
            <div class="status-card">
                <div class="number yellow">{{ targets_liked }}</div>
                <div class="label">🎯 Targets Liked</div>
            </div>
        </div>

        <div class="section-title">🎯 Auto-Like Targets</div>
        <div class="target-list">
            {% for target in targets %}
            <div class="target-item">📱 UID: {{ target }}</div>
            {% endfor %}
        </div>

        <div class="section-title">📊 Account Status</div>
        <table>
            <thead>
                <tr>
                    <th>UID</th>
                    <th>Status</th>
                    <th>Last Check</th>
                    <th>Reset Time</th>
                    <th>Last Error</th>
                </tr>
            </thead>
            <tbody>
                {% for acc in accounts %}
                <tr>
                    <td><strong>{{ acc.uid }}</strong></td>
                    <td><span class="badge badge-{{ acc.status }}">{{ acc.status }}</span></td>
                    <td>{{ acc.last_check or 'Never' }}</td>
                    <td>{{ acc.reset_time or 'N/A' }}</td>
                    <td>{{ acc.last_error or 'None' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <div class="section-title">📝 Activity Log</div>
        <div class="log-area" id="log-area">
            <div class="log-entry"><span class="log-info">🔄 Auto-like system running...</span></div>
        </div>
    </div>
</body>
</html>
'''

# Auto-like background task
auto_like_enabled = True
auto_logs = []

def add_log(message, log_type="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    auto_logs.append({"time": timestamp, "message": message, "type": log_type})
    if len(auto_logs) > 50:
        auto_logs.pop(0)
    print(f"[{timestamp}] {message}")

async def auto_like_loop():
    """Background auto-like task"""
    add_log("🚀 Auto-like service started!", "info")
    
    while True:
        try:
            if not auto_like_enabled:
                await asyncio.sleep(10)
                continue
            
            accounts = load_accounts("IND")
            if not accounts:
                add_log("⚠️ No accounts found", "error")
                await asyncio.sleep(60)
                continue
            
            # Check all accounts status
            add_log(f"🔍 Checking {len(accounts)} accounts...", "info")
            
            for target in AUTO_LIKE_TARGETS:
                add_log(f"🎯 Processing target: {target}", "info")
                
                # Send likes
                result = await send_likes_ultra_fast(
                    target, 
                    "IND", 
                    "https://client.ind.freefiremobile.com/LikeProfile",
                    10  # Send 10 likes per target per cycle
                )
                
                if result['success'] > 0:
                    add_log(f"✅ Sent {result['success']} likes to {target}", "success")
                else:
                    if result.get('skipped_24hr', 0) > 0:
                        add_log(f"⏭️ {result['skipped_24hr']} accounts already liked {target} in 24hrs", "info")
                    else:
                        add_log(f"❌ No likes sent to {target}", "error")
                
                await asyncio.sleep(2)
            
            # Update account status
            for acc in accounts:
                if acc['uid'] not in account_status:
                    account_status[acc['uid']] = {'status': 'unknown'}
                    save_account_status()
            
            add_log(f"💤 Sleeping 5 minutes until next cycle...", "info")
            await asyncio.sleep(300)  # 5 minutes
            
        except Exception as e:
            add_log(f"❌ Auto-like error: {str(e)}", "error")
            await asyncio.sleep(60)

def start_auto_like():
    asyncio.run(auto_like_loop())

# Start background thread
auto_thread = threading.Thread(target=start_auto_like, daemon=True)
auto_thread.start()
add_log("🤖 Auto-like thread started!", "success")

@app.route('/')
def dashboard():
    accounts = load_accounts("IND")
    total = len(accounts)
    
    working_count = 0
    timeout_count = 0
    account_list = []
    
    for acc in accounts:
        uid = acc['uid']
        status_info = account_status.get(uid, {'status': 'unknown'})
        status = status_info.get('status', 'unknown')
        
        if status == 'working':
            working_count += 1
        elif status == 'timeout':
            timeout_count += 1
        
        account_list.append({
            'uid': uid,
            'status': status,
            'last_check': status_info.get('last_check', 'Never'),
            'reset_time': status_info.get('reset_time', 'N/A'),
            'last_error': status_info.get('last_error', 'None')
        })
    
    total_likes = sum(len(v) for v in liked_cache.values())
    targets_liked = len(liked_cache)
    next_reset = get_next_reset_time().strftime('%Y-%m-%d %H:%M:%S IST')
    
    return render_template_string(
        DASHBOARD_HTML,
        total_accounts=total,
        working_count=working_count,
        timeout_count=timeout_count,
        total_likes=total_likes,
        targets_liked=targets_liked,
        targets=AUTO_LIKE_TARGETS,
        accounts=account_list,
        next_reset=next_reset,
        logs=auto_logs[-20:]
    )

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

    valid_servers = ["IND", "BR", "US", "SAC", "NA", "BD", "RU"]
    if server_name not in valid_servers:
        return jsonify({"error": f"Invalid server. Use: {valid_servers}"}), 400

    accounts = load_accounts(server_name)
    if not accounts:
        return jsonify({"error": f"No accounts found for server {server_name}"}), 500
    
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
    else:
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"

    if requested_likes and requested_likes > 0:
        result = asyncio.run(send_likes_ultra_fast(uid, server_name, like_url, requested_likes))
        success_count = result['success']
    else:
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
    
    reset_all_data()
    return jsonify({"message": "All data reset - accounts can like again", "credit": "@minister_69"})

@app.route('/stats', methods=['GET'])
def get_stats():
    key = request.args.get("key")
    if key != "JMLB":
        return jsonify({"error": "Invalid key"}), 403
    
    total_likes = sum(len(v) for v in liked_cache.values())
    total_uids = len(liked_cache)
    next_reset = get_next_reset_time()
    
    working = sum(1 for v in account_status.values() if v.get('status') == 'working')
    timeout = sum(1 for v in account_status.values() if v.get('status') == 'timeout')
    
    return jsonify({
        "total_uids_liked": total_uids,
        "total_successful_likes": total_likes,
        "next_reset_at": next_reset.strftime('%Y-%m-%d %H:%M:%S IST'),
        "reset_time": "3:00 AM IST Daily",
        "working_accounts": working,
        "timeout_accounts": timeout,
        "total_accounts": len(account_status)
    })

@app.route('/health', methods=['GET'])
def health_check():
    accounts = load_accounts("IND")
    return jsonify({
        "status": "healthy",
        "accounts_loaded": len(accounts),
        "server": "Railway",
        "24hr_rule": "Active",
        "reset_time": "3:00 AM IST Daily",
        "auto_like": "Running",
        "targets": AUTO_LIKE_TARGETS
    })

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    print("🚀 Auto-Like Server started!")
    print(f"📁 Accounts: {len(load_accounts('IND'))}")
    print(f"🎯 Targets: {AUTO_LIKE_TARGETS}")
    print("⏰ Resets at 3:00 AM IST daily")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)