from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
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

app = Flask(__name__)
app.secret_key = 'hex-cheats-secret-key-2024'

TOKEN_CACHE = {}
KEY_LIMIT = 500
tracker = defaultdict(lambda: [0, time.time()])

LIKED_DATA_FILE = "liked_data.pkl"
liked_cache = defaultdict(set)
like_timestamps = {}

ACCOUNT_STATUS_FILE = "account_status.pkl"
account_status = {}

USERS_FILE = "users.pkl"
auto_like_users = []
user_stats = {}
like_history = []

RESET_HOUR = 4
RESET_MINUTE = 2
RESET_SECOND = 0
AUTO_LIKE_LIMIT = 492
AUTO_LIKE_VERIFIED_LIMIT = 220

RATE_LIMIT_DELAYS = [0.02, 0.05, 0.08, 0.1]

REGION_URLS = {
    'IND': 'https://client.ind.freefiremobile.com',
    'BR': 'https://client.us.freefiremobile.com',
    'US': 'https://client.us.freefiremobile.com',
    'SAC': 'https://client.us.freefiremobile.com',
    'NA': 'https://client.us.freefiremobile.com',
    'BD': 'https://clientbp.ggpolarbear.com',
    'RU': 'https://clientbp.ggpolarbear.com',
    'MENA': 'https://clientbp.ggpolarbear.com'
}

# Activity Logs
activity_logs = []

def add_activity_log(message, log_type="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = {'time': timestamp, 'type': log_type, 'message': message}
    activity_logs.append(entry)
    if len(activity_logs) > 100:
        activity_logs.pop(0)
    print(f"[{timestamp}] [{log_type.upper()}] {message}")

def load_users():
    global auto_like_users, user_stats, like_history
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'rb') as f:
                data = pickle.load(f)
                if isinstance(data, dict):
                    auto_like_users = data.get('users', [])
                    user_stats = data.get('stats', {})
                    like_history = data.get('history', [])
                else:
                    auto_like_users = data
                    user_stats = {}
                    like_history = []
                print(f"Loaded {len(auto_like_users)} users, {len(like_history)} history entries")
        else:
            auto_like_users = []
            user_stats = {}
            like_history = []
            save_users()
    except Exception as e:
        print(f"Error loading users: {e}")
        auto_like_users = []
        user_stats = {}
        like_history = []

