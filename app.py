# MINISTER LIKE API SRC UID PASSWORD 
# POWERED BY : @minister_69
# CHANNEL : @minister_6T9
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
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
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

TOKEN_CACHE = {}
app = Flask(__name__)

KEY_LIMIT = 500
tracker = defaultdict(lambda: [0, time.time()])

LIKED_DATA_FILE = "liked_data.pkl"
liked_cache = defaultdict(set)
like_timestamps = {}

ACCOUNT_STATUS_FILE = "account_status.pkl"
account_status = {}

AUTO_TARGETS_FILE = "auto_targets.pkl"
auto_targets = set()

RESET_HOUR = 3
RESET_MINUTE = 0
RESET_SECOND = 0

RATE_LIMIT_DELAYS = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]

# Indian timezone
IST = pytz.timezone('Asia/Kolkata')

def load_auto_targets():
    global auto_targets
    try:
        if os.path.exists(AUTO_TARGETS_FILE):
            with open(AUTO_TARGETS_FILE, 'rb') as f:
                auto_targets = pickle.load(f)
                print(f"✅ Loaded auto targets: {len(auto_targets)}")
    except:
        auto_targets = set()

def save_auto_targets():
    try:
        with open(AUTO_TARGETS_FILE, 'wb') as f:
            pickle.dump(auto_targets, f)
    except Exception as e:
        print(f"❌ Error saving auto targets: {e}")

def load_account_status():
    global account_status
    try:
        if os.path.exists(ACCOUNT_STATUS_FILE):
            with open(ACCOUNT_STATUS_FILE, 'rb') as f:
                account_status = pickle.load(f)
                print(f"✅ Loaded account status: {len(account_status)}")
    except:
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
    except:
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
load_auto_targets()

def get_today_midnight_timestamp():
    now = datetime.now()
    midnight = datetime(now.year, now.month, now.day)
    return midnight.timestamp()

def load_accounts(server_name="IND"):
    try:
        filename = "account_ind.txt"
        if not os.path.exists(filename):
            print(f"⚠️ {filename} not found")
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
                        return data.get('jwt_token') or data.get('token')
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

def create_protobuf_message(user_id, region="IND"):
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
    return False, "max_retries_exceeded"

async def send_likes_concurrent(target_uid, server_name, url, limit=10):
    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0}
    fresh = []
    skipped = 0
    for acc in accounts:
        if is_uid_liked_in_24hrs(target_uid, acc['uid']):
            skipped += 1
        else:
            fresh.append(acc)
    if not fresh:
        return {'success': 0, 'failed': 0, 'total': len(accounts), 'skipped_24hr': skipped}
    random.shuffle(fresh)
    use = fresh[:min(limit, len(fresh))]
    protobuf = create_protobuf_message(target_uid, server_name)
    encrypted = encrypt_message(protobuf)
    sem = asyncio.Semaphore(50)
    tasks = []
    for acc in use:
        tasks.append(send_single_like(encrypted, acc, url, sem))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success = 0
    fail = 0
    for r in results:
        if isinstance(r, dict) and r.get('status') == 'success':
            success += 1
            mark_as_liked(target_uid, r['uid'])
        else:
            fail += 1
    return {'success': success, 'failed': fail, 'total': len(accounts), 'skipped_24hr': skipped, 'used': len(use)}

async def send_single_like(encrypted_uid, account, url, semaphore):
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
    msg = uid_generator_pb2.uid_generator()
    msg.krishna_ = int(uid)
    msg.teamXdarks = 1
    return encrypt_message(msg.SerializeToString())

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

