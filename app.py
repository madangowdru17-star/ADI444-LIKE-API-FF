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

USERS_FILE = "users.pkl"
auto_like_users = []
user_stats = {}  # uid -> {'total_likes': 0, 'today_likes': 0, 'last_like': None, 'username': '', 'current_likes': 0}

RESET_HOUR = 4
RESET_MINUTE = 0
RESET_SECOND = 0

RATE_LIMIT_DELAYS = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]

def load_users():
    global auto_like_users, user_stats
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'rb') as f:
                data = pickle.load(f)
                if isinstance(data, dict):
                    auto_like_users = data.get('users', [])
                    user_stats = data.get('stats', {})
                else:
                    auto_like_users = data
                    user_stats = {}
                print(f"Loaded {len(auto_like_users)} users")
        else:
            auto_like_users = []
            user_stats = {}
            save_users()
    except Exception as e:
        print(f"Error loading users: {e}")
        auto_like_users = []
        user_stats = {}

def save_users():
    try:
        data = {
            'users': auto_like_users,
            'stats': user_stats
        }
        with open(USERS_FILE, 'wb') as f:
            pickle.dump(data, f)
    except Exception as e:
        print(f"Error saving users: {e}")

def load_account_status():
    global account_status
    try:
        if os.path.exists(ACCOUNT_STATUS_FILE):
            with open(ACCOUNT_STATUS_FILE, 'rb') as f:
                account_status = pickle.load(f)
                print(f"Loaded account status: {len(account_status)} accounts")
    except Exception as e:
        print(f"Error loading account status: {e}")
        account_status = {}

def save_account_status():
    try:
        with open(ACCOUNT_STATUS_FILE, 'wb') as f:
            pickle.dump(account_status, f)
    except Exception as e:
        print(f"Error saving account status: {e}")

def load_liked_data():
    global liked_cache, like_timestamps
    try:
        if os.path.exists(LIKED_DATA_FILE):
            with open(LIKED_DATA_FILE, 'rb') as f:
                data = pickle.load(f)
                liked_cache = data.get('liked_cache', defaultdict(set))
                like_timestamps = data.get('like_timestamps', {})
                print(f"Loaded liked data: {len(liked_cache)} entries")
    except Exception as e:
        print(f"Error loading liked data: {e}")
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
        print(f"Error saving liked data: {e}")

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

