from flask import Flask, render_template, request, send_file, redirect, url_for, flash, Response, jsonify, stream_with_context, session
from Cryptodome.Cipher import AES
from Cryptodome.Random import get_random_bytes
import os, sqlite3, uuid, json, random, time, subprocess, smtplib, mimetypes
from datetime import datetime, timedelta
import requests as http_requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.urandom(16)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

DATA_DIR = os.environ.get('DATA_DIR', 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_FILE = os.path.join(DATA_DIR, 'file_data.db')

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'nhập vào đây') # Đổi token của bạn
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', 'nhập vào đây')
TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id, username, role, email=None):
        self.id, self.username, self.role, self.email = id, username, role, email

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    u = cursor.execute("SELECT id, username, role, email FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return User(*u) if u else None

def convert_bytes(byte_size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if byte_size < 1024.0: break
        byte_size /= 1024.0
    return f"{byte_size:.2f} {unit}"

def get_setting(key, default_value=''):
    conn = sqlite3.connect(DATABASE_FILE); row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone(); conn.close()
    return row[0] if row else default_value

def set_setting(key, value):
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)); conn.commit(); conn.close()

@app.context_processor
def inject_settings():
    conn = sqlite3.connect(DATABASE_FILE); settings = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
    user_storage, is_2fa_enabled = "0 B", 0
    if current_user.is_authenticated:
        res = conn.execute("SELECT SUM(file_size) FROM files WHERE owner_id = ?", (current_user.id,)).fetchone()
        user_storage = convert_bytes(res[0] or 0)
        u_data = conn.execute("SELECT is_2fa_enabled FROM users WHERE id = ?", (current_user.id,)).fetchone()
        is_2fa_enabled = u_data[0] if u_data else 0
    conn.close()
    return {'system_message': settings.get('system_message', ''), 'allow_registration': settings.get('allow_registration', 'true') == 'true', 'site_name': settings.get('site_name', 'Telegram Drive'), 'site_logo': settings.get('site_logo', 'https://upload.wikimedia.org/wikipedia/commons/d/da/Google_Drive_logo.png'), 'user_storage': user_storage, 'is_2fa_enabled': is_2fa_enabled}

