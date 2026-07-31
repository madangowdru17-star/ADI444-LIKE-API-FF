<?php
// Auto-Like System Dashboard
// Powered by Advanced AI | Auto-reset daily at 4:00 AM IST

// Configuration
$API_URL = "https://adi444-like-api-ff-production.up.railway.app";
$API_KEY = "JMLB";
$SERVER_NAME = "IND";

// Function to call API
function callAPI($endpoint, $params = []) {
    global $API_URL, $API_KEY;
    
    $url = $API_URL . $endpoint . "?key=" . $API_KEY;
    foreach ($params as $key => $value) {
        $url .= "&" . urlencode($key) . "=" . urlencode($value);
    }
    
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 30);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    
    if ($httpCode == 200) {
        return json_decode($response, true);
    }
    return null;
}

// Get stats
$stats = callAPI("/stats");
$health = callAPI("/health");

// Get users
$users = [];
$userStats = [];
if (file_exists('users.json')) {
    $data = json_decode(file_get_contents('users.json'), true);
    $users = $data['users'] ?? [];
    $userStats = $data['stats'] ?? [];
}

// Save users
function saveUsers($users, $userStats) {
    file_put_contents('users.json', json_encode([
        'users' => $users,
        'stats' => $userStats
    ], JSON_PRETTY_PRINT));
}

// Handle AJAX requests
if (isset($_GET['action'])) {
    header('Content-Type: application/json');
    
    if ($_GET['action'] == 'add_user' && isset($_POST['uid'])) {
        $uid = trim($_POST['uid']);
        if (!empty($uid) && !in_array($uid, $users)) {
            $users[] = $uid;
            $userStats[$uid] = [
                'total_likes' => 0,
                'today_likes' => 0,
                'last_like' => null
            ];
            saveUsers($users, $userStats);
            echo json_encode(['success' => true, 'message' => 'User added']);
        } else {
            echo json_encode(['success' => false, 'message' => 'Invalid or duplicate UID']);
        }
        exit;
    }
    
    if ($_GET['action'] == 'delete_user' && isset($_POST['uid'])) {
        $uid = trim($_POST['uid']);
        if (in_array($uid, $users)) {
            $users = array_values(array_diff($users, [$uid]));
            unset($userStats[$uid]);
            saveUsers($users, $userStats);
            echo json_encode(['success' => true, 'message' => 'User deleted']);
        } else {
            echo json_encode(['success' => false, 'message' => 'User not found']);
        }
        exit;
    }
    
    if ($_GET['action'] == 'delete_all_users') {
        $users = [];
        $userStats = [];
        saveUsers($users, $userStats);
        echo json_encode(['success' => true, 'message' => 'All users deleted']);
        exit;
    }
    
    if ($_GET['action'] == 'send_like' && isset($_POST['uid'])) {
        $uid = trim($_POST['uid']);
        $likes = isset($_POST['likes']) ? intval($_POST['likes']) : 10;
        $result = callAPI("/like", ['uid' => $uid, 'server_name' => 'IND', 'likes' => $likes]);
        if ($result) {
            echo json_encode(['success' => true, 'data' => $result]);
        } else {
            echo json_encode(['success' => false, 'message' => 'API error']);
        }
        exit;
    }
    
    if ($_GET['action'] == 'get_status') {
        echo json_encode([
            'health' => $health,
            'stats' => $stats,
            'users' => $users,
            'userStats' => $userStats
        ]);
        exit;
    }
    
    if ($_GET['action'] == 'get_player_info' && isset($_POST['uid'])) {
        $uid = trim($_POST['uid']);
        $result = callAPI("/like", ['uid' => $uid, 'server_name' => 'IND', 'likes' => 1]);
        if ($result) {
            echo json_encode(['success' => true, 'data' => $result]);
        } else {
            echo json_encode(['success' => false, 'message' => 'Player not found']);
        }
        exit;
    }
}