def update_user_stats(target_uid, likes_given, username="", current_likes=0):
    if target_uid not in user_stats:
        user_stats[target_uid] = {'total_likes': 0, 'today_likes': 0, 'last_like': None, 'username': '', 'current_likes': 0}
    user_stats[target_uid]['total_likes'] += likes_given
    user_stats[target_uid]['today_likes'] += likes_given
    user_stats[target_uid]['last_like'] = datetime.now().isoformat()
    if username:
        user_stats[target_uid]['username'] = username
    if current_likes > 0:
        user_stats[target_uid]['current_likes'] = current_likes
    save_users()

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
                print(f"Next reset at: {next_reset.strftime('%Y-%m-%d %H:%M:%S')} IST")
                time.sleep(wait_seconds)
            print(f"Performing daily reset at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
            reset_all_data()
        except Exception as e:
            print(f"Reset task error: {e}")
            time.sleep(60)

def reset_all_data():
    global liked_cache, like_timestamps, account_status, user_stats
    liked_cache.clear()
    like_timestamps.clear()
    for uid in account_status:
        account_status[uid]['status'] = 'reset'
        account_status[uid]['reset_time'] = datetime.now().isoformat()
    for uid in user_stats:
        user_stats[uid]['today_likes'] = 0
    save_liked_data()
    save_account_status()
    save_users()

def load_accounts(server_name):
    try:
        if server_name == "IND":
            filename = "account_ind.txt"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            filename = "account_br.txt"
        else:
            filename = "account_bd.txt"
        
        if not os.path.exists(filename):
            return []
        
        accounts = []
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
                        accounts.append({"uid": uid, "password": password})
        return accounts
    except:
        return []

async def get_user_info(target_uid, server_name="IND"):
    """Get user info like name, likes count"""
    try:
        accounts = load_accounts(server_name)
        if not accounts:
            return None
        
        check_token = None
        for account in accounts[:5]:
            check_token = await get_valid_token(account['uid'], account['password'])
            if check_token:
                break
        
        if not check_token:
            return None
        
        encrypted_uid = enc(target_uid)
        info = get_player_info(encrypted_uid, server_name, check_token)
        if info:
            try:
                data = json.loads(MessageToJson(info))
                return {
                    'uid': data['AccountInfo'].get('UID', target_uid),
                    'name': data['AccountInfo'].get('PlayerNickname', 'Unknown'),
                    'likes': int(data['AccountInfo'].get('Likes', 0))
                }
            except:
                return None
        return None
    except:
        return None

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
        TOKEN_CACHE[uid] = {"token": token, "expires_at": datetime.utcfromtimestamp(exp)}
    except:
        TOKEN_CACHE[uid] = {"token": token, "expires_at": datetime.utcnow() + timedelta(hours=24)}

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
                        if account_uid in account_status:
                            account_status[account_uid]['status'] = 'timeout'
                            account_status[account_uid]['reset_time'] = get_next_reset_time().isoformat()
                            save_account_status()
                        return False, "limit_reached"
                    elif response.status == 429:
                        await asyncio.sleep(random.choice(RATE_LIMIT_DELAYS) * 2)
                        continue
                    else:
                        await asyncio.sleep(random.choice(RATE_LIMIT_DELAYS))
                        continue
        except:
            continue
    return False, "max_retries"

async def send_single_like(target_uid, encrypted_uid, account, url, semaphore):
    async with semaphore:
        try:
            token = await get_valid_token(account['uid'], account['password'])
            if not token:
                return {'status': 'failed', 'uid': account['uid']}
            
            success, _ = await send_like_with_retry(encrypted_uid, token, url, account['uid'])
            
            if success:
                return {'status': 'success', 'uid': account['uid']}
            else:
                return {'status': 'failed', 'uid': account['uid']}
        except:
            return {'status': 'failed', 'uid': account['uid']}

async def send_likes_batch(target_uid, server_name, url, limit):
    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0}
    
    fresh_accounts = []
    skipped = 0
    
    for acc in accounts:
        if is_uid_liked_in_24hrs(target_uid, acc['uid']):
            skipped += 1
        else:
            fresh_accounts.append(acc)
    
    if not fresh_accounts:
        return {'success': 0, 'failed': 0, 'total': len(accounts), 'skipped': skipped}
    
    random.shuffle(fresh_accounts)
    accounts_to_use = fresh_accounts[:min(limit, len(fresh_accounts))]
    
    protobuf_message = create_protobuf_message(target_uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)
    
    semaphore = asyncio.Semaphore(30)
    tasks = []
    for acc in accounts_to_use:
        tasks.append(send_single_like(target_uid, encrypted_uid, acc, url, semaphore))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    successful = 0
    failed = 0
    
    for r in results:
        if isinstance(r, dict) and r.get('status') == 'success':
            successful += 1
            mark_as_liked(target_uid, r['uid'])
        else:
            failed += 1
    
    if successful > 0:
        # Get user info
        user_info = await get_user_info(target_uid, server_name)
        username = user_info.get('name', '') if user_info else ''
        current_likes = user_info.get('likes', 0) if user_info else 0
        update_user_stats(target_uid, successful, username, current_likes)
    
    return {
        'success': successful,
        'failed': failed,
        'total': len(accounts),
        'accounts_used': len(accounts_to_use),
        'skipped': skipped
    }

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

# Check account status in background
async def check_all_accounts_status():
    """Check all accounts status"""
    accounts = load_accounts("IND")
    for acc in accounts:
        try:
            token = await get_valid_token(acc['uid'], acc['password'])
            if token:
                # Test if account can like
                protobuf_message = create_protobuf_message("3997461446", "IND")
                encrypted_uid = encrypt_message(protobuf_message)
                url = "https://client.ind.freefiremobile.com/LikeProfile"
                success, _ = await send_like_with_retry(encrypted_uid, token, url, acc['uid'])
                
                if success:
                    account_status[acc['uid']] = {
                        'status': 'working',
                        'last_check': datetime.now().isoformat()
                    }
                else:
                    account_status[acc['uid']] = {
                        'status': 'timeout',
                        'last_check': datetime.now().isoformat(),
                        'reset_time': get_next_reset_time().isoformat()
                    }
            else:
                account_status[acc['uid']] = {
                    'status': 'unknown',
                    'last_check': datetime.now().isoformat()
                }
            save_account_status()
            await asyncio.sleep(0.5)
        except:
            continue