def save_users():
    try:
        data = {
            'users': auto_like_users,
            'stats': user_stats,
            'history': like_history[-100:]
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
        data = {'liked_cache': liked_cache, 'like_timestamps': like_timestamps}
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
        user_stats[target_uid] = {'total_likes': 0, 'today_likes': 0, 'last_like': None,
                                  'username': '', 'current_likes': 0}
    user_stats[target_uid]['total_likes'] += likes_given
    user_stats[target_uid]['today_likes'] += likes_given
    user_stats[target_uid]['last_like'] = datetime.now().isoformat()
    if username:
        user_stats[target_uid]['username'] = username
    if current_likes > 0:
        user_stats[target_uid]['current_likes'] = current_likes
    save_users()

def add_to_history(target_uid, likes_sent, before, after, username, server="IND"):
    entry = {
        'uid': target_uid,
        'username': username,
        'likes_sent': likes_sent,
        'before': before,
        'after': after,
        'verified_added': after - before,
        'server': server,
        'timestamp': datetime.now().isoformat()
    }
    like_history.append(entry)
    save_users()
    add_activity_log(f"✅ {username} | Sent {likes_sent} likes | Before: {before} | After: {after} | Gained: {after - before}", "success")

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
        server_map = {
            'IND': 'account_ind.txt',
            'BR': 'account_br.txt',
            'US': 'account_br.txt',
            'SAC': 'account_br.txt',
            'NA': 'account_br.txt',
            'BD': 'account_bd.txt',
            'RU': 'account_bd.txt',
            'MENA': 'account_mena.txt'
        }
        filename = server_map.get(server_name, 'account_ind.txt')
        if not os.path.exists(filename):
            return []
        accounts = []
        with open(filename, 'r') as f:
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
    try:
        accounts = load_accounts(server_name)
        if not accounts:
            return None
        
        check_token = None
        for account in accounts[:3]:
            token = await get_valid_token(account['uid'], account['password'])
            if token:
                check_token = token
                break
        
        if not check_token:
            return None
        
        encrypted_uid = enc(target_uid)
        info = get_player_info(encrypted_uid, server_name, check_token)
        
        if info:
            try:
                data = json.loads(MessageToJson(info))
                account_info = data.get('AccountInfo', {})
                return {
                    'uid': account_info.get('UID', target_uid),
                    'name': account_info.get('PlayerNickname', 'Unknown'),
                    'likes': int(account_info.get('Likes', 0))
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
            async with session.get(url, timeout=8) as response:
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

async def send_like_fast(encrypted_uid, token, url, account_uid):
    edata = bytes.fromhex(encrypted_uid)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB54"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=headers, timeout=3) as response:
                if response.status == 200:
                    if account_uid in account_status:
                        account_status[account_uid]['status'] = 'working'
                        account_status[account_uid]['last_check'] = datetime.now().isoformat()
                        save_account_status()
                    return True
                return False
    except:
        return False

async def send_likes_with_verified_limit(target_uid, server_name, url, max_likes, verified_limit=0):
    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0, 'stopped': False}
    
    fresh_accounts = []
    skipped = 0
    for acc in accounts:
        if is_uid_liked_in_24hrs(target_uid, acc['uid']):
            skipped += 1
        else:
            fresh_accounts.append(acc)
    
    if not fresh_accounts:
        return {'success': 0, 'failed': 0, 'total': len(accounts), 'skipped': skipped, 'stopped': True}
    
    accounts_to_use = fresh_accounts[:min(max_likes, len(fresh_accounts))]
    
    protobuf_message = create_protobuf_message(target_uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)
    
    token_tasks = []
    for acc in accounts_to_use:
        token_tasks.append(get_valid_token(acc['uid'], acc['password']))
    tokens = await asyncio.gather(*token_tasks, return_exceptions=True)
    
    like_tasks = []
    for i, acc in enumerate(accounts_to_use):
        if isinstance(tokens[i], str) and tokens[i]:
            like_tasks.append(send_like_fast(encrypted_uid, tokens[i], url, acc['uid']))
        else:
            like_tasks.append(asyncio.sleep(0, result=False))
    
    results = await asyncio.gather(*like_tasks, return_exceptions=True)
    
    successful = 0
    failed = 0
    for r in results:
        if isinstance(r, bool) and r:
            successful += 1
        else:
            failed += 1
    
    stopped = False
    if verified_limit > 0 and successful >= verified_limit:
        stopped = True
    
    user_info = None
    if successful > 0:
        user_info = await get_user_info(target_uid, server_name)
        if user_info:
            username = user_info.get('name', 'Unknown')
            current_likes = user_info.get('likes', 0)
            before_likes = user_stats.get(target_uid, {}).get('current_likes', 0)
            if before_likes == 0:
                before_likes = current_likes - successful
            update_user_stats(target_uid, successful, username, current_likes)
            add_to_history(target_uid, successful, before_likes, current_likes, username, server_name)
    
    return {
        'success': successful,
        'failed': failed,
        'total': len(accounts),
        'accounts_used': len(accounts_to_use),
        'skipped': skipped,
        'stopped': stopped,
        'user_info': user_info
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
    base_url = REGION_URLS.get(server_name, 'https://clientbp.ggpolarbear.com')
    url = f"{base_url}/GetPlayerPersonalShow"
    
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

async def check_all_accounts_status():
    accounts = load_accounts("IND")
    for acc in accounts[:50]:
        try:
            token = await get_valid_token(acc['uid'], acc['password'])
            if token:
                protobuf_message = create_protobuf_message("3997461446", "IND")
                encrypted_uid = encrypt_message(protobuf_message)
                url = "https://client.ind.freefiremobile.com/LikeProfile"
                success = await send_like_fast(encrypted_uid, token, url, acc['uid'])
                if success:
                    account_status[acc['uid']] = {'status': 'working', 'last_check': datetime.now().isoformat()}
                else:
                    account_status[acc['uid']] = {'status': 'timeout', 'last_check': datetime.now().isoformat(),
                                                  'reset_time': get_next_reset_time().isoformat()}
            else:
                account_status[acc['uid']] = {'status': 'unknown', 'last_check': datetime.now().isoformat()}
            save_account_status()
            await asyncio.sleep(0.3)
        except:
            continue

def run_status_check():
    asyncio.run(check_all_accounts_status())

async def auto_like_daily():
    add_activity_log("🚀 Auto-like scheduler started", "info")
    while True:
        try:
            now = datetime.now()
            target_time = now.replace(hour=RESET_HOUR, minute=RESET_MINUTE, second=RESET_SECOND, microsecond=0)
            if now >= target_time:
                target_time += timedelta(days=1)
            wait_seconds = (target_time - now).total_seconds()
            if wait_seconds > 0:
                add_activity_log(f"⏰ Next auto-like at: {target_time.strftime('%Y-%m-%d %H:%M:%S')} IST", "info")
                await asyncio.sleep(wait_seconds)
            
            add_activity_log(f"🔄 Starting auto-like at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST", "info")
            
            users_to_remove = []
            for user_uid in auto_like_users:
                add_activity_log(f"📱 Processing user: {user_uid}", "info")
                
                user_info_before = await get_user_info(user_uid, "IND")
                before_likes = user_info_before.get('likes', 0) if user_info_before else 0
                before_name = user_info_before.get('name', 'Unknown') if user_info_before else 'Unknown'
                
                result = await send_likes_with_verified_limit(
                    user_uid,
                    "IND",
                    "https://client.ind.freefiremobile.com/LikeProfile",
                    AUTO_LIKE_LIMIT,
                    AUTO_LIKE_VERIFIED_LIMIT
                )
                likes_sent = result['success']
                
                user_info_after = await get_user_info(user_uid, "IND")
                if user_info_after:
                    after_likes = user_info_after.get('likes', 0)
                    username = user_info_after.get('name', 'Unknown')
                    update_user_stats(user_uid, likes_sent, username, after_likes)
                    add_to_history(user_uid, likes_sent, before_likes, after_likes, username, "IND")
                    add_activity_log(f"✅ {username} | Before: {before_likes} | After: {after_likes} | Gained: {after_likes - before_likes}", "success")
                    if result['stopped']:
                        add_activity_log(f"🛑 Verified limit {AUTO_LIKE_VERIFIED_LIMIT} reached! Stopped.", "info")
                else:
                    add_activity_log(f"⚠️ {user_uid} | Failed to get profile", "error")
                
                if likes_sent > 0:
                    users_to_remove.append(user_uid)
                    add_activity_log(f"✅ Auto-removed: {user_uid}", "success")
                
                await asyncio.sleep(0.5)
            
            for uid in users_to_remove:
                if uid in auto_like_users:
                    auto_like_users.remove(uid)
                    save_users()
            
            add_activity_log(f"✅ Auto-like cycle complete. Removed {len(users_to_remove)} users.", "success")
            
        except Exception as e:
            add_activity_log(f"❌ Auto-like error: {str(e)}", "error")
            await asyncio.sleep(60)

def start_auto_like():
    asyncio.run(auto_like_daily())

def set_auto_time(hour, minute):
    global RESET_HOUR, RESET_MINUTE
    RESET_HOUR = hour
    RESET_MINUTE = minute
    add_activity_log(f"⏰ Auto-like time changed to {hour:02d}:{minute:02d} IST", "info")
    return f"Auto-like time set to {hour:02d}:{minute:02d} IST"

# ============================================================
# LOGIN PAGE
# ============================================================
LOGIN_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HEX CHEATS - Login</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: #0D1117;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background-image: radial-gradient(circle at 20% 30%, rgba(0,229,255,0.05) 0%, transparent 50%),
                              radial-gradient(circle at 80% 70%, rgba(0,230,118,0.05) 0%, transparent 50%);
        }
        .login-container {
            background: rgba(22,27,34,0.9);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(43,52,66,0.4);
            border-radius: 24px;
            padding: 50px 40px;
            max-width: 420px;
            width: 90%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        }
        .login-container .logo {
            text-align: center;
            margin-bottom: 30px;
        }
        .login-container .logo h1 {
            font-family: 'Orbitron', monospace;
            font-size: 2em;
            font-weight: 900;
            background: linear-gradient(135deg, #00E5FF, #00E676);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: 2px;
        }
        .login-container .logo p {
            color: #A8B3CF;
            font-size: 0.8em;
            letter-spacing: 4px;
            text-transform: uppercase;
            margin-top: 4px;
        }
        .login-container .input-group {
            margin-bottom: 16px;
        }
        .login-container .input-group label {
            color: #A8B3CF;
            font-size: 0.8em;
            font-weight: 600;
            letter-spacing: 0.5px;
            display: block;
            margin-bottom: 6px;
        }
        .login-container .input-group input {
            width: 100%;
            padding: 12px 16px;
            border-radius: 12px;
            border: 1px solid rgba(43,52,66,0.4);
            background: rgba(0,0,0,0.3);
            color: #F8FAFC;
            font-size: 1em;
            font-family: 'Inter', sans-serif;
            transition: 0.3s;
        }
        .login-container .input-group input:focus {
            outline: none;
            border-color: rgba(0,229,255,0.3);
            box-shadow: 0 0 20px rgba(0,229,255,0.05);
        }
        .login-container .login-btn {
            width: 100%;
            padding: 14px;
            border: none;
            border-radius: 12px;
            background: linear-gradient(135deg, #00E5FF, #00E676);
            color: #0D1117;
            font-size: 1em;
            font-weight: 700;
            cursor: pointer;
            transition: 0.3s;
            font-family: 'Inter', sans-serif;
            letter-spacing: 0.5px;
            margin-top: 8px;
        }
        .login-container .login-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 0 30px rgba(0,229,255,0.2);
        }
        .login-container .error-msg {
            color: #FF4D6D;
            font-size: 0.85em;
            text-align: center;
            margin-top: 12px;
            display: none;
        }
        .login-container .footer {
            text-align: center;
            margin-top: 20px;
            color: #4a5a7a;
            font-size: 0.7em;
            letter-spacing: 1px;
        }
        .login-container .footer i { color: #00E5FF; }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">
            <h1>HEX CHEATS</h1>
            <p>Like Bot System</p>
        </div>
        <form method="POST" action="/login">
            <div class="input-group">
                <label><i class="fas fa-user"></i> Username</label>
                <input type="text" name="username" placeholder="Enter username" required />
            </div>
            <div class="input-group">
                <label><i class="fas fa-lock"></i> Password</label>
                <input type="password" name="password" placeholder="Enter password" required />
            </div>
            <button type="submit" class="login-btn"><i class="fas fa-sign-in-alt"></i> Login</button>
        </form>
        <div class="error-msg" id="login-error">Invalid username or password!</div>
        <div class="footer"><i class="fas fa-shield-alt"></i> Secure Connection</div>
    </div>
    <script>
        // Show error if login fails
        if (window.location.search.includes('error=1')) {
            document.getElementById('login-error').style.display = 'block';
        }
    </script>
</body>
</html>
'''

# ============================================================
# PREMIUM DASHBOARD – FINAL VERSION
# ============================================================
WEBSITE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HEX CHEATS</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: #0D1117;
            color: #F8FAFC;
            min-height: 100vh;
            background-image: 
                radial-gradient(circle at 15% 20%, rgba(0,229,255,0.04) 0%, transparent 50%),
                radial-gradient(circle at 85% 80%, rgba(77,124,254,0.04) 0%, transparent 50%);
        }
        
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: rgba(255,255,255,0.02); }
        ::-webkit-scrollbar-thumb { background: rgba(0,229,255,0.2); border-radius: 10px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(0,229,255,0.35); }
        
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes neonPulse { 0%,100% { box-shadow: 0 0 15px rgba(0,229,255,0.05), 0 0 30px rgba(0,229,255,0.02); } 50% { box-shadow: 0 0 25px rgba(0,229,255,0.12), 0 0 50px rgba(0,229,255,0.04); } }
        @keyframes glowPulse { 0%,100% { opacity: 0.6; } 50% { opacity: 1; } }
        @keyframes titleGlow { 0%,100% { text-shadow: 0 0 20px rgba(0,229,255,0.2), 0 0 40px rgba(0,229,255,0.05); } 50% { text-shadow: 0 0 30px rgba(0,229,255,0.35), 0 0 60px rgba(0,229,255,0.1); } }
        
        .fade-in { animation: fadeInUp 0.4s ease forwards; }
        
        .main {
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px 28px;
            width: 100%;
        }
        
        /* ===== HEADER ===== */
        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 8px;
        }
        .title-section h1 {
            font-family: 'Orbitron', monospace;
            font-size: 2.4em;
            font-weight: 900;
            background: linear-gradient(135deg, #00E5FF, #00E676);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: 3px;
            animation: titleGlow 3s ease-in-out infinite;
        }
        .title-section .sub-title {
            font-family: 'Inter', sans-serif;
            font-size: 0.85em;
            color: #A8B3CF;
            letter-spacing: 6px;
            text-transform: uppercase;
            margin-top: 2px;
            font-weight: 400;
        }
        .header-right {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        .server-status {
            background: rgba(22,27,34,0.6);
            padding: 6px 16px;
            border-radius: 20px;
            border: 1px solid rgba(43,52,66,0.3);
            font-size: 0.75em;
            color: #A8B3CF;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .server-status .server-name { color: #00E5FF; font-weight: 600; }
        .server-status .accounts-count { color: #00E676; font-weight: 600; }
        .logout-btn {
            padding: 6px 16px;
            border: 1px solid rgba(43,52,66,0.3);
            border-radius: 12px;
            background: rgba(255,255,255,0.03);
            color: #A8B3CF;
            cursor: pointer;
            font-size: 0.75em;
            font-weight: 600;
            transition: 0.3s;
            font-family: 'Inter', sans-serif;
        }
        .logout-btn:hover { background: rgba(255,77,109,0.1); color: #FF4D6D; border-color: rgba(255,77,109,0.2); }
        
        .glass {
            background: rgba(22,27,34,0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(43,52,66,0.4);
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
            border-radius: 16px;
            transition: 0.3s;
        }
        .glass:hover { border-color: rgba(0,229,255,0.12); }
        
        .status-row {
            display: flex;
            flex-wrap: wrap;
            gap: 14px;
            margin-bottom: 18px;
            justify-content: center;
            padding: 4px 0;
        }
        .status-row .item {
            background: rgba(22,27,34,0.5);
            padding: 6px 18px;
            border-radius: 20px;
            font-size: 0.82em;
            border: 1px solid rgba(43,52,66,0.3);
            color: #A8B3CF;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .status-row .item i { color: #00E5FF; font-size: 0.9em; }
        .status-row .item span { color: #F8FAFC; font-weight: 500; }
        
        /* ===== NAV BUTTONS ===== */
        .nav-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(105px, 1fr));
            gap: 10px;
            margin-bottom: 22px;
        }
        .nav-btn {
            padding: 12px 14px;
            border: 1px solid rgba(43,52,66,0.3);
            border-radius: 16px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.8em;
            transition: 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            font-family: 'Inter', sans-serif;
            letter-spacing: 0.3px;
            background: #232A36;
            color: #A8B3CF;
            min-height: 44px;
            text-align: center;
        }
        .nav-btn:hover { 
            background: rgba(0,229,255,0.06); 
            color: #00E5FF; 
            transform: translateY(-2px);
            border-color: rgba(0,229,255,0.15);
            box-shadow: 0 0 20px rgba(0,229,255,0.05);
        }
        .nav-btn.active-nav {
            background: linear-gradient(135deg, rgba(0,229,255,0.15), rgba(0,230,118,0.10));
            color: #00E5FF;
            border-color: rgba(0,229,255,0.2);
            box-shadow: 0 0 30px rgba(0,229,255,0.06);
            animation: neonPulse 2.5s infinite;
        }
        .nav-btn i { font-size: 0.9em; }
        
        /* ===== STATS ===== */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px;
            margin-bottom: 22px;
        }
        .stat-card {
            padding: 18px 14px;
            text-align: center;
            background: rgba(22,27,34,0.6);
            backdrop-filter: blur(8px);
            border: 1px solid rgba(43,52,66,0.3);
            border-radius: 16px;
            transition: 0.3s;
            min-height: 100px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
        }
        .stat-card:hover { 
            border-color: rgba(0,229,255,0.08); 
            transform: translateY(-3px);
            box-shadow: 0 8px 30px rgba(0,0,0,0.15);
        }
        .stat-card .num {
            font-family: 'Orbitron', monospace;
            font-size: 2.2em;
            font-weight: 700;
            line-height: 1.2;
        }
        .stat-card .lbl {
            color: #A8B3CF;
            font-size: 0.7em;
            margin-top: 5px;
            letter-spacing: 1.2px;
            text-transform: uppercase;
        }
        .stat-card .icon { font-size: 1.1em; margin-bottom: 4px; opacity: 0.4; }
        .num-accounts { color: #4D7CFE; }
        .num-working { color: #00E676; }
        .num-timeout { color: #FF4D6D; }
        .num-likes { color: #A855F7; }
        .num-targets { color: #FFC107; }
        .num-queue { color: #00E5FF; }
        
        /* ===== PANELS ===== */
        .panel {
            padding: 20px 24px;
            margin-bottom: 20px;
            background: rgba(28,33,40,0.6);
            backdrop-filter: blur(8px);
            border: 1px solid rgba(43,52,66,0.3);
            border-radius: 16px;
        }
        .panel h2 {
            color: #A8B3CF;
            font-size: 0.9em;
            margin-bottom: 14px;
            letter-spacing: 1.2px;
            text-transform: uppercase;
            font-weight: 600;
        }
        .panel h2 i { margin-right: 10px; color: #00E5FF; }
        
        .input-group {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
        }
        .input-group input, .input-group select {
            padding: 10px 16px;
            border-radius: 12px;
            border: 1px solid rgba(43,52,66,0.3);
            background: rgba(0,0,0,0.25);
            color: #F8FAFC;
            font-size: 0.9em;
            font-family: 'Inter', sans-serif;
            min-width: 140px;
            transition: 0.3s;
        }
        .input-group input:focus, .input-group select:focus {
            outline: none;
            border-color: rgba(0,229,255,0.2);
            box-shadow: 0 0 20px rgba(0,229,255,0.04);
        }
        .input-group select option { background: #0D1117; }
        
        /* ===== BUTTONS ===== */
        .btn {
            padding: 10px 22px;
            border: none;
            border-radius: 16px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.85em;
            transition: 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-family: 'Inter', sans-serif;
            letter-spacing: 0.3px;
            min-height: 42px;
        }
        .btn:hover { transform: translateY(-2px); }
        .btn-primary {
            background: linear-gradient(135deg, #00E5FF, #00E676);
            color: #0D1117;
            border: none;
        }
        .btn-primary:hover { box-shadow: 0 0 30px rgba(0,229,255,0.2); }
        .btn-success {
            background: rgba(0,230,118,0.12);
            color: #00E676;
            border: 1px solid rgba(0,230,118,0.1);
        }
        .btn-success:hover { background: rgba(0,230,118,0.2); }
        .btn-danger {
            background: rgba(255,77,109,0.12);
            color: #FF4D6D;
            border: 1px solid rgba(255,77,109,0.1);
        }
        .btn-danger:hover { background: rgba(255,77,109,0.2); }
        .btn-warning {
            background: rgba(255,193,7,0.12);
            color: #FFC107;
            border: 1px solid rgba(255,193,7,0.1);
        }
        .btn-warning:hover { background: rgba(255,193,7,0.2); }
        .btn-rocket {
            background: linear-gradient(135deg, #FF4D6D, #FF6B8A);
            color: #0D1117;
            border: none;
        }
        .btn-rocket:hover { box-shadow: 0 0 30px rgba(255,77,109,0.2); transform: scale(1.02); }
        .btn-ghost {
            background: rgba(255,255,255,0.03);
            color: #A8B3CF;
            border: 1px solid rgba(43,52,66,0.3);
        }
        .btn-ghost:hover { background: rgba(255,255,255,0.06); color: #F8FAFC; }
        
        .user-list {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 12px;
        }
        .user-item {
            background: rgba(22,27,34,0.5);
            padding: 6px 16px;
            border-radius: 20px;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            border: 1px solid rgba(43,52,66,0.3);
            margin: 3px;
            font-size: 0.85em;
            transition: 0.3s;
        }
        .user-item:hover { border-color: rgba(0,229,255,0.1); }
        .user-item .uid { font-weight: 600; color: #00E5FF; }
        .user-item .stats { color: #A8B3CF; font-size: 0.75em; }
        .user-item .stats span { color: #00E676; font-weight: 600; }
        .user-item .del-btn {
            background: none;
            border: none;
            color: #FF4D6D;
            cursor: pointer;
            padding: 0 4px;
            font-size: 1em;
        }
        
        .table-wrap { overflow-x: auto; }
        table {
            width: 100%;
            border-collapse: collapse;
            background: rgba(0,0,0,0.15);
            border-radius: 12px;
            overflow: hidden;
            margin-top: 10px;
            font-size: 0.85em;
        }
        th {
            background: rgba(0,229,255,0.03);
            padding: 10px 16px;
            text-align: left;
            font-weight: 600;
            color: #A8B3CF;
            border-bottom: 1px solid rgba(43,52,66,0.3);
            text-transform: uppercase;
            font-size: 0.7em;
            letter-spacing: 0.8px;
        }
        td { padding: 10px 16px; border-bottom: 1px solid rgba(43,52,66,0.15); }
        .badge {
            padding: 2px 12px;
            border-radius: 20px;
            font-size: 0.65em;
            font-weight: 600;
            display: inline-block;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .badge-working { background: rgba(0,230,118,0.12); color: #00E676; border: 1px solid rgba(0,230,118,0.06); }
        .badge-timeout { background: rgba(255,77,109,0.12); color: #FF4D6D; border: 1px solid rgba(255,77,109,0.06); }
        .badge-reset { background: rgba(255,193,7,0.12); color: #FFC107; border: 1px solid rgba(255,193,7,0.06); }
        .badge-unknown { background: rgba(168,179,207,0.12); color: #A8B3CF; border: 1px solid rgba(168,179,207,0.06); }
        
        .section-title {
            font-size: 1em;
            color: #F8FAFC;
            margin: 20px 0 10px;
            display: flex;
            align-items: center;
            gap: 10px;
            font-weight: 600;
            letter-spacing: 0.3px;
        }
        .live-dot {
            display: inline-block;
            width: 7px;
            height: 7px;
            background: #00E676;
            border-radius: 50%;
            animation: glowPulse 1.5s infinite;
        }
        .note { color: #A8B3CF; font-size: 0.8em; margin-top: 8px; }
        
        .history-item {
            padding: 8px 0;
            border-bottom: 1px solid rgba(43,52,66,0.2);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 6px;
            font-size: 0.85em;
        }
        .history-item .uid { color: #00E5FF; font-weight: 600; }
        .history-item .name { color: #F8FAFC; }
        .history-item .likes { color: #00E676; font-weight: 600; }
        .history-item .time { color: #A8B3CF; font-size: 0.75em; }
        
        /* ===== LOGS ===== */
        .logs-container {
            max-height: 300px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.8em;
            background: rgba(0,0,0,0.2);
            border-radius: 12px;
            padding: 12px 16px;
            border: 1px solid rgba(43,52,66,0.15);
        }
        .log-entry {
            padding: 4px 0;
            border-bottom: 1px solid rgba(43,52,66,0.08);
            color: #A8B3CF;
            display: flex;
            gap: 12px;
        }
        .log-entry .log-time { color: #00E5FF; min-width: 60px; }
        .log-entry .log-success { color: #00E676; }
        .log-entry .log-error { color: #FF4D6D; }
        .log-entry .log-info { color: #FFC107; }
        
        /* ===== MODAL ===== */
        .result-modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 999;
            align-items: center;
            justify-content: center;
            backdrop-filter: blur(8px);
        }
        .result-modal.active { display: flex; }
        .result-box {
            background: #161B22;
            padding: 32px 36px;
            border-radius: 18px;
            max-width: 500px;
            width: 90%;
            border: 1px solid rgba(43,52,66,0.4);
            box-shadow: 0 0 60px rgba(0,229,255,0.03);
            animation: fadeInUp 0.4s ease;
        }
        .result-box h2 {
            font-family: 'Orbitron', monospace;
            font-size: 1.1em;
            color: #00E5FF;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .result-box .row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid rgba(43,52,66,0.2);
        }
        .result-box .row .label { color: #A8B3CF; font-size: 0.9em; }
        .result-box .row .value { color: #00E676; font-weight: 600; font-size: 0.9em; }
        .result-box .row .value-failed { color: #FF4D6D; }
        .result-box .close-btn {
            margin-top: 16px;
            padding: 10px;
            background: rgba(255,255,255,0.04);
            color: #A8B3CF;
            border: 1px solid rgba(43,52,66,0.3);
            border-radius: 12px;
            cursor: pointer;
            font-weight: 600;
            width: 100%;
            transition: 0.3s;
            font-family: 'Inter', sans-serif;
            font-size: 0.9em;
        }
        .result-box .close-btn:hover { background: rgba(255,255,255,0.08); color: #F8FAFC; }
        
        .section { display: none; }
        .section.active { display: block; animation: fadeInUp 0.35s ease; }
        
        /* ===== RESPONSIVE ===== */
        @media (max-width: 992px) {
            .main { padding: 18px 20px; }
            .stats-grid { grid-template-columns: repeat(3, 1fr); }
            .nav-grid { grid-template-columns: repeat(4, 1fr); }
            .title-section h1 { font-size: 2em; }
            .header-top { flex-direction: column; align-items: flex-start; }
        }
        @media (max-width: 768px) {
            .main { padding: 14px 16px; }
            .stats-grid { grid-template-columns: repeat(3, 1fr); gap: 12px; }
            .stat-card { padding: 14px 10px; min-height: 80px; }
            .stat-card .num { font-size: 1.6em; }
            .nav-grid { grid-template-columns: repeat(3, 1fr); gap: 8px; }
            .nav-btn { font-size: 0.7em; padding: 10px 12px; min-height: 38px; }
            .title-section h1 { font-size: 1.6em; }
            .title-section .sub-title { font-size: 0.7em; letter-spacing: 4px; }
            .panel { padding: 16px 18px; }
            .input-group input, .input-group select { min-width: 100px; font-size: 0.8em; padding: 8px 12px; }
            .btn { font-size: 0.8em; padding: 8px 16px; min-height: 36px; }
            .result-box { padding: 20px; }
            .status-row .item { font-size: 0.7em; padding: 4px 12px; }
            .server-status { font-size: 0.65em; padding: 4px 12px; }
        }
        @media (max-width: 480px) {
            .main { padding: 10px 12px; }
            .stats-grid { grid-template-columns: 1fr 1fr; }
            .nav-grid { grid-template-columns: repeat(2, 1fr); }
            .nav-btn { font-size: 0.65em; padding: 8px 10px; min-height: 34px; }
            .title-section h1 { font-size: 1.3em; }
            .title-section .sub-title { font-size: 0.6em; letter-spacing: 3px; }
            .stat-card .num { font-size: 1.3em; }
            .stat-card { padding: 12px 8px; min-height: 70px; }
            .status-row .item { font-size: 0.65em; padding: 3px 10px; }
        }
    </style>
</head>
<body>
    <div class="main">
        <!-- Header -->
        <div class="header-top">
            <div class="title-section">
                <h1>HEX CHEATS</h1>
                <div class="sub-title">Like Bot System</div>
            </div>
            <div class="header-right">
                <div class="server-status">
                    <i class="fas fa-server"></i>
                    Server: <span class="server-name" id="current-server">IND</span>
                    | Accounts: <span class="accounts-count" id="server-accounts">0</span>
                </div>
                <a href="/logout"><button class="logout-btn"><i class="fas fa-sign-out-alt"></i> Logout</button></a>
            </div>
        </div>
        
        <!-- Status Row -->
        <div class="status-row">
            <div class="item"><i class="fas fa-history"></i> Last Auto-Run: <span id="lastAutoRun">Never</span></div>
            <div class="item"><i class="fas fa-info-circle"></i> Status: <span id="autoRunStatus">Idle</span></div>
            <div class="item"><i class="fas fa-comment"></i> Message: <span id="autoRunMessage">-</span></div>
        </div>
        
        <!-- Navigation -->
        <div class="nav-grid">
            <button class="nav-btn active-nav" onclick="showSection('dashboard')"><i class="fas fa-home"></i> Dashboard</button>
            <button class="nav-btn" onclick="showSection('likes20')"><i class="fas fa-crosshairs"></i> 20 Likes</button>
            <button class="nav-btn" onclick="showSection('unlimited')"><i class="fas fa-infinity"></i> Unlimited</button>
            <button class="nav-btn" onclick="showSection('auto')"><i class="fas fa-clock"></i> Auto Like</button>
            <button class="nav-btn" onclick="showSection('verify')"><i class="fas fa-check-double"></i> Verify</button>
            <button class="nav-btn" onclick="showSection('history')"><i class="fas fa-history"></i> History</button>
            <button class="nav-btn" onclick="showSection('accounts')"><i class="fas fa-users"></i> Accounts</button>
            <button class="nav-btn" onclick="showSection('stats')"><i class="fas fa-chart-bar"></i> Stats</button>
            <button class="nav-btn" onclick="showSection('logs')"><i class="fas fa-terminal"></i> Logs</button>
            <button class="nav-btn" onclick="showSection('settings')"><i class="fas fa-cog"></i> Settings</button>
        </div>
        
        <!-- Dashboard -->
        <div id="section-dashboard" class="section active">
            <div class="stats-grid">
                <div class="stat-card"><div class="icon" style="color:#4D7CFE;"><i class="fas fa-users"></i></div><div class="num num-accounts" id="total-accounts">0</div><div class="lbl">Accounts</div></div>
                <div class="stat-card"><div class="icon" style="color:#00E676;"><i class="fas fa-check-circle"></i></div><div class="num num-working" id="working-count">0</div><div class="lbl">Working</div></div>
                <div class="stat-card"><div class="icon" style="color:#FF4D6D;"><i class="fas fa-exclamation-triangle"></i></div><div class="num num-timeout" id="timeout-count">0</div><div class="lbl">Limit</div></div>
                <div class="stat-card"><div class="icon" style="color:#A855F7;"><i class="fas fa-heart"></i></div><div class="num num-likes" id="total-likes">0</div><div class="lbl">Likes</div></div>
                <div class="stat-card"><div class="icon" style="color:#FFC107;"><i class="fas fa-bullseye"></i></div><div class="num num-targets" id="targets-liked">0</div><div class="lbl">Targets</div></div>
                <div class="stat-card"><div class="icon" style="color:#00E5FF;"><i class="fas fa-list-ul"></i></div><div class="num num-queue" id="auto-users">0</div><div class="lbl">Queue</div></div>
            </div>
        </div>
        
        <!-- 20 Likes -->
        <div id="section-likes20" class="section">
            <div class="panel">
                <h2><i class="fas fa-crosshairs"></i> 20 Likes</h2>
                <div class="input-group">
                    <input type="number" id="target-uid-20" placeholder="Enter Free Fire UID" />
                    <select id="server-20" onchange="updateServerStatus(this.value)">
                        <option value="IND">India</option>
                        <option value="BD">Bangladesh</option>
                        <option value="MENA">MENA</option>
                        <option value="BR">Brazil</option>
                        <option value="US">US</option>
                        <option value="SAC">SAC</option>
                        <option value="NA">NA</option>
                        <option value="RU">Russia</option>
                    </select>
                    <button class="btn btn-primary" onclick="sendLikes(20)"><i class="fas fa-arrow-right"></i> Send 20 Likes</button>
                </div>
                <div class="note"><i class="fas fa-info-circle"></i> Sends exactly 20 verified likes. All accounts checked, stops automatically.</div>
            </div>
        </div>
        
        <!-- Unlimited -->
        <div id="section-unlimited" class="section">
            <div class="panel">
                <h2><i class="fas fa-infinity"></i> Unlimited Likes</h2>
                <div class="input-group">
                    <input type="number" id="target-uid-unlimited" placeholder="Enter Free Fire UID" />
                    <select id="server-unlimited" onchange="updateServerStatus(this.value)">
                        <option value="IND">India</option>
                        <option value="BD">Bangladesh</option>
                        <option value="MENA">MENA</option>
                        <option value="BR">Brazil</option>
                        <option value="US">US</option>
                        <option value="SAC">SAC</option>
                        <option value="NA">NA</option>
                        <option value="RU">Russia</option>
                    </select>
                    <button class="btn btn-rocket" onclick="sendLikes(999999)"><i class="fas fa-rocket"></i> Send All Likes</button>
                </div>
                <div class="note"><i class="fas fa-info-circle"></i> Sends all available likes, stops after verification.</div>
            </div>
        </div>
        
        <!-- Auto Like -->
        <div id="section-auto" class="section">
            <div class="panel">
                <h2><i class="fas fa-clock"></i> Auto Like</h2>
                <p style="color:#A8B3CF; margin-bottom:12px; font-size:0.85em;">Daily auto-like. All accounts send at same time. Stops after verified limit.</p>
                <div class="input-group">
                    <input type="number" id="target-uid-auto" placeholder="Enter Free Fire UID" />
                    <input type="number" id="auto-limit" placeholder="Verified Limit" value="220" style="width:130px;" />
                    <button class="btn btn-success" onclick="addAutoUser()"><i class="fas fa-plus"></i> Add</button>
                    <button class="btn btn-danger" onclick="deleteAllAuto()"><i class="fas fa-trash"></i> Clear</button>
                </div>
                <div class="user-list" id="auto-user-list"></div>
                <div class="note"><i class="fas fa-info-circle"></i> Users auto-remove after successful like. Stops when verified limit reached.</div>
            </div>
        </div>
        
        <!-- Verify -->
        <div id="section-verify" class="section">
            <div class="panel">
                <h2><i class="fas fa-check-double"></i> Verify Likes</h2>
                <div class="input-group">
                    <input type="number" id="target-uid-verify" placeholder="Enter Free Fire UID" />
                    <select id="server-verify" onchange="updateServerStatus(this.value)">
                        <option value="IND">India</option>
                        <option value="BD">Bangladesh</option>
                        <option value="MENA">MENA</option>
                        <option value="BR">Brazil</option>
                        <option value="US">US</option>
                        <option value="SAC">SAC</option>
                        <option value="NA">NA</option>
                        <option value="RU">Russia</option>
                    </select>
                    <button class="btn btn-primary" onclick="verifyLikes()"><i class="fas fa-check-double"></i> Verify</button>
                </div>
                <div id="verify-result" style="margin-top:12px;"></div>
            </div>
        </div>
        
        <!-- History -->
        <div id="section-history" class="section">
            <div class="panel">
                <h2><i class="fas fa-history"></i> Like History</h2>
                <div id="history-list"></div>
            </div>
        </div>
        
        <!-- Accounts -->
        <div id="section-accounts" class="section">
            <div class="section-title"><i class="fas fa-users"></i> Account Status <span class="live-dot"></span></div>
            <div class="glass" style="padding:0; overflow:hidden;">
                <table>
                    <thead><tr><th>UID</th><th>Status</th><th>Last Check</th><th>Reset Time</th></tr></thead>
                    <tbody id="account-table"></tbody>
                </table>
            </div>
        </div>
        
        <!-- Stats -->
        <div id="section-stats" class="section">
            <div class="panel">
                <h2><i class="fas fa-chart-bar"></i> Statistics</h2>
                <div id="stats-content"></div>
            </div>
        </div>
        
        <!-- Logs -->
        <div id="section-logs" class="section">
            <div class="panel">
                <h2><i class="fas fa-terminal"></i> Activity Logs</h2>
                <div class="logs-container" id="logs-container">
                    <div class="log-entry"><span class="log-time">[--:--:--]</span> <span class="log-info">System ready...</span></div>
                </div>
            </div>
        </div>
        
        <!-- Settings -->
        <div id="section-settings" class="section">
            <div class="panel">
                <h2><i class="fas fa-cog"></i> Settings</h2>
                <div style="margin-bottom:12px;">
                    <label style="color:#A8B3CF; font-size:0.85em;">Auto-Like Time (IST)</label>
                    <div class="input-group" style="margin-top:6px;">
                        <input type="number" id="set-hour" placeholder="Hour" value="4" style="width:80px;" />
                        <input type="number" id="set-minute" placeholder="Minute" value="2" style="width:80px;" />
                        <button class="btn btn-primary" onclick="setAutoTime()"><i class="fas fa-save"></i> Save Time</button>
                    </div>
                </div>
                <div id="time-status" style="color:#00E676; font-size:0.85em;"></div>
            </div>
        </div>
    </div>
    
    <!-- Result Modal -->
    <div class="result-modal" id="resultModal">
        <div class="result-box">
            <h2><i class="fas fa-check-circle"></i> Like Result</h2>
            <div id="result-content">
                <div class="row"><span class="label">Player Name</span><span class="value" id="res-name">-</span></div>
                <div class="row"><span class="label">Likes Sent</span><span class="value" id="res-sent">0</span></div>
                <div class="row"><span class="label">Likes Before</span><span class="value" id="res-before">0</span></div>
                <div class="row"><span class="label">Likes After</span><span class="value" id="res-after">0</span></div>
                <div class="row"><span class="label">Verified Added</span><span class="value" id="res-added">0</span></div>
                <div class="row"><span class="label">Failed</span><span class="value value-failed" id="res-failed">0</span></div>
            </div>
            <button class="close-btn" onclick="closeResult()"><i class="fas fa-times"></i> Close</button>
        </div>
    </div>

    <script>
        // ============================================
        // UI FUNCTIONS
        // ============================================
        let currentServer = 'IND';
        
        function updateServerStatus(server) {
            currentServer = server;
            document.getElementById('current-server').textContent = server;
            // Update accounts count from data
            loadData();
        }
        
        function showSection(id) {
            document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
            document.getElementById('section-' + id).classList.add('active');
            document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active-nav'));
            document.querySelector(`.nav-btn[onclick*="${id}"]`).classList.add('active-nav');
            if (id === 'history') loadHistory();
            if (id === 'stats') loadStats();
            if (id === 'logs') loadLogs();
        }
        
        function formatTime(iso) {
            if (!iso) return 'Never';
            try { const d = new Date(iso); return d.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch { return iso; }
        }
        
        // ============================================
        // DATA LOADING
        // ============================================
        function loadData() {
            fetch('/api/dashboard-data?server=' + currentServer)
                .then(res => res.json())
                .then(data => {
                    if (data.error) return;
                    document.getElementById('total-accounts').textContent = data.total_accounts || 0;
                    document.getElementById('working-count').textContent = data.working_count || 0;
                    document.getElementById('timeout-count').textContent = data.timeout_count || 0;
                    document.getElementById('total-likes').textContent = data.total_likes || 0;
                    document.getElementById('targets-liked').textContent = data.targets_liked || 0;
                    document.getElementById('auto-users').textContent = data.auto_users || 0;
                    document.getElementById('server-accounts').textContent = data.total_accounts || 0;
                    document.getElementById('lastAutoRun').textContent = data.last_auto_run ? formatTime(data.last_auto_run) : 'Never';
                    document.getElementById('autoRunStatus').textContent = data.auto_run_status || 'Idle';
                    document.getElementById('autoRunMessage').textContent = data.auto_run_message || '-';
                    
                    // Auto queue
                    let userHtml = '';
                    if (data.users && data.users.length > 0) {
                        data.users.forEach(user => {
                            const s = data.user_stats[user] || { total_likes: 0, today_likes: 0 };
                            userHtml += `<div class="user-item"><span class="uid">${user}</span><span class="stats">T:<span>${s.total_likes||0}</span> D:<span>${s.today_likes||0}</span></span><button class="del-btn" onclick="deleteUser('${user}')"><i class="fas fa-times"></i></button></div>`;
                        });
                    } else {
                        userHtml = '<div class="note">No users in auto-queue</div>';
                    }
                    document.getElementById('auto-user-list').innerHTML = userHtml;
                    
                    // Accounts table
                    let tableHtml = '';
                    if (data.accounts && data.accounts.length > 0) {
                        data.accounts.forEach(acc => {
                            const cls = acc.status === 'working' ? 'working' : acc.status === 'timeout' ? 'timeout' : 'unknown';
                            tableHtml += `<tr><td><strong>${acc.uid}</strong></td><td><span class="badge badge-${cls}">${acc.status}</span></td><td>${acc.last_check ? formatTime(acc.last_check) : 'Never'}</td><td>${acc.reset_time ? formatTime(acc.reset_time) : 'N/A'}</td></tr>`;
                        });
                    } else {
                        tableHtml = '<tr><td colspan="4">No accounts loaded</td></tr>';
                    }
                    document.getElementById('account-table').innerHTML = tableHtml;
                });
        }
        
        function loadHistory() {
            fetch('/api/history')
                .then(res => res.json())
                .then(data => {
                    let html = '';
                    if (data.history && data.history.length > 0) {
                        data.history.forEach(h => {
                            html += `<div class="history-item">
                                <span><span class="uid">${h.uid}</span> <span class="name">${h.username || 'Unknown'}</span></span>
                                <span class="likes">+${h.likes_sent} (${h.verified_added} verified)</span>
                                <span class="time">${formatTime(h.timestamp)}</span>
                            </div>`;
                        });
                    } else {
                        html = '<div class="note">No history yet</div>';
                    }
                    document.getElementById('history-list').innerHTML = html;
                });
        }
        
        function loadStats() {
            fetch('/api/stats')
                .then(res => res.json())
                .then(data => {
                    let html = `
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(43,52,66,0.2);">
                            <span style="color:#A8B3CF;">Total Likes Sent</span>
                            <span style="color:#00E676;font-weight:600;">${data.total_likes_sent}</span>
                        </div>
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(43,52,66,0.2);">
                            <span style="color:#A8B3CF;">Total Targets</span>
                            <span style="color:#00E676;font-weight:600;">${data.total_targets}</span>
                        </div>
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(43,52,66,0.2);">
                            <span style="color:#A8B3CF;">Working Accounts</span>
                            <span style="color:#00E676;font-weight:600;">${data.working_accounts}</span>
                        </div>
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(43,52,66,0.2);">
                            <span style="color:#A8B3CF;">Auto Queue Users</span>
                            <span style="color:#00E676;font-weight:600;">${data.auto_users}</span>
                        </div>
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;">
                            <span style="color:#A8B3CF;">Next Reset</span>
                            <span style="color:#FFC107;font-weight:600;">${data.next_reset}</span>
                        </div>
                    `;
                    document.getElementById('stats-content').innerHTML = html;
                });
        }
        
        function loadLogs() {
            fetch('/api/logs')
                .then(res => res.json())
                .then(data => {
                    let html = '';
                    if (data.logs && data.logs.length > 0) {
                        data.logs.forEach(log => {
                            const colorClass = log.type === 'success' ? 'log-success' : log.type === 'error' ? 'log-error' : 'log-info';
                            html += `<div class="log-entry"><span class="log-time">[${log.time}]</span> <span class="${colorClass}">${log.message}</span></div>`;
                        });
                    } else {
                        html = '<div class="log-entry"><span class="log-time">[--:--:--]</span> <span class="log-info">No logs yet</span></div>';
                    }
                    document.getElementById('logs-container').innerHTML = html;
                });
        }
        
        // ============================================
        // RESULT MODAL
        // ============================================
        function showResult(data) {
            document.getElementById('res-name').textContent = data.username || 'Unknown';
            document.getElementById('res-sent').textContent = data.likes_sent || 0;
            document.getElementById('res-before').textContent = data.likes_before || 0;
            document.getElementById('res-after').textContent = data.total_likes || 0;
            document.getElementById('res-added').textContent = data.verified_added || 0;
            document.getElementById('res-failed').textContent = data.failed || 0;
            document.getElementById('resultModal').classList.add('active');
        }
        
        function closeResult() { document.getElementById('resultModal').classList.remove('active'); }
        document.getElementById('resultModal').addEventListener('click', function(e) { if (e.target === this) closeResult(); });
        
        // ============================================
        // ACTIONS
        // ============================================
        function getServer() {
            const activeSection = document.querySelector('.section.active');
            if (activeSection) {
                const id = activeSection.id;
                if (id === 'section-likes20') return document.getElementById('server-20').value;
                if (id === 'section-unlimited') return document.getElementById('server-unlimited').value;
                if (id === 'section-verify') return document.getElementById('server-verify').value;
            }
            return 'IND';
        }
        
        function getUid() {
            const activeSection = document.querySelector('.section.active');
            if (activeSection) {
                const id = activeSection.id;
                if (id === 'section-likes20') return document.getElementById('target-uid-20').value.trim();
                if (id === 'section-unlimited') return document.getElementById('target-uid-unlimited').value.trim();
                if (id === 'section-verify') return document.getElementById('target-uid-verify').value.trim();
            }
            return '';
        }
        
        function sendLikes(count) {
            const uid = getUid();
            const server = getServer();
            if (!uid) { alert('Enter a UID'); return; }
            if (!confirm(`Send ${count === 999999 ? 'unlimited' : count} likes to ${uid} on ${server}?`)) return;
            
            const btn = document.querySelector('.btn-primary, .btn-rocket');
            if (!btn) return;
            const originalText = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';
            btn.disabled = true;
            
            fetch('/send-likes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, server_name: server, key: 'JMLB', count: count })
            })
            .then(res => res.json())
            .then(data => {
                btn.innerHTML = originalText;
                btn.disabled = false;
                if (data.success) {
                    showResult(data);
                    loadData();
                } else {
                    alert('Error: ' + (data.error || 'Unknown error'));
                }
            });
        }
        
        function verifyLikes() {
            const uid = getUid();
            const server = getServer();
            if (!uid) { alert('Enter a UID'); return; }
            
            fetch('/verify-likes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, server_name: server, key: 'JMLB' })
            })
            .then(res => res.json())
            .then(data => {
                document.getElementById('verify-result').innerHTML = `
                    <div style="background:rgba(22,27,34,0.5);padding:14px;border-radius:12px;border:1px solid rgba(43,52,66,0.3);">
                        <div style="color:#00E5FF;font-weight:600;font-size:1em;">UID: ${data.uid}</div>
                        <div style="color:#F8FAFC;font-size:0.9em;">Name: ${data.username}</div>
                        <div style="display:flex;justify-content:space-between;margin-top:4px;font-size:0.85em;color:#A8B3CF;"><span>Total Likes</span><span style="color:#00E676;font-weight:600;">${data.likes}</span></div>
                    </div>
                `;
            });
        }
        
        function addAutoUser() {
            const uid = document.getElementById('target-uid-auto').value.trim();
            const limit = parseInt(document.getElementById('auto-limit').value) || 220;
            if (!uid) { alert('Enter a UID'); return; }
            fetch('/add-auto-user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, limit })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) { alert('Added to queue: ' + uid); loadData(); } else { alert(data.message); }
            });
        }
        
        function deleteUser(uid) {
            if (!confirm(`Remove ${uid} from queue?`)) return;
            fetch('/delete-user', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid }) })
                .then(res => res.json())
                .then(data => { if (data.success) loadData(); else alert(data.message); });
        }
        
        function deleteAllAuto() {
            if (!confirm('Clear entire auto-queue?')) return;
            fetch('/delete-all-users', { method: 'POST' })
                .then(res => res.json())
                .then(data => { if (data.success) loadData(); else alert(data.message); });
        }
        
        function setAutoTime() {
            const hour = parseInt(document.getElementById('set-hour').value);
            const minute = parseInt(document.getElementById('set-minute').value);
            if (isNaN(hour) || isNaN(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
                alert('Enter valid time (Hour: 0-23, Minute: 0-59)');
                return;
            }
            fetch('/set-auto-time', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hour, minute })
            })
            .then(res => res.json())
            .then(data => {
                document.getElementById('time-status').textContent = data.message;
                loadData();
            });
        }
        
        // ============================================
        // INIT
        // ============================================
        loadData();
        setInterval(loadData, 5000);
        setInterval(loadLogs, 5000);
        setInterval(loadHistory, 10000);
    </script>
</body>
</html>
'''

# ============================================================
# ROUTES
# ============================================================
@app.route('/')
def index():
    if session.get('logged_in'):
        return render_template_string(WEBSITE_HTML)
    return render_template_string(LOGIN_HTML)

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    
    if username == 'HexMods' and password == 'ADI444':
        session['logged_in'] = True
        add_activity_log("✅ User HexMods logged in", "success")
        return redirect('/')
    
    return redirect('/?error=1')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    add_activity_log("👋 User logged out", "info")
    return redirect('/')

@app.route('/api/dashboard-data')
def dashboard_data():
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    server = request.args.get('server', 'IND')
    accounts = load_accounts(server)
    if not accounts:
        return jsonify({'error': f'No accounts found for server {server}'})
    
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
            'last_check': status_info.get('last_check'),
            'reset_time': status_info.get('reset_time')
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
        'accounts': account_list,
        'last_auto_run': None,
        'auto_run_status': 'Idle',
        'auto_run_message': '',
        'auto_time': f"{RESET_HOUR:02d}:{RESET_MINUTE:02d}"
    })

@app.route('/api/history')
def get_history():
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'history': like_history[-50:]})