// Get next reset time
$nextReset = date('Y-m-d H:i:s', strtotime('tomorrow 04:00:00'));
if (date('H') < 4) {
    $nextReset = date('Y-m-d H:i:s', strtotime('today 04:00:00'));
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auto-Like System</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Orbitron', sans-serif;
            background: #0a0e1a;
            color: #ffffff;
            min-height: 100vh;
            overflow-x: hidden;
        }
        .loader-wrapper {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: #0a0e1a;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            z-index: 9999;
            transition: opacity 0.8s ease, visibility 0.8s ease;
        }
        .loader-wrapper.hidden {
            opacity: 0;
            visibility: hidden;
        }
        .loader {
            width: 80px;
            height: 80px;
            border: 4px solid #1a237e;
            border-top: 4px solid #42a5f5;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 30px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .loader-text {
            color: #42a5f5;
            font-size: 1.2em;
            letter-spacing: 4px;
            animation: pulse 1.5s ease-in-out infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 0.4; }
            50% { opacity: 1; }
        }
        .loader-sub {
            color: #8899bb;
            font-size: 0.8em;
            margin-top: 10px;
            letter-spacing: 2px;
        }
        .ddos-badge {
            display: inline-block;
            padding: 8px 20px;
            border-radius: 30px;
            background: linear-gradient(135deg, #1a237e, #283593);
            border: 1px solid #42a5f5;
            color: #42a5f5;
            font-size: 0.7em;
            letter-spacing: 3px;
            margin-top: 20px;
            box-shadow: 0 0 20px rgba(66, 165, 245, 0.3);
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            opacity: 0;
            animation: fadeIn 0.8s ease 0.5s forwards;
        }
        @keyframes fadeIn {
            to { opacity: 1; }
        }
        .header {
            background: linear-gradient(135deg, #1a237e, #283593);
            padding: 30px;
            border-radius: 15px;
            margin-bottom: 25px;
            text-align: center;
            border: 1px solid #2a3a5a;
            position: relative;
            overflow: hidden;
        }
        .header::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, rgba(66, 165, 245, 0.05) 0%, transparent 70%);
            animation: rotateGlow 20s linear infinite;
        }
        @keyframes rotateGlow {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .header h1 {
            font-size: 2.8em;
            font-weight: 900;
            background: linear-gradient(135deg, #42a5f5, #4caf50);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            position: relative;
            letter-spacing: 5px;
        }
        .header p {
            color: #8899bb;
            margin-top: 10px;
            position: relative;
            letter-spacing: 2px;
        }
        .badge-container {
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 15px;
            flex-wrap: wrap;
            position: relative;
        }
        .badge-item {
            padding: 8px 20px;
            border-radius: 30px;
            background: rgba(255,255,255,0.05);
            border: 1px solid #2a3a5a;
            font-size: 0.75em;
            color: #8899bb;
        }
        .badge-item .highlight { color: #42a5f5; font-weight: bold; }
        .badge-item .highlight.green { color: #4caf50; }
        .badge-item .highlight.red { color: #f44336; }
        .badge-item .highlight.yellow { color: #ffc107; }
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-bottom: 25px;
        }
        .status-card {
            background: #141928;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
            border: 1px solid #1e2a4a;
            transition: transform 0.3s ease, border-color 0.3s ease;
        }
        .status-card:hover {
            transform: translateY(-5px);
            border-color: #2a3a5a;
        }
        .status-card .number {
            font-size: 2.8em;
            font-weight: 900;
        }
        .status-card .label {
            color: #8899bb;
            font-size: 0.8em;
            margin-top: 5px;
            letter-spacing: 1px;
        }
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
        .panel h2 {
            color: #8899bb;
            font-size: 1.1em;
            margin-bottom: 15px;
            letter-spacing: 2px;
        }
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
            font-family: 'Orbitron', sans-serif;
        }
        .input-group input:focus {
            outline: none;
            border-color: #42a5f5;
        }
        .btn {
            padding: 12px 25px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            font-size: 0.9em;
            transition: 0.3s;
            font-family: 'Orbitron', sans-serif;
            letter-spacing: 1px;
        }
        .btn-add { background: #4caf50; color: #fff; }
        .btn-add:hover { background: #388e3c; transform: scale(1.05); }
        .btn-delete { background: #f44336; color: #fff; }
        .btn-delete:hover { background: #c62828; transform: scale(1.05); }
        .btn-send { background: #42a5f5; color: #fff; }
        .btn-send:hover { background: #1a237e; transform: scale(1.05); }
        .btn-refresh { background: #283593; color: #fff; }
        .btn-refresh:hover { background: #1a237e; transform: scale(1.05); }
        .btn-search { background: #ffc107; color: #000; }
        .btn-search:hover { background: #f9a825; transform: scale(1.05); }
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
        }
        .user-item .uid { font-weight: bold; color: #42a5f5; }
        .user-item .stats { font-size: 0.75em; color: #8899bb; }
        .user-item .stats span { color: #4caf50; font-weight: bold; }
        .user-item .delete-btn {
            background: none;
            border: none;
            color: #f44336;
            cursor: pointer;
            font-size: 1.2em;
            padding: 0 5px;
        }
        .user-item .delete-btn:hover { color: #ff1744; transform: scale(1.2); }
        .note {
            color: #8899bb;
            font-size: 0.75em;
            margin-top: 10px;
            letter-spacing: 1px;
        }
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
            font-size: 0.8em;
            letter-spacing: 1px;
        }
        td {
            padding: 12px 15px;
            border-bottom: 1px solid #1a2240;
            font-size: 0.85em;
        }
        .badge {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.7em;
            font-weight: bold;
            display: inline-block;
            letter-spacing: 1px;
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
        .user-stat-card .uid { color: #42a5f5; font-weight: bold; font-size: 1em; }
        .user-stat-card .stat-row {
            display: flex;
            justify-content: space-between;
            margin-top: 8px;
            font-size: 0.8em;
            color: #8899bb;
        }
        .user-stat-card .stat-row .value { color: #4caf50; font-weight: bold; }
        .user-stat-card .last-like { font-size: 0.7em; color: #666; margin-top: 5px; }
        .like-result {
            padding: 15px;
            border-radius: 10px;
            margin-top: 15px;
            display: none;
            border: 1px solid #1e2a4a;
        }
        .like-result.show { display: block; }
        .like-result.success { border-color: #4caf50; background: #4caf5022; }
        .like-result.error { border-color: #f44336; background: #f4433622; }
        .like-result .result-row {
            display: flex;
            justify-content: space-between;
            padding: 5px 0;
            font-size: 0.85em;
        }
        .like-result .result-label { color: #8899bb; }
        .like-result .result-value { color: #fff; font-weight: bold; }
        .like-result .result-value.green { color: #4caf50; }
        .like-result .result-value.red { color: #f44336; }
        .like-result .result-value.yellow { color: #ffc107; }
        .player-info {
            background: #1a2240;
            padding: 15px;
            border-radius: 10px;
            margin-top: 15px;
            border: 1px solid #2a3a5a;
            display: none;
        }
        .player-info.show { display: block; }
        .player-info .info-row {
            display: flex;
            justify-content: space-between;
            padding: 5px 0;
            font-size: 0.85em;
        }
        .log-area {
            background: #0a0e1a;
            padding: 15px;
            border-radius: 12px;
            max-height: 200px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.75em;
            border: 1px solid #1e2a4a;
            margin-top: 15px;
        }
        .log-entry {
            padding: 4px 0;
            border-bottom: 1px solid #141928;
        }
        .log-time { color: #42a5f5; }
        .log-success { color: #4caf50; }
        .log-error { color: #f44336; }
        .log-info { color: #ffc107; }
        .section-title {
            font-size: 1.2em;
            color: #fff;
            margin-top: 25px;
            margin-bottom: 15px;
            letter-spacing: 2px;
        }
        .flex-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        .status-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
        }
        .status-dot.green { background: #4caf50; box-shadow: 0 0 10px #4caf50; }
        .status-dot.red { background: #f44336; box-shadow: 0 0 10px #f44336; }
        .status-dot.yellow { background: #ffc107; box-shadow: 0 0 10px #ffc107; }
        .status-dot.blue { background: #42a5f5; box-shadow: 0 0 10px #42a5f5; }
        @media (max-width: 768px) {
            .header h1 { font-size: 1.8em; }
            .status-card .number { font-size: 2em; }
            .user-item { flex-wrap: wrap; }
            table { font-size: 0.7em; }
            td, th { padding: 8px 10px; }
        }
    </style>
</head>
<body>
    <!-- Loading Screen -->
    <div class="loader-wrapper" id="loader">
        <div class="loader"></div>
        <div class="loader-text">INITIALIZING SYSTEM</div>
        <div class="loader-sub">Loading accounts and security protocols...</div>
        <div class="ddos-badge">DDOS PROTECTION ACTIVE</div>
    </div>

    <!-- Main Content -->
    <div class="container" id="main">
        <div class="header">
            <h1>AUTO-LIKE SYSTEM</h1>
            <p>Powered by Advanced AI | Auto-reset daily at 4:00 AM IST</p>
            <div class="badge-container">
                <span class="badge-item"><span class="status-dot green"></span>Status: <span class="highlight green">Active</span></span>
                <span class="badge-item"><span class="status-dot blue"></span>Protection: <span class="highlight">Level 5</span></span>
                <span class="badge-item"><span class="status-dot yellow"></span>Next Reset: <span class="highlight yellow"><?php echo $nextReset; ?> IST</span></span>
            </div>
        </div>

        <div class="status-grid" id="statusGrid">
            <div class="status-card">
                <div class="number blue" id="totalAccounts"><?php echo $health['accounts_loaded'] ?? 0; ?></div>
                <div class="label">Total Accounts</div>
            </div>
            <div class="status-card">
                <div class="number green" id="workingAccounts"><?php echo $stats['working_accounts'] ?? 0; ?></div>
                <div class="label">Working Now</div>
            </div>
            <div class="status-card">
                <div class="number red" id="timeoutAccounts"><?php echo $stats['timeout_accounts'] ?? 0; ?></div>
                <div class="label">Limit Reached</div>
            </div>
            <div class="status-card">
                <div class="number purple" id="totalLikes"><?php echo $stats['total_successful_likes'] ?? 0; ?></div>
                <div class="label">Total Likes Sent</div>
            </div>
            <div class="status-card">
                <div class="number yellow" id="targetsLiked"><?php echo $stats['total_uids_liked'] ?? 0; ?></div>
                <div class="label">Targets Liked</div>
            </div>
        </div>

        <div class="panel">
            <h2>MANAGE AUTO-LIKE USERS</h2>
            <div class="input-group">
                <input type="number" id="userUid" placeholder="Enter Free Fire UID" />
                <button class="btn btn-add" onclick="addUser()">+ Add User</button>
                <button class="btn btn-delete" onclick="deleteAllUsers()">X Delete All</button>
            </div>
            <div class="user-list" id="userList">
                <?php foreach ($users as $uid): ?>
                <div class="user-item" id="user-<?php echo $uid; ?>">
                    <span class="uid"><?php echo $uid; ?></span>
                    <span class="stats">Total: <span><?php echo $userStats[$uid]['total_likes'] ?? 0; ?></span> | Today: <span><?php echo $userStats[$uid]['today_likes'] ?? 0; ?></span></span>
                    <button class="delete-btn" onclick="deleteUser('<?php echo $uid; ?>')">X</button>
                </div>
                <?php endforeach; ?>
            </div>
            <div class="note">Users added here will receive auto-likes daily at 4:00 AM IST</div>
        </div>

        <div class="panel">
            <h2>SEND INSTANT LIKE</h2>
            <div class="input-group">
                <input type="number" id="sendUid" placeholder="Enter Target UID" />
                <input type="number" id="sendCount" placeholder="Number of Likes" value="10" style="max-width:150px;" />
                <button class="btn btn-send" onclick="sendLike()">Send Likes</button>
                <button class="btn btn-search" onclick="getPlayerInfo()">Get Info</button>
            </div>
            <div id="playerInfo" class="player-info"></div>
            <div id="likeResult" class="like-result"></div>
        </div>

        <div class="section-title">ACCOUNT STATUS</div>
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
            <tbody id="accountTable">
                <tr><td colspan="5" style="text-align:center;color:#8899bb;">Loading accounts...</td></tr>
            </tbody>
        </table>

        <div class="section-title">USER STATISTICS</div>
        <div class="user-stats-grid" id="userStatsGrid">
            <?php foreach ($userStats as $uid => $stats): ?>
            <div class="user-stat-card">
                <div class="uid">UID: <?php echo $uid; ?></div>
                <div class="stat-row">
                    <span>Total Likes</span>
                    <span class="value"><?php echo $stats['total_likes']; ?></span>
                </div>
                <div class="stat-row">
                    <span>Today's Likes</span>
                    <span class="value"><?php echo $stats['today_likes']; ?></span>
                </div>
                <div class="last-like">Last Like: <?php echo $stats['last_like'] ?? 'Never'; ?></div>
            </div>
            <?php endforeach; ?>
        </div>

        <div class="section-title">ACTIVITY LOG</div>
        <div class="log-area" id="logArea">
            <div class="log-entry"><span class="log-info">System initialized...</span></div>
        </div>
    </div>

    <script>
        function addLog(message, type = 'info') {
            const logArea = document.getElementById('logArea');
            const time = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-${type}">${message}</span>`;
            logArea.prepend(entry);
            if (logArea.children.length > 50) {
                logArea.removeChild(logArea.lastChild);
            }
        }

        function addUser() {
            const uid = document.getElementById('userUid').value.trim();
            if (!uid) { alert('Please enter a UID'); return; }
            
            fetch(window.location.href + '?action=add_user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: 'uid=' + encodeURIComponent(uid)
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    addLog('User added: ' + uid, 'success');
                    location.reload();
                } else {
                    alert(data.message || 'Error adding user');
                }
            });
        }

        function deleteUser(uid) {
            if (!confirm('Remove this user from auto-like?')) return;
            
            fetch(window.location.href + '?action=delete_user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: 'uid=' + encodeURIComponent(uid)
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    addLog('User deleted: ' + uid, 'success');
                    location.reload();
                } else {
                    alert(data.message || 'Error deleting user');
                }
            });
        }

        function deleteAllUsers() {
            if (!confirm('Delete ALL users from auto-like?')) return;
            
            fetch(window.location.href + '?action=delete_all_users', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    addLog('All users deleted', 'info');
                    location.reload();
                }
            });
        }

        function sendLike() {
            const uid = document.getElementById('sendUid').value.trim();
            const likes = document.getElementById('sendCount').value || 10;
            
            if (!uid) { alert('Please enter a target UID'); return; }
            
            const resultDiv = document.getElementById('likeResult');
            resultDiv.className = 'like-result show';
            resultDiv.innerHTML = '<div style="text-align:center;color:#ffc107;">Sending likes...</div>';
            
            fetch(window.location.href + '?action=send_like', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: 'uid=' + encodeURIComponent(uid) + '&likes=' + encodeURIComponent(likes)
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const r = data.data;
                    resultDiv.className = 'like-result show success';
                    resultDiv.innerHTML = `
                        <div class="result-row"><span class="result-label">Likes Given</span><span class="result-value green">${r.LikesGivenByAPI}</span></div>
                        <div class="result-row"><span class="result-label">Verified Added</span><span class="result-value green">${r.VerifiedLikesAdded}</span></div>
                        <div class="result-row"><span class="result-label">Player Name</span><span class="result-value">${r.PlayerNickname}</span></div>
                        <div class="result-row"><span class="result-label">UID</span><span class="result-value">${r.UID}</span></div>
                        <div class="result-row"><span class="result-label">Total Likes</span><span class="result-value">${r.LikesafterCommand}</span></div>
                        <div class="result-row"><span class="result-label">24hr Skipped</span><span class="result-value yellow">${r.skipped_24hr || 0}</span></div>
                        <div class="result-row"><span class="result-label">Status</span><span class="result-value">${r.status == 1 ? 'Success' : 'Failed'}</span></div>
                    `;
                    addLog('Sent ' + r.LikesGivenByAPI + ' likes to ' + uid, 'success');
                    updateStats();
                } else {
                    resultDiv.className = 'like-result show error';
                    resultDiv.innerHTML = '<div class="result-row"><span class="result-label">Error</span><span class="result-value red">' + (data.message || 'API error') + '</span></div>';
                    addLog('Error sending likes to ' + uid, 'error');
                }
            });
        }

        function getPlayerInfo() {
            const uid = document.getElementById('sendUid').value.trim();
            if (!uid) { alert('Please enter a UID'); return; }
            
            const infoDiv = document.getElementById('playerInfo');
            infoDiv.className = 'player-info show';
            infoDiv.innerHTML = '<div style="text-align:center;color:#ffc107;">Loading player info...</div>';
            
            fetch(window.location.href + '?action=get_player_info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: 'uid=' + encodeURIComponent(uid)
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const r = data.data;
                    infoDiv.className = 'player-info show';
                    infoDiv.innerHTML = `
                        <div class="info-row"><span class="result-label">Player Name</span><span class="result-value">${r.PlayerNickname || 'Unknown'}</span></div>
                        <div class="info-row"><span class="result-label">UID</span><span class="result-value">${r.UID}</span></div>
                        <div class="info-row"><span class="result-label">Total Likes</span><span class="result-value green">${r.LikesafterCommand || 0}</span></div>
                        <div class="info-row"><span class="result-label">Region</span><span class="result-value">IND</span></div>
                    `;
                    addLog('Player info loaded for ' + uid, 'info');
                } else {
                    infoDiv.className = 'player-info show';
                    infoDiv.innerHTML = '<div class="info-row"><span class="result-label">Error</span><span class="result-value red">' + (data.message || 'Player not found') + '</span></div>';
                }
            });
        }

        function updateStats() {
            fetch(window.location.href + '?action=get_status')
            .then(response => response.json())
            .then(data => {
                if (data.stats) {
                    document.getElementById('totalLikes').textContent = data.stats.total_successful_likes || 0;
                    document.getElementById('targetsLiked').textContent = data.stats.total_uids_liked || 0;
                    document.getElementById('workingAccounts').textContent = data.stats.working_accounts || 0;
                    document.getElementById('timeoutAccounts').textContent = data.stats.timeout_accounts || 0;
                }
            });
        }

        // Auto-refresh every 30 seconds
        setInterval(updateStats, 30000);

        // Hide loader
        window.addEventListener('load', function() {
            setTimeout(function() {
                document.getElementById('loader').classList.add('hidden');
                addLog('System ready', 'success');
                updateStats();
            }, 2000);
        });

        // Enter key support
        document.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                if (document.activeElement === document.getElementById('userUid')) {
                    addUser();
                } else if (document.activeElement === document.getElementById('sendUid')) {
                    sendLike();
                }
            }
        });
    </script>
</body>
</html>