def init_db():
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT, password TEXT, role TEXT, reset_token TEXT, is_banned INTEGER DEFAULT 0, ban_reason TEXT, is_2fa_enabled INTEGER DEFAULT 0, telegram_id TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS folders (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, parent_id INTEGER, owner_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY AUTOINCREMENT, file_name TEXT, chunk_list TEXT, message_ids TEXT, key_hex TEXT, file_size INTEGER, upload_date TEXT, folder_id INTEGER, owner_id INTEGER, status TEXT DEFAULT 'Ready', job_id TEXT, public_token TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS form_responses (id INTEGER PRIMARY KEY AUTOINCREMENT, form_id INTEGER, answers_json TEXT, submitted_at TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS forms (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, fields_json TEXT, owner_id INTEGER, folder_id INTEGER, public_token TEXT, created_at TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS folder_shares (id INTEGER PRIMARY KEY AUTOINCREMENT, folder_id INTEGER, user_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS file_shares (id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER, user_id INTEGER, access_type TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS support_chats (id TEXT PRIMARY KEY, status TEXT DEFAULT 'open', updated_at TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS chat_msgs (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, sender TEXT, msg TEXT, timestamp TEXT)''')
    
    # Tự động cập nhật cột nếu thiếu
    cursor.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cursor.fetchall()]
    if 'telegram_id' not in cols: cursor.execute("ALTER TABLE users ADD COLUMN telegram_id TEXT")
    
    if not cursor.execute("SELECT id FROM users WHERE username = 'admin'").fetchone():
        cursor.execute("INSERT INTO users (username, email, password, role) VALUES ('admin', 'admin@localhost', ?, 'admin')", (generate_password_hash('admin'),))
    conn.commit(); conn.close()

init_db()

@app.before_request
def check_banned():
    if current_user.is_authenticated and request.endpoint not in ['login', 'logout', 'static']:
        conn = sqlite3.connect(DATABASE_FILE); user = conn.execute("SELECT is_banned, ban_reason FROM users WHERE id = ?", (current_user.id,)).fetchone(); conn.close()
        if user and user[0] == 1: logout_user(); flash(f'Tài khoản bị khóa: {user[1]}', 'error'); return redirect(url_for('login'))

def send_email_util(to_email, subject, body):
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    settings = {row[0]: row[1] for row in c.execute("SELECT key, value FROM settings").fetchall()}
    conn.close()
    
    smtp_server, smtp_port = settings.get('smtp_server', ''), int(settings.get('smtp_port', '587'))
    smtp_user, smtp_pass = settings.get('smtp_user', ''), settings.get('smtp_pass', '')
    if not smtp_server or not smtp_user or not smtp_pass: return False, "Chưa cấu hình SMTP."
    msg = MIMEMultipart(); msg['From'], msg['To'], msg['Subject'] = smtp_user, to_email, subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    try:
        server = smtplib.SMTP(smtp_server, smtp_port); server.starttls(); server.login(smtp_user, smtp_pass); server.send_message(msg); server.quit()
        return True, "Thành công"
    except Exception as e: return False, str(e)

# ================= AUTH & TÀI KHOẢN =================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'verify_2fa':
            otp_code = request.form.get('otp_code')
            if otp_code and otp_code == session.get('pending_2fa_otp'):
                user_id, remember = session.get('pending_2fa_user_id'), session.get('pending_2fa_remember')
                conn = sqlite3.connect(DATABASE_FILE); u_data = conn.execute("SELECT id, username, role, email FROM users WHERE id = ?", (user_id,)).fetchone(); conn.close()
                login_user(User(*u_data), remember=remember)
                session.pop('pending_2fa_otp', None); session.pop('pending_2fa_user_id', None)
                return redirect(url_for('index'))
            flash('Mã xác thực không chính xác!', 'error')
            return render_template('login.html', require_2fa=True, email_masked=session.get('pending_2fa_email_masked'))

        username, password, remember = request.form.get('username'), request.form.get('password'), True if request.form.get('remember') else False
        conn = sqlite3.connect(DATABASE_FILE); u = conn.execute("SELECT id, username, password, role, email, is_banned, ban_reason, is_2fa_enabled FROM users WHERE username=?", (username,)).fetchone(); conn.close()
        
        if u and check_password_hash(u[2], password):
            if u[5] == 1: flash(f'Tài khoản bị khóa! Lý do: {u[6]}', 'error'); return render_template('login.html')
            
            if u[7] == 1: # Xử lý 2FA
                otp = str(random.randint(100000, 999999))
                session['pending_2fa_user_id'], session['pending_2fa_otp'], session['pending_2fa_remember'] = u[0], otp, remember
                email_masked = u[4][:3] + "***" + u[4][u[4].find('@'):]
                session['pending_2fa_email_masked'] = email_masked
                send_email_util(u[4], "Mã xác thực đăng nhập (2FA)", f"Mã xác thực (OTP) của bạn là: {otp}\nVui lòng không chia sẻ mã này cho bất kỳ ai.")
                return render_template('login.html', require_2fa=True, email_masked=email_masked)

            login_user(User(u[0], u[1], u[3], u[4]), remember=remember)
            return redirect(url_for('index'))
        flash('Sai tài khoản hoặc mật khẩu!', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/api/account/req_otp', methods=['POST'])
@login_required
def req_otp():
    new_email = request.form.get('new_email')
    if not new_email: return jsonify({'error': 'Email không hợp lệ'})
    otp = str(random.randint(100000, 999999)); session['change_email_otp'] = otp; session['change_email_target'] = new_email
    success, msg = send_email_util(new_email, "Xác nhận đổi Email", f"Mã OTP xác nhận Email mới của bạn là: {otp}")
    if success: return jsonify({'success': True})
    return jsonify({'error': f'Lỗi gửi mail: {msg}'})

@app.route('/api/account/verify_email', methods=['POST'])
@login_required
def verify_email():
    otp = request.form.get('otp')
    if otp and otp == session.get('change_email_otp'):
        conn = sqlite3.connect(DATABASE_FILE); conn.execute("UPDATE users SET email = ? WHERE id = ?", (session.get('change_email_target'), current_user.id))
        conn.commit(); conn.close(); session.pop('change_email_otp', None); return jsonify({'success': True})
    return jsonify({'error': 'Mã OTP không chính xác'})

@app.route('/api/account/toggle_2fa', methods=['POST'])
@login_required
def toggle_2fa():
    state = 1 if request.form.get('state') == 'true' else 0
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("UPDATE users SET is_2fa_enabled = ? WHERE id = ?", (state, current_user.id)); conn.commit(); conn.close()
    return jsonify({'success': True})

@app.route('/api/account/change_password', methods=['POST'])
@login_required
def change_password():
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(request.form.get('new_password')), current_user.id)); conn.commit(); conn.close()
    flash('Đã thay đổi mật khẩu thành công', 'success'); return redirect(request.referrer or url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if get_setting('allow_registration', 'true') != 'true': return redirect(url_for('login'))
    if request.method == 'POST':
        username, email, password = request.form.get('username'), request.form.get('email'), request.form.get('password')
        conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
        if cursor.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            flash('Tên đăng nhập đã tồn tại!', 'error'); conn.close(); return redirect(url_for('register'))
        cursor.execute("INSERT INTO users (username, email, password, role) VALUES (?, ?, ?, ?)", (username, email, generate_password_hash(password), 'user'))
        conn.commit(); conn.close(); flash('Tạo tài khoản thành công! Vui lòng đăng nhập.', 'success'); return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        conn = sqlite3.connect(DATABASE_FILE); user = conn.execute("SELECT id, username FROM users WHERE email = ?", (email,)).fetchone()
        if user:
            token = str(uuid.uuid4()); conn.execute("UPDATE users SET reset_token = ? WHERE id = ?", (token, user[0])); conn.commit()
            send_email_util(email, f"Khôi phục mật khẩu - {get_setting('site_name', 'Telegram Drive')}", f"Chào {user[1]},\n\nTruy cập đường dẫn sau để đặt mật khẩu mới:\n{url_for('reset_password', token=token, _external=True)}")
            flash('Email hướng dẫn khôi phục đã được gửi.', 'success')
        else: flash('Email không tồn tại.', 'error')
        conn.close(); return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    conn = sqlite3.connect(DATABASE_FILE); user = conn.execute("SELECT id, username FROM users WHERE reset_token = ?", (token,)).fetchone()
    if not user: conn.close(); flash('Đường dẫn không hợp lệ.', 'error'); return redirect(url_for('login'))
    if request.method == 'POST':
        conn.execute("UPDATE users SET password = ?, reset_token = NULL WHERE id = ?", (generate_password_hash(request.form.get('password')), user[0]))
        conn.commit(); conn.close(); flash('Đổi mật khẩu thành công!', 'success'); return redirect(url_for('login'))
    conn.close(); return render_template('reset_password.html', token=token, username=user[1])

# ================= QUẢN LÝ DRIVE (FILES & FOLDERS) =================
@app.route('/')
@app.route('/folder/<int:folder_id>')
@login_required
def index(folder_id=None):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    view_mode = request.args.get('view', 'drive')
    folders_info, files_info, forms_info, group_folders_info, shared_files_info = [], [], [], [], []
    is_shared_folder = False; current_folder_name = "Drive của tôi"; parent_folder_id = None

    if view_mode == 'shared':
        current_folder_name = "Được chia sẻ"
        group_folders_info = [{"id": r[0], "name": r[1], "owner": r[2], "is_owner": False} for r in cursor.execute("SELECT f.id, f.name, u.username FROM folders f JOIN folder_shares fs ON f.id = fs.folder_id JOIN users u ON f.owner_id = u.id WHERE fs.user_id = ?", (current_user.id,)).fetchall()]
        shared_files_info = [{'id': r[0], 'file_name': r[1], 'formatted_size': convert_bytes(r[2]), 'status': r[3], 'public_token': r[4], 'owner': r[5], 'is_shared': True, 'is_owner': False} for r in cursor.execute("SELECT f.id, f.file_name, f.file_size, f.status, f.public_token, u.username FROM files f JOIN file_shares fs ON f.id = fs.file_id JOIN users u ON f.owner_id = u.id WHERE fs.user_id = ?", (current_user.id,)).fetchall()]
    elif folder_id is None:
        folders_info = [{"id": r[0], "name": r[1], "is_owner": True} for r in cursor.execute("SELECT id, name FROM folders WHERE parent_id IS NULL AND owner_id = ?", (current_user.id,)).fetchall()]
        files_info = [{'id': r[0], 'file_name': r[1], 'formatted_size': convert_bytes(r[2]), 'status': r[4], 'public_token': r[6], 'owner': current_user.username, 'is_owner': True} for r in cursor.execute("SELECT id, file_name, file_size, chunk_list, status, job_id, public_token FROM files WHERE folder_id IS NULL AND owner_id = ?", (current_user.id,)).fetchall()]
        forms_info = [{'id': r[0], 'title': r[1], 'public_token': r[6], 'created_at': r[7]} for r in cursor.execute("SELECT id, title, description, fields_json, owner_id, folder_id, public_token, created_at FROM forms WHERE folder_id IS NULL AND owner_id = ?", (current_user.id,)).fetchall()]
    else:
        f_data = cursor.execute("SELECT owner_id, name, parent_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if f_data:
            f_owner_id, current_folder_name, parent_folder_id = f_data
            if f_owner_id != current_user.id:
                if not cursor.execute("SELECT 1 FROM folder_shares WHERE folder_id = ? AND user_id = ?", (folder_id, current_user.id)).fetchone(): return "Access Denied", 403
                is_shared_folder = True
            folders_info = [{"id": r[0], "name": r[1], "is_owner": not is_shared_folder} for r in cursor.execute("SELECT id, name FROM folders WHERE parent_id = ?", (folder_id,)).fetchall()]
            files_info = [{'id': r[0], 'file_name': r[1], 'formatted_size': convert_bytes(r[2]), 'status': r[3], 'public_token': r[4], 'owner': r[5], 'is_owner': r[6]==current_user.id} for r in cursor.execute("SELECT f.id, f.file_name, f.file_size, f.status, f.public_token, u.username, f.owner_id FROM files f JOIN users u ON f.owner_id = u.id WHERE f.folder_id = ?", (folder_id,)).fetchall()]
            forms_info = [{'id': r[0], 'title': r[1], 'public_token': r[6], 'created_at': r[7]} for r in cursor.execute("SELECT id, title, description, fields_json, owner_id, folder_id, public_token, created_at FROM forms WHERE folder_id = ? AND owner_id = ?", (folder_id, current_user.id)).fetchall()]
    
    all_user_folders = [{"id": r[0], "name": r[1]} for r in cursor.execute("SELECT id, name FROM folders WHERE owner_id = ?", (current_user.id,)).fetchall()]
    conn.close()
    return render_template('index.html', files_info=files_info, forms_info=forms_info, folders_info=folders_info, group_folders_info=group_folders_info, shared_files_info=shared_files_info, current_folder_id=folder_id, current_folder_name=current_folder_name, parent_folder_id=parent_folder_id, all_user_folders=all_user_folders, is_shared_folder=is_shared_folder, view_mode=view_mode)

@app.route('/create_folder', methods=['POST'])
@login_required
def create_folder():
    name, parent_id = request.form.get('name'), request.form.get('parent_id')
    parent_id = None if not parent_id or parent_id == 'None' else int(parent_id)
    if name:
        conn = sqlite3.connect(DATABASE_FILE); conn.execute("INSERT INTO folders (name, parent_id, owner_id) VALUES (?, ?, ?)", (name, parent_id, current_user.id)); conn.commit(); conn.close()
    return redirect(request.referrer)

@app.route('/rename_folder', methods=['POST'])
@login_required
def rename_folder():
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("UPDATE folders SET name = ? WHERE id = ? AND owner_id = ?", (request.form.get('name'), request.form.get('folder_id'), current_user.id)); conn.commit(); conn.close()
    return redirect(request.referrer)

@app.route('/api/file/rename', methods=['POST'])
@login_required
def rename_file():
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("UPDATE files SET file_name = ? WHERE id = ? AND owner_id = ?", (request.form.get('name'), request.form.get('file_id'), current_user.id)); conn.commit(); conn.close()
    return redirect(request.referrer)

@app.route('/delete_folder/<int:folder_id>')
@login_required
def delete_folder(folder_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    cursor.execute("DELETE FROM folder_shares WHERE folder_id = ? OR folder_id IN (SELECT id FROM folders WHERE parent_id = ?)", (folder_id, folder_id))
    cursor.execute("DELETE FROM folders WHERE (id = ? OR parent_id = ?) AND owner_id = ?", (folder_id, folder_id, current_user.id))
    cursor.execute("UPDATE files SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
    conn.commit(); conn.close()
    return redirect(request.referrer or url_for('index'))

# ================= UPLOAD & DOWNLOAD AUTO-RETRY =================
@app.route('/init_upload', methods=['POST'])
@login_required
def init_upload():
    folder_id = request.form.get('folder_id') or None
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    if folder_id:
        f_owner = cursor.execute("SELECT owner_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if f_owner and f_owner[0] != current_user.id and not cursor.execute("SELECT 1 FROM folder_shares WHERE folder_id = ? AND user_id = ?", (folder_id, current_user.id)).fetchone():
            conn.close(); return jsonify({'error': 'Bạn không có quyền tải lên thư mục này'}), 403

    cursor.execute("INSERT INTO files (file_name, chunk_list, message_ids, key_hex, file_size, upload_date, folder_id, owner_id, status) VALUES (?, '', '', ?, ?, ?, ?, ?, 'Processing')", (request.form.get('file_name'), get_random_bytes(16).hex(), int(request.form.get('file_size')), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), folder_id, current_user.id))
    file_id = cursor.lastrowid; conn.commit(); conn.close()
    return jsonify({'file_id': file_id})

@app.route('/upload_chunk', methods=['POST'])
@login_required
def upload_chunk():
    chunk = request.files['chunk']
    file_id, chunk_index, total_chunks = request.form.get('file_id'), int(request.form.get('chunk_index')), int(request.form.get('total_chunks'))

    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    f_name, key_hex, chunk_list_str, msg_ids_str = cursor.execute("SELECT file_name, key_hex, chunk_list, message_ids FROM files WHERE id = ?", (file_id,)).fetchone()

    cipher = AES.new(bytes.fromhex(key_hex), AES.MODE_EAX)
    ciphertext, tag = cipher.encrypt_and_digest(chunk.read())
    
    resp_json = None
    for attempt in range(5): # Thử lại 5 lần nếu Rate Limit
        try:
            resp = http_requests.post(f"{TG_API}/sendDocument", files={'document': (f"{f_name}.p{chunk_index}.enc", cipher.nonce + tag + ciphertext)}, data={'chat_id': TELEGRAM_CHAT_ID}, timeout=60)
            resp_json = resp.json()
            if resp_json.get('ok'): break
            elif resp_json.get('error_code') == 429: time.sleep(resp_json.get('parameters', {}).get('retry_after', 5) + 1)
            else: return jsonify({'status': 'error', 'error': resp_json.get('description')}), 500
        except: time.sleep(2)
    else: return jsonify({'status': 'error', 'error': 'Vượt quá số lần thử tải lại hoặc bị lỗi mạng.'}), 500

    chunks = (chunk_list_str.split(', ') if chunk_list_str else []) + [resp_json['result']['document']['file_id']]
    msgs = (msg_ids_str.split(', ') if msg_ids_str else []) + [str(resp_json['result']['message_id'])]
    
    cursor.execute("UPDATE files SET chunk_list=?, message_ids=?, status=? WHERE id=?", (', '.join(chunks), ', '.join(msgs), 'Ready' if chunk_index == total_chunks - 1 else 'Processing', file_id))
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})

def generate_download_stream(chunks_urls, key_hex):
    key = bytes.fromhex(key_hex)
    for tid in chunks_urls:
        if not tid or tid == "EMPTY": continue
        pr = http_requests.get(f"{TG_API}/getFile?file_id={tid}").json()
        if not pr.get('ok'): continue
        chunk_data = http_requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{pr['result']['file_path']}").content
        cipher = AES.new(key, AES.MODE_EAX, nonce=chunk_data[:16])
        yield cipher.decrypt_and_verify(chunk_data[32:], chunk_data[16:32])

@app.route('/download/<int:file_id>')
@login_required
def download_and_decrypt(file_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    result = cursor.execute("SELECT f.file_name, f.chunk_list, f.key_hex, f.folder_id, f.owner_id FROM files f WHERE f.id = ?", (file_id,)).fetchone()
    if not result: conn.close(); return "404", 404
    
    has_access = False
    if result[4] == current_user.id: has_access = True
    else:
        if cursor.execute("SELECT 1 FROM file_shares WHERE file_id = ? AND user_id = ?", (file_id, current_user.id)).fetchone() or (result[3] and cursor.execute("SELECT 1 FROM folder_shares WHERE folder_id = ? AND user_id = ?", (result[3], current_user.id)).fetchone()): has_access = True
    conn.close()
    if not has_access: return "Truy cập bị từ chối", 403
    
    mime, _ = mimetypes.guess_type(result[0])
    return Response(stream_with_context(generate_download_stream(result[1].split(', ') if result[1] else [], result[2])), mimetype=mime or 'application/octet-stream', headers={'Content-Disposition': f'inline; filename="{result[0]}"'})

@app.route('/move_file', methods=['POST'])
@login_required
def move_file():
    conn = sqlite3.connect(DATABASE_FILE); target_id = None if request.form.get('target_folder_id') == 'root' else request.form.get('target_folder_id')
    conn.execute("UPDATE files SET folder_id = ? WHERE id = ? AND owner_id = ?", (target_id, request.form.get('file_id'), current_user.id)); conn.commit(); conn.close(); return redirect(request.referrer)

@app.route('/delete/<int:file_id>')
@login_required
def delete_file_entry(file_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    res = cursor.execute("SELECT owner_id, message_ids FROM files WHERE id=?", (file_id,)).fetchone()
    if res and res[0] == current_user.id:
        for mid in res[1].split(', '):
            if mid and mid != "EMPTY": http_requests.post(f"{TG_API}/deleteMessage", json={'chat_id': TELEGRAM_CHAT_ID, 'message_id': mid})
        cursor.execute("DELETE FROM file_shares WHERE file_id=?", (file_id,))
        cursor.execute("DELETE FROM files WHERE id=?", (file_id,))
        conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('index'))

@app.route('/toggle_public_link/<int:file_id>', methods=['POST'])
@login_required
def toggle_public_link(file_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    if cursor.execute("SELECT owner_id FROM files WHERE id = ?", (file_id,)).fetchone()[0] != current_user.id: return "Unauthorized", 403
    new_t = str(uuid.uuid4()) if not cursor.execute("SELECT public_token FROM files WHERE id = ?", (file_id,)).fetchone()[0] else None
    cursor.execute("UPDATE files SET public_token = ? WHERE id = ?", (new_t, file_id))
    conn.commit(); conn.close(); return redirect(request.referrer or url_for('index'))

@app.route('/s/<token>', methods=['GET'])
def public_download(token):
    conn = sqlite3.connect(DATABASE_FILE); result = conn.execute("SELECT id, file_name, file_size, upload_date FROM files WHERE public_token = ?", (token,)).fetchone(); conn.close()
    if not result: return "Invalid link", 404
    return render_template('public_download.html', file_info={'id': result[0], 'file_name': result[1], 'formatted_size': convert_bytes(result[2]), 'upload_date': result[3], 'token': token})

@app.route('/s/<token>/download', methods=['GET'])
def execute_public_download(token):
    conn = sqlite3.connect(DATABASE_FILE); result = conn.execute("SELECT file_name, chunk_list, key_hex FROM files WHERE public_token = ?", (token,)).fetchone(); conn.close()
    if not result: return "Invalid link", 404
    mime, _ = mimetypes.guess_type(result[0])
    return Response(stream_with_context(generate_download_stream(result[1].split(', '), result[2])), mimetype=mime or 'application/octet-stream', headers={'Content-Disposition': f'inline; filename="{result[0]}"'})

# ================= API SHARE =================
@app.route('/api/folder/details/<int:folder_id>')
@login_required
def get_folder_details(folder_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    f_data = cursor.execute("SELECT name, owner_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if not f_data or f_data[1] != current_user.id: return jsonify({'error': 'Lỗi'}), 403
    shared = [{"share_id": r[0], "username": r[1], "email": r[2]} for r in cursor.execute("SELECT s.id, u.username, u.email FROM folder_shares s JOIN users u ON s.user_id = u.id WHERE s.folder_id = ?", (folder_id,)).fetchall()]
    conn.close(); return jsonify({'folder_name': f_data[0], 'owner': current_user.username, 'shared_users': shared})

@app.route('/api/folder/share_add', methods=['POST'])
@login_required
def folder_share_add():
    folder_id, target = request.form.get('folder_id'), request.form.get('target').strip()
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    if cursor.execute("SELECT owner_id FROM folders WHERE id = ?", (folder_id,)).fetchone()[0] != current_user.id: return jsonify({'error': 'Lỗi'}), 403
    u_data = cursor.execute("SELECT id FROM users WHERE username = ? OR email = ?", (target, target)).fetchone()
    if not u_data: return jsonify({'error': 'Không tìm thấy'}), 404
    cursor.execute("INSERT INTO folder_shares (folder_id, user_id) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM folder_shares WHERE folder_id = ? AND user_id = ?)", (folder_id, u_data[0], folder_id, u_data[0]))
    conn.commit(); conn.close(); return jsonify({'success': True})

@app.route('/api/folder/share_remove', methods=['POST'])
@login_required
def folder_share_remove():
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("DELETE FROM folder_shares WHERE id = ? AND folder_id IN (SELECT id FROM folders WHERE owner_id = ?)", (request.form.get('share_id'), current_user.id)); conn.commit(); conn.close(); return jsonify({'success': True})

@app.route('/api/file/details/<int:file_id>')
@login_required
def get_file_details(file_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    f_data = cursor.execute("SELECT f.file_name, f.file_size, f.upload_date, f.public_token, u.username, f.owner_id FROM files f JOIN users u ON f.owner_id = u.id WHERE f.id = ?", (file_id,)).fetchone()
    shared = [{"share_id": r[0], "username": r[1], "email": r[2]} for r in cursor.execute("SELECT s.id, u.username, u.email FROM file_shares s JOIN users u ON s.user_id = u.id WHERE s.file_id = ?", (file_id,)).fetchall()]
    conn.close(); return jsonify({'file_name': f_data[0], 'size': convert_bytes(f_data[1]), 'upload_date': f_data[2], 'public_token': f_data[3], 'owner': f_data[4], 'is_owner': f_data[5] == current_user.id, 'shared_users': shared})

@app.route('/api/file/share_add', methods=['POST'])
@login_required
def share_add():
    file_id, target = request.form.get('file_id'), request.form.get('target').strip()
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    if cursor.execute("SELECT owner_id FROM files WHERE id = ?", (file_id,)).fetchone()[0] != current_user.id: return jsonify({'error': 'Lỗi'}), 403
    u_data = cursor.execute("SELECT id FROM users WHERE username = ? OR email = ?", (target, target)).fetchone()
    if not u_data: return jsonify({'error': 'Không tìm thấy'}), 404
    cursor.execute("INSERT INTO file_shares (file_id, user_id, access_type) SELECT ?, ?, 'viewer' WHERE NOT EXISTS (SELECT 1 FROM file_shares WHERE file_id = ? AND user_id = ?)", (file_id, u_data[0], file_id, u_data[0]))
    conn.commit(); conn.close(); return jsonify({'success': True})

@app.route('/api/file/share_remove', methods=['POST'])
@login_required
def share_remove():
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("DELETE FROM file_shares WHERE id = ? AND file_id IN (SELECT id FROM files WHERE owner_id = ?)", (request.form.get('share_id'), current_user.id)); conn.commit(); conn.close(); return jsonify({'success': True})

@app.route('/api/file/toggle_public', methods=['POST'])
@login_required
def api_toggle_public():
    file_id = request.form.get('file_id'); conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    res = cursor.execute("SELECT owner_id, public_token FROM files WHERE id = ?", (file_id,)).fetchone()
    new_t = None if res[1] else str(uuid.uuid4()); cursor.execute("UPDATE files SET public_token = ? WHERE id = ?", (new_t, file_id)); conn.commit(); conn.close(); return jsonify({'success': True, 'token': new_t})

# ================= FORMS =================
@app.route('/form/create')
@login_required
def create_form():
    fid = request.args.get('folder_id'); fid = None if not fid or fid == 'None' else int(fid)
    token, now = str(uuid.uuid4()), datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    defs = json.dumps([{"id": f"q_{int(datetime.now().timestamp())}", "type": "text", "label": "Câu hỏi", "options": "", "required": False}])
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    cursor.execute("INSERT INTO forms (title, description, fields_json, owner_id, folder_id, public_token, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", ("Biểu mẫu", "", defs, current_user.id, fid, token, now))
    form_id = cursor.lastrowid; conn.commit(); conn.close()
    return redirect(url_for('form_admin', form_id=form_id))

@app.route('/form/admin/<int:form_id>', methods=['GET', 'POST'])
@login_required
def form_admin(form_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    if request.method == 'POST':
        cursor.execute("UPDATE forms SET title=?, description=?, fields_json=? WHERE id=? AND owner_id=?", (request.form.get('title'), request.form.get('description'), request.form.get('fields_json'), form_id, current_user.id))
        conn.commit(); return redirect(url_for('form_admin', form_id=form_id))
    form = cursor.execute("SELECT * FROM forms WHERE id=? AND owner_id=?", (form_id, current_user.id)).fetchone()
    responses = [{"id": r[0], "answers": json.loads(r[1]), "time": r[2]} for r in cursor.execute("SELECT id, answers_json, submitted_at FROM form_responses WHERE form_id=? ORDER BY id DESC", (form_id,)).fetchall()]
    conn.close()
    return render_template('form_admin.html', form={"id": form[0], "title": form[1], "description": form[2], "fields": json.loads(form[3]), "token": form[6]}, responses=responses)

@app.route('/form/delete/<int:form_id>')
@login_required
def delete_form(form_id):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    if cursor.execute("SELECT id FROM forms WHERE id=? AND owner_id=?", (form_id, current_user.id)).fetchone():
        cursor.execute("DELETE FROM form_responses WHERE form_id=?", (form_id,)); cursor.execute("DELETE FROM forms WHERE id=?", (form_id,)); conn.commit()
    conn.close(); return redirect(request.referrer or url_for('index'))

@app.route('/f/<token>', methods=['GET', 'POST'])
def form_public(token):
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    form = cursor.execute("SELECT id, title, description, fields_json FROM forms WHERE public_token=?", (token,)).fetchone()
    if not form: conn.close(); return "Lỗi", 404
    f_dict = {"id": form[0], "title": form[1], "description": form[2], "fields": json.loads(form[3])}
    if request.method == 'POST':
        answers = {}
        for f in f_dict['fields']: answers[f['id']] = ", ".join(request.form.getlist(f['id'])) if f['type'] == 'checkbox' else request.form.get(f['id'], '')
        cursor.execute("INSERT INTO form_responses (form_id, answers_json, submitted_at) VALUES (?, ?, ?)", (form[0], json.dumps(answers), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit(); conn.close(); return render_template('form_public.html', form=f_dict, success=True)
    conn.close(); return render_template('form_public.html', form=f_dict, success=False)

# ================= CHAT API BÁO QUA TELEGRAM =================
@app.route('/api/chat/user_send', methods=['POST'])
def chat_user_send():
    chat_id, msg, now = request.form.get('chat_id'), request.form.get('msg'), datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    if not cursor.execute("SELECT status FROM support_chats WHERE id = ?", (chat_id,)).fetchone(): cursor.execute("INSERT INTO support_chats (id, status, updated_at) VALUES (?, 'open', ?)", (chat_id, now))
    else: cursor.execute("UPDATE support_chats SET status = 'open', updated_at = ? WHERE id = ?", (now, chat_id))
    cursor.execute("INSERT INTO chat_msgs (chat_id, sender, msg, timestamp) VALUES (?, 'user', ?, ?)", (chat_id, msg, now))
    
    # BẮN THÔNG BÁO QUA TELEGRAM CHO ADMIN
    admin_tg = cursor.execute("SELECT telegram_id FROM users WHERE role = 'admin' AND telegram_id IS NOT NULL").fetchone()
    if admin_tg:
        tg_msg = f"🔔 *TIN NHẮN TỪ TRANG WEB*\n\n👤 *ID Khách:* `{chat_id}`\n💬 *Nội dung:* {msg}\n\n👉 Gõ để trả lời:\n`/reply {chat_id} ` [Nhập câu trả lời vào đây]"
        try: http_requests.post(f"{TG_API}/sendMessage", data={'chat_id': admin_tg[0], 'text': tg_msg, 'parse_mode': 'Markdown'})
        except: pass

    conn.commit(); conn.close(); return jsonify({'status': 'ok'})

@app.route('/api/chat/user_sync', methods=['POST'])
def chat_user_sync():
    chat_id = request.form.get('chat_id'); conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    status = cursor.execute("SELECT status FROM support_chats WHERE id = ?", (chat_id,)).fetchone()
    msgs = [{"sender": r[0], "msg": r[1], "time": r[2]} for r in cursor.execute("SELECT sender, msg, timestamp FROM chat_msgs WHERE chat_id = ? ORDER BY id ASC", (chat_id,)).fetchall()]
    conn.close(); return jsonify({'status': status[0] if status else 'open', 'msgs': msgs})

@app.route('/api/chat/admin_send', methods=['POST'])
@login_required
def chat_admin_send():
    if current_user.role != 'admin': return "Forbidden", 403
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S'); conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_msgs (chat_id, sender, msg, timestamp) VALUES (?, 'admin', ?, ?)", (request.form.get('chat_id'), request.form.get('msg'), now))
    cursor.execute("UPDATE support_chats SET updated_at = ? WHERE id = ?", (now, request.form.get('chat_id')))
    conn.commit(); conn.close(); return redirect(url_for('admin_panel'))

@app.route('/api/chat/admin_close', methods=['POST'])
@login_required
def chat_admin_close():
    if current_user.role != 'admin': return "Forbidden", 403
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("UPDATE support_chats SET status = 'closed' WHERE id = ?", (request.form.get('chat_id'),)); conn.commit(); conn.close(); return redirect(url_for('admin_panel'))

@app.route('/api/chat/admin_delete', methods=['POST'])
@login_required
def chat_admin_delete():
    if current_user.role != 'admin': return "Forbidden", 403
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_msgs WHERE chat_id = ?", (request.form.get('chat_id'),)); cursor.execute("DELETE FROM support_chats WHERE id = ?", (request.form.get('chat_id'),))
    conn.commit(); conn.close(); return redirect(url_for('admin_panel'))

# ================= ADMIN PANEL =================
@app.route('/admin')
@login_required
def admin_panel():
    if current_user.role != 'admin': return "Access Forbidden", 403
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    old = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("DELETE FROM chat_msgs WHERE chat_id IN (SELECT id FROM support_chats WHERE updated_at < ?)", (old,)); cursor.execute("DELETE FROM support_chats WHERE updated_at < ?", (old,)); conn.commit()
    
    users = [{"id": r[0], "username": r[1], "role": r[2], "email": r[3], "is_banned": r[4], "ban_reason": r[5], "storage": convert_bytes(r[6])} for r in cursor.execute("SELECT u.id, u.username, u.role, u.email, u.is_banned, u.ban_reason, COALESCE((SELECT SUM(file_size) FROM files WHERE owner_id = u.id), 0) FROM users u").fetchall()]
    chats = [{"id": c[0], "status": c[1], "updated_at": c[2], "messages": [{"sender": m[0], "msg": m[1], "time": m[2]} for m in cursor.execute("SELECT sender, msg, timestamp FROM chat_msgs WHERE chat_id = ? ORDER BY id ASC", (c[0],)).fetchall()]} for c in cursor.execute("SELECT id, status, updated_at FROM support_chats ORDER BY updated_at DESC").fetchall()]
    conn.close(); return render_template('admin.html', users=users, chats=chats)

@app.route('/admin/ban_user', methods=['POST'])
@login_required
def ban_user():
    if current_user.role != 'admin': return "Access Forbidden", 403
    user_id = request.form.get('user_id')
    if int(user_id) == current_user.id: return redirect(url_for('admin_panel'))
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("UPDATE users SET is_banned = 1, ban_reason = ? WHERE id = ?", (request.form.get('ban_reason', 'Vi phạm chính sách'), user_id)); conn.commit(); conn.close(); flash('Đã khóa tài khoản', 'success'); return redirect(url_for('admin_panel'))

@app.route('/admin/unban_user/<int:user_id>')
@login_required
def unban_user(user_id):
    if current_user.role != 'admin': return "Access Forbidden", 403
    conn = sqlite3.connect(DATABASE_FILE); conn.execute("UPDATE users SET is_banned = 0, ban_reason = NULL WHERE id = ?", (user_id,)); conn.commit(); conn.close(); flash('Đã mở khóa', 'success'); return redirect(url_for('admin_panel'))

@app.route('/admin/settings', methods=['POST'])
@login_required
def update_settings():
    if current_user.role != 'admin': return "Forbidden", 403
    set_setting('allow_registration', 'true' if request.form.get('allow_registration') == 'on' else 'false')
    set_setting('system_message', request.form.get('system_message', ''))
    set_setting('site_name', request.form.get('site_name', 'Telegram Drive'))
    set_setting('site_logo', request.form.get('site_logo', ''))
    set_setting('smtp_server', request.form.get('smtp_server', ''))
    set_setting('smtp_port', request.form.get('smtp_port', '587'))
    set_setting('smtp_user', request.form.get('smtp_user', ''))
    if request.form.get('smtp_pass'): set_setting('smtp_pass', request.form.get('smtp_pass'))
    flash('Đã lưu cấu hình', 'success'); return redirect(url_for('admin_panel'))

@app.route('/admin/edit_user', methods=['POST'])
@login_required
def edit_user():
    if current_user.role != 'admin': return "Forbidden", 403
    user_id, password, role = request.form.get('user_id'), request.form.get('password'), request.form.get('role')
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    if password: cursor.execute("UPDATE users SET password = ?, role = ? WHERE id = ?", (generate_password_hash(password), role, user_id))
    else: cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit(); conn.close(); return redirect(url_for('admin_panel'))

@app.route('/admin/delete_user/<int:user_id>')
@login_required
def delete_user(user_id):
    if current_user.role != 'admin': return "Forbidden", 403
    if int(user_id) == current_user.id: return redirect(url_for('admin_panel'))
    conn = sqlite3.connect(DATABASE_FILE); cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    cursor.execute("DELETE FROM file_shares WHERE file_id IN (SELECT id FROM files WHERE owner_id = ?)", (user_id,))
    cursor.execute("DELETE FROM folder_shares WHERE folder_id IN (SELECT id FROM folders WHERE owner_id = ?)", (user_id,))
    cursor.execute("DELETE FROM files WHERE owner_id = ?", (user_id,))
    cursor.execute("DELETE FROM folders WHERE owner_id = ?", (user_id,))
    conn.commit(); conn.close(); return redirect(url_for('admin_panel'))

# ================= CHẠY SERVER & BOT SONG SONG =================
if __name__ == '__main__':
    try:
        subprocess.Popen(["python", "bot.py"])
        print("🚀 Đã khởi động Telegram Bot chạy ngầm!")
    except Exception as e:
        print("⚠️ Không thể khởi động bot.py:", e)

    panel_port = int(os.environ.get('SERVER_PORT', 9006))
    app.run(host='0.0.0.0', port=panel_port, use_reloader=False)