@app.route('/api/stats')
def get_stats():
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    total_likes = sum(len(v) for v in liked_cache.values())
    total_targets = len(liked_cache)
    working = sum(1 for v in account_status.values() if v.get('status') == 'working')
    return jsonify({
        'total_likes_sent': total_likes,
        'total_targets': total_targets,
        'working_accounts': working,
        'auto_users': len(auto_like_users),
        'next_reset': get_next_reset_time().strftime('%Y-%m-%d %H:%M:%S IST')
    })

@app.route('/api/logs')
def get_logs():
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'logs': activity_logs[-50:]})

@app.route('/verify-likes', methods=['POST'])
def verify_likes():
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    uid = data.get('uid', '').strip()
    server_name = data.get('server_name', 'IND').upper()
    key = data.get('key', 'JMLB')
    if key != "JMLB":
        return jsonify({'error': 'Invalid key'})
    if not uid:
        return jsonify({'error': 'UID required'})
    user_info = asyncio.run(get_user_info(uid, server_name))
    if user_info:
        return jsonify({
            'uid': user_info['uid'],
            'username': user_info['name'],
            'likes': user_info['likes']
        })
    return jsonify({'error': 'User not found'})

@app.route('/send-likes', methods=['POST'])
def send_likes():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    uid = data.get('uid', '').strip()
    server_name = data.get('server_name', 'IND').upper()
    key = data.get('key', 'JMLB')
    count = int(data.get('count', 20))
    
    if key != "JMLB":
        return jsonify({'success': False, 'error': 'Invalid key'})
    if not uid:
        return jsonify({'success': False, 'error': 'UID required'})
    
    user_info_before = asyncio.run(get_user_info(uid, server_name))
    if user_info_before:
        before_likes = user_info_before.get('likes', 0)
        before_name = user_info_before.get('name', 'Unknown')
    else:
        before_likes = 0
        before_name = 'Unknown'
    
    base_url = REGION_URLS.get(server_name, 'https://clientbp.ggpolarbear.com')
    like_url = f"{base_url}/LikeProfile"
    
    if count == 20:
        result = asyncio.run(send_likes_with_verified_limit(uid, server_name, like_url, 20, 20))
    else:
        result = asyncio.run(send_likes_with_verified_limit(uid, server_name, like_url, 999999, 0))
    
    likes_sent = result['success']
    
    user_info_after = asyncio.run(get_user_info(uid, server_name))
    if user_info_after:
        username = user_info_after.get('name', 'Unknown')
        current_likes = user_info_after.get('likes', 0)
        update_user_stats(uid, likes_sent, username, current_likes)
        add_to_history(uid, likes_sent, before_likes, current_likes, username, server_name)
        after_likes = current_likes
    else:
        after_likes = before_likes
        username = before_name
    
    if likes_sent > 0 and uid not in auto_like_users:
        auto_like_users.append(uid)
        save_users()
        add_activity_log(f"📌 Added {uid} to auto-queue (from manual like)", "info")
    
    return jsonify({
        'success': likes_sent > 0,
        'likes_sent': likes_sent,
        'username': username,
        'total_likes': after_likes,
        'likes_before': before_likes,
        'verified_added': after_likes - before_likes,
        'failed': result.get('failed', 0),
        'server': server_name,
        'stopped': result.get('stopped', False)
    })

