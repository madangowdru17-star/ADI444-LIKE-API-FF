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

app = Flask(__name__)

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

async def send_likes_verified(target_uid, server_name, url, target_count):
    """
    Send likes until target_count verified likes are reached.
    Stops automatically when verified count equals target_count.
    """
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
    
    # Use all fresh accounts
    accounts_to_use = fresh_accounts[:min(target_count * 3, len(fresh_accounts))]  # 3x buffer
    
    protobuf_message = create_protobuf_message(target_uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)
    
    # Pre-fetch tokens
    token_tasks = []
    for acc in accounts_to_use:
        token_tasks.append(get_valid_token(acc['uid'], acc['password']))
    tokens = await asyncio.gather(*token_tasks, return_exceptions=True)
    
    # Send likes concurrently
    like_tasks = []
    successful = 0
    failed = 0
    stopped = False
    
    for i, acc in enumerate(accounts_to_use):
        if successful >= target_count:
            stopped = True
            break
        
        if isinstance(tokens[i], str) and tokens[i]:
            success = await send_like_fast(encrypted_uid, tokens[i], url, acc['uid'])
            if success:
                successful += 1
            else:
                failed += 1
        else:
            failed += 1
        
        # Small delay between batches to avoid rate limit
        if i % 50 == 0 and i > 0:
            await asyncio.sleep(0.1)
    
    # Get user info for verification
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
    print("Auto-like scheduler started")
    while True:
        try:
            now = datetime.now()
            target_time = now.replace(hour=RESET_HOUR, minute=RESET_MINUTE, second=RESET_SECOND, microsecond=0)
            if now >= target_time:
                target_time += timedelta(days=1)
            wait_seconds = (target_time - now).total_seconds()
            if wait_seconds > 0:
                print(f"Next auto-like at: {target_time.strftime('%Y-%m-%d %H:%M:%S')} IST")
                await asyncio.sleep(wait_seconds)
            
            print(f"Starting auto-like at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
            
            users_to_remove = []
            for user_uid in auto_like_users:
                print(f"Processing user: {user_uid}")
                
                user_info_before = await get_user_info(user_uid, "IND")
                before_likes = user_info_before.get('likes', 0) if user_info_before else 0
                before_name = user_info_before.get('name', 'Unknown') if user_info_before else 'Unknown'
                
                result = await send_likes_verified(
                    user_uid,
                    "IND",
                    "https://client.ind.freefiremobile.com/LikeProfile",
                    AUTO_LIKE_VERIFIED_LIMIT
                )
                likes_sent = result['success']
                
                user_info_after = await get_user_info(user_uid, "IND")
                if user_info_after:
                    after_likes = user_info_after.get('likes', 0)
                    username = user_info_after.get('name', 'Unknown')
                    update_user_stats(user_uid, likes_sent, username, after_likes)
                    add_to_history(user_uid, likes_sent, before_likes, after_likes, username, "IND")
                    print(f"✅ {username} | Before: {before_likes} | After: {after_likes} | Gained: {after_likes - before_likes}")
                    if result['stopped']:
                        print(f"🛑 Verified limit {AUTO_LIKE_VERIFIED_LIMIT} reached! Stopped.")
                else:
                    print(f"⚠️ {user_uid} | Failed to get profile")
                
                if likes_sent > 0:
                    users_to_remove.append(user_uid)
                    print(f"✅ Auto-removed: {user_uid}")
                
                await asyncio.sleep(0.5)
            
            for uid in users_to_remove:
                if uid in auto_like_users:
                    auto_like_users.remove(uid)
                    save_users()
            
            print(f"Auto-like cycle complete. Removed {len(users_to_remove)} users.")
            
        except Exception as e:
            print(f"Auto-like error: {e}")
            await asyncio.sleep(60)

def start_auto_like():
    asyncio.run(auto_like_daily())

def set_auto_time(hour, minute):
    global RESET_HOUR, RESET_MINUTE
    RESET_HOUR = hour
    RESET_MINUTE = minute
    print(f"Auto-like time changed to {hour:02d}:{minute:02d} IST")
    return f"Auto-like time set to {hour:02d}:{minute:02d} IST"

# ============================================================
# KALI LINUX STYLE WEBSITE – HEX CHEATS LIKE BOT
# ============================================================
WEBSITE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HEX CHEATS LIKE BOT</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Share Tech Mono', monospace;
            background: #0a0a0a;
            color: #00ff41;
            min-height: 100vh;
            display: flex;
            background-image: radial-gradient(circle at 10% 20%, rgba(0,255,65,0.02) 0%, transparent 50%);
        }
        
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #0a0a0a; }
        ::-webkit-scrollbar-thumb { background: #00ff41; border-radius: 3px; }
        
        .sidebar {
            width: 240px;
            background: #0d0d0d;
            border-right: 1px solid rgba(0,255,65,0.15);
            min-height: 100vh;
            padding: 20px 0;
            position: fixed;
            top: 0; left: 0;
            z-index: 100;
            box-shadow: 0 0 40px rgba(0,255,65,0.03);
        }
        .sidebar .logo {
            text-align: center;
            padding: 0 20px 20px;
            border-bottom: 1px solid rgba(0,255,65,0.1);
            margin-bottom: 15px;
        }
        .sidebar .logo h1 {
            color: #00ff41;
            font-size: 1.1em;
            letter-spacing: 3px;
            text-shadow: 0 0 20px rgba(0,255,65,0.2);
        }
        .sidebar .logo small {
            color: #00ff41;
            opacity: 0.5;
            font-size: 0.65em;
            letter-spacing: 2px;
        }
        .sidebar .nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 22px;
            color: #00ff41;
            opacity: 0.6;
            text-decoration: none;
            transition: 0.3s;
            cursor: pointer;
            border-left: 2px solid transparent;
            font-size: 0.85em;
            letter-spacing: 0.5px;
        }
        .sidebar .nav-item:hover, .sidebar .nav-item.active {
            opacity: 1;
            background: rgba(0,255,65,0.05);
            border-left-color: #00ff41;
            text-shadow: 0 0 10px rgba(0,255,65,0.1);
        }
        .sidebar .nav-item i { width: 20px; text-align: center; font-size: 0.9em; }
        
        .main {
            margin-left: 240px;
            padding: 20px;
            flex: 1;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        
        .glass {
            background: rgba(10,10,10,0.7);
            backdrop-filter: blur(8px);
            border: 1px solid rgba(0,255,65,0.08);
            box-shadow: 0 8px 32px rgba(0,0,0,0.5);
            border-radius: 8px;
            transition: 0.3s;
        }
        .glass:hover { border-color: rgba(0,255,65,0.15); }
        
        .header {
            padding: 18px 22px;
            margin-bottom: 20px;
            border-bottom: 1px solid rgba(0,255,65,0.05);
        }
        .header h1 {
            color: #00ff41;
            font-size: 1.4em;
            letter-spacing: 2px;
            text-shadow: 0 0 30px rgba(0,255,65,0.1);
        }
        .header .sub {
            opacity: 0.4;
            font-size: 0.8em;
            margin-top: 3px;
            letter-spacing: 1px;
        }
        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
        }
        
        .badge-auto {
            background: rgba(0,255,65,0.1);
            color: #00ff41;
            padding: 4px 14px;
            border-radius: 4px;
            border: 1px solid rgba(0,255,65,0.2);
            font-size: 0.75em;
            letter-spacing: 1px;
        }
        .badge-reset { color: #ffcc00; opacity: 0.8; }
        
        .btn {
            padding: 8px 16px;
            border: 1px solid rgba(0,255,65,0.2);
            border-radius: 4px;
            cursor: pointer;
            font-family: 'Share Tech Mono', monospace;
            font-weight: 400;
            font-size: 0.8em;
            transition: 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: transparent;
            color: #00ff41;
            letter-spacing: 0.5px;
        }
        .btn:hover {
            background: rgba(0,255,65,0.08);
            border-color: rgba(0,255,65,0.4);
            box-shadow: 0 0 20px rgba(0,255,65,0.05);
        }
        .btn-primary {
            background: rgba(0,255,65,0.08);
            border-color: rgba(0,255,65,0.3);
        }
        .btn-primary:hover { background: rgba(0,255,65,0.15); }
        .btn-success { border-color: rgba(0,255,65,0.2); }
        .btn-success:hover { background: rgba(0,255,65,0.08); }
        .btn-danger { border-color: rgba(255,0,50,0.2); color: #ff0044; }
        .btn-danger:hover { background: rgba(255,0,50,0.08); border-color: rgba(255,0,50,0.3); }
        .btn-warning { border-color: rgba(255,200,0,0.2); color: #ffcc00; }
        .btn-warning:hover { background: rgba(255,200,0,0.08); }
        .btn-rocket { border-color: rgba(255,100,0,0.2); color: #ff6600; }
        .btn-rocket:hover { background: rgba(255,100,0,0.08); }
        
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }
        .status-card {
            padding: 16px 10px;
            text-align: center;
            transition: 0.3s;
            background: rgba(0,255,65,0.02);
            border: 1px solid rgba(0,255,65,0.05);
        }
        .status-card:hover {
            border-color: rgba(0,255,65,0.15);
            background: rgba(0,255,65,0.03);
        }
        .status-card .num { font-size: 1.8em; font-weight: bold; color: #00ff41; text-shadow: 0 0 20px rgba(0,255,65,0.05); }
        .status-card .lbl { opacity: 0.4; font-size: 0.7em; margin-top: 4px; letter-spacing: 1px; }
        
        .panel { padding: 18px 20px; margin-bottom: 20px; }
        .panel h2 { opacity: 0.6; font-size: 0.9em; margin-bottom: 12px; letter-spacing: 1px; }
        .input-group { display: flex; flex-wrap: wrap; gap: 8px; }
        .input-group input {
            flex: 1 1 180px;
            padding: 10px 14px;
            border-radius: 4px;
            border: 1px solid rgba(0,255,65,0.12);
            background: rgba(0,0,0,0.5);
            color: #00ff41;
            font-family: 'Share Tech Mono', monospace;
            font-size: 0.85em;
            min-width: 130px;
        }
        .input-group input:focus { outline: none; border-color: rgba(0,255,65,0.3); }
        .input-group select {
            padding: 10px 14px;
            border-radius: 4px;
            border: 1px solid rgba(0,255,65,0.12);
            background: rgba(0,0,0,0.5);
            color: #00ff41;
            font-family: 'Share Tech Mono', monospace;
            font-size: 0.85em;
            cursor: pointer;
        }
        .input-group select:focus { outline: none; border-color: rgba(0,255,65,0.3); }
        .input-group select option { background: #0a0a0a; color: #00ff41; }
        
        .table-wrap { overflow-x: auto; }
        table {
            width: 100%;
            border-collapse: collapse;
            background: rgba(0,0,0,0.3);
            border-radius: 8px;
            overflow: hidden;
            margin-top: 10px;
            font-size: 0.8em;
        }
        th {
            background: rgba(0,255,65,0.03);
            padding: 10px 14px;
            text-align: left;
            font-weight: 400;
            opacity: 0.5;
            border-bottom: 1px solid rgba(0,255,65,0.05);
            letter-spacing: 1px;
        }
        td { padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.02); }
        
        .badge {
            padding: 2px 10px;
            border-radius: 3px;
            font-size: 0.7em;
            display: inline-block;
            border: 1px solid;
        }
        .badge-working { background: rgba(0,255,65,0.08); color: #00ff41; border-color: rgba(0,255,65,0.2); }
        .badge-timeout { background: rgba(255,0,50,0.08); color: #ff0044; border-color: rgba(255,0,50,0.2); }
        .badge-reset { background: rgba(255,200,0,0.08); color: #ffcc00; border-color: rgba(255,200,0,0.2); }
        .badge-unknown { background: rgba(136,153,187,0.08); color: #8899bb; border-color: rgba(136,153,187,0.2); }
        
        .user-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
        .user-item {
            background: rgba(0,255,65,0.03);
            padding: 6px 12px;
            border-radius: 4px;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            border: 1px solid rgba(0,255,65,0.06);
            font-size: 0.8em;
        }
        .user-item .uid { color: #00ff41; font-weight: bold; }
        .user-item .stats { opacity: 0.4; font-size: 0.75em; }
        .user-item .stats span { color: #00ff41; opacity: 1; }
        .user-item .del-btn { background: none; border: none; color: #ff0044; cursor: pointer; padding: 0 4px; }
        
        .status-row { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 15px; }
        .status-row .item {
            background: rgba(0,255,65,0.02);
            padding: 5px 14px;
            border-radius: 4px;
            font-size: 0.8em;
            border: 1px solid rgba(0,255,65,0.04);
        }
        
        .section-title {
            font-size: 0.9em;
            color: #00ff41;
            opacity: 0.6;
            margin: 20px 0 10px;
            display: flex;
            align-items: center;
            gap: 10px;
            letter-spacing: 1px;
        }
        .live-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            background: #00ff41;
            border-radius: 50%;
            animation: pulse 1s infinite;
            box-shadow: 0 0 10px rgba(0,255,65,0.2);
        }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.2; } }
        
        .note { opacity: 0.3; font-size: 0.75em; margin-top: 8px; letter-spacing: 0.5px; }
        
        .user-stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 8px;
            margin-top: 10px;
        }
        .user-stat-card {
            background: rgba(0,255,65,0.02);
            padding: 12px 14px;
            border-radius: 4px;
            border: 1px solid rgba(0,255,65,0.04);
        }
        .user-stat-card .uid { color: #00ff41; font-weight: bold; font-size: 0.85em; }
        .user-stat-card .name { opacity: 0.6; font-size: 0.8em; }
        .user-stat-card .row { display: flex; justify-content: space-between; margin-top: 3px; font-size: 0.75em; opacity: 0.5; }
        .user-stat-card .row .val { color: #00ff41; opacity: 1; }
        .user-stat-card .last { font-size: 0.7em; opacity: 0.3; margin-top: 4px; }
        
        .result-modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.85);
            z-index: 999;
            align-items: center;
            justify-content: center;
        }
        .result-modal.active { display: flex; }
        .result-box {
            background: #0a0a0a;
            padding: 30px 35px;
            border-radius: 8px;
            max-width: 500px;
            width: 90%;
            border: 1px solid rgba(0,255,65,0.15);
            box-shadow: 0 0 60px rgba(0,255,65,0.03);
        }
        .result-box h2 { color: #00ff41; margin-bottom: 12px; font-size: 1.1em; letter-spacing: 1px; }
        .result-box .row {
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid rgba(255,255,255,0.03);
            font-size: 0.85em;
        }
        .result-box .row .label { opacity: 0.4; }
        .result-box .row .value { color: #00ff41; }
        .result-box .close-btn {
            margin-top: 15px;
            padding: 8px 30px;
            background: transparent;
            border: 1px solid rgba(0,255,65,0.2);
            color: #00ff41;
            border-radius: 4px;
            cursor: pointer;
            font-family: 'Share Tech Mono', monospace;
            font-size: 0.8em;
        }
        .result-box .close-btn:hover { background: rgba(0,255,65,0.05); }
        
        .section { display: none; }
        .section.active { display: block; }
        
        .history-item {
            padding: 8px 14px;
            border-bottom: 1px solid rgba(255,255,255,0.02);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            font-size: 0.8em;
        }
        .history-item .uid { color: #00ff41; font-weight: bold; }
        .history-item .name { opacity: 0.6; }
        .history-item .likes { color: #00ff41; }
        .history-item .time { opacity: 0.3; font-size: 0.75em; }
        
        @media (max-width: 768px) {
            .sidebar { width: 60px; }
            .sidebar .logo h1, .sidebar .logo small, .sidebar .nav-item span { display: none; }
            .sidebar .nav-item { padding: 10px 14px; justify-content: center; }
            .main { margin-left: 60px; }
            .status-grid { grid-template-columns: repeat(3, 1fr); }
        }
        @media (max-width: 480px) {
            .status-grid { grid-template-columns: 1fr 1fr; }
            .sidebar { width: 50px; }
            .main { margin-left: 50px; padding: 10px; }
            .sidebar .nav-item { padding: 8px 10px; }
            .result-box { padding: 20px; }
        }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="logo">
            <h1># HEX</h1>
            <small>CHEATS · LIKE BOT</small>
        </div>
        <div class="nav-item active" onclick="showSection('dashboard')"><i class="fas fa-home"></i> <span>DASHBOARD</span></div>
        <div class="nav-item" onclick="showSection('likes20')"><i class="fas fa-arrow-right"></i> <span>20 LIKES</span></div>
        <div class="nav-item" onclick="showSection('unlimited')"><i class="fas fa-infinity"></i> <span>UNLIMITED</span></div>
        <div class="nav-item" onclick="showSection('auto')"><i class="fas fa-clock"></i> <span>AUTO LIKE</span></div>
        <div class="nav-item" onclick="showSection('accounts')"><i class="fas fa-users"></i> <span>ACCOUNTS</span></div>
        <div class="nav-item" onclick="showSection('history')"><i class="fas fa-history"></i> <span>HISTORY</span></div>
        <div class="nav-item" onclick="showSection('settings')"><i class="fas fa-cog"></i> <span>SETTINGS</span></div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="header glass">
                <div class="header-top">
                    <div>
                        <h1><i class="fas fa-terminal"></i> HEX CHEATS · LIKE BOT</h1>
                        <div class="sub">v3.0 · <span id="auto-time-display">4:02</span> IST · <span id="next-reset">LOADING</span></div>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                        <span class="badge-auto"><i class="fas fa-play"></i> ONLINE</span>
                        <button class="btn" onclick="location.reload()"><i class="fas fa-sync"></i></button>
                    </div>
                </div>
            </div>
            
            <div class="status-row">
                <div class="item"><i class="fas fa-history"></i> LAST RUN: <span id="lastAutoRun">NEVER</span></div>
                <div class="item"><i class="fas fa-circle" style="color:#00ff41;font-size:0.5em;"></i> STATUS: <span id="autoRunStatus">IDLE</span></div>
                <div class="item"><i class="fas fa-comment"></i> MSG: <span id="autoRunMessage">-</span></div>
            </div>
            
            <div id="section-dashboard" class="section active">
                <div class="status-grid">
                    <div class="status-card glass"><div class="num" id="total-accounts">0</div><div class="lbl">ACCOUNTS</div></div>
                    <div class="status-card glass"><div class="num" id="working-count">0</div><div class="lbl">WORKING</div></div>
                    <div class="status-card glass"><div class="num" id="timeout-count">0</div><div class="lbl">LIMIT</div></div>
                    <div class="status-card glass"><div class="num" id="total-likes">0</div><div class="lbl">LIKES</div></div>
                    <div class="status-card glass"><div class="num" id="targets-liked">0</div><div class="lbl">TARGETS</div></div>
                    <div class="status-card glass"><div class="num" id="auto-users">0</div><div class="lbl">QUEUE</div></div>
                </div>
            </div>
            
            <div id="section-likes20" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-arrow-right"></i> SEND 20 LIKES</h2>
                    <div class="input-group">
                        <input type="number" id="target-uid-20" placeholder="ENTER UID" />
                        <select id="server-20">
                            <option value="IND">INDIA</option>
                            <option value="BD">BANGLADESH</option>
                            <option value="MENA">MENA</option>
                            <option value="BR">BRAZIL</option>
                            <option value="US">US</option>
                            <option value="SAC">SAC</option>
                            <option value="NA">NA</option>
                            <option value="RU">RUSSIA</option>
                        </select>
                        <button class="btn btn-primary" onclick="sendLikes(20)"><i class="fas fa-arrow-right"></i> SEND</button>
                    </div>
                    <div class="note">SENDS EXACTLY 20 VERIFIED LIKES · AUTO-STOP</div>
                </div>
            </div>
            
            <div id="section-unlimited" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-infinity"></i> UNLIMITED LIKES</h2>
                    <div class="input-group">
                        <input type="number" id="target-uid-unlimited" placeholder="ENTER UID" />
                        <select id="server-unlimited">
                            <option value="IND">INDIA</option>
                            <option value="BD">BANGLADESH</option>
                            <option value="MENA">MENA</option>
                            <option value="BR">BRAZIL</option>
                            <option value="US">US</option>
                            <option value="SAC">SAC</option>
                            <option value="NA">NA</option>
                            <option value="RU">RUSSIA</option>
                        </select>
                        <button class="btn btn-rocket" onclick="sendLikes(999999)"><i class="fas fa-rocket"></i> SEND ALL</button>
                    </div>
                    <div class="note">SENDS ALL AVAILABLE LIKES · VERIFIED</div>
                </div>
            </div>
            
            <div id="section-auto" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-clock"></i> AUTO LIKE</h2>
                    <p style="opacity:0.4;font-size:0.8em;margin-bottom:12px;">DAILY AUTO-LIKE · STOPS AT VERIFIED LIMIT</p>
                    <div class="input-group">
                        <input type="number" id="target-uid-auto" placeholder="ENTER UID" />
                        <input type="number" id="auto-limit" placeholder="LIMIT" value="220" style="width:100px; padding:10px 14px; border-radius:4px; border:1px solid rgba(0,255,65,0.12); background:rgba(0,0,0,0.5); color:#00ff41; font-family:'Share Tech Mono',monospace; font-size:0.85em;" />
                        <button class="btn btn-warning" onclick="addAutoUser()"><i class="fas fa-plus"></i> ADD</button>
                        <button class="btn btn-danger" onclick="deleteAllAuto()"><i class="fas fa-trash"></i> CLEAR</button>
                    </div>
                    <div class="user-list" id="auto-user-list"></div>
                    <div class="note">AUTO-REMOVE AFTER SUCCESS · VERIFIED LIMIT</div>
                </div>
            </div>
            
            <div id="section-accounts" class="section">
                <div class="section-title"><i class="fas fa-users"></i> ACCOUNT STATUS <span class="live-dot"></span></div>
                <div class="table-wrap glass" style="padding:0; overflow:hidden;">
                    <table>
                        <thead><tr><th>UID</th><th>STATUS</th><th>LAST CHECK</th><th>RESET</th></tr></thead>
                        <tbody id="account-table"></tbody>
                    </table>
                </div>
            </div>
            
            <div id="section-history" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-history"></i> LIKE HISTORY</h2>
                    <div id="history-list"></div>
                </div>
            </div>
            
            <div id="section-settings" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-cog"></i> SETTINGS</h2>
                    <div style="margin-bottom:12px;">
                        <label style="opacity:0.4;font-size:0.8em;">AUTO-LIKE TIME (IST)</label>
                        <div class="input-group" style="margin-top:6px;">
                            <input type="number" id="set-hour" placeholder="HOUR" value="4" style="width:70px; padding:10px 14px; border-radius:4px; border:1px solid rgba(0,255,65,0.12); background:rgba(0,0,0,0.5); color:#00ff41; font-family:'Share Tech Mono',monospace; font-size:0.85em;" />
                            <input type="number" id="set-minute" placeholder="MIN" value="2" style="width:70px; padding:10px 14px; border-radius:4px; border:1px solid rgba(0,255,65,0.12); background:rgba(0,0,0,0.5); color:#00ff41; font-family:'Share Tech Mono',monospace; font-size:0.85em;" />
                            <button class="btn btn-primary" onclick="setAutoTime()"><i class="fas fa-save"></i> SAVE</button>
                        </div>
                    </div>
                    <div id="time-status" style="color:#00ff41;opacity:0.6;font-size:0.85em;"></div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="result-modal" id="resultModal">
        <div class="result-box">
            <h2><i class="fas fa-check-circle"></i> RESULT</h2>
            <div id="result-content">
                <div class="row"><span class="label">PLAYER</span><span class="value" id="res-name">-</span></div>
                <div class="row"><span class="label">SENT</span><span class="value" id="res-sent">0</span></div>
                <div class="row"><span class="label">BEFORE</span><span class="value" id="res-before">0</span></div>
                <div class="row"><span class="label">AFTER</span><span class="value" id="res-after">0</span></div>
                <div class="row"><span class="label">VERIFIED</span><span class="value" id="res-added">0</span></div>
                <div class="row"><span class="label">FAILED</span><span class="value" id="res-failed" style="color:#ff0044;">0</span></div>
            </div>
            <button class="close-btn" onclick="closeResult()"><i class="fas fa-times"></i> CLOSE</button>
        </div>
    </div>

    <script>
        function showSection(id) {
            document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
            document.getElementById('section-' + id).classList.add('active');
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.querySelector(`.nav-item[onclick*="${id}"]`).classList.add('active');
            if (id === 'history') loadHistory();
        }
        
        function formatTime(iso) {
            if (!iso) return 'NEVER';
            try { const d = new Date(iso); return d.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch { return iso; }
        }
        
        function loadData() {
            fetch('/api/dashboard-data')
                .then(res => res.json())
                .then(data => {
                    if (data.error) return;
                    document.getElementById('total-accounts').textContent = data.total_accounts || 0;
                    document.getElementById('working-count').textContent = data.working_count || 0;
                    document.getElementById('timeout-count').textContent = data.timeout_count || 0;
                    document.getElementById('total-likes').textContent = data.total_likes || 0;
                    document.getElementById('targets-liked').textContent = data.targets_liked || 0;
                    document.getElementById('auto-users').textContent = data.auto_users || 0;
                    document.getElementById('next-reset').textContent = data.next_reset || 'LOADING...';
                    document.getElementById('lastAutoRun').textContent = data.last_auto_run ? formatTime(data.last_auto_run) : 'NEVER';
                    document.getElementById('autoRunStatus').textContent = data.auto_run_status || 'IDLE';
                    document.getElementById('autoRunMessage').textContent = data.auto_run_message || '-';
                    document.getElementById('auto-time-display').textContent = data.auto_time || '4:02';
                    
                    let userHtml = '';
                    if (data.users && data.users.length > 0) {
                        data.users.forEach(user => {
                            const s = data.user_stats[user] || { total_likes: 0, today_likes: 0 };
                            userHtml += `<div class="user-item"><span class="uid">${user}</span><span class="stats">T:<span>${s.total_likes||0}</span> D:<span>${s.today_likes||0}</span></span><button class="del-btn" onclick="deleteUser('${user}')"><i class="fas fa-times"></i></button></div>`;
                        });
                    } else {
                        userHtml = '<span class="note">NO USERS IN QUEUE</span>';
                    }
                    document.getElementById('auto-user-list').innerHTML = userHtml;
                    
                    let tableHtml = '';
                    if (data.accounts && data.accounts.length > 0) {
                        data.accounts.forEach(acc => {
                            const cls = acc.status === 'working' ? 'working' : acc.status === 'timeout' ? 'timeout' : 'unknown';
                            tableHtml += `<tr><td><strong>${acc.uid}</strong></td><td><span class="badge badge-${cls}">${acc.status.toUpperCase()}</span></td><td>${acc.last_check ? formatTime(acc.last_check) : 'NEVER'}</td><td>${acc.reset_time ? formatTime(acc.reset_time) : 'N/A'}</td></tr>`;
                        });
                    } else {
                        tableHtml = '<tr><td colspan="4">NO ACCOUNTS LOADED</td></tr>';
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
                                <span><span class="uid">${h.uid}</span> <span class="name">${h.username || 'UNKNOWN'}</span></span>
                                <span class="likes">+${h.likes_sent} (${h.verified_added} VERIFIED)</span>
                                <span class="time">${formatTime(h.timestamp)}</span>
                            </div>`;
                        });
                    } else {
                        html = '<div class="note">NO HISTORY</div>';
                    }
                    document.getElementById('history-list').innerHTML = html;
                });
        }
        
        function showResult(data) {
            document.getElementById('res-name').textContent = data.username || 'UNKNOWN';
            document.getElementById('res-sent').textContent = data.likes_sent || 0;
            document.getElementById('res-before').textContent = data.likes_before || 0;
            document.getElementById('res-after').textContent = data.total_likes || 0;
            document.getElementById('res-added').textContent = data.verified_added || 0;
            document.getElementById('res-failed').textContent = data.failed || 0;
            document.getElementById('resultModal').classList.add('active');
        }
        
        function closeResult() { document.getElementById('resultModal').classList.remove('active'); }
        
        function getActiveServer() {
            const activeSection = document.querySelector('.section.active');
            if (activeSection) {
                const id = activeSection.id;
                if (id === 'section-likes20') return document.getElementById('server-20').value;
                if (id === 'section-unlimited') return document.getElementById('server-unlimited').value;
            }
            return 'IND';
        }
        
        function getActiveUid() {
            const activeSection = document.querySelector('.section.active');
            if (activeSection) {
                const id = activeSection.id;
                if (id === 'section-likes20') return document.getElementById('target-uid-20').value.trim();
                if (id === 'section-unlimited') return document.getElementById('target-uid-unlimited').value.trim();
            }
            return '';
        }
        
        function sendLikes(count) {
            const uid = getActiveUid();
            const server = getActiveServer();
            if (!uid) { alert('ENTER A UID'); return; }
            if (!confirm(`SEND ${count === 999999 ? 'UNLIMITED' : count} LIKES TO ${uid} ON ${server}?`)) return;
            
            const btn = document.querySelector('#section-likes20 .btn-primary, #section-unlimited .btn-rocket');
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            btn.disabled = true;
            
            fetch('/send-likes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, server_name: server, key: 'JMLB', count: count })
            })
            .then(res => res.json())
            .then(data => {
                btn.innerHTML = count === 999999 ? '<i class="fas fa-rocket"></i> SEND ALL' : '<i class="fas fa-arrow-right"></i> SEND';
                btn.disabled = false;
                if (data.success) {
                    showResult(data);
                    loadData();
                } else {
                    alert('ERROR: ' + (data.error || 'UNKNOWN'));
                }
            });
        }
        
        function addAutoUser() {
            const uid = document.getElementById('target-uid-auto').value.trim();
            const limit = parseInt(document.getElementById('auto-limit').value) || 220;
            if (!uid) { alert('ENTER A UID'); return; }
            fetch('/add-auto-user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, limit })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) { alert('ADDED: ' + uid); loadData(); } else { alert(data.message); }
            });
        }
        
        function deleteUser(uid) {
            if (!confirm('REMOVE ' + uid + ' FROM QUEUE?')) return;
            fetch('/delete-user', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid }) })
                .then(res => res.json())
                .then(data => { if (data.success) loadData(); else alert(data.message); });
        }
        
        function deleteAllAuto() {
            if (!confirm('CLEAR ENTIRE QUEUE?')) return;
            fetch('/delete-all-users', { method: 'POST' })
                .then(res => res.json())
                .then(data => { if (data.success) loadData(); else alert(data.message); });
        }
        
        function setAutoTime() {
            const hour = parseInt(document.getElementById('set-hour').value);
            const minute = parseInt(document.getElementById('set-minute').value);
            if (isNaN(hour) || isNaN(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
                alert('ENTER VALID TIME (HOUR: 0-23, MINUTE: 0-59)');
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
                document.getElementById('auto-time-display').textContent = `${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}`;
                loadData();
            });
        }
        
        document.getElementById('resultModal').addEventListener('click', function(e) { if (e.target === this) closeResult(); });
        
        loadData();
        setInterval(loadData, 3000);
        setInterval(loadHistory, 5000);
    </script>
</body>
</html>
'''

@app.route('/')
def dashboard():
    return render_template_string(WEBSITE_HTML)

@app.route('/api/dashboard-data')
def dashboard_data():
    server = 'IND'
    accounts = load_accounts(server)
    if not accounts:
        return jsonify({'error': 'No accounts found'})
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
    return jsonify({'history': like_history[-50:]})

@app.route('/send-likes', methods=['POST'])
def send_likes():
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
    
    # For 20 likes, stop at exactly 20 verified
    result = asyncio.run(send_likes_verified(uid, server_name, like_url, count))
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
    return jsonify({'success': True, 'message': f'Added {uid} with verified limit {limit}'})

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

@app.route('/set-auto-time', methods=['POST'])
def set_auto_time():
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
    result = asyncio.run(send_likes_verified(uid, server_name, like_url, limit))
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

load_liked_data()
load_account_status()
load_users()

reset_thread = threading.Thread(target=daily_reset_task, daemon=True)
reset_thread.start()

auto_thread = threading.Thread(target=start_auto_like, daemon=True)
auto_thread.start()

threading.Thread(target=run_status_check).start()

print("✅ HEX CHEATS LIKE BOT STARTED – KALI LINUX STYLE")
print(f"📁 Accounts: {len(load_accounts('IND'))} (IND)")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)