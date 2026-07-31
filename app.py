from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
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
app.secret_key = 'your-secret-key-here'

TOKEN_CACHE = {}
KEY_LIMIT = 500
tracker = defaultdict(lambda: [0, time.time()])

LIKED_DATA_FILE = "liked_data.pkl"
liked_cache = defaultdict(set)
like_timestamps = {}

ACCOUNT_STATUS_FILE = "account_status.pkl"
account_status = {}

USERS_FILE = "users.pkl"
auto_queue = []          # list of UIDs to auto-like daily
user_stats = {}          # uid -> {total, today, last, username, current, auto_sent}

RESET_HOUR = 4
RESET_MINUTE = 0
RESET_SECOND = 0

RATE_LIMIT_DELAYS = [0.02, 0.05, 0.08, 0.1, 0.15, 0.2]

# Extra: last auto-like run info
last_auto_run = None
auto_run_status = "Idle"
auto_run_message = ""

def load_users():
    global auto_queue, user_stats
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'rb') as f:
                data = pickle.load(f)
                if isinstance(data, dict):
                    auto_queue = data.get('users', [])
                    user_stats = data.get('stats', {})
                else:
                    auto_queue = data
                    user_stats = {}
                print(f"Loaded auto queue: {len(auto_queue)} UIDs")
        else:
            auto_queue = []
            user_stats = {}
            save_users()
    except Exception as e:
        print(f"Error loading users: {e}")
        auto_queue = []
        user_stats = {}

def save_users():
    try:
        data = {'users': auto_queue, 'stats': user_stats}
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
                                  'username': '', 'current_likes': 0, 'auto_sent': 0}
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
        user_stats[uid]['auto_sent'] = 0
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

async def send_like_ultra_fast(encrypted_uid, token, url, account_uid):
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
                    return False, "limit"
                else:
                    return False, "failed"
    except:
        return False, "error"

async def send_likes_ultra_fast(target_uid, server_name, url, limit):
    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0}
    fresh_accounts = []
    skipped_24hr = 0
    for acc in accounts:
        if is_uid_liked_in_24hrs(target_uid, acc['uid']):
            skipped_24hr += 1
        else:
            fresh_accounts.append(acc)
    if not fresh_accounts:
        return {'success': 0, 'failed': 0, 'total': len(accounts), 'skipped': skipped_24hr}
    accounts_to_use = fresh_accounts[:min(limit, len(fresh_accounts))]
    protobuf_message = create_protobuf_message(target_uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)
    tasks = []
    for acc in accounts_to_use:
        tasks.append(send_single_ultra_fast(target_uid, encrypted_uid, acc, url))
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
        user_info = await get_user_info(target_uid, server_name)
        username = user_info.get('name', '') if user_info else ''
        current_likes = user_info.get('likes', 0) if user_info else 0
        update_user_stats(target_uid, successful, username, current_likes)
    return {
        'success': successful,
        'failed': failed,
        'total': len(accounts),
        'accounts_used': len(accounts_to_use),
        'skipped': skipped_24hr
    }

async def send_single_ultra_fast(target_uid, encrypted_uid, account, url):
    try:
        token = await get_valid_token(account['uid'], account['password'])
        if not token:
            return {'status': 'failed', 'uid': account['uid']}
        success, _ = await send_like_ultra_fast(encrypted_uid, token, url, account['uid'])
        if success:
            return {'status': 'success', 'uid': account['uid']}
        else:
            return {'status': 'failed', 'uid': account['uid']}
    except:
        return {'status': 'failed', 'uid': account['uid']}

async def check_all_accounts_ultra_fast():
    accounts = load_accounts("IND")
    if not accounts:
        return
    tasks = []
    for acc in accounts[:100]:
        tasks.append(check_single_account(acc))
    await asyncio.gather(*tasks, return_exceptions=True)

async def check_single_account(account):
    try:
        token = await get_valid_token(account['uid'], account['password'])
        if token:
            account_status[account['uid']] = {
                'status': 'working',
                'last_check': datetime.now().isoformat()
            }
        else:
            account_status[account['uid']] = {
                'status': 'unknown',
                'last_check': datetime.now().isoformat()
            }
        save_account_status()
    except:
        pass