def run_status_check():
    """Run status check in thread"""
    asyncio.run(check_all_accounts_status())

# HTML WEBSITE
WEBSITE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auto-Like System</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0e1a; color: #fff; min-height: 100vh; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .header { background: linear-gradient(135deg, #1a237e, #283593); padding: 25px; border-radius: 15px; margin-bottom: 25px; }
        .header h1 { font-size: 2.2em; }
        .header .sub { opacity: 0.8; margin-top: 5px; }
        .header-top { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
        .badge-auto { background: #4caf5022; color: #4caf50; padding: 6px 18px; border-radius: 20px; border: 1px solid #4caf50; }
        .badge-reset { color: #ffc107; font-weight: bold; }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 25px; }
        .status-card { background: #141928; padding: 20px; border-radius: 12px; text-align: center; border: 1px solid #1e2a4a; }
        .status-card .num { font-size: 2.5em; font-weight: bold; }
        .status-card .lbl { color: #8899bb; font-size: 0.85em; margin-top: 5px; }
        .green { color: #4caf50; }
        .red { color: #f44336; }
        .yellow { color: #ffc107; }
        .blue { color: #42a5f5; }
        .purple { color: #ab47bc; }
        .cyan { color: #26c6da; }
        .panel { background: #141928; padding: 20px; border-radius: 12px; border: 1px solid #1e2a4a; margin-bottom: 25px; }
        .panel h2 { color: #8899bb; font-size: 1.1em; margin-bottom: 15px; }
        .input-group { display: flex; gap: 10px; flex-wrap: wrap; }
        .input-group input { flex: 1; min-width: 200px; padding: 12px 15px; border-radius: 8px; border: 1px solid #1e2a4a; background: #0a0e1a; color: #fff; font-size: 1em; }
        .btn { padding: 12px 25px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; font-size: 1em; transition: 0.3s; }
        .btn-add { background: #4caf50; color: #fff; }
        .btn-add:hover { background: #388e3c; }
        .btn-del { background: #f44336; color: #fff; }
        .btn-del:hover { background: #c62828; }
        .btn-refresh { background: #1a237e; color: #fff; }
        .btn-refresh:hover { background: #283593; }
        .btn-like { background: #ff9800; color: #fff; }
        .btn-like:hover { background: #e68900; }
        .user-list { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 15px; }
        .user-item { background: #1a2240; padding: 10px 15px; border-radius: 20px; display: flex; align-items: center; gap: 15px; border: 1px solid #2a3a5a; flex-wrap: wrap; }
        .user-item .uid { font-weight: bold; color: #42a5f5; }
        .user-item .stats { font-size: 0.8em; color: #8899bb; }
        .user-item .stats span { color: #4caf50; font-weight: bold; }
        .user-item .del-btn { background: none; border: none; color: #f44336; cursor: pointer; font-size: 1.2em; padding: 0 5px; }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; background: #141928; border-radius: 12px; overflow: hidden; margin-top: 15px; }
        th { background: #1e2a4a; padding: 12px 15px; text-align: left; font-weight: 600; color: #8899bb; white-space: nowrap; }
        td { padding: 12px 15px; border-bottom: 1px solid #1a2240; font-size: 0.9em; }
        .badge { padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: bold; display: inline-block; }
        .badge-working { background: #4caf5022; color: #4caf50; border: 1px solid #4caf50; }
        .badge-timeout { background: #f4433622; color: #f44336; border: 1px solid #f44336; }
        .badge-reset { background: #ffc10722; color: #ffc107; border: 1px solid #ffc107; }
        .badge-unknown { background: #8899bb22; color: #8899bb; border: 1px solid #8899bb; }
        .user-stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; margin-top: 15px; }
        .user-stat-card { background: #1a2240; padding: 15px; border-radius: 10px; border: 1px solid #2a3a5a; }
        .user-stat-card .uid { color: #42a5f5; font-weight: bold; font-size: 1.1em; }
        .user-stat-card .name { color: #fff; font-size: 0.9em; }
        .user-stat-card .row { display: flex; justify-content: space-between; margin-top: 5px; font-size: 0.85em; color: #8899bb; }
        .user-stat-card .row .val { color: #4caf50; font-weight: bold; }
        .user-stat-card .last { font-size: 0.75em; color: #666; margin-top: 5px; }
        .section-title { font-size: 1.3em; color: #fff; margin-top: 25px; margin-bottom: 15px; }
        .live-dot { display: inline-block; width: 10px; height: 10px; background: #4caf50; border-radius: 50%; margin-left: 10px; animation: pulse 1s infinite; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
        .note { color: #8899bb; font-size: 0.85em; margin-top: 10px; }
        @media (max-width: 768px) { .status-grid { grid-template-columns: repeat(2, 1fr); } }
        @media (max-width: 480px) { .header h1 { font-size: 1.5em; } .status-grid { grid-template-columns: 1fr 1fr; } }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="header-top">
            <div>
                <h1>Auto-Like Dashboard</h1>
                <div class="sub">Real-time monitoring • Auto-reset daily at 4:00 AM IST</div>
            </div>
            <div>
                <span class="badge-auto">▶ Auto-Like Running</span>
                <span style="margin-left:15px;">Next Reset: <span class="badge-reset" id="next-reset">Loading...</span></span>
                <button class="btn btn-refresh" onclick="location.reload()" style="margin-left:15px;">⟳ Refresh</button>
                <button class="btn btn-refresh" onclick="checkStatus()" style="margin-left:10px;">🔍 Check Status</button>
            </div>
        </div>
    </div>

    <div class="status-grid">
        <div class="status-card"><div class="num blue" id="total-accounts">0</div><div class="lbl">Total Accounts</div></div>
        <div class="status-card"><div class="num green" id="working-count">0</div><div class="lbl">Working Now</div></div>
        <div class="status-card"><div class="num red" id="timeout-count">0</div><div class="lbl">Limit Reached</div></div>
        <div class="status-card"><div class="num purple" id="total-likes">0</div><div class="lbl">Total Likes Sent</div></div>
        <div class="status-card"><div class="num yellow" id="targets-liked">0</div><div class="lbl">Targets Liked</div></div>
        <div class="status-card"><div class="num cyan" id="auto-users">0</div><div class="lbl">Auto Users</div></div>
    </div>

    <div class="panel">
        <h2>Manage Auto-Like Users</h2>
        <div class="input-group">
            <input type="number" id="user-uid" placeholder="Enter Free Fire UID" />
            <button class="btn btn-add" onclick="addUser()">+ Add User</button>
            <button class="btn btn-del" onclick="deleteAllUsers()">✕ Delete All</button>
            <button class="btn btn-like" onclick="sendInstantLike()">⚡ Send Like</button>
        </div>
        <div class="user-list" id="user-list"></div>
        <div class="note">Users added here will receive auto-likes daily at 4:00 AM IST • Click "Send Like" for instant like</div>
    </div>

    <div class="section-title">Account Status <span class="live-dot"></span></div>
    <div class="table-wrap">
        <table>
            <thead><tr><th>UID</th><th>Status</th><th>Last Check</th><th>Reset Time</th><th>Last Error</th></tr></thead>
            <tbody id="account-table"></tbody>
        </table>
    </div>

    <div class="section-title">User Statistics</div>
    <div class="user-stats-grid" id="user-stats-grid"></div>
</div>

<script>
    function loadData() {
        fetch('/api/dashboard-data')
            .then(res => res.json())
            .then(data => {
                document.getElementById('total-accounts').textContent = data.total_accounts || 0;
                document.getElementById('working-count').textContent = data.working_count || 0;
                document.getElementById('timeout-count').textContent = data.timeout_count || 0;
                document.getElementById('total-likes').textContent = data.total_likes || 0;
                document.getElementById('targets-liked').textContent = data.targets_liked || 0;
                document.getElementById('auto-users').textContent = data.auto_users || 0;
                document.getElementById('next-reset').textContent = data.next_reset || 'Loading...';

                let userHtml = '';
                data.users.forEach(user => {
                    const s = data.user_stats[user] || { total_likes: 0, today_likes: 0 };
                    userHtml += `<div class="user-item">
                        <span class="uid">${user}</span>
                        <span class="stats">Total: <span>${s.total_likes||0}</span> | Today: <span>${s.today_likes||0}</span></span>
                        <button class="del-btn" onclick="deleteUser('${user}')">✕</button>
                    </div>`;
                });
                document.getElementById('user-list').innerHTML = userHtml || '<div class="note">No users added yet</div>';

                let tableHtml = '';
                data.accounts.forEach(acc => {
                    const cls = acc.status === 'working' ? 'working' : acc.status === 'timeout' ? 'timeout' : 'unknown';
                    tableHtml += `<tr><td><strong>${acc.uid}</strong></td>
                        <td><span class="badge badge-${cls}">${acc.status}</span></td>
                        <td>${acc.last_check || 'Never'}</td>
                        <td>${acc.reset_time || 'N/A'}</td>
                        <td>${acc.last_error || 'None'}</td></tr>`;
                });
                document.getElementById('account-table').innerHTML = tableHtml || '<tr><td colspan="5">No accounts loaded</td></tr>';

                let statsHtml = '';
                Object.keys(data.user_stats).forEach(uid => {
                    const s = data.user_stats[uid];
                    statsHtml += `<div class="user-stat-card">
                        <div class="uid">UID: ${uid}</div>
                        <div class="name">Name: ${s.username || 'Unknown'}</div>
                        <div class="row"><span>Total Likes</span><span class="val">${s.total_likes||0}</span></div>
                        <div class="row"><span>Today's Likes</span><span class="val">${s.today_likes||0}</span></div>
                        <div class="row"><span>Current Likes</span><span class="val">${s.current_likes||0}</span></div>
                        <div class="last">Last: ${s.last_like || 'Never'}</div>
                    </div>`;
                });
                document.getElementById('user-stats-grid').innerHTML = statsHtml || '<div class="note">No stats yet</div>';
            });
    }

    function checkStatus() {
        fetch('/api/check-status')
            .then(res => res.json())
            .then(data => {
                alert(data.message);
                setTimeout(loadData, 2000);
            });
    }

    function addUser() {
        const uid = document.getElementById('user-uid').value.trim();
        if (!uid) { alert('Enter a UID'); return; }
        fetch('/add-user', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid }) })
            .then(res => res.json())
            .then(data => { if (data.success) { loadData(); document.getElementById('user-uid').value = ''; } else { alert(data.message); } });
    }

    function deleteUser(uid) {
        if (!confirm('Remove this user?')) return;
        fetch('/delete-user', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid }) })
            .then(res => res.json())
            .then(data => { if (data.success) loadData(); else alert(data.message); });
    }

    function deleteAllUsers() {
        if (!confirm('Delete ALL users?')) return;
        fetch('/delete-all-users', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
            .then(res => res.json())
            .then(data => { if (data.success) loadData(); else alert(data.message); });
    }

    function sendInstantLike() {
        const uid = document.getElementById('user-uid').value.trim();
        if (!uid) { alert('Enter a UID to like'); return; }
        if (!confirm(`Send likes to ${uid}?`)) return;
        
        fetch('/like-instant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uid, server_name: 'IND', key: 'JMLB', likes: 10 })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                alert(`✅ Sent ${data.likes_sent} likes to ${data.username || uid}\nTotal Likes: ${data.total_likes}`);
                loadData();
            } else {
                alert('❌ Error: ' + (data.error || 'Unknown error'));
            }
        });
    }

    setInterval(loadData, 3000);
    window.onload = loadData;
</script>
</body>
</html>
'''

# Routes
@app.route('/')
def dashboard():
    return render_template_string(WEBSITE_HTML)

@app.route('/api/dashboard-data')
def dashboard_data():
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
    
    return jsonify({
        'total_accounts': total,
        'working_count': working_count,
        'timeout_count': timeout_count,
        'total_likes': total_likes,
        'targets_liked': targets_liked,
        'auto_users': len(auto_like_users),
        'next_reset': next_reset,
        'users': auto_like_users,
        'user_stats': user_stats,
        'accounts': account_list
    })

@app.route('/api/check-status')
def check_status_api():
    threading.Thread(target=run_status_check).start()
    return jsonify({'message': 'Status check started! Refresh in 10 seconds'})

@app.route('/add-user', methods=['POST'])
def add_user():
    data = request.get_json()
    uid = data.get('uid', '').strip()
    
    if not uid:
        return jsonify({'success': False, 'message': 'UID required'})
    
    if uid in auto_like_users:
        return jsonify({'success': False, 'message': 'UID already in list'})
    
    auto_like_users.append(uid)
    user_stats[uid] = {'total_likes': 0, 'today_likes': 0, 'last_like': None, 'username': '', 'current_likes': 0}
    save_users()
    return jsonify({'success': True, 'message': f'Added {uid}'})

@app.route('/delete-user', methods=['POST'])
def delete_user():
    data = request.get_json()
    uid = data.get('uid', '').strip()
    
    if uid in auto_like_users:
        auto_like_users.remove(uid)
        if uid in user_stats:
            del user_stats[uid]
        save_users()
        return jsonify({'success': True, 'message': f'Removed {uid}'})
    
    return jsonify({'success': False, 'message': 'UID not found'})

@app.route('/delete-all-users', methods=['POST'])
def delete_all_users():
    auto_like_users.clear()
    user_stats.clear()
    save_users()
    return jsonify({'success': True, 'message': 'All users deleted'})

@app.route('/like-instant', methods=['POST'])
def like_instant():
    data = request.get_json()
    uid = data.get('uid', '').strip()
    server_name = data.get('server_name', 'IND').upper()
    key = data.get('key', 'JMLB')
    likes = int(data.get('likes', 10))
    
    if key != "JMLB":
        return jsonify({'success': False, 'error': 'Invalid key'})
    
    if not uid:
        return jsonify({'success': False, 'error': 'UID required'})
    
    # Get user info before
    user_info_before = asyncio.run(get_user_info(uid, server_name))
    before_likes = user_info_before.get('likes', 0) if user_info_before else 0
    
    # Send likes
    if server_name == "IND":
        like_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        like_url = "https://client.us.freefiremobile.com/LikeProfile"
    else:
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"
    
    result = asyncio.run(send_likes_batch(uid, server_name, like_url, likes))
    
    # Get user info after
    user_info_after = asyncio.run(get_user_info(uid, server_name))
    
    if user_info_after:
        username = user_info_after.get('name', 'Unknown')
        current_likes = user_info_after.get('likes', 0)
        update_user_stats(uid, result['success'], username, current_likes)
    else:
        update_user_stats(uid, result['success'])
    
    return jsonify({
        'success': result['success'] > 0,
        'likes_sent': result['success'],
        'username': user_info_after.get('name', 'Unknown') if user_info_after else 'Unknown',
        'total_likes': user_info_after.get('likes', 0) if user_info_after else 0,
        'skipped': result.get('skipped', 0),
        'failed': result.get('failed', 0)
    })

@app.route('/like', methods=['GET'])
def handle_requests():
    uid = request.args.get("uid")
    server_name = request.args.get("server_name", "").upper()
    key = request.args.get("key")
    client_ip = request.remote_addr
    
    likes_param = request.args.get("likes")
    requested_likes = int(likes_param) if likes_param and likes_param.isdigit() else None

    if key != "JMLB":
        return jsonify({"error": "Invalid API key"}), 403

    if not uid or not server_name:
        return jsonify({"error": "UID and server_name required"}), 400

    valid_servers = ["IND", "BR", "US", "SAC", "NA", "BD", "RU"]
    if server_name not in valid_servers:
        return jsonify({"error": f"Invalid server. Use: {valid_servers}"}), 400

    accounts = load_accounts(server_name)
    if not accounts:
        return jsonify({"error": f"No accounts for {server_name}"}), 500
    
    today_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
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
        return jsonify({"error": "No valid accounts"}), 500
    
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

    limit = requested_likes if requested_likes and requested_likes > 0 else 50
    result = asyncio.run(send_likes_batch(uid, server_name, like_url, limit))
    success_count = result['success']

    after = get_player_info(encrypted_uid, server_name, check_token)
    if after is None:
        return jsonify({"error": "Could not verify likes", "status": 0}), 200

    try:
        after_data = json.loads(MessageToJson(after))
        after_like = int(after_data['AccountInfo']['Likes'])
        player_id = int(after_data['AccountInfo']['UID'])
        player_name = str(after_data['AccountInfo']['PlayerNickname'])
        
        like_given = after_like - before_like
        status = 1 if success_count > 0 else 2

        return jsonify({
            "LikesGivenByAPI": success_count,
            "VerifiedLikesAdded": like_given,
            "LikesafterCommand": after_like,
            "LikesbeforeCommand": before_like,
            "PlayerNickname": player_name,
            "UID": player_id,
            "status": status,
            "remains": f"({KEY_LIMIT - count}/{KEY_LIMIT})",
            "total_accounts": len(accounts),
            "limit_requested": limit,
            "skipped_24hr": result.get('skipped', 0),
            "accounts_used": result.get('accounts_used', 0),
            "failed": result.get('failed', 0),
            "next_reset_at": get_next_reset_time().strftime('%Y-%m-%d %H:%M:%S IST')
        })
    except Exception as e:
        return jsonify({"error": str(e), "status": 0}), 500

@app.route('/reset-cache', methods=['GET'])
def reset_cache():
    key = request.args.get("key")
    if key != "JMLB":
        return jsonify({"error": "Invalid key"}), 403
    
    reset_all_data()
    return jsonify({"message": "All data reset", "credit": "@minister_69"})

@app.route('/health', methods=['GET'])
def health_check():
    accounts = load_accounts("IND")
    return jsonify({
        "status": "healthy",
        "accounts_loaded": len(accounts),
        "server": "Railway",
        "reset_time": "4:00 AM IST Daily",
        "auto_like": "Running",
        "users": len(auto_like_users)
    })

# Background Auto-Like Task
async def auto_like_daily():
    print("Auto-like scheduler started")
    while True:
        try:
            now = datetime.now()
            target_time = now.replace(hour=4, minute=0, second=0, microsecond=0)
            
            if now.hour >= 4:
                target_time = target_time + timedelta(days=1)
            
            wait_seconds = (target_time - now).total_seconds()
            if wait_seconds > 0:
                print(f"Next auto-like at: {target_time.strftime('%Y-%m-%d %H:%M:%S')} IST")
                await asyncio.sleep(wait_seconds)
            
            print(f"Starting auto-like at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
            
            accounts = load_accounts("IND")
            if not accounts:
                print("No accounts available")
                await asyncio.sleep(60)
                continue
            
            for user_uid in auto_like_users:
                print(f"Processing user: {user_uid}")
                
                result = await send_likes_batch(
                    user_uid,
                    "IND",
                    "https://client.ind.freefiremobile.com/LikeProfile",
                    50
                )
                
                print(f"Sent {result['success']} likes to {user_uid}")
                await asyncio.sleep(3)
            
            print(f"Auto-like cycle complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
            
        except Exception as e:
            print(f"Auto-like error: {e}")
            await asyncio.sleep(60)

def start_auto_like():
    asyncio.run(auto_like_daily())

# Load data
load_liked_data()
load_account_status()
load_users()

# Start background threads
reset_thread = threading.Thread(target=daily_reset_task, daemon=True)
reset_thread.start()

auto_thread = threading.Thread(target=start_auto_like, daemon=True)
auto_thread.start()

# Initial status check
threading.Thread(target=run_status_check).start()

print("Auto-Like System Started!")
print(f"Users loaded: {len(auto_like_users)}")
print(f"Accounts loaded: {len(load_accounts('IND'))}")
print("Auto-reset at 4:00 AM IST")

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)