# ---------- Auto-like background job ----------
def auto_like_job():
    """Scheduled job: run at 5:00 AM IST daily"""
    print(f"🚀 Running auto-like job at {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST")
    if not auto_targets:
        print("⚠️ No auto targets configured.")
        return
    accounts = load_accounts("IND")
    if not accounts:
        print("❌ No accounts loaded.")
        return
    # Run for each target
    for target in list(auto_targets):
        print(f"🎯 Processing target {target}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(send_likes_concurrent(target, "IND", "https://client.ind.freefiremobile.com/LikeProfile", 10))
        loop.close()
        print(f"Result for {target}: {result}")

# Schedule daily at 5:00 AM IST
scheduler = BackgroundScheduler(timezone=IST)
scheduler.add_job(auto_like_job, 'cron', hour=5, minute=0, id='auto_like_daily')
scheduler.start()
print("⏰ Daily auto-like scheduled at 5:00 AM IST")

# Also run every 30 minutes to keep accounts active (optional)
def check_accounts_job():
    # Just check and update status, no likes
    accounts = load_accounts("IND")
    for acc in accounts:
        if acc['uid'] not in account_status:
            account_status[acc['uid']] = {'status': 'unknown'}
    save_account_status()
scheduler.add_job(check_accounts_job, 'interval', minutes=30, id='account_check')
print("🔄 Account status check every 30 minutes")

# ---------- Flask routes ----------
@app.route('/')
def dashboard():
    accounts = load_accounts("IND")
    total = len(accounts)
    working = 0
    timeout = 0
    account_list = []
    for acc in accounts:
        uid = acc['uid']
        info = account_status.get(uid, {'status': 'unknown'})
        status = info.get('status', 'unknown')
        if status == 'working':
            working += 1
        elif status == 'timeout':
            timeout += 1
        account_list.append({
            'uid': uid,
            'status': status,
            'last_check': info.get('last_check', 'Never'),
            'reset_time': info.get('reset_time', 'N/A'),
            'last_error': info.get('last_error', 'None')
        })
    total_likes = sum(len(v) for v in liked_cache.values())
    targets_liked = len(liked_cache)
    next_reset = get_next_reset_time().strftime('%Y-%m-%d %H:%M:%S IST')
    return render_template_string(HTML_TEMPLATE,
        total_accounts=total,
        working_count=working,
        timeout_count=timeout,
        total_likes=total_likes,
        targets_liked=targets_liked,
        targets=list(auto_targets),
        accounts=account_list,
        next_reset=next_reset
    )

@app.route('/add-target', methods=['POST'])
def add_target():
    uid = request.form.get('uid', '').strip()
    if uid and uid.isdigit():
        auto_targets.add(uid)
        save_auto_targets()
    return redirect(url_for('dashboard'))

@app.route('/delete-target', methods=['POST'])
def delete_target():
    uid = request.form.get('uid', '').strip()
    if uid in auto_targets:
        auto_targets.remove(uid)
        save_auto_targets()
    return redirect(url_for('dashboard'))

@app.route('/like', methods=['GET'])
def handle_like():
    uid = request.args.get("uid")
    server_name = request.args.get("server_name", "IND").upper()
    key = request.args.get("key")
    likes_param = request.args.get("likes")
    requested_likes = int(likes_param) if likes_param and likes_param.isdigit() else None
    if key != "JMLB":
        return jsonify({"error": "Invalid API key"}), 403
    if not uid:
        return jsonify({"error": "UID required"}), 400
    if server_name not in ["IND", "BR", "US", "SAC", "NA", "BD", "RU"]:
        return jsonify({"error": "Invalid server"}), 400

    accounts = load_accounts(server_name)
    if not accounts:
        return jsonify({"error": "No accounts"}), 500

    # Get a token for before check
    check_token = None
    for acc in accounts[:5]:
        check_token = asyncio.run(get_valid_token(acc['uid'], acc['password']))
        if check_token:
            break
    if not check_token:
        return jsonify({"error": "Token failed"}), 500

    encrypted_uid = enc(uid)
    before = get_player_info(encrypted_uid, server_name, check_token)
    before_like = 0
    if before:
        try:
            data = json.loads(MessageToJson(before))
            before_like = int(data['AccountInfo'].get('Likes', 0))
        except:
            pass

    like_url = "https://client.ind.freefiremobile.com/LikeProfile" if server_name=="IND" else "https://client.us.freefiremobile.com/LikeProfile" if server_name in ["BR","US","SAC","NA"] else "https://clientbp.ggpolarbear.com/LikeProfile"
    limit = requested_likes if requested_likes and requested_likes > 0 else 50
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(send_likes_concurrent(uid, server_name, like_url, limit))
    loop.close()

    after = get_player_info(encrypted_uid, server_name, check_token)
    after_like = 0
    if after:
        try:
            data = json.loads(MessageToJson(after))
            after_like = int(data['AccountInfo'].get('Likes', 0))
        except:
            pass

    likes_given = after_like - before_like
    return jsonify({
        "LikesGivenByAPI": result['success'],
        "VerifiedLikesAdded": likes_given,
        "LikesafterCommand": after_like,
        "LikesbeforeCommand": before_like,
        "status": 1 if result['success'] > 0 else 2,
        "total_accounts": len(accounts),
        "limit_requested": limit,
        "skipped_24hr": result.get('skipped_24hr', 0),
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

@app.route('/stats', methods=['GET'])
def stats():
    key = request.args.get("key")
    if key != "JMLB":
        return jsonify({"error": "Invalid key"}), 403
    total_likes = sum(len(v) for v in liked_cache.values())
    total_uids = len(liked_cache)
    working = sum(1 for v in account_status.values() if v.get('status') == 'working')
    timeout = sum(1 for v in account_status.values() if v.get('status') == 'timeout')
    return jsonify({
        "total_uids_liked": total_uids,
        "total_likes": total_likes,
        "working_accounts": working,
        "timeout_accounts": timeout,
        "total_accounts": len(account_status),
        "auto_targets": list(auto_targets)
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "accounts": len(load_accounts()), "auto_targets": len(auto_targets)})

# ---------- HTML Template with FontAwesome icons ----------
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auto-Like Dashboard</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0b0e1a; color: #e0e5f0; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #1a1f35, #2a2f4a); padding: 25px 30px; border-radius: 12px; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; border: 1px solid #3a4055; }
        .header h1 { font-size: 2rem; font-weight: 300; }
        .header h1 i { color: #6c8cff; margin-right: 10px; }
        .header .reset-info { background: #1e2338; padding: 8px 16px; border-radius: 20px; font-size: 0.9rem; color: #aab; border: 1px solid #3a4055; }
        .header .reset-info i { color: #ffd93d; margin-right: 6px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 25px; }
        .stat-card { background: #141a2b; padding: 18px 20px; border-radius: 10px; border: 1px solid #252b42; text-align: center; transition: 0.2s; }
        .stat-card:hover { border-color: #4a5a8a; }
        .stat-card .number { font-size: 2.2rem; font-weight: 600; }
        .stat-card .label { color: #8899bb; font-size: 0.85rem; margin-top: 5px; }
        .stat-card .number i { margin-right: 8px; font-size: 1.8rem; }
        .color-blue { color: #6c8cff; }
        .color-green { color: #4ade80; }
        .color-red { color: #f87171; }
        .color-yellow { color: #fbbf24; }
        .color-purple { color: #a78bfa; }
        .color-cyan { color: #22d3ee; }

        .section-title { font-size: 1.3rem; margin: 25px 0 15px 0; font-weight: 400; }
        .section-title i { margin-right: 8px; color: #6c8cff; }

        .target-area { background: #141a2b; border-radius: 10px; padding: 20px; border: 1px solid #252b42; margin-bottom: 20px; }
        .target-area form { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        .target-area input[type="text"] { background: #1e2338; border: 1px solid #3a4055; padding: 10px 14px; border-radius: 8px; color: #fff; flex: 1; min-width: 200px; }
        .target-area input[type="text"]:focus { outline: none; border-color: #6c8cff; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 0.9rem; transition: 0.2s; }
        .btn-primary { background: #4a6cf7; color: #fff; }
        .btn-primary:hover { background: #5a7cf7; }
        .btn-danger { background: #dc2626; color: #fff; }
        .btn-danger:hover { background: #ef4444; }
        .btn-sm { padding: 5px 12px; font-size: 0.8rem; }

        .target-list { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 15px; }
        .target-tag { background: #1e2338; padding: 6px 14px; border-radius: 20px; border: 1px solid #3a4055; display: flex; align-items: center; gap: 8px; }
        .target-tag .uid { color: #c8d0e0; }
        .target-tag .delete-btn { background: none; border: none; color: #f87171; cursor: pointer; font-size: 0.9rem; }
        .target-tag .delete-btn:hover { color: #ef4444; }

        table { width: 100%; border-collapse: collapse; background: #141a2b; border-radius: 10px; overflow: hidden; border: 1px solid #252b42; }
        th { background: #1e2338; padding: 12px 15px; text-align: left; font-weight: 500; color: #8899bb; font-size: 0.85rem; }
        td { padding: 12px 15px; border-bottom: 1px solid #1a1f35; }
        .badge { padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; display: inline-block; }
        .badge-working { background: #4ade8022; color: #4ade80; border: 1px solid #4ade80; }
        .badge-timeout { background: #f8717122; color: #f87171; border: 1px solid #f87171; }
        .badge-reset { background: #fbbf2422; color: #fbbf24; border: 1px solid #fbbf24; }
        .badge-unknown { background: #8899bb22; color: #8899bb; border: 1px solid #8899bb; }

        .log-area { background: #0b0e1a; padding: 15px; border-radius: 10px; max-height: 200px; overflow-y: auto; border: 1px solid #252b42; margin-top: 20px; font-family: monospace; font-size: 0.85rem; }
        .log-entry { padding: 4px 0; border-bottom: 1px solid #141a2b; }
        .log-time { color: #6c8cff; }
        .log-success { color: #4ade80; }
        .log-error { color: #f87171; }
        .log-info { color: #fbbf24; }

        .footer { text-align: center; margin-top: 30px; color: #4a5a7a; font-size: 0.8rem; border-top: 1px solid #1a1f35; padding-top: 20px; }
        .footer i { margin: 0 4px; }
        .mt-10 { margin-top: 10px; }
        .flex { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .ml-auto { margin-left: auto; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1><i class="fas fa-robot"></i> Auto-Like Panel</h1>
        <div class="reset-info">
            <i class="fas fa-clock"></i> Next Reset: {{ next_reset }}
        </div>
    </div>

    <div class="stats-grid">
        <div class="stat-card"><div class="number color-blue"><i class="fas fa-users"></i>{{ total_accounts }}</div><div class="label">Total Accounts</div></div>
        <div class="stat-card"><div class="number color-green"><i class="fas fa-check-circle"></i>{{ working_count }}</div><div class="label">Working Now</div></div>
        <div class="stat-card"><div class="number color-red"><i class="fas fa-clock"></i>{{ timeout_count }}</div><div class="label">Limit Reached</div></div>
        <div class="stat-card"><div class="number color-purple"><i class="fas fa-heart"></i>{{ total_likes }}</div><div class="label">Total Likes Sent</div></div>
        <div class="stat-card"><div class="number color-yellow"><i class="fas fa-bullseye"></i>{{ targets_liked }}</div><div class="label">Targets Liked</div></div>
    </div>

    <div class="section-title"><i class="fas fa-plus-circle"></i> Manage Auto-Like Targets</div>
    <div class="target-area">
        <form method="POST" action="/add-target">
            <input type="text" name="uid" placeholder="Enter UID to auto-like" required>
            <button type="submit" class="btn btn-primary"><i class="fas fa-plus"></i> Add Target</button>
        </form>
        <div class="target-list">
            {% for target in targets %}
            <div class="target-tag">
                <span class="uid"><i class="fas fa-user"></i> {{ target }}</span>
                <form method="POST" action="/delete-target" style="display:inline;">
                    <input type="hidden" name="uid" value="{{ target }}">
                    <button type="submit" class="delete-btn"><i class="fas fa-times"></i></button>
                </form>
            </div>
            {% else %}
            <span style="color:#667; font-size:0.9rem;">No targets added yet.</span>
            {% endfor %}
        </div>
        <div class="mt-10" style="color:#8899bb; font-size:0.85rem;">
            <i class="fas fa-info-circle"></i> Auto-like runs daily at 5:00 AM IST for all targets.
        </div>
    </div>

    <div class="section-title"><i class="fas fa-server"></i> Account Status</div>
    <table>
        <thead><tr><th>UID</th><th>Status</th><th>Last Check</th><th>Reset Time</th><th>Last Error</th></tr></thead>
        <tbody>
        {% for acc in accounts %}
        <tr>
            <td><strong>{{ acc.uid }}</strong></td>
            <td><span class="badge badge-{{ acc.status }}">{{ acc.status|title }}</span></td>
            <td>{{ acc.last_check if acc.last_check != 'Never' else '—' }}</td>
            <td>{{ acc.reset_time if acc.reset_time != 'N/A' else '—' }}</td>
            <td>{{ acc.last_error if acc.last_error != 'None' else '—' }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>

    <div class="section-title"><i class="fas fa-history"></i> Activity Log</div>
    <div class="log-area" id="logArea">
        <div class="log-entry"><span class="log-time">[System]</span> <span class="log-info">Auto-like service running. Next run at 5:00 AM IST.</span></div>
        <!-- logs will be updated via JS if needed, or static -->
    </div>

    <div class="footer">
        <i class="fas fa-crown"></i> Powered by @minister_69 &nbsp;|&nbsp; <i class="fas fa-sync-alt"></i> Auto reset daily at 3:00 AM IST
    </div>
</div>

<script>
    // Simple auto-refresh every 30 seconds
    setTimeout(function(){ location.reload(); }, 30000);
</script>
</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print("🚀 Server started")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)