@app.route('/add-auto-user', methods=['POST'])
def add_auto_user():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    uid = data.get('uid', '').strip()
    limit = data.get('limit', 220)
    if not uid:
        return jsonify({'success': False, 'message': 'UID required'})
    if uid in auto_like_users:
        return jsonify({'success': False, 'message': 'UID already in list'})
    auto_like_users.append(uid)
    user_stats[uid] = {'total_likes': 0, 'today_likes': 0, 'last_like': None, 'username': '', 'current_likes': 0}
    save_users()
    global AUTO_LIKE_VERIFIED_LIMIT
    AUTO_LIKE_VERIFIED_LIMIT = limit
    add_activity_log(f"📌 Added {uid} to auto-queue (limit: {limit})", "info")
    return jsonify({'success': True, 'message': f'Added {uid} with verified limit {limit}'})

@app.route('/delete-user', methods=['POST'])
def delete_user():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    uid = data.get('uid', '').strip()
    if uid in auto_like_users:
        auto_like_users.remove(uid)
        if uid in user_stats:
            del user_stats[uid]
        save_users()
        add_activity_log(f"🗑️ Removed {uid} from auto-queue", "info")
        return jsonify({'success': True, 'message': f'Removed {uid}'})
    return jsonify({'success': False, 'message': 'UID not found'})