def run_ultra_fast_check():
    asyncio.run(check_all_accounts_ultra_fast())

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

# LOGIN PAGE
LOGIN_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0e1a; color: #fff; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .login-box { background: #141928; padding: 40px 30px; border-radius: 16px; border: 1px solid #1e2a4a; max-width: 400px; width: 90%; position: relative; overflow: hidden; }
        .login-box::before { content: ''; position: absolute; top: -2px; left: -2px; right: -2px; bottom: -2px; background: linear-gradient(45deg, #ff1744, transparent, #ff1744, transparent); background-size: 300% 300%; animation: borderGlow 2s ease infinite; border-radius: 16px; z-index: -1; }
        @keyframes borderGlow { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
        .login-box h1 { color: #ff1744; font-size: 2em; text-align: center; margin-bottom: 10px; }
        .login-box p { color: #8899bb; text-align: center; margin-bottom: 30px; font-size: 0.95em; }
        .login-box input { width: 100%; padding: 12px 15px; border-radius: 8px; border: 1px solid #1e2a4a; background: #0a0e1a; color: #fff; font-size: 1em; margin-bottom: 15px; }
        .login-box input:focus { outline: none; border-color: #ff1744; }
        .login-box .login-btn { width: 100%; padding: 14px; border: none; border-radius: 8px; background: linear-gradient(135deg, #ff1744, #d50000); color: #fff; font-size: 1.1em; font-weight: bold; cursor: pointer; transition: 0.3s; }
        .login-box .login-btn:hover { transform: scale(1.02); box-shadow: 0 0 30px rgba(255, 23, 68, 0.3); }
        .login-error { color: #ff1744; text-align: center; margin-top: 15px; display: none; }
        .icon { font-size: 3em; text-align: center; margin-bottom: 10px; }
    </style>
</head>
<body>
    <div class="login-box">
        <div class="icon">&#9888;</div>
        <h1>Auto-Like System</h1>
        <p>Enter credentials to access the dashboard</p>
        <form method="POST" action="/login">
            <input type="text" name="username" placeholder="Username" value="admin" required />
            <input type="password" name="password" placeholder="Password" value="admin123" required />
            <button type="submit" class="login-btn">&#128274; Login</button>
        </form>
        <div class="login-error" id="login-error">Invalid credentials!</div>
    </div>
</body>
</html>
'''

# DASHBOARD HTML - NEW USER-FRIENDLY MOBILE VERSION
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>Auto-Like Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0e1a; color: #fff; min-height: 100vh; padding-bottom: 30px; }
        .container { max-width: 1200px; margin: 0 auto; padding: 15px; }

        /* Header */
        .header { background: linear-gradient(135deg, #1a237e, #283593); padding: 20px; border-radius: 15px; margin-bottom: 20px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; }
        .header h1 { font-size: 1.8em; }
        .header .sub { opacity: 0.8; font-size: 0.85em; margin-top: 3px; }
        .header-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; width: 100%; }
        .header-actions .btn { padding: 8px 14px; font-size: 0.85em; }
        .badge-auto { background: #4caf5022; color: #4caf50; padding: 4px 12px; border-radius: 20px; border: 1px solid #4caf50; font-size: 0.85em; }
        .badge-reset { color: #ffc107; font-weight: bold; }

        /* Buttons */
        .btn { padding: 10px 18px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; font-size: 0.9em; transition: 0.3s; display: inline-flex; align-items: center; gap: 6px; text-decoration: none; }
        .btn-refresh { background: #1a237e; color: #fff; }
        .btn-refresh:hover { background: #283593; }
        .btn-check { background: #ff6f00; color: #fff; }
        .btn-check:hover { background: #e65100; }
        .btn-add { background: #4caf50; color: #fff; }
        .btn-add:hover { background: #388e3c; }
        .btn-del { background: #f44336; color: #fff; }
        .btn-del:hover { background: #c62828; }
        .btn-like { background: #ff6f00; color: #fff; }
        .btn-like:hover { background: #e65100; }
        .btn-like20 { background: #0d47a1; color: #fff; }
        .btn-like20:hover { background: #1565c0; }
        .btn-like220 { background: #bf360c; color: #fff; }
        .btn-like220:hover { background: #d84315; }
        .btn-logout { background: #1a2240; color: #fff; }
        .btn-logout:hover { background: #2a3a5a; }
        .btn-auto-run { background: #4caf50; color: #fff; }
        .btn-auto-run:hover { background: #2e7d32; }
        .btn-auto-run:disabled { opacity: 0.5; cursor: not-allowed; }

        /* Status Grid */
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .status-card { background: #141928; padding: 15px 10px; border-radius: 12px; text-align: center; border: 1px solid #1e2a4a; }
        .status-card .num { font-size: 2em; font-weight: bold; }
        .status-card .lbl { color: #8899bb; font-size: 0.8em; margin-top: 4px; }

        /* Panel */
        .panel { background: #141928; padding: 18px; border-radius: 12px; border: 1px solid #1e2a4a; margin-bottom: 20px; }
        .panel h2 { color: #8899bb; font-size: 1.1em; margin-bottom: 12px; }
        .input-group { display: flex; flex-wrap: wrap; gap: 8px; }
        .input-group input { flex: 1 1 200px; padding: 12px 15px; border-radius: 8px; border: 1px solid #1e2a4a; background: #0a0e1a; color: #fff; font-size: 1em; min-width: 150px; }
        .input-group input:focus { outline: none; border-color: #4caf50; }
        .btn-group { display: flex; flex-wrap: wrap; gap: 6px; }

        /* Queue list */
        .user-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
        .user-item { background: #1a2240; padding: 8px 14px; border-radius: 20px; display: flex; align-items: center; gap: 10px; border: 1px solid #2a3a5a; flex-wrap: wrap; font-size: 0.9em; }
        .user-item .uid { font-weight: bold; color: #42a5f5; }
        .user-item .stats { color: #8899bb; font-size: 0.8em; }
        .user-item .stats span { color: #4caf50; font-weight: bold; }
        .user-item .del-btn { background: none; border: none; color: #f44336; cursor: pointer; font-size: 1.2em; padding: 0 5px; }

        /* Tables */
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; background: #141928; border-radius: 12px; overflow: hidden; margin-top: 12px; font-size: 0.9em; }
        th { background: #1e2a4a; padding: 10px 12px; text-align: left; font-weight: 600; color: #8899bb; white-space: nowrap; }
        td { padding: 10px 12px; border-bottom: 1px solid #1a2240; }
        .badge { padding: 3px 10px; border-radius: 20px; font-size: 0.75em; font-weight: bold; display: inline-block; }
        .badge-working { background: #4caf5022; color: #4caf50; border: 1px solid #4caf50; }
        .badge-timeout { background: #f4433622; color: #f44336; border: 1px solid #f44336; }
        .badge-reset { background: #ffc10722; color: #ffc107; border: 1px solid #ffc107; }
        .badge-unknown { background: #8899bb22; color: #8899bb; border: 1px solid #8899bb; }

        /* User stats cards */
        .user-stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin-top: 12px; }
        .user-stat-card { background: #1a2240; padding: 14px; border-radius: 10px; border: 1px solid #2a3a5a; }
        .user-stat-card .uid { color: #42a5f5; font-weight: bold; font-size: 1em; }
        .user-stat-card .name { color: #fff; font-size: 0.9em; }
        .user-stat-card .row { display: flex; justify-content: space-between; margin-top: 4px; font-size: 0.85em; color: #8899bb; }
        .user-stat-card .row .val { color: #4caf50; font-weight: bold; }
        .user-stat-card .last { font-size: 0.75em; color: #666; margin-top: 5px; }

        /* Logs */
        .log-area { background: #0a0e1a; padding: 12px; border-radius: 12px; max-height: 200px; overflow-y: auto; font-family: monospace; font-size: 0.8em; border: 1px solid #1e2a4a; margin-top: 12px; }
        .log-entry { padding: 3px 0; border-bottom: 1px solid #141928; }
        .log-time { color: #42a5f5; }
        .log-success { color: #4caf50; }
        .log-error { color: #f44336; }
        .log-info { color: #ffc107; }

        .section-title { font-size: 1.2em; color: #fff; margin: 20px 0 10px; display: flex; align-items: center; gap: 10px; }
        .live-dot { display: inline-block; width: 10px; height: 10px; background: #4caf50; border-radius: 50%; animation: pulse 1s infinite; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
        .note { color: #8899bb; font-size: 0.85em; margin-top: 8px; }

        .status-row { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 15px; align-items: center; }
        .status-row .item { background: #1a2240; padding: 6px 15px; border-radius: 20px; font-size: 0.9em; border: 1px solid #2a3a5a; }

        @media (max-width: 600px) {
            .header h1 { font-size: 1.5em; }
            .btn { font-size: 0.8em; padding: 8px 12px; }
            .status-grid { grid-template-columns: repeat(3, 1fr); }
            .user-item { width: 100%; }
            .input-group input { min-width: 120px; }
            .header-actions { flex-direction: column; align-items: stretch; }
            .header-actions .btn { width: 100%; justify-content: center; }
            .status-row { flex-direction: column; align-items: stretch; }
        }
        @media (max-width: 400px) {
            .status-grid { grid-template-columns: 1fr 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <div>
                <h1>&#9889; Auto-Like Dashboard</h1>
                <div class="sub">Real-time monitoring · Auto-reset daily at 4:00 AM IST</div>
            </div>
            <div class="header-actions">
                <span class="badge-auto">&#9654; Auto-Like Running</span>
                <span>Reset: <span class="badge-reset" id="next-reset">Loading...</span></span>
                <button class="btn btn-refresh" onclick="location.reload()">&#8635;</button>
                <button class="btn btn-check" onclick="checkStatus()">&#128270;</button>
                <button class="btn btn-auto-run" onclick="forceAutoRun()" id="forceAutoBtn">&#9655; Run Auto</button>
                <a href="/logout"><button class="btn btn-logout">&#128682;</button></a>
            </div>
        </div>

        <!-- Status Row -->
        <div class="status-row">
            <div class="item">Last Auto-Run: <span id="lastAutoRun">Never</span></div>
            <div class="item">Status: <span id="autoRunStatus">Idle</span></div>
            <div class="item">Message: <span id="autoRunMessage">-</span></div>
        </div>

        <!-- Stats Cards -->
        <div class="status-grid">
            <div class="status-card"><div class="num blue" id="total-accounts">0</div><div class="lbl">&#128203; Accounts</div></div>
            <div class="status-card"><div class="num green" id="working-count">0</div><div class="lbl">&#9989; Working</div></div>
            <div class="status-card"><div class="num red" id="timeout-count">0</div><div class="lbl">&#9888; Limit</div></div>
            <div class="status-card"><div class="num purple" id="total-likes">0</div><div class="lbl">&#10084; Likes</div></div>
            <div class="status-card"><div class="num yellow" id="targets-liked">0</div><div class="lbl">&#128101; Targets</div></div>
            <div class="status-card"><div class="num cyan" id="auto-users">0</div><div class="lbl">&#128100; Queue</div></div>
        </div>

        <!-- Send Likes Panel -->
        <div class="panel">
            <h2>&#9889; Send Likes</h2>
            <div class="input-group">
                <input type="number" id="target-uid" placeholder="Enter Free Fire UID" />
                <div class="btn-group">
                    <button class="btn btn-like20" onclick="sendLikes(20)">20</button>
                    <button class="btn btn-like220" onclick="sendLikes(220)">220</button>
                    <button class="btn btn-like" onclick="sendLikes(492)">All</button>
                </div>
            </div>
            <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:8px;">
                <button class="btn btn-add" onclick="addAutoUser()">&#43; Add to Queue</button>
                <button class="btn btn-del" onclick="deleteAllAuto()">&#10007; Clear Queue</button>
            </div>
            <div class="user-list" id="auto-user-list"></div>
            <div class="note">&#9432; Enter UID and click like count. Successful likes automatically add to auto-queue.</div>
        </div>

        <!-- Account Status Table -->
        <div class="section-title">&#128202; Account Status <span class="live-dot"></span></div>
        <div class="table-wrap">
            <table>
                <thead><tr><th>UID</th><th>Status</th><th>Last Check</th><th>Reset Time</th><th>Last Error</th></tr></thead>
                <tbody id="account-table"></tbody>
            </table>
        </div>

        <!-- Auto-Queue Users Stats -->
        <div class="section-title">&#128202; Auto-Queue Users</div>
        <div class="user-stats-grid" id="auto-queue-stats"></div>

        <!-- Logs -->
        <div class="section-title">&#128202; Activity Log</div>
        <div class="log-area" id="log-area">
            <div class="log-entry"><span class="log-info">System ready.</span></div>
        </div>
    </div>

    <script>
        // Helper to format time
        function formatTime(iso) {
            if (!iso) return 'Never';
            try {
                const d = new Date(iso);
                return d.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
            } catch { return iso; }
        }

        function loadData() {
            fetch('/api/dashboard-data')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    document.getElementById('total-accounts').textContent = data.total_accounts || 0;
                    document.getElementById('working-count').textContent = data.working_count || 0;
                    document.getElementById('timeout-count').textContent = data.timeout_count || 0;
                    document.getElementById('total-likes').textContent = data.total_likes || 0;
                    document.getElementById('targets-liked').textContent = data.targets_liked || 0;
                    document.getElementById('auto-users').textContent = data.auto_users || 0;
                    document.getElementById('next-reset').textContent = data.next_reset || 'Loading...';
                    document.getElementById('lastAutoRun').textContent = data.last_auto_run ? formatTime(data.last_auto_run) : 'Never';
                    document.getElementById('autoRunStatus').textContent = data.auto_run_status || 'Idle';
                    document.getElementById('autoRunMessage').textContent = data.auto_run_message || '-';

                    // Auto-queue list
                    var userHtml = '';
                    if (data.auto_queue && data.auto_queue.length > 0) {
                        data.auto_queue.forEach(function(uid) {
                            var s = data.user_stats[uid] || { total_likes: 0, today_likes: 0, auto_sent: 0 };
                            userHtml += '<div class="user-item">' +
                                '<span class="uid">' + uid + '</span>' +
                                '<span class="stats">T:<span>' + (s.total_likes||0) + '</span> D:<span>' + (s.today_likes||0) + '</span> A:<span>' + (s.auto_sent||0) + '</span></span>' +
                                '<button class="del-btn" onclick="removeAutoUser(\'' + uid + '\')">&#10005;</button>' +
                            '</div>';
                        });
                    } else {
                        userHtml = '<div class="note">No users in auto-queue</div>';
                    }
                    document.getElementById('auto-user-list').innerHTML = userHtml;

                    // Account table
                    var tableHtml = '';
                    if (data.accounts && data.accounts.length > 0) {
                        data.accounts.forEach(function(acc) {
                            var cls = acc.status === 'working' ? 'working' : acc.status === 'timeout' ? 'timeout' : 'unknown';
                            tableHtml += '<tr>' +
                                '<td><strong>' + acc.uid + '</strong></td>' +
                                '<td><span class="badge badge-' + cls + '">' + acc.status + '</span></td>' +
                                '<td>' + (acc.last_check ? formatTime(acc.last_check) : 'Never') + '</td>' +
                                '<td>' + (acc.reset_time ? formatTime(acc.reset_time) : 'N/A') + '</td>' +
                                '<td>' + (acc.last_error || 'None') + '</td>' +
                            '</tr>';
                        });
                    } else {
                        tableHtml = '<tr><td colspan="5">No accounts loaded</td></tr>';
                    }
                    document.getElementById('account-table').innerHTML = tableHtml;

                    // Auto-queue stats
                    var statsHtml = '';
                    if (data.auto_queue && data.auto_queue.length > 0) {
                        data.auto_queue.forEach(function(uid) {
                            var s = data.user_stats[uid] || { total_likes: 0, today_likes: 0, last_like: null, username: '', current_likes: 0, auto_sent: 0 };
                            statsHtml += '<div class="user-stat-card">' +
                                '<div class="uid">UID: ' + uid + '</div>' +
                                '<div class="name">' + (s.username || 'Unknown') + '</div>' +
                                '<div class="row"><span>Total</span><span class="val">' + (s.total_likes||0) + '</span></div>' +
                                '<div class="row"><span>Today</span><span class="val">' + (s.today_likes||0) + '</span></div>' +
                                '<div class="row"><span>Auto Sent</span><span class="val">' + (s.auto_sent||0) + '</span></div>' +
                                '<div class="last">Last: ' + (s.last_like ? formatTime(s.last_like) : 'Never') + '</div>' +
                            '</div>';
                        });
                    } else {
                        statsHtml = '<div class="note">No auto-queue users</div>';
                    }
                    document.getElementById('auto-queue-stats').innerHTML = statsHtml;

                    // Logs
                    if (data.logs && data.logs.length > 0) {
                        var logHtml = '';
                        data.logs.forEach(function(log) {
                            logHtml += '<div class="log-entry">' +
                                '<span class="log-time">[' + log.time + ']</span> ' +
                                '<span class="log-' + log.type + '">' + log.message + '</span>' +
                            '</div>';
                        });
                        document.getElementById('log-area').innerHTML = logHtml;
                    }
                });
        }

        function checkStatus() {
            fetch('/api/check-status')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    console.log('Status check started');
                    setTimeout(loadData, 3000);
                });
        }

        function sendLikes(count) {
            var uid = document.getElementById('target-uid').value.trim();
            if (!uid) { alert('Enter a UID'); return; }
            if (!confirm('Send ' + count + ' likes to ' + uid + '?')) return;
            
            var btn = document.querySelector('.btn-like, .btn-like20, .btn-like220');
            btn.textContent = '⏳';
            btn.disabled = true;
            
            fetch('/send-likes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: uid, server_name: 'IND', key: 'JMLB', count: count })
            })
            .then(function(res) { return res.json(); })
            .then(function(data) {
                btn.textContent = '✔';
                btn.disabled = false;
                if (data.success) {
                    alert('✅ Sent ' + data.likes_sent + ' likes to ' + (data.username || uid) + '\nTotal: ' + data.total_likes);
                } else {
                    alert('❌ Error: ' + (data.error || 'Unknown error'));
                }
                loadData();
            });
        }

        function addAutoUser() {
            var uid = document.getElementById('target-uid').value.trim();
            if (!uid) { alert('Enter a UID'); return; }
            fetch('/add-auto-user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: uid })
            })
            .then(function(res) { return res.json(); })
            .then(function(data) {
                if (data.success) {
                    alert('Added to queue: ' + uid);
                    loadData();
                } else {
                    alert(data.message);
                }
            });
        }

        function removeAutoUser(uid) {
            if (!confirm('Remove ' + uid + ' from queue?')) return;
            fetch('/remove-auto-user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: uid })
            })
            .then(function(res) { return res.json(); })
            .then(function(data) {
                if (data.success) loadData();
                else alert(data.message);
            });
        }

        function deleteAllAuto() {
            if (!confirm('Clear entire auto-queue?')) return;
            fetch('/clear-auto-queue', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            })
            .then(function(res) { return res.json(); })
            .then(function(data) {
                if (data.success) loadData();
                else alert(data.message);
            });
        }

        function forceAutoRun() {
            var btn = document.getElementById('forceAutoBtn');
            btn.textContent = '⏳';
            btn.disabled = true;
            fetch('/force-auto-run', { method: 'POST' })
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    btn.textContent = '&#9655; Run Auto';
                    btn.disabled = false;
                    if (data.success) {
                        alert('Auto-run triggered! Check logs for progress.');
                    } else {
                        alert('Error: ' + (data.error || 'Unknown'));
                    }
                    loadData();
                });
        }

        loadData();
        setInterval(loadData, 3000);
        setInterval(checkStatus, 10000);
    </script>
</body>
</html>
'''

# Routes
@app.route('/')
def index():
    if session.get('logged_in'):
        return render_template_string(DASHBOARD_HTML)
    return render_template_string(LOGIN_HTML)

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    if username == 'admin' and password == 'admin123':
        session['logged_in'] = True
        return redirect('/')
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/')

@app.route('/api/dashboard-data')
def dashboard_data():
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
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
            'last_check': status_info.get('last_check'),
            'reset_time': status_info.get('reset_time'),
            'last_error': status_info.get('last_error')
        })
    total_likes = sum(len(v) for v in liked_cache.values())
    targets_liked = len(liked_cache)
    next_reset = get_next_reset_time().strftime('%Y-%m-%d %H:%M:%S IST')
    global last_auto_run, auto_run_status, auto_run_message
    logs = []
    try:
        with open('logs.txt', 'r') as f:
            lines = f.readlines()[-50:]
            for line in lines:
                parts = line.strip().split('|')
                if len(parts) == 3:
                    logs.append({'time': parts[0], 'type': parts[1], 'message': parts[2]})
    except:
        pass
    return jsonify({
        'total_accounts': total,
        'working_count': working_count,
        'timeout_count': timeout_count,
        'total_likes': total_likes,
        'targets_liked': targets_liked,
        'auto_users': len(auto_queue),
        'next_reset': next_reset,
        'auto_queue': auto_queue,
        'user_stats': user_stats,
        'accounts': account_list,
        'logs': logs,
        'last_auto_run': last_auto_run,
        'auto_run_status': auto_run_status,
        'auto_run_message': auto_run_message
    })

@app.route('/api/check-status')
def check_status_api():
    threading.Thread(target=run_ultra_fast_check).start()
    return jsonify({'message': 'Status check started'})

@app.route('/send-likes', methods=['POST'])
def send_likes_manual():
    data = request.get_json()
    uid = data.get('uid', '').strip()
    server_name = data.get('server_name', 'IND').upper()
    key = data.get('key', 'JMLB')
    count = int(data.get('count', 20))
    if key != "JMLB":
        return jsonify({'success': False, 'error': 'Invalid key'})
    if not uid:
        return jsonify({'success': False, 'error': 'UID required'})
    if server_name == "IND":
        like_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        like_url = "https://client.us.freefiremobile.com/LikeProfile"
    else:
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"
    result = asyncio.run(send_likes_ultra_fast(uid, server_name, like_url, count))
    if result['success'] > 0 and uid not in auto_queue:
        auto_queue.append(uid)
        save_users()
        log_message(f"Added {uid} to auto-queue (manual like)", "success")
    user_info = asyncio.run(get_user_info(uid, server_name))
    if user_info:
        username = user_info.get('name', 'Unknown')
        current_likes = user_info.get('likes', 0)
        update_user_stats(uid, result['success'], username, current_likes)
    else:
        update_user_stats(uid, result['success'])
    return jsonify({
        'success': result['success'] > 0,
        'likes_sent': result['success'],
        'username': user_info.get('name', 'Unknown') if user_info else 'Unknown',
        'total_likes': user_info.get('likes', 0) if user_info else 0,
        'skipped': result.get('skipped', 0),
        'failed': result.get('failed', 0),
        'accounts_used': result.get('accounts_used', 0)
    })

@app.route('/add-auto-user', methods=['POST'])
def add_auto_user():
    data = request.get_json()
    uid = data.get('uid', '').strip()
    if not uid:
        return jsonify({'success': False, 'message': 'UID required'})
    if uid in auto_queue:
        return jsonify({'success': False, 'message': 'UID already in queue'})
    auto_queue.append(uid)
    save_users()
    log_message(f"Added {uid} to auto-queue manually", "info")
    return jsonify({'success': True, 'message': f'Added {uid}'})

@app.route('/remove-auto-user', methods=['POST'])
def remove_auto_user():
    data = request.get_json()
    uid = data.get('uid', '').strip()
    if uid in auto_queue:
        auto_queue.remove(uid)
        save_users()
        log_message(f"Removed {uid} from auto-queue", "info")
        return jsonify({'success': True, 'message': f'Removed {uid}'})
    return jsonify({'success': False, 'message': 'UID not in queue'})

@app.route('/clear-auto-queue', methods=['POST'])
def clear_auto_queue():
    auto_queue.clear()
    save_users()
    log_message("Cleared auto-queue", "info")
    return jsonify({'success': True, 'message': 'Queue cleared'})

@app.route('/force-auto-run', methods=['POST'])
def force_auto_run():
    global last_auto_run, auto_run_status, auto_run_message
    # Run auto-like in background
    def run_auto():
        global last_auto_run, auto_run_status, auto_run_message
        try:
            auto_run_status = "Running"
            auto_run_message = "Starting auto-like..."
            log_message("Manual auto-run triggered", "info")
            asyncio.run(auto_like_once())
            last_auto_run = datetime.now().isoformat()
            auto_run_status = "Completed"
            auto_run_message = "Auto-like cycle finished"
            log_message("Manual auto-run completed", "success")
        except Exception as e:
            auto_run_status = "Error"
            auto_run_message = str(e)
            log_message(f"Manual auto-run error: {e}", "error")
    threading.Thread(target=run_auto).start()
    return jsonify({'success': True, 'message': 'Auto-run started'})

async def auto_like_once():
    # Single auto-like cycle (no infinite loop)
    log_message("Starting auto-like cycle", "info")
    accounts = load_accounts("IND")
    if not accounts:
        log_message("No accounts for auto-like", "error")
        return
    for user_uid in auto_queue:
        log_message(f"Processing auto-like for {user_uid}", "info")
        result = await send_likes_ultra_fast(
            user_uid,
            "IND",
            "https://client.ind.freefiremobile.com/LikeProfile",
            220
        )
        if user_uid in user_stats:
            user_stats[user_uid]['auto_sent'] = result['success']
        else:
            user_stats[user_uid] = {'auto_sent': result['success']}
        save_users()
        log_message(f"Sent {result['success']} likes to {user_uid}", "success")
        await asyncio.sleep(3)
    log_message("Auto-like cycle complete", "success")

def log_message(message, log_type="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    with open('logs.txt', 'a') as f:
        f.write(f"{timestamp}|{log_type}|{message}\n")
    print(f"[{timestamp}] [{log_type.upper()}] {message}")

async def auto_like_daily():
    global last_auto_run, auto_run_status, auto_run_message
    log_message("Auto-like scheduler started", "info")
    while True:
        try:
            now = datetime.now()
            target_time = now.replace(hour=4, minute=0, second=0, microsecond=0)
            if now.hour >= 4:
                target_time = target_time + timedelta(days=1)
            wait_seconds = (target_time - now).total_seconds()
            if wait_seconds > 0:
                log_message(f"Next auto-like at {target_time.strftime('%Y-%m-%d %H:%M:%S')} IST", "info")
                await asyncio.sleep(wait_seconds)
            log_message(f"Daily auto-like starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST", "info")
            await auto_like_once()
            last_auto_run = datetime.now().isoformat()
            auto_run_status = "Completed"
            auto_run_message = "Daily auto-like finished"
            log_message("Daily auto-like cycle complete", "success")
        except Exception as e:
            log_message(f"Auto-like error: {str(e)}", "error")
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

threading.Thread(target=run_ultra_fast_check).start()

log_message("System started - User-Friendly UI", "info")
log_message(f"Accounts: {len(load_accounts('IND'))}", "info")
log_message(f"Auto-queue: {len(auto_queue)}", "info")
log_message("Auto-reset at 4:00 AM IST", "info")

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)