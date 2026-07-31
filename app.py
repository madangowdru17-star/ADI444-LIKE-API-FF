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
user_stats = {}

RESET_HOUR = 4
RESET_MINUTE = 0
RESET_SECOND = 0

RATE_LIMIT_DELAYS = [0.05, 0.1, 0.15, 0.2, 0.3, 0.5]

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
    for acc in accounts[:50]:
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

WEBSITE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auto-Like System</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0e1a; color: #ffffff; min-height: 100vh; }
        
        #ddos-overlay {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: #0a0e1a;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 99998;
        }
        #ddos-overlay.hidden { opacity: 0; pointer-events: none; }
        
        .ddos-box {
            background: #141928;
            padding: 50px;
            border-radius: 16px;
            text-align: center;
            border: 1px solid #1e2a4a;
            max-width: 450px;
            width: 90%;
            position: relative;
            overflow: hidden;
        }
        .ddos-box::before {
            content: '';
            position: absolute;
            top: -2px; left: -2px; right: -2px; bottom: -2px;
            background: linear-gradient(45deg, #ff1744, transparent, #ff1744, transparent);
            background-size: 300% 300%;
            animation: borderGlow 2s ease infinite;
            border-radius: 16px;
            z-index: -1;
        }
        @keyframes borderGlow { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
        
        .ddos-icon { font-size: 3.5em; color: #ff1744; margin-bottom: 15px; }
        .ddos-box h2 { color: #ff1744; font-size: 1.8em; margin-bottom: 8px; }
        .ddos-box p { color: #8899bb; margin-bottom: 25px; font-size: 0.95em; }
        
        .verify-btn {
            background: linear-gradient(135deg, #ff1744, #d50000);
            color: #fff;
            border: none;
            padding: 14px 45px;
            border-radius: 30px;
            font-size: 1.05em;
            cursor: pointer;
            transition: 0.3s;
            font-weight: bold;
            letter-spacing: 1px;
        }
        .verify-btn:hover { transform: scale(1.05); box-shadow: 0 0 40px rgba(255, 23, 68, 0.3); }
        .verify-btn:active { transform: scale(0.95); }
        
        .admin-section {
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #1a2240;
        }
        .admin-section input {
            background: #0a0e1a;
            border: 1px solid #1e2a4a;
            color: #fff;
            padding: 8px 15px;
            border-radius: 6px;
            margin: 5px;
            width: 150px;
        }
        .admin-section input:focus { outline: none; border-color: #ff1744; }
        .admin-btn {
            background: #1a2240;
            color: #fff;
            border: none;
            padding: 8px 20px;
            border-radius: 6px;
            cursor: pointer;
        }
        .admin-btn:hover { background: #2a3a5a; }
        .admin-error { color: #ff1744; font-size: 0.85em; margin-top: 10px; display: none; }
        
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; display: none; }
        .container.visible { display: block; }
        
        .header {
            background: linear-gradient(135deg, #1a237e, #283593);
            padding: 25px;
            border-radius: 15px;
            margin-bottom: 25px;
        }
        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
        }
        .header h1 { font-size: 2.2em; }
        .header .sub { opacity: 0.8; margin-top: 5px; font-size: 0.95em; }
        
        .badge-auto {
            background: #4caf5022;
            color: #4caf50;
            padding: 6px 18px;
            border-radius: 20px;
            border: 1px solid #4caf50;
            display: inline-block;
            font-size: 0.9em;
        }
        .badge-reset { color: #ffc107; font-weight: bold; }
        
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            font-size: 0.9em;
            transition: 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
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
        
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 25px;
        }
        .status-card {
            background: #141928;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
            border: 1px solid #1e2a4a;
            transition: 0.3s;
        }
        .status-card:hover { border-color: #4caf50; }
        .status-card .num { font-size: 2.5em; font-weight: bold; }
        .status-card .lbl { color: #8899bb; font-size: 0.85em; margin-top: 5px; }
        .green { color: #4caf50; }
        .red { color: #f44336; }
        .yellow { color: #ffc107; }
        .blue { color: #42a5f5; }
        .purple { color: #ab47bc; }
        .cyan { color: #26c6da; }
        
        .panel {
            background: #141928;
            padding: 20px;
            border-radius: 12px;
            border: 1px solid #1e2a4a;
            margin-bottom: 25px;
        }
        .panel h2 { color: #8899bb; font-size: 1.1em; margin-bottom: 15px; }
        
        .input-group {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .input-group input {
            flex: 1;
            min-width: 200px;
            padding: 12px 15px;
            border-radius: 8px;
            border: 1px solid #1e2a4a;
            background: #0a0e1a;
            color: #fff;
            font-size: 1em;
        }
        .input-group input:focus { outline: none; border-color: #4caf50; }
        
        .user-list {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 15px;
        }
        .user-item {
            background: #1a2240;
            padding: 10px 15px;
            border-radius: 20px;
            display: flex;
            align-items: center;
            gap: 15px;
            border: 1px solid #2a3a5a;
            flex-wrap: wrap;
        }
        .user-item .uid { font-weight: bold; color: #42a5f5; }
        .user-item .stats { font-size: 0.8em; color: #8899bb; }
        .user-item .stats span { color: #4caf50; font-weight: bold; }
        .user-item .del-btn {
            background: none;
            border: none;
            color: #f44336;
            cursor: pointer;
            font-size: 1.2em;
            padding: 0 5px;
        }
        
        .table-wrap { overflow-x: auto; }
        table {
            width: 100%;
            border-collapse: collapse;
            background: #141928;
            border-radius: 12px;
            overflow: hidden;
            margin-top: 15px;
        }
        th {
            background: #1e2a4a;
            padding: 12px 15px;
            text-align: left;
            font-weight: 600;
            color: #8899bb;
            white-space: nowrap;
        }
        td {
            padding: 12px 15px;
            border-bottom: 1px solid #1a2240;
            font-size: 0.9em;
        }
        
        .badge {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.75em;
            font-weight: bold;
            display: inline-block;
        }
        .badge-working { background: #4caf5022; color: #4caf50; border: 1px solid #4caf50; }
        .badge-timeout { background: #f4433622; color: #f44336; border: 1px solid #f44336; }
        .badge-reset { background: #ffc10722; color: #ffc107; border: 1px solid #ffc107; }
        .badge-unknown { background: #8899bb22; color: #8899bb; border: 1px solid #8899bb; }
        
        .user-stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 10px;
            margin-top: 15px;
        }
        .user-stat-card {
            background: #1a2240;
            padding: 15px;
            border-radius: 10px;
            border: 1px solid #2a3a5a;
        }
        .user-stat-card .uid { color: #42a5f5; font-weight: bold; font-size: 1.1em; }
        .user-stat-card .name { color: #fff; font-size: 0.9em; }
        .user-stat-card .row {
            display: flex;
            justify-content: space-between;
            margin-top: 5px;
            font-size: 0.85em;
            color: #8899bb;
        }
        .user-stat-card .row .val { color: #4caf50; font-weight: bold; }
        .user-stat-card .last { font-size: 0.75em; color: #666; margin-top: 5px; }
        
        .section-title {
            font-size: 1.3em;
            color: #fff;
            margin-top: 25px;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .live-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            background: #4caf50;
            border-radius: 50%;
            animation: pulse 1s infinite;
        }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.2; } }
        
        .note { color: #8899bb; font-size: 0.85em; margin-top: 10px; }
        .icon { font-size: 1.2em; margin-right: 6px; }
        
        @media (max-width: 768px) {
            .status-grid { grid-template-columns: repeat(2, 1fr); }
            .header h1 { font-size: 1.5em; }
            .ddos-box { padding: 30px; }
        }
        @media (max-width: 480px) {
            .status-grid { grid-template-columns: 1fr 1fr; }
            .header-top { flex-direction: column; align-items: flex-start; }
        }
    </style>
</head>
<body>

<div id="ddos-overlay">
    <div class="ddos-box">
        <div class="ddos-icon">&#9888;</div>
        <h2>Security Verification</h2>
        <p>Enter admin credentials to access the dashboard.</p>
        
        <div class="admin-section" id="admin-section" style="display:block;">
            <input type="text" id="admin-user" placeholder="Username" value="admin" />
            <input type="password" id="admin-pass" placeholder="Password" value="admin123" />
            <button class="admin-btn" onclick="verifyAdmin()">&#128274; Login</button>
            <div class="admin-error" id="admin-error">Invalid credentials!</div>
        </div>
    </div>
</div>

<div class="container" id="main-dashboard">
    <div class="header">
        <div class="header-top">
            <div>
                <h1>&#9889; Auto-Like Dashboard</h1>
                <div class="sub">Real-time monitoring &#8226; Auto-reset daily at 4:00 AM IST</div>
            </div>
            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                <span class="badge-auto">&#9654; Auto-Like Running</span>
                <span>Next Reset: <span class="badge-reset" id="next-reset">Loading...</span></span>
                <button class="btn btn-refresh" onclick="location.reload()">&#8635; Refresh</button>
                <button class="btn btn-check" onclick="checkStatus()">&#128270; Check</button>
            </div>
        </div>
    </div>

    <div class="status-grid">
        <div class="status-card"><div class="num blue" id="total-accounts">0</div><div class="lbl">&#128203; Total Accounts</div></div>
        <div class="status-card"><div class="num green" id="working-count">0</div><div class="lbl">&#9989; Working Now</div></div>
        <div class="status-card"><div class="num red" id="timeout-count">0</div><div class="lbl">&#9888; Limit Reached</div></div>
        <div class="status-card"><div class="num purple" id="total-likes">0</div><div class="lbl">&#10084; Total Likes</div></div>
        <div class="status-card"><div class="num yellow" id="targets-liked">0</div><div class="lbl">&#128101; Targets Liked</div></div>
        <div class="status-card"><div class="num cyan" id="auto-users">0</div><div class="lbl">&#128100; Auto Users</div></div>
    </div>

    <div class="panel">
        <h2>&#9889; Manage Auto-Like Users</h2>
        <div class="input-group">
            <input type="number" id="user-uid" placeholder="Enter Free Fire UID" />
            <button class="btn btn-add" onclick="addUser()">&#43; Add User</button>
            <button class="btn btn-del" onclick="deleteAllUsers()">&#10007; Delete All</button>
            <button class="btn btn-like" onclick="sendInstantLike()">&#9889; Send Like</button>
        </div>
        <div class="user-list" id="user-list"></div>
        <div class="note">&#9432; Users added here will receive auto-likes daily at 4:00 AM IST &#8226; Click "Send Like" for instant like</div>
    </div>

    <div class="section-title">&#128202; Account Status <span class="live-dot"></span></div>
    <div class="table-wrap">
        <table>
            <thead><tr><th>UID</th><th>Status</th><th>Last Check</th><th>Reset Time</th><th>Last Error</th></tr></thead>
            <tbody id="account-table"></tbody>
        </table>
    </div>

    <div class="section-title">&#128202; User Statistics</div>
    <div class="user-stats-grid" id="user-stats-grid"></div>
</div>

<script>
    function verifyAdmin() {
        var user = document.getElementById('admin-user').value;
        var pass = document.getElementById('admin-pass').value;
        
        if (user === 'admin' && pass === 'admin123') {
            document.getElementById('ddos-overlay').classList.add('hidden');
            document.getElementById('main-dashboard').classList.add('visible');
            loadData();
            setInterval(loadData, 3000);
            setInterval(checkStatus, 10000);
        } else {
            document.getElementById('admin-error').style.display = 'block';
            setTimeout(function() {
                document.getElementById('admin-error').style.display = 'none';
            }, 3000);
        }
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

                var userHtml = '';
                if (data.users && data.users.length > 0) {
                    data.users.forEach(function(user) {
                        var s = data.user_stats[user] || { total_likes: 0, today_likes: 0 };
                        userHtml += '<div class="user-item">' +
                            '<span class="uid">' + user + '</span>' +
                            '<span class="stats">Total: <span>' + (s.total_likes||0) + '</span> | Today: <span>' + (s.today_likes||0) + '</span></span>' +
                            '<button class="del-btn" onclick="deleteUser(\'' + user + '\')">&#10005;</button>' +
                        '</div>';
                    });
                } else {
                    userHtml = '<div class="note">No users added yet</div>';
                }
                document.getElementById('user-list').innerHTML = userHtml;

                var tableHtml = '';
                if (data.accounts && data.accounts.length > 0) {
                    data.accounts.forEach(function(acc) {
                        var cls = acc.status === 'working' ? 'working' : acc.status === 'timeout' ? 'timeout' : 'unknown';
                        tableHtml += '<tr>' +
                            '<td><strong>' + acc.uid + '</strong></td>' +
                            '<td><span class="badge badge-' + cls + '">' + acc.status + '</span></td>' +
                            '<td>' + (acc.last_check || 'Never') + '</td>' +
                            '<td>' + (acc.reset_time || 'N/A') + '</td>' +
                            '<td>' + (acc.last_error || 'None') + '</td>' +
                        '</tr>';
                    });
                } else {
                    tableHtml = '<tr><td colspan="5">No accounts loaded</td></tr>';
                }
                document.getElementById('account-table').innerHTML = tableHtml;

                var statsHtml = '';
                if (data.user_stats && Object.keys(data.user_stats).length > 0) {
                    var keys = Object.keys(data.user_stats);
                    keys.forEach(function(uid) {
                        var s = data.user_stats[uid];
                        statsHtml += '<div class="user-stat-card">' +
                            '<div class="uid">UID: ' + uid + '</div>' +
                            '<div class="name">Name: ' + (s.username || 'Unknown') + '</div>' +
                            '<div class="row"><span>Total Likes</span><span class="val">' + (s.total_likes||0) + '</span></div>' +
                            '<div class="row"><span>Today\'s Likes</span><span class="val">' + (s.today_likes||0) + '</span></div>' +
                            '<div class="row"><span>Current Likes</span><span class="val">' + (s.current_likes||0) + '</span></div>' +
                            '<div class="last">Last: ' + (s.last_like || 'Never') + '</div>' +
                        '</div>';
                    });
                } else {
                    statsHtml = '<div class="note">No stats yet</div>';
                }
                document.getElementById('user-stats-grid').innerHTML = statsHtml;
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

    function addUser() {
        var uid = document.getElementById('user-uid').value.trim();
        if (!uid) { alert('Enter a UID'); return; }
        fetch('/add-user', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uid: uid })
        })
        .then(function(res) { return res.json(); })
        .then(function(data) {
            if (data.success) {
                loadData();
                document.getElementById('user-uid').value = '';
            } else {
                alert(data.message);
            }
        });
    }

    function deleteUser(uid) {
        if (!confirm('Remove this user?')) return;
        fetch('/delete-user', {
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

    function deleteAllUsers() {
        if (!confirm('Delete ALL users?')) return;
        fetch('/delete-all-users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
        .then(function(res) { return res.json(); })
        .then(function(data) {
            if (data.success) loadData();
            else alert(data.message);
        });
    }

    function sendInstantLike() {
        var uid = document.getElementById('user-uid').value.trim();
        if (!uid) { alert('Enter a UID to like'); return; }
        if (!confirm('Send likes to ' + uid + '?')) return;
        
        var btn = document.querySelector('.btn-like');
        btn.textContent = '⏳ Sending...';
        btn.disabled = true;
        
        fetch('/like-instant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uid: uid, server_name: 'IND', key: 'JMLB', likes: 492 })
        })
        .then(function(res) { return res.json(); })
        .then(function(data) {
            btn.textContent = '⚡ Send Like';
            btn.disabled = false;
            if (data.success) {
                alert('✅ Sent ' + data.likes_sent + ' likes to ' + (data.username || uid) + '\nTotal Likes: ' + data.total_likes);
                loadData();
            } else {
                alert('❌ Error: ' + (data.error || 'Unknown error'));
            }
        });
    }

    setInterval(checkStatus, 10000);
</script>
</body>
</html>
'''

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
    threading.Thread(target=run_ultra_fast_check).start()
    return jsonify({'message': 'Status check started'})

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
    likes = int(data.get('likes', 492))
    
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
    
    result = asyncio.run(send_likes_ultra_fast(uid, server_name, like_url, likes))
    
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
        return jsonify({"error": "Invalid server. Use: " + str(valid_servers)}), 400

    accounts = load_accounts(server_name)
    if not accounts:
        return jsonify({"error": "No accounts for " + server_name}), 500
    
    today_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    count, last_reset = tracker[client_ip]

    if last_reset < today_midnight:
        tracker[client_ip] = [0, time.time()]
        count = 0

    if count >= KEY_LIMIT:
        return jsonify({"error": "Daily limit reached", "remains": "(0/" + str(KEY_LIMIT) + ")"}), 429
    
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
    result = asyncio.run(send_likes_ultra_fast(uid, server_name, like_url, limit))
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
            "remains": "(" + str(KEY_LIMIT - count) + "/" + str(KEY_LIMIT) + ")",
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
                print("Next auto-like at: " + target_time.strftime('%Y-%m-%d %H:%M:%S') + " IST")
                await asyncio.sleep(wait_seconds)
            
            print("Starting auto-like at " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + " IST")
            
            for user_uid in auto_like_users:
                print("Processing user: " + user_uid)
                
                result = await send_likes_ultra_fast(
                    user_uid,
                    "IND",
                    "https://client.ind.freefiremobile.com/LikeProfile",
                    492
                )
                
                print("Sent " + str(result['success']) + " likes to " + user_uid)
                await asyncio.sleep(2)
            
            print("Auto-like cycle complete at " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + " IST")
            
        except Exception as e:
            print("Auto-like error: " + str(e))
            await asyncio.sleep(60)

def start_auto_like():
    asyncio.run(auto_like_daily())

load_liked_data()
load_account_status()
load_users()

reset_thread = threading.Thread(target=daily_reset_task, daemon=True)
reset_thread.start()

auto_thread = threading.Thread(target=start_auto_like, daemon=True)
auto_thread.start()

threading.Thread(target=run_ultra_fast_check).start()

print("Ultra-Fast Auto-Like System Started!")
print("Users loaded: " + str(len(auto_like_users)))
print("Accounts loaded: " + str(len(load_accounts("IND"))))
print("Auto-reset at 4:00 AM IST")

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)