@app.route('/delete-all-users', methods=['POST'])
def delete_all_users():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    auto_like_users.clear()
    user_stats.clear()
    save_users()
    add_activity_log("🗑️ Cleared entire auto-queue", "info")
    return jsonify({'success': True, 'message': 'All users deleted'})

@app.route('/set-auto-time', methods=['POST'])
def set_auto_time():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    hour = data.get('hour', 4)
    minute = data.get('minute', 2)
    global RESET_HOUR, RESET_MINUTE
    RESET_HOUR = hour
    RESET_MINUTE = minute
    return jsonify({'success': True, 'message': f'Auto-like time set to {hour:02d}:{minute:02d} IST'})

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
    valid_servers = ["IND", "BR", "US", "SAC", "NA", "BD", "RU", "MENA"]
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
    for account in accounts[:3]:
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
        before_name = before_data['AccountInfo'].get('PlayerNickname', 'Unknown')
    except:
        return jsonify({"error": "Data parsing failed", "status": 0}), 200

    base_url = REGION_URLS.get(server_name, 'https://clientbp.ggpolarbear.com')
    like_url = f"{base_url}/LikeProfile"

    limit = requested_likes if requested_likes and requested_likes > 0 else 50
    result = asyncio.run(send_likes_with_verified_limit(uid, server_name, like_url, limit, 0))
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
    except Exception as e:
        return jsonify({"error": str(e), "status": 0}), 500

    if success_count > 0:
        tracker[client_ip][0] += 1
        count += 1

    add_to_history(uid, success_count, before_like, after_like, player_name, server_name)

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
        "server": server_name,
        "next_reset_at": get_next_reset_time().strftime('%Y-%m-%d %H:%M:%S IST')
    })

@app.route('/reset-cache', methods=['GET'])
def reset_cache():
    key = request.args.get("key")
    if key != "JMLB":
        return jsonify({"error": "Invalid key"}), 403
    reset_all_data()
    return jsonify({"message": "All data reset"})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "accounts": len(load_accounts("IND"))})

# ============================================================
# STARTUP
# ============================================================
load_liked_data()
load_account_status()
load_users()

reset_thread = threading.Thread(target=daily_reset_task, daemon=True)
reset_thread.start()

auto_thread = threading.Thread(target=start_auto_like, daemon=True)
auto_thread.start()

threading.Thread(target=run_status_check).start()

add_activity_log("🚀 HEX CHEATS System Started", "info")
add_activity_log(f"📁 Accounts: {len(load_accounts('IND'))} (IND)", "info")
add_activity_log(f"📌 Auto-queue: {len(auto_like_users)} users", "info")
add_activity_log("⏰ Auto-reset at 4:02 AM IST", "info")

print("✅ HEX CHEATS – Final Version Started")
print(f"📁 Accounts: {len(load_accounts('IND'))} (IND)")
print("🔐 Login: HexMods / ADI444")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)