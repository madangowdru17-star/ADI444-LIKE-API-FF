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
like_history = []  # Store history of likes

RESET_HOUR = 4
RESET_MINUTE = 2
RESET_SECOND = 0

RATE_LIMIT_DELAYS = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]

# ---------- Data persistence ----------
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
            'history': like_history[-100:]  # Keep last 100 entries
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

def add_to_history(target_uid, likes_sent, before, after, username):
    entry = {
        'uid': target_uid,
        'username': username,
        'likes_sent': likes_sent,
        'before': before,
        'after': after,
        'verified_added': after - before,
        'timestamp': datetime.now().isoformat(),
        'server': 'IND'
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

# ---------- Core async functions ----------
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

async def send_likes_until_complete(target_uid, server_name, url, target_count):
    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0, 'exhausted': True}
    
    fresh_accounts = []
    skipped = 0
    for acc in accounts:
        if is_uid_liked_in_24hrs(target_uid, acc['uid']):
            skipped += 1
        else:
            fresh_accounts.append(acc)
    
    if not fresh_accounts:
        return {'success': 0, 'failed': 0, 'total': len(accounts), 'skipped': skipped, 'exhausted': True}
    
    random.shuffle(fresh_accounts)
    
    successful = 0
    failed = 0
    used_accounts = []
    
    for acc in fresh_accounts:
        if successful >= target_count:
            break
        
        token = await get_valid_token(acc['uid'], acc['password'])
        if not token:
            failed += 1
            continue
        
        protobuf_message = create_protobuf_message(target_uid, server_name)
        encrypted_uid = encrypt_message(protobuf_message)
        
        success, _ = await send_like_with_retry(encrypted_uid, token, url, acc['uid'])
        
        if success:
            successful += 1
            mark_as_liked(target_uid, acc['uid'])
            used_accounts.append(acc['uid'])
        else:
            failed += 1
        
        await asyncio.sleep(0.3)
    
    if successful < target_count:
        remaining = [acc for acc in fresh_accounts if acc['uid'] not in used_accounts]
        if remaining:
            for acc in remaining:
                if successful >= target_count:
                    break
                token = await get_valid_token(acc['uid'], acc['password'])
                if not token:
                    continue
                protobuf_message = create_protobuf_message(target_uid, server_name)
                encrypted_uid = encrypt_message(protobuf_message)
                success, _ = await send_like_with_retry(encrypted_uid, token, url, acc['uid'])
                if success:
                    successful += 1
                    mark_as_liked(target_uid, acc['uid'])
                await asyncio.sleep(0.3)
    
    return {
        'success': successful,
        'failed': failed,
        'total': len(accounts),
        'skipped': skipped,
        'exhausted': successful < target_count and len(fresh_accounts) == 0
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

# ---------- Background tasks ----------
async def check_all_accounts_status():
    accounts = load_accounts("IND")
    for acc in accounts:
        try:
            token = await get_valid_token(acc['uid'], acc['password'])
            if token:
                protobuf_message = create_protobuf_message("3997461446", "IND")
                encrypted_uid = encrypt_message(protobuf_message)
                url = "https://client.ind.freefiremobile.com/LikeProfile"
                success, _ = await send_like_with_retry(encrypted_uid, token, url, acc['uid'])
                if success:
                    account_status[acc['uid']] = {'status': 'working', 'last_check': datetime.now().isoformat()}
                else:
                    account_status[acc['uid']] = {'status': 'timeout', 'last_check': datetime.now().isoformat(),
                                                  'reset_time': get_next_reset_time().isoformat()}
            else:
                account_status[acc['uid']] = {'status': 'unknown', 'last_check': datetime.now().isoformat()}
            save_account_status()
            await asyncio.sleep(0.5)
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
            accounts = load_accounts("IND")
            if not accounts:
                print("No accounts available")
                await asyncio.sleep(60)
                continue
            
            for user_uid in auto_like_users:
                print(f"Processing user: {user_uid}")
                result = await send_likes_until_complete(
                    user_uid,
                    "IND",
                    "https://client.ind.freefiremobile.com/LikeProfile",
                    50
                )
                print(f"Sent {result['success']} likes to {user_uid}")
                await asyncio.sleep(2)
            
            print(f"Auto-like cycle complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
        except Exception as e:
            print(f"Auto-like error: {e}")
            await asyncio.sleep(60)

def start_auto_like():
    asyncio.run(auto_like_daily())

# ---------- HTML Dashboard with Sidebar ----------
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auto-Like Dashboard</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0a0e1a;
            color: #fff;
            min-height: 100vh;
            display: flex;
        }
        
        /* SIDEBAR */
        .sidebar {
            width: 220px;
            background: #0d1225;
            border-right: 1px solid rgba(0,255,255,0.1);
            min-height: 100vh;
            padding: 20px 0;
            position: fixed;
            top: 0; left: 0;
            z-index: 100;
        }
        .sidebar .logo {
            text-align: center;
            padding: 0 20px 20px;
            border-bottom: 1px solid rgba(0,255,255,0.05);
            margin-bottom: 15px;
        }
        .sidebar .logo h2 { color: #00ffff; font-size: 1.2em; }
        .sidebar .logo small { color: #8899bb; font-size: 0.7em; }
        .sidebar .nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 25px;
            color: #8899bb;
            text-decoration: none;
            transition: 0.3s;
            cursor: pointer;
            border-left: 3px solid transparent;
        }
        .sidebar .nav-item:hover, .sidebar .nav-item.active {
            color: #00ffff;
            background: rgba(0,255,255,0.05);
            border-left-color: #00ffff;
        }
        .sidebar .nav-item i { width: 20px; text-align: center; }
        
        /* MAIN CONTENT */
        .main {
            margin-left: 220px;
            padding: 20px;
            flex: 1;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        
        .glass {
            background: rgba(10,14,26,0.6);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(0,255,255,0.15);
            box-shadow: 0 8px 32px rgba(0,0,0,0.5);
            border-radius: 16px;
            transition: 0.3s;
        }
        .glass:hover { border-color: rgba(0,255,255,0.3); }
        
        .header { padding: 20px; margin-bottom: 20px; }
        .header h1 { color: #00ffff; font-size: 1.8em; }
        .header .sub { opacity: 0.7; color: #8899bb; }
        .header-top { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
        
        .badge-auto { background: rgba(0,255,100,0.15); color: #00ff66; padding: 4px 14px; border-radius: 20px; border: 1px solid #00ff66; font-size: 0.85em; }
        .badge-reset { color: #ffcc00; font-weight: bold; }
        
        .btn {
            padding: 10px 18px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.9em;
            transition: 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(0,255,255,0.1);
            color: #00ffff;
            border: 1px solid rgba(0,255,255,0.2);
        }
        .btn:hover { background: rgba(0,255,255,0.2); transform: translateY(-2px); box-shadow: 0 0 20px rgba(0,255,255,0.1); }
        .btn-primary { background: linear-gradient(135deg, #00ffff, #0088ff); color: #000; border: none; }
        .btn-primary:hover { background: linear-gradient(135deg, #00ddff, #0066dd); }
        .btn-success { background: rgba(0,255,100,0.2); border-color: rgba(0,255,100,0.3); color: #00ff66; }
        .btn-success:hover { background: rgba(0,255,100,0.3); }
        .btn-danger { background: rgba(255,0,50,0.2); border-color: rgba(255,0,50,0.3); color: #ff0044; }
        .btn-danger:hover { background: rgba(255,0,50,0.3); }
        .btn-warning { background: rgba(255,200,0,0.2); border-color: rgba(255,200,0,0.3); color: #ffcc00; }
        .btn-warning:hover { background: rgba(255,200,0,0.3); }
        
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .status-card { padding: 18px 10px; text-align: center; transition: 0.3s; }
        .status-card:hover { transform: translateY(-5px); border-color: rgba(0,255,255,0.3); }
        .status-card .num { font-size: 2.2em; font-weight: bold; }
        .status-card .lbl { color: #8899bb; font-size: 0.8em; margin-top: 4px; }
        
        .panel { padding: 20px; margin-bottom: 20px; }
        .panel h2 { color: #8899bb; font-size: 1.1em; margin-bottom: 15px; }
        .input-group { display: flex; flex-wrap: wrap; gap: 10px; }
        .input-group input { flex: 1 1 200px; padding: 12px 15px; border-radius: 8px; border: 1px solid rgba(0,255,255,0.15); background: rgba(0,0,0,0.4); color: #fff; font-size: 1em; min-width: 150px; }
        .input-group input:focus { outline: none; border-color: #00ffff; box-shadow: 0 0 20px rgba(0,255,255,0.05); }
        .btn-group { display: flex; flex-wrap: wrap; gap: 6px; }
        
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; background: rgba(0,0,0,0.3); border-radius: 12px; overflow: hidden; margin-top: 12px; font-size: 0.9em; }
        th { background: rgba(0,255,255,0.05); padding: 12px 15px; text-align: left; font-weight: 600; color: #8899bb; white-space: nowrap; border-bottom: 1px solid rgba(0,255,255,0.05); }
        td { padding: 12px 15px; border-bottom: 1px solid rgba(255,255,255,0.03); }
        
        .badge { padding: 3px 10px; border-radius: 20px; font-size: 0.75em; font-weight: bold; display: inline-block; }
        .badge-working { background: rgba(0,255,100,0.15); color: #00ff66; border: 1px solid #00ff66; }
        .badge-timeout { background: rgba(255,0,50,0.15); color: #ff0044; border: 1px solid #ff0044; }
        .badge-reset { background: rgba(255,200,0,0.15); color: #ffcc00; border: 1px solid #ffcc00; }
        .badge-unknown { background: rgba(136,153,187,0.15); color: #8899bb; border: 1px solid #8899bb; }
        
        .user-stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin-top: 12px; }
        .user-stat-card { background: rgba(0,255,255,0.03); padding: 14px; border-radius: 10px; border: 1px solid rgba(0,255,255,0.05); }
        .user-stat-card .uid { color: #00ffff; font-weight: bold; font-size: 1em; }
        .user-stat-card .name { color: #fff; font-size: 0.9em; }
        .user-stat-card .row { display: flex; justify-content: space-between; margin-top: 4px; font-size: 0.85em; color: #8899bb; }
        .user-stat-card .row .val { color: #00ff66; font-weight: bold; }
        .user-stat-card .last { font-size: 0.75em; color: #666; margin-top: 5px; }
        
        .log-area { background: rgba(0,0,0,0.4); padding: 12px; border-radius: 12px; max-height: 200px; overflow-y: auto; font-family: 'Courier New', monospace; font-size: 0.8em; border: 1px solid rgba(0,255,255,0.05); margin-top: 12px; }
        .log-entry { padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
        .log-time { color: #00ffff; }
        .log-success { color: #00ff66; }
        .log-error { color: #ff0044; }
        .log-info { color: #ffcc00; }
        
        .section-title { font-size: 1.2em; color: #fff; margin: 25px 0 12px; display: flex; align-items: center; gap: 10px; }
        .live-dot { display: inline-block; width: 10px; height: 10px; background: #00ff66; border-radius: 50%; animation: pulse 1s infinite; box-shadow: 0 0 10px rgba(0,255,100,0.3); }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
        .note { color: #8899bb; font-size: 0.85em; margin-top: 8px; }
        .status-row { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 15px; align-items: center; }
        .status-row .item { background: rgba(0,255,255,0.05); padding: 6px 15px; border-radius: 20px; font-size: 0.9em; border: 1px solid rgba(0,255,255,0.05); }
        
        .result-modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 999;
            align-items: center;
            justify-content: center;
        }
        .result-modal.active { display: flex; }
        .result-box { background: #141928; padding: 30px; border-radius: 16px; max-width: 500px; width: 90%; border: 1px solid rgba(0,255,255,0.2); box-shadow: 0 0 40px rgba(0,255,255,0.1); }
        .result-box h2 { color: #00ffff; margin-bottom: 15px; }
        .result-box .row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .result-box .row .label { color: #8899bb; }
        .result-box .row .value { color: #00ff66; font-weight: bold; }
        .result-box .close-btn { margin-top: 15px; padding: 10px 30px; background: #00ffff; color: #000; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; }
        
        .section { display: none; }
        .section.active { display: block; }
        
        .history-item { padding: 10px 15px; border-bottom: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
        .history-item .uid { color: #00ffff; font-weight: bold; }
        .history-item .name { color: #fff; }
        .history-item .likes { color: #00ff66; font-weight: bold; }
        .history-item .time { color: #8899bb; font-size: 0.8em; }
        
        @media (max-width: 768px) {
            .sidebar { width: 60px; }
            .sidebar .logo h2, .sidebar .logo small { display: none; }
            .sidebar .nav-item span { display: none; }
            .sidebar .nav-item { padding: 12px 18px; }
            .main { margin-left: 60px; }
            .status-grid { grid-template-columns: repeat(3, 1fr); }
        }
        @media (max-width: 480px) {
            .status-grid { grid-template-columns: 1fr 1fr; }
            .sidebar { width: 50px; }
            .main { margin-left: 50px; padding: 10px; }
            .sidebar .nav-item { padding: 10px 14px; }
        }
    </style>
</head>
<body>
    <!-- SIDEBAR -->
    <div class="sidebar">
        <div class="logo">
            <h2>⚡ AL</h2>
            <small>Auto-Like</small>
        </div>
        <div class="nav-item active" onclick="showSection('dashboard')"><i class="fas fa-home"></i> <span>Dashboard</span></div>
        <div class="nav-item" onclick="showSection('likes20')"><i class="fas fa-arrow-right"></i> <span>20 Likes</span></div>
        <div class="nav-item" onclick="showSection('unlimited')"><i class="fas fa-infinity"></i> <span>Unlimited</span></div>
        <div class="nav-item" onclick="showSection('auto')"><i class="fas fa-clock"></i> <span>Auto Like</span></div>
        <div class="nav-item" onclick="showSection('verify')"><i class="fas fa-check-double"></i> <span>Verify</span></div>
        <div class="nav-item" onclick="showSection('history')"><i class="fas fa-history"></i> <span>History</span></div>
        <div class="nav-item" onclick="showSection('accounts')"><i class="fas fa-users"></i> <span>Accounts</span></div>
        <div class="nav-item" onclick="showSection('stats')"><i class="fas fa-chart-bar"></i> <span>Statistics</span></div>
        <div class="nav-item" onclick="showSection('logs')"><i class="fas fa-terminal"></i> <span>Logs</span></div>
    </div>
    
    <!-- MAIN -->
    <div class="main">
        <div class="container">
            <!-- Header -->
            <div class="header glass">
                <div class="header-top">
                    <div>
                        <h1><i class="fas fa-bolt"></i> Auto-Like Dashboard</h1>
                        <div class="sub"><i class="far fa-clock"></i> Real-time monitoring · Auto-reset daily at 4:02 AM IST</div>
                    </div>
                    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                        <span class="badge-auto"><i class="fas fa-play"></i> Auto-Like Running</span>
                        <span><i class="fas fa-sync-alt"></i> Reset: <span class="badge-reset" id="next-reset">Loading...</span></span>
                        <button class="btn" onclick="location.reload()"><i class="fas fa-sync"></i></button>
                    </div>
                </div>
            </div>
            
            <!-- Status Row -->
            <div class="status-row">
                <div class="item"><i class="fas fa-history"></i> Last Auto-Run: <span id="lastAutoRun">Never</span></div>
                <div class="item"><i class="fas fa-info-circle"></i> Status: <span id="autoRunStatus">Idle</span></div>
                <div class="item"><i class="fas fa-comment"></i> Message: <span id="autoRunMessage">-</span></div>
            </div>
            
            <!-- Dashboard Section -->
            <div id="section-dashboard" class="section active">
                <div class="status-grid">
                    <div class="status-card glass"><div class="num" style="color:#4488ff;" id="total-accounts">0</div><div class="lbl"><i class="fas fa-users"></i> Accounts</div></div>
                    <div class="status-card glass"><div class="num" style="color:#00ff66;" id="working-count">0</div><div class="lbl"><i class="fas fa-check-circle"></i> Working</div></div>
                    <div class="status-card glass"><div class="num" style="color:#ff0044;" id="timeout-count">0</div><div class="lbl"><i class="fas fa-exclamation-triangle"></i> Limit</div></div>
                    <div class="status-card glass"><div class="num" style="color:#ff66ff;" id="total-likes">0</div><div class="lbl"><i class="fas fa-heart"></i> Likes</div></div>
                    <div class="status-card glass"><div class="num" style="color:#ffcc00;" id="targets-liked">0</div><div class="lbl"><i class="fas fa-bullseye"></i> Targets</div></div>
                    <div class="status-card glass"><div class="num" style="color:#00ffff;" id="auto-users">0</div><div class="lbl"><i class="fas fa-list-ul"></i> Queue</div></div>
                </div>
            </div>
            
            <!-- 20 Likes Section -->
            <div id="section-likes20" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-arrow-right"></i> Send 20 Likes</h2>
                    <div class="input-group">
                        <input type="number" id="target-uid20" placeholder="Enter Free Fire UID" />
                        <button class="btn btn-primary" onclick="sendLikes(20, 'target-uid20')"><i class="fas fa-arrow-right"></i> Send 20 Likes</button>
                    </div>
                    <div class="note"><i class="fas fa-info-circle"></i> Sends exactly 20 verified likes to the target UID.</div>
                </div>
            </div>
            
            <!-- Unlimited Section -->
            <div id="section-unlimited" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-infinity"></i> Unlimited Likes</h2>
                    <div class="input-group">
                        <input type="number" id="target-uid-unlimited" placeholder="Enter Free Fire UID" />
                        <button class="btn btn-success" onclick="sendLikes(492, 'target-uid-unlimited')"><i class="fas fa-infinity"></i> Send All Likes</button>
                    </div>
                    <div class="note"><i class="fas fa-info-circle"></i> Sends all available likes from all accounts.</div>
                </div>
            </div>
            
            <!-- Auto Like Section -->
            <div id="section-auto" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-clock"></i> Auto Like</h2>
                    <p style="color:#8899bb; margin-bottom:15px;">Daily auto-like at 4:02 AM IST. Add UIDs to the queue.</p>
                    <div class="input-group">
                        <input type="number" id="target-uid-auto" placeholder="Enter Free Fire UID" />
                        <button class="btn btn-warning" onclick="addAutoUser()"><i class="fas fa-plus"></i> Add to Queue</button>
                        <button class="btn btn-danger" onclick="deleteAllAuto()"><i class="fas fa-trash"></i> Clear Queue</button>
                    </div>
                    <div class="user-list" id="auto-user-list" style="margin-top:12px;"></div>
                </div>
            </div>
            
            <!-- Verify Section -->
            <div id="section-verify" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-check-double"></i> Verify Likes</h2>
                    <div class="input-group">
                        <input type="number" id="target-uid-verify" placeholder="Enter Free Fire UID" />
                        <button class="btn btn-primary" onclick="verifyLikes()"><i class="fas fa-check-double"></i> Verify</button>
                    </div>
                    <div id="verify-result" style="margin-top:15px;"></div>
                </div>
            </div>
            
            <!-- History Section -->
            <div id="section-history" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-history"></i> Like History</h2>
                    <div id="history-list"></div>
                </div>
            </div>
            
            <!-- Accounts Section -->
            <div id="section-accounts" class="section">
                <div class="section-title"><i class="fas fa-users"></i> Account Status <span class="live-dot"></span></div>
                <div class="table-wrap glass" style="padding:0; overflow:hidden;">
                    <table>
                        <thead><tr><th>UID</th><th>Status</th><th>Last Check</th><th>Reset Time</th><th>Last Error</th></tr></thead>
                        <tbody id="account-table"></tbody>
                    </table>
                </div>
            </div>
            
            <!-- Statistics Section -->
            <div id="section-stats" class="section">
                <div class="panel glass">
                    <h2><i class="fas fa-chart-bar"></i> Statistics</h2>
                    <div id="stats-content"></div>
                </div>
            </div>
            
            <!-- Logs Section -->
            <div id="section-logs" class="section">
                <div class="section-title"><i class="fas fa-terminal"></i> Activity Log</div>
                <div class="log-area glass" style="background:rgba(0,0,0,0.3);">
                    <div id="log-content"><div class="log-entry"><span class="log-info">System ready.</span></div></div>
                </div>
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
                <div class="row"><span class="label">Before</span><span class="value" id="res-before">0</span></div>
                <div class="row"><span class="label">After</span><span class="value" id="res-after">0</span></div>
                <div class="row"><span class="label">Verified Added</span><span class="value" id="res-added">0</span></div>
            </div>
            <button class="close-btn" onclick="closeResult()"><i class="fas fa-times"></i> Close</button>
        </div>
    </div>

    <script>
        let currentSection = 'dashboard';
        
        function showSection(id) {
            document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
            document.getElementById('section-' + id).classList.add('active');
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.querySelector(`.nav-item[onclick*="${id}"]`).classList.add('active');
            currentSection = id;
            if (id === 'history') loadHistory();
            if (id === 'stats') loadStats();
        }
        
        function formatTime(iso) {
            if (!iso) return 'Never';
            try { const d = new Date(iso); return d.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch { return iso; }
        }
        
        function loadData() {
            fetch('/api/dashboard-data')
                .then(res => res.json())
                .then(data => {
                    if (data.error) { return; }
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
                    
                    // Auto queue
                    let userHtml = '';
                    if (data.users && data.users.length > 0) {
                        data.users.forEach(user => {
                            const s = data.user_stats[user] || { total_likes: 0, today_likes: 0 };
                            userHtml += `<div class="user-item" style="background:rgba(0,255,255,0.05);padding:8px 14px;border-radius:20px;display:inline-flex;align-items:center;gap:10px;border:1px solid rgba(0,255,255,0.1);margin:4px;">
                                <span style="font-weight:bold;color:#00ffff;">${user}</span>
                                <span style="color:#8899bb;font-size:0.8em;">T:<span style="color:#00ff66;font-weight:bold;">${s.total_likes||0}</span> D:<span style="color:#00ff66;font-weight:bold;">${s.today_likes||0}</span></span>
                                <button onclick="deleteUser('${user}')" style="background:none;border:none;color:#ff0044;cursor:pointer;"><i class="fas fa-times"></i></button>
                            </div>`;
                        });
                    } else {
                        userHtml = '<div class="note">No users in auto-queue</div>';
                    }
                    document.getElementById('auto-user-list').innerHTML = userHtml;
                    
                    // Account table
                    let tableHtml = '';
                    if (data.accounts && data.accounts.length > 0) {
                        data.accounts.forEach(acc => {
                            const cls = acc.status === 'working' ? 'working' : acc.status === 'timeout' ? 'timeout' : 'unknown';
                            tableHtml += `<tr><td><strong>${acc.uid}</strong></td><td><span class="badge badge-${cls}">${acc.status}</span></td><td>${acc.last_check ? formatTime(acc.last_check) : 'Never'}</td><td>${acc.reset_time ? formatTime(acc.reset_time) : 'N/A'}</td><td>${acc.last_error || 'None'}</td></tr>`;
                        });
                    } else {
                        tableHtml = '<tr><td colspan="5">No accounts loaded</td></tr>';
                    }
                    document.getElementById('account-table').innerHTML = tableHtml;
                    
                    // Logs
                    if (data.logs && data.logs.length > 0) {
                        let logHtml = '';
                        data.logs.forEach(log => {
                            logHtml += `<div class="log-entry"><span class="log-time">[${log.time}]</span> <span class="log-${log.type}">${log.message}</span></div>`;
                        });
                        document.getElementById('log-content').innerHTML = logHtml;
                    }
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
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);"><span style="color:#8899bb;">Total Likes Sent</span><span style="color:#00ff66;font-weight:bold;">${data.total_likes_sent}</span></div>
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);"><span style="color:#8899bb;">Total Targets</span><span style="color:#00ff66;font-weight:bold;">${data.total_targets}</span></div>
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);"><span style="color:#8899bb;">Working Accounts</span><span style="color:#00ff66;font-weight:bold;">${data.working_accounts}</span></div>
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);"><span style="color:#8899bb;">Auto Queue Users</span><span style="color:#00ff66;font-weight:bold;">${data.auto_users}</span></div>
                        <div class="row" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);"><span style="color:#8899bb;">Next Reset</span><span style="color:#ffcc00;font-weight:bold;">${data.next_reset}</span></div>
                    `;
                    document.getElementById('stats-content').innerHTML = html;
                });
        }
        
        function showResult(data) {
            document.getElementById('res-name').textContent = data.username || 'Unknown';
            document.getElementById('res-sent').textContent = data.likes_sent || 0;
            document.getElementById('res-before').textContent = data.likes_before || 0;
            document.getElementById('res-after').textContent = data.likes_after || 0;
            document.getElementById('res-added').textContent = data.verified_added || 0;
            document.getElementById('resultModal').classList.add('active');
        }
        
        function closeResult() { document.getElementById('resultModal').classList.remove('active'); }
        
        function sendLikes(count, inputId) {
            const uid = document.getElementById(inputId).value.trim();
            if (!uid) { alert('Enter a UID'); return; }
            if (!confirm(`Send ${count} likes to ${uid}?`)) return;
            
            const btn = document.querySelector(`#section-${inputId.includes('20') ? 'likes20' : 'unlimited'} .btn-primary, #section-${inputId.includes('20') ? 'likes20' : 'unlimited'} .btn-success`);
            if (btn) { btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; btn.disabled = true; }
            
            fetch('/send-likes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, server_name: 'IND', key: 'JMLB', count })
            })
            .then(res => res.json())
            .then(data => {
                if (btn) { btn.innerHTML = '<i class="fas fa-check"></i> Done'; setTimeout(() => { btn.innerHTML = count === 492 ? '<i class="fas fa-infinity"></i> Send All Likes' : '<i class="fas fa-arrow-right"></i> Send 20 Likes'; btn.disabled = false; }, 2000); }
                if (data.success) {
                    showResult(data);
                    loadData();
                    if (currentSection === 'history') loadHistory();
                } else {
                    alert('✗ Error: ' + (data.error || 'Unknown error'));
                }
            });
        }
        
        function verifyLikes() {
            const uid = document.getElementById('target-uid-verify').value.trim();
            if (!uid) { alert('Enter a UID'); return; }
            
            fetch('/verify-likes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, server_name: 'IND', key: 'JMLB' })
            })
            .then(res => res.json())
            .then(data => {
                document.getElementById('verify-result').innerHTML = `
                    <div class="user-stat-card" style="background:rgba(0,255,255,0.03);padding:14px;border-radius:10px;border:1px solid rgba(0,255,255,0.05);">
                        <div class="uid" style="color:#00ffff;font-weight:bold;font-size:1em;">UID: ${data.uid}</div>
                        <div class="name" style="color:#fff;font-size:0.9em;">Name: ${data.username}</div>
                        <div class="row" style="display:flex;justify-content:space-between;margin-top:4px;font-size:0.85em;color:#8899bb;"><span>Total Likes</span><span class="val" style="color:#00ff66;font-weight:bold;">${data.likes}</span></div>
                    </div>
                `;
            });
        }
        
        function addAutoUser() {
            const uid = document.getElementById('target-uid-auto').value.trim();
            if (!uid) { alert('Enter a UID'); return; }
            fetch('/add-user', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid }) })
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
        
        document.getElementById('resultModal').addEventListener('click', function(e) { if (e.target === this) closeResult(); });
        
        loadData();
        setInterval(loadData, 3000);
    </script>
</body>
</html>
'''

# ---------- Routes ----------
@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/dashboard-data')
def dashboard_data():
    server = 'IND'
    accounts = load_accounts(server)
    if not accounts:
        return jsonify({'error': f'No accounts found for server {server}.'})
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
        'auto_users': len(auto_like_users),
        'next_reset': next_reset,
        'users': auto_like_users,
        'user_stats': user_stats,
        'accounts': account_list,
        'logs': logs,
        'last_auto_run': None,
        'auto_run_status': 'Idle',
        'auto_run_message': ''
    })

@app.route('/api/history')
def get_history():
    return jsonify({'history': like_history[-50:]})

@app.route('/api/stats')
def get_stats():
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

@app.route('/verify-likes', methods=['POST'])
def verify_likes():
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

    user_info_before = asyncio.run(get_user_info(uid, server_name))
    before_likes = user_info_before.get('likes', 0) if user_info_before else 0
    before_name = user_info_before.get('name', 'Unknown') if user_info_before else 'Unknown'

    if server_name == "IND":
        like_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        like_url = "https://client.us.freefiremobile.com/LikeProfile"
    else:
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"
    
    result = asyncio.run(send_likes_until_complete(uid, server_name, like_url, count))
    likes_sent = result['success']

    user_info_after = asyncio.run(get_user_info(uid, server_name))
    if user_info_after:
        username = user_info_after.get('name', 'Unknown')
        current_likes = user_info_after.get('likes', 0)
        update_user_stats(uid, likes_sent, username, current_likes)
        after_likes = current_likes
    else:
        after_likes = before_likes
        username = before_name

    if likes_sent > 0 and uid not in auto_like_users:
        auto_like_users.append(uid)
        save_users()

    # Add to history
    add_to_history(uid, likes_sent, before_likes, after_likes, username)

    return jsonify({
        'success': likes_sent > 0,
        'likes_sent': likes_sent,
        'username': username,
        'total_likes': after_likes,
        'likes_before': before_likes,
        'likes_after': after_likes,
        'verified_added': after_likes - before_likes,
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
        before_name = before_data['AccountInfo'].get('PlayerNickname', 'Unknown')
    except:
        return jsonify({"error": "Data parsing failed", "status": 0}), 200

    if server_name == "IND":
        like_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        like_url = "https://client.us.freefiremobile.com/LikeProfile"
    else:
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"

    limit = requested_likes if requested_likes and requested_likes > 0 else 50
    result = asyncio.run(send_likes_until_complete(uid, server_name, like_url, limit))
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

    # Add to history
    add_to_history(uid, success_count, before_like, after_like, player_name)

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

# ---------- Startup ----------
load_liked_data()
load_account_status()
load_users()

reset_thread = threading.Thread(target=daily_reset_task, daemon=True)
reset_thread.start()

auto_thread = threading.Thread(target=start_auto_like, daemon=True)
auto_thread.start()

threading.Thread(target=run_status_check).start()

print("✅ Auto-Like System Started – New UI with All Buttons")
print(f"📁 Accounts: {len(load_accounts('IND'))} (IND)")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)