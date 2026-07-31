# ------------------------------------------------------------
#   FINAL – ACCOUNTS LOAD, 20 LIKES, FULL PROFILE RESPONSE
#   Cyberpunk UI with account status table
# ------------------------------------------------------------

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

RESET_HOUR = 4
RESET_MINUTE = 2
RESET_SECOND = 0

RATE_LIMIT_DELAYS = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]

# ---------- Data persistence ----------
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
        data = {'users': auto_like_users, 'stats': user_stats}
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

# ---------- HTML Dashboard (Cyberpunk, full account table) ----------
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
            padding-bottom: 30px;
            background-image: radial-gradient(circle at 20% 20%, rgba(0,255,255,0.05) 0%, transparent 50%),
                              radial-gradient(circle at 80% 80%, rgba(255,0,255,0.05) 0%, transparent 50%);
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 15px; }

        .glass {
            background: rgba(10,14,26,0.6);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(0,255,255,0.15);
            box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 15px rgba(0,255,255,0.05);
            border-radius: 16px;
            transition: 0.3s;
        }
        .glass:hover { border-color: rgba(0,255,255,0.3); }

        .header { padding: 20px; margin-bottom: 20px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; }
        .header h1 { font-size: 1.8em; color: #00ffff; text-shadow: 0 0 20px rgba(0,255,255,0.3); }
        .header .sub { opacity: 0.7; font-size: 0.85em; margin-top: 3px; color: #8899bb; }
        .header-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; width: 100%; }

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
            text-decoration: none;
            background: rgba(0,255,255,0.1);
            color: #00ffff;
            border: 1px solid rgba(0,255,255,0.2);
        }
        .btn:hover { background: rgba(0,255,255,0.2); transform: translateY(-2px); box-shadow: 0 0 20px rgba(0,255,255,0.1); }
        .btn-refresh { background: rgba(0,100,255,0.2); border-color: rgba(0,100,255,0.3); color: #4488ff; }
        .btn-refresh:hover { background: rgba(0,100,255,0.3); }
        .btn-check { background: rgba(255,100,0,0.2); border-color: rgba(255,100,0,0.3); color: #ff8800; }
        .btn-check:hover { background: rgba(255,100,0,0.3); }
        .btn-add { background: rgba(0,255,100,0.2); border-color: rgba(0,255,100,0.3); color: #00ff66; }
        .btn-add:hover { background: rgba(0,255,100,0.3); }
        .btn-del { background: rgba(255,0,50,0.2); border-color: rgba(255,0,50,0.3); color: #ff0044; }
        .btn-del:hover { background: rgba(255,0,50,0.3); }
        .btn-like { background: rgba(255,100,0,0.2); border-color: rgba(255,100,0,0.3); color: #ff8800; }
        .btn-like:hover { background: rgba(255,100,0,0.3); }
        .btn-like20 { background: rgba(0,50,200,0.2); border-color: rgba(0,50,200,0.3); color: #4488ff; }
        .btn-like20:hover { background: rgba(0,50,200,0.3); }
        .btn-like220 { background: rgba(200,50,0,0.2); border-color: rgba(200,50,0,0.3); color: #ff6644; }
        .btn-like220:hover { background: rgba(200,50,0,0.3); }
        .btn-auto-run { background: rgba(0,255,100,0.2); border-color: rgba(0,255,100,0.3); color: #00ff66; }
        .btn-auto-run:hover { background: rgba(0,255,100,0.3); }
        .btn-auto-run:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

        .badge-auto { background: rgba(0,255,100,0.15); color: #00ff66; padding: 4px 14px; border-radius: 20px; border: 1px solid #00ff66; font-size: 0.85em; }
        .badge-reset { color: #ffcc00; font-weight: bold; }

        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .status-card { padding: 18px 10px; text-align: center; transition: 0.3s; }
        .status-card:hover { transform: translateY(-5px); border-color: rgba(0,255,255,0.3); }
        .status-card .num { font-size: 2.2em; font-weight: bold; }
        .status-card .lbl { color: #8899bb; font-size: 0.8em; margin-top: 4px; }

        .panel { padding: 20px; margin-bottom: 20px; }
        .panel h2 { color: #8899bb; font-size: 1.1em; margin-bottom: 15px; }
        .input-group { display: flex; flex-wrap: wrap; gap: 10px; }
        .input-group input { flex: 1 1 200px; padding: 12px 15px; border-radius: 8px; border: 1px solid rgba(0,255,255,0.15); background: rgba(0,0,0,0.4); color: #fff; font-size: 1em; min-width: 150px; transition: 0.3s; }
        .input-group input:focus { outline: none; border-color: #00ffff; box-shadow: 0 0 20px rgba(0,255,255,0.05); }
        .btn-group { display: flex; flex-wrap: wrap; gap: 6px; }

        .server-selector { display: flex; align-items: center; gap: 10px; margin-bottom: 15px; flex-wrap: wrap; }
        .server-selector select { padding: 10px 15px; border-radius: 8px; border: 1px solid rgba(0,255,255,0.15); background: rgba(0,0,0,0.4); color: #fff; font-size: 1em; cursor: pointer; min-width: 120px; }
        .server-selector select:focus { outline: none; border-color: #00ffff; }

        .user-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
        .user-item { background: rgba(0,255,255,0.05); padding: 8px 14px; border-radius: 20px; display: flex; align-items: center; gap: 10px; border: 1px solid rgba(0,255,255,0.1); flex-wrap: wrap; font-size: 0.9em; }
        .user-item .uid { font-weight: bold; color: #00ffff; }
        .user-item .stats { color: #8899bb; font-size: 0.8em; }
        .user-item .stats span { color: #00ff66; font-weight: bold; }
        .user-item .del-btn { background: none; border: none; color: #ff0044; cursor: pointer; font-size: 1.2em; padding: 0 5px; }

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

        .error-msg { background: rgba(255,0,50,0.1); border: 1px solid #ff0044; color: #ff0044; padding: 15px; border-radius: 12px; margin: 10px 0; text-align: center; }

        @media (max-width: 600px) {
            .header h1 { font-size: 1.5em; }
            .btn { font-size: 0.8em; padding: 8px 12px; }
            .status-grid { grid-template-columns: repeat(3, 1fr); }
            .user-item { width: 100%; }
            .input-group input { min-width: 120px; }
            .header-actions { flex-direction: column; align-items: stretch; }
            .header-actions .btn { width: 100%; justify-content: center; }
            .status-row { flex-direction: column; align-items: stretch; }
            .server-selector { flex-direction: column; align-items: stretch; }
        }
        @media (max-width: 400px) {
            .status-grid { grid-template-columns: 1fr 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header glass">
            <div>
                <h1><i class="fas fa-bolt"></i> Auto-Like Dashboard</h1>
                <div class="sub"><i class="far fa-clock"></i> Real-time monitoring · Auto-reset daily at 4:02 AM IST</div>
            </div>
            <div class="header-actions">
                <span class="badge-auto"><i class="fas fa-play"></i> Auto-Like Running</span>
                <span><i class="fas fa-sync-alt"></i> Reset: <span class="badge-reset" id="next-reset">Loading...</span></span>
                <button class="btn btn-refresh" onclick="location.reload()"><i class="fas fa-sync"></i></button>
                <button class="btn btn-check" onclick="checkStatus()"><i class="fas fa-search"></i></button>
                <button class="btn btn-auto-run" onclick="forceAutoRun()" id="forceAutoBtn"><i class="fas fa-play"></i> Run Auto</button>
            </div>
        </div>

        <div class="status-row">
            <div class="item"><i class="fas fa-history"></i> Last Auto-Run: <span id="lastAutoRun">Never</span></div>
            <div class="item"><i class="fas fa-info-circle"></i> Status: <span id="autoRunStatus">Idle</span></div>
            <div class="item"><i class="fas fa-comment"></i> Message: <span id="autoRunMessage">-</span></div>
        </div>

        <div class="status-grid">
            <div class="status-card glass"><div class="num" style="color:#4488ff;" id="total-accounts">0</div><div class="lbl"><i class="fas fa-users"></i> Accounts</div></div>
            <div class="status-card glass"><div class="num" style="color:#00ff66;" id="working-count">0</div><div class="lbl"><i class="fas fa-check-circle"></i> Working</div></div>
            <div class="status-card glass"><div class="num" style="color:#ff0044;" id="timeout-count">0</div><div class="lbl"><i class="fas fa-exclamation-triangle"></i> Limit</div></div>
            <div class="status-card glass"><div class="num" style="color:#ff66ff;" id="total-likes">0</div><div class="lbl"><i class="fas fa-heart"></i> Likes</div></div>
            <div class="status-card glass"><div class="num" style="color:#ffcc00;" id="targets-liked">0</div><div class="lbl"><i class="fas fa-bullseye"></i> Targets</div></div>
            <div class="status-card glass"><div class="num" style="color:#00ffff;" id="auto-users">0</div><div class="lbl"><i class="fas fa-list-ul"></i> Queue</div></div>
        </div>

        <div class="panel glass">
            <h2><i class="fas fa-paper-plane"></i> Send Likes</h2>
            <div class="server-selector">
                <label for="server-select"><i class="fas fa-globe"></i> Server:</label>
                <select id="server-select" onchange="changeServer()">
                    <option value="IND">India</option>
                    <option value="BD">Bangladesh</option>
                    <option value="MENA">MENA</option>
                    <option value="BR">Brazil</option>
                    <option value="US">US</option>
                    <option value="SAC">SAC</option>
                    <option value="NA">NA</option>
                    <option value="RU">Russia</option>
                </select>
            </div>
            <div class="input-group">
                <input type="number" id="target-uid" placeholder="Enter Free Fire UID" />
                <div class="btn-group">
                    <button class="btn btn-like20" onclick="sendLikes(20)">20</button>
                    <button class="btn btn-like220" onclick="sendLikes(220)">220</button>
                    <button class="btn btn-like" onclick="sendLikes(492)">All</button>
                </div>
            </div>
            <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:8px;">
                <button class="btn btn-add" onclick="addAutoUser()"><i class="fas fa-plus"></i> Add to Queue</button>
                <button class="btn btn-del" onclick="deleteAllAuto()"><i class="fas fa-trash"></i> Clear Queue</button>
            </div>
            <div class="user-list" id="auto-user-list"></div>
            <div class="note"><i class="fas fa-info-circle"></i> Enter UID and click like count. Successful likes automatically add to auto-queue.</div>
        </div>

        <!-- ACCOUNT STATUS TABLE – FIXED, SHOWS ALL ACCOUNTS -->
        <div class="section-title"><i class="fas fa-table"></i> Account Status <span class="live-dot"></span></div>
        <div id="account-error" class="error-msg" style="display:none;"><i class="fas fa-exclamation-circle"></i> <span id="error-text">No accounts loaded. Check account file.</span></div>
        <div class="table-wrap glass" style="padding:0; overflow:hidden;">
            <table>
                <thead><tr><th>UID</th><th>Status</th><th>Last Check</th><th>Reset Time</th><th>Last Error</th></tr></thead>
                <tbody id="account-table"></tbody>
            </table>
        </div>

        <div class="section-title"><i class="fas fa-users"></i> Auto-Queue Users</div>
        <div class="user-stats-grid" id="auto-queue-stats"></div>

        <div class="section-title"><i class="fas fa-terminal"></i> Activity Log</div>
        <div class="log-area glass" style="background:rgba(0,0,0,0.3);">
            <div class="log-entry"><span class="log-info">System ready.</span></div>
        </div>
    </div>

    <script>
        let currentServer = 'IND';

        function changeServer() {
            currentServer = document.getElementById('server-select').value;
            loadData();
            checkStatus();
        }

        function formatTime(iso) {
            if (!iso) return 'Never';
            try {
                const d = new Date(iso);
                return d.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
            } catch { return iso; }
        }

        function loadData() {
            fetch('/api/dashboard-data?server=' + currentServer)
                .then(res => res.json())
                .then(data => {
                    if (data.error) {
                        document.getElementById('account-error').style.display = 'block';
                        document.getElementById('error-text').textContent = data.error;
                        return;
                    }
                    document.getElementById('account-error').style.display = 'none';

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

                    let userHtml = '';
                    if (data.users && data.users.length > 0) {
                        data.users.forEach(user => {
                            const s = data.user_stats[user] || { total_likes: 0, today_likes: 0 };
                            userHtml += `<div class="user-item">
                                <span class="uid">${user}</span>
                                <span class="stats">T:<span>${s.total_likes||0}</span> D:<span>${s.today_likes||0}</span></span>
                                <button class="del-btn" onclick="deleteUser('${user}')"><i class="fas fa-times"></i></button>
                            </div>`;
                        });
                    } else {
                        userHtml = '<div class="note">No users in auto-queue</div>';
                    }
                    document.getElementById('auto-user-list').innerHTML = userHtml;

                    // ACCOUNT TABLE – THIS WILL SHOW ALL ACCOUNTS
                    let tableHtml = '';
                    if (data.accounts && data.accounts.length > 0) {
                        data.accounts.forEach(acc => {
                            const cls = acc.status === 'working' ? 'working' : acc.status === 'timeout' ? 'timeout' : 'unknown';
                            tableHtml += `<tr>
                                <td><strong>${acc.uid}</strong></td>
                                <td><span class="badge badge-${cls}">${acc.status}</span></td>
                                <td>${acc.last_check ? formatTime(acc.last_check) : 'Never'}</td>
                                <td>${acc.reset_time ? formatTime(acc.reset_time) : 'N/A'}</td>
                                <td>${acc.last_error || 'None'}</td>
                            </tr>`;
                        });
                    } else {
                        tableHtml = '<tr><td colspan="5">No accounts loaded</td></tr>';
                    }
                    document.getElementById('account-table').innerHTML = tableHtml;

                    let statsHtml = '';
                    if (data.users && data.users.length > 0) {
                        data.users.forEach(uid => {
                            const s = data.user_stats[uid] || { total_likes: 0, today_likes: 0, last_like: null, username: '' };
                            statsHtml += `<div class="user-stat-card">
                                <div class="uid">UID: ${uid}</div>
                                <div class="name">${s.username || 'Unknown'}</div>
                                <div class="row"><span>Total</span><span class="val">${s.total_likes||0}</span></div>
                                <div class="row"><span>Today</span><span class="val">${s.today_likes||0}</span></div>
                                <div class="last">Last: ${s.last_like ? formatTime(s.last_like) : 'Never'}</div>
                            </div>`;
                        });
                    } else {
                        statsHtml = '<div class="note">No auto-queue users</div>';
                    }
                    document.getElementById('auto-queue-stats').innerHTML = statsHtml;

                    if (data.logs && data.logs.length > 0) {
                        let logHtml = '';
                        data.logs.forEach(log => {
                            logHtml += `<div class="log-entry">
                                <span class="log-time">[${log.time}]</span>
                                <span class="log-${log.type}">${log.message}</span>
                            </div>`;
                        });
                        document.getElementById('log-area').innerHTML = logHtml;
                    }
                })
                .catch(err => {
                    document.getElementById('account-error').style.display = 'block';
                    document.getElementById('error-text').textContent = 'Failed to load data: ' + err.message;
                });
        }

        function checkStatus() {
            fetch('/api/check-status?server=' + currentServer)
                .then(res => res.json())
                .then(data => {
                    console.log('Status check started');
                    setTimeout(loadData, 3000);
                });
        }

        function sendLikes(count) {
            const uid = document.getElementById('target-uid').value.trim();
            if (!uid) { alert('Enter a UID'); return; }
            if (!confirm(`Send ${count} likes to ${uid} on server ${currentServer}?`)) return;

            const btns = document.querySelectorAll('.btn-like, .btn-like20, .btn-like220');
            const btn = btns[0];
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            btn.disabled = true;

            fetch('/send-likes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, server_name: currentServer, key: 'JMLB', count })
            })
            .then(res => res.json())
            .then(data => {
                btn.innerHTML = '<i class="fas fa-check"></i>';
                btn.disabled = false;
                if (data.success) {
                    alert(`✓ Sent ${data.likes_sent} likes to ${data.username || uid}\nTotal: ${data.total_likes}\nBefore: ${data.likes_before}\nAfter: ${data.likes_after}\nVerified Added: ${data.verified_added}`);
                } else {
                    alert('✗ Error: ' + (data.error || 'Unknown error'));
                }
                loadData();
            });
        }

        function addAutoUser() {
            const uid = document.getElementById('target-uid').value.trim();
            if (!uid) { alert('Enter a UID'); return; }
            fetch('/add-user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    alert('Added to queue: ' + uid);
                    loadData();
                } else {
                    alert(data.message);
                }
            });
        }

        function deleteUser(uid) {
            if (!confirm(`Remove ${uid} from queue?`)) return;
            fetch('/delete-user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) loadData();
                else alert(data.message);
            });
        }

        function deleteAllAuto() {
            if (!confirm('Clear entire auto-queue?')) return;
            fetch('/delete-all-users', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data.success) loadData();
                    else alert(data.message);
                });
        }

        function forceAutoRun() {
            const btn = document.getElementById('forceAutoBtn');
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            btn.disabled = true;
            fetch('/force-auto-run', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    btn.innerHTML = '<i class="fas fa-play"></i> Run Auto';
                    btn.disabled = false;
                    if (data.success) {
                        alert('Auto-run triggered! Check logs.');
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

# ---------- Routes ----------
@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/dashboard-data')
def dashboard_data():
    server = request.args.get('server', 'IND')
    accounts = load_accounts(server)
    if not accounts:
        return jsonify({'error': f'No accounts found for server {server}. Check account file.'})
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

@app.route('/api/check-status')
def check_status_api():
    server = request.args.get('server', 'IND')
    threading.Thread(target=run_status_check).start()
    return jsonify({'message': f'Status check started for {server}'})

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

    # Get user info BEFORE
    user_info_before = asyncio.run(get_user_info(uid, server_name))
    before_likes = user_info_before.get('likes', 0) if user_info_before else 0
    before_name = user_info_before.get('name', 'Unknown') if user_info_before else 'Unknown'

    # Send likes
    if server_name == "IND":
        like_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        like_url = "https://client.us.freefiremobile.com/LikeProfile"
    else:
        like_url = "https://clientbp.ggpolarbear.com/LikeProfile"
    result = asyncio.run(send_likes_batch(uid, server_name, like_url, count))
    likes_sent = result['success']

    # Get user info AFTER
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

@app.route('/force-auto-run', methods=['POST'])
def force_auto_run():
    def run_auto():
        asyncio.run(auto_like_daily_once())
    threading.Thread(target=run_auto).start()
    return jsonify({'success': True, 'message': 'Auto-run started'})

async def auto_like_daily_once():
    print("Manual auto-like run started")
    accounts = load_accounts("IND")
    if not accounts:
        print("No accounts")
        return
    for user_uid in auto_like_users:
        print(f"Processing {user_uid}")
        result = await send_likes_batch(
            user_uid,
            "IND",
            "https://client.ind.freefiremobile.com/LikeProfile",
            50
        )
        print(f"Sent {result['success']} likes to {user_uid}")
        await asyncio.sleep(3)

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
    except Exception as e:
        return jsonify({"error": str(e), "status": 0}), 500

    if success_count > 0:
        tracker[client_ip][0] += 1
        count += 1

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

print("✅ Auto-Like System Started – Cyberpunk UI with Full Account Table")
print(f"📁 Accounts: {len(load_accounts('IND'))} (IND)")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)