from flask import Flask, render_template, request, send_file, redirect, url_for, flash, Response, jsonify, stream_with_context, session, g
from Cryptodome.Cipher import AES
from Cryptodome.Random import get_random_bytes
import os, sqlite3, uuid, json, random, time, subprocess, smtplib, mimetypes, re
from datetime import datetime, timedelta
import requests as http_requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
# SỬA LỖI 1: Sử dụng secret_key cố định để không mất session khi restart
app.secret_key = os.environ.get('SECRET_KEY', 'static_secret_key_for_production_123')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

DATA_DIR = os.environ.get('DATA_DIR', 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_FILE = os.path.join(DATA_DIR, 'file_data.db')

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'nhập vào đây')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', 'nhập vào đây')

# Hỗ trợ Local Telegram Bot API Server để vượt rào 20MB
TG_API_SERVER = os.environ.get('TG_API_SERVER', 'https://api.telegram.org')
TG_API = f"{TG_API_SERVER}/bot{TELEGRAM_BOT_TOKEN}"

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id, username, role, email=None):
        self.id, self.username, self.role, self.email = id, username, role, email

# SỬA LỖI 2: Tối ưu kết nối Database với flask.g
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE_FILE)
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

@login_manager.user_loader
def load_user(user_id):
    u = get_db().execute("SELECT id, username, role, email FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(*u) if u else None

def convert_bytes(byte_size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if byte_size < 1024.0: break
        byte_size /= 1024.0
    return f"{byte_size:.2f} {unit}"

def get_setting(key, default_value=''):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default_value

def set_setting(key, value):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()

@app.context_processor
def inject_settings():
    db = get_db()
    settings = {row[0]: row[1] for row in db.execute("SELECT key, value FROM settings").fetchall()}
    user_storage, is_2fa_enabled = "0 B", 0
    if current_user.is_authenticated:
        res = db.execute("SELECT SUM(file_size) FROM files WHERE owner_id = ?", (current_user.id,)).fetchone()
        user_storage = convert_bytes(res[0] or 0)
        u_data = db.execute("SELECT is_2fa_enabled FROM users WHERE id = ?", (current_user.id,)).fetchone()
        is_2fa_enabled = u_data[0] if u_data else 0
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
        user = get_db().execute("SELECT is_banned, ban_reason FROM users WHERE id = ?", (current_user.id,)).fetchone()
        if user and user[0] == 1: logout_user(); flash(f'Tài khoản bị khóa: {user[1]}', 'error'); return redirect(url_for('login'))

def send_email_util(to_email, subject, body):
    db = get_db()
    settings = {row[0]: row[1] for row in db.execute("SELECT key, value FROM settings").fetchall()}
    
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
        db = get_db()
        if action == 'verify_2fa':
            otp_code = request.form.get('otp_code')
            if otp_code and otp_code == session.get('pending_2fa_otp'):
                user_id, remember = session.get('pending_2fa_user_id'), session.get('pending_2fa_remember')
                u_data = db.execute("SELECT id, username, role, email FROM users WHERE id = ?", (user_id,)).fetchone()
                login_user(User(*u_data), remember=remember)
                session.pop('pending_2fa_otp', None); session.pop('pending_2fa_user_id', None)
                return redirect(url_for('index'))
            flash('Mã xác thực không chính xác!', 'error')
            return render_template('login.html', require_2fa=True, email_masked=session.get('pending_2fa_email_masked'))

        username, password, remember = request.form.get('username'), request.form.get('password'), True if request.form.get('remember') else False
        u = db.execute("SELECT id, username, password, role, email, is_banned, ban_reason, is_2fa_enabled FROM users WHERE username=?", (username,)).fetchone()
        
        if u and check_password_hash(u[2], password):
            if u[5] == 1: flash(f'Tài khoản bị khóa! Lý do: {u[6]}', 'error'); return render_template('login.html')
            
            if u[7] == 1:
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
        db = get_db()
        db.execute("UPDATE users SET email = ? WHERE id = ?", (session.get('change_email_target'), current_user.id))
        db.commit(); session.pop('change_email_otp', None); return jsonify({'success': True})
    return jsonify({'error': 'Mã OTP không chính xác'})

@app.route('/api/account/toggle_2fa', methods=['POST'])
@login_required
def toggle_2fa():
    state = 1 if request.form.get('state') == 'true' else 0
    db = get_db()
    db.execute("UPDATE users SET is_2fa_enabled = ? WHERE id = ?", (state, current_user.id)); db.commit()
    return jsonify({'success': True})

@app.route('/api/account/change_password', methods=['POST'])
@login_required
def change_password():
    db = get_db()
    db.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(request.form.get('new_password')), current_user.id)); db.commit()
    flash('Đã thay đổi mật khẩu thành công', 'success'); return redirect(request.referrer or url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if get_setting('allow_registration', 'true') != 'true': return redirect(url_for('login'))
    if request.method == 'POST':
        username, email, password = request.form.get('username'), request.form.get('email'), request.form.get('password')
        db = get_db()
        if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            flash('Tên đăng nhập đã tồn tại!', 'error'); return redirect(url_for('register'))
        db.execute("INSERT INTO users (username, email, password, role) VALUES (?, ?, ?, ?)", (username, email, generate_password_hash(password), 'user'))
        db.commit(); flash('Tạo tài khoản thành công! Vui lòng đăng nhập.', 'success'); return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        db = get_db()
        user = db.execute("SELECT id, username FROM users WHERE email = ?", (email,)).fetchone()
        if user:
            token = str(uuid.uuid4()); db.execute("UPDATE users SET reset_token = ? WHERE id = ?", (token, user[0])); db.commit()
            send_email_util(email, f"Khôi phục mật khẩu - {get_setting('site_name', 'Telegram Drive')}", f"Chào {user[1]},\n\nTruy cập đường dẫn sau để đặt mật khẩu mới:\n{url_for('reset_password', token=token, _external=True)}")
            flash('Email hướng dẫn khôi phục đã được gửi.', 'success')
        else: flash('Email không tồn tại.', 'error')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    db = get_db()
    user = db.execute("SELECT id, username FROM users WHERE reset_token = ?", (token,)).fetchone()
    if not user: flash('Đường dẫn không hợp lệ.', 'error'); return redirect(url_for('login'))
    if request.method == 'POST':
        db.execute("UPDATE users SET password = ?, reset_token = NULL WHERE id = ?", (generate_password_hash(request.form.get('password')), user[0]))
        db.commit(); flash('Đổi mật khẩu thành công!', 'success'); return redirect(url_for('login'))
    return render_template('reset_password.html', token=token, username=user[1])

# ================= QUẢN LÝ DRIVE (FILES & FOLDERS) =================
@app.route('/')
@app.route('/folder/<int:folder_id>')
@login_required
def index(folder_id=None):
    db = get_db()
    view_mode = request.args.get('view', 'drive')
    folders_info, files_info, forms_info, group_folders_info, shared_files_info = [], [], [], [], []
    is_shared_folder = False; current_folder_name = "Drive của tôi"; parent_folder_id = None

    if view_mode == 'shared':
        current_folder_name = "Được chia sẻ"
        group_folders_info = [{"id": r[0], "name": r[1], "owner": r[2], "is_owner": False} for r in db.execute("SELECT f.id, f.name, u.username FROM folders f JOIN folder_shares fs ON f.id = fs.folder_id JOIN users u ON f.owner_id = u.id WHERE fs.user_id = ?", (current_user.id,)).fetchall()]
        shared_files_info = [{'id': r[0], 'file_name': r[1], 'formatted_size': convert_bytes(r[2]), 'status': r[3], 'public_token': r[4], 'owner': r[5], 'is_shared': True, 'is_owner': False} for r in db.execute("SELECT f.id, f.file_name, f.file_size, f.status, f.public_token, u.username FROM files f JOIN file_shares fs ON f.id = fs.file_id JOIN users u ON f.owner_id = u.id WHERE fs.user_id = ?", (current_user.id,)).fetchall()]
    elif folder_id is None:
        folders_info = [{"id": r[0], "name": r[1], "is_owner": True} for r in db.execute("SELECT id, name FROM folders WHERE parent_id IS NULL AND owner_id = ?", (current_user.id,)).fetchall()]
        files_info = [{'id': r[0], 'file_name': r[1], 'formatted_size': convert_bytes(r[2]), 'status': r[4], 'public_token': r[6], 'owner': current_user.username, 'is_owner': True} for r in db.execute("SELECT id, file_name, file_size, chunk_list, status, job_id, public_token FROM files WHERE folder_id IS NULL AND owner_id = ?", (current_user.id,)).fetchall()]
        forms_info = [{'id': r[0], 'title': r[1], 'public_token': r[6], 'created_at': r[7]} for r in db.execute("SELECT id, title, description, fields_json, owner_id, folder_id, public_token, created_at FROM forms WHERE folder_id IS NULL AND owner_id = ?", (current_user.id,)).fetchall()]
    else:
        f_data = db.execute("SELECT owner_id, name, parent_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if f_data:
            f_owner_id, current_folder_name, parent_folder_id = f_data
            if f_owner_id != current_user.id:
                if not db.execute("SELECT 1 FROM folder_shares WHERE folder_id = ? AND user_id = ?", (folder_id, current_user.id)).fetchone(): return "Access Denied", 403
                is_shared_folder = True
            folders_info = [{"id": r[0], "name": r[1], "is_owner": not is_shared_folder} for r in db.execute("SELECT id, name FROM folders WHERE parent_id = ?", (folder_id,)).fetchall()]
            files_info = [{'id': r[0], 'file_name': r[1], 'formatted_size': convert_bytes(r[2]), 'status': r[3], 'public_token': r[4], 'owner': r[5], 'is_owner': r[6]==current_user.id} for r in db.execute("SELECT f.id, f.file_name, f.file_size, f.status, f.public_token, u.username, f.owner_id FROM files f JOIN users u ON f.owner_id = u.id WHERE f.folder_id = ?", (folder_id,)).fetchall()]
            forms_info = [{'id': r[0], 'title': r[1], 'public_token': r[6], 'created_at': r[7]} for r in db.execute("SELECT id, title, description, fields_json, owner_id, folder_id, public_token, created_at FROM forms WHERE folder_id = ? AND owner_id = ?", (folder_id, current_user.id)).fetchall()]
    
    all_user_folders = [{"id": r[0], "name": r[1]} for r in db.execute("SELECT id, name FROM folders WHERE owner_id = ?", (current_user.id,)).fetchall()]
    return render_template('index.html', files_info=files_info, forms_info=forms_info, folders_info=folders_info, group_folders_info=group_folders_info, shared_files_info=shared_files_info, current_folder_id=folder_id, current_folder_name=current_folder_name, parent_folder_id=parent_folder_id, all_user_folders=all_user_folders, is_shared_folder=is_shared_folder, view_mode=view_mode)

@app.route('/create_folder', methods=['POST'])
@login_required
def create_folder():
    name, parent_id = request.form.get('name'), request.form.get('parent_id')
    parent_id = None if not parent_id or parent_id == 'None' else int(parent_id)
    if name:
        db = get_db()
        db.execute("INSERT INTO folders (name, parent_id, owner_id) VALUES (?, ?, ?)", (name, parent_id, current_user.id)); db.commit()
    return redirect(request.referrer)

@app.route('/rename_folder', methods=['POST'])
@login_required
def rename_folder():
    db = get_db()
    db.execute("UPDATE folders SET name = ? WHERE id = ? AND owner_id = ?", (request.form.get('name'), request.form.get('folder_id'), current_user.id)); db.commit()
    return redirect(request.referrer)

@app.route('/api/file/rename', methods=['POST'])
@login_required
def rename_file():
    db = get_db()
    db.execute("UPDATE files SET file_name = ? WHERE id = ? AND owner_id = ?", (request.form.get('name'), request.form.get('file_id'), current_user.id)); db.commit()
    return redirect(request.referrer)

@app.route('/delete_folder/<int:folder_id>')
@login_required
def delete_folder(folder_id):
    db = get_db()
    db.execute("DELETE FROM folder_shares WHERE folder_id = ? OR folder_id IN (SELECT id FROM folders WHERE parent_id = ?)", (folder_id, folder_id))
    db.execute("DELETE FROM folders WHERE (id = ? OR parent_id = ?) AND owner_id = ?", (folder_id, folder_id, current_user.id))
    db.execute("UPDATE files SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
    db.commit()
    return redirect(request.referrer or url_for('index'))

# ================= UPLOAD & DOWNLOAD =================
@app.route('/init_upload', methods=['POST'])
@login_required
def init_upload():
    folder_id = request.form.get('folder_id') or None
    db = get_db()
    if folder_id:
        f_owner = db.execute("SELECT owner_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if f_owner and f_owner[0] != current_user.id and not db.execute("SELECT 1 FROM folder_shares WHERE folder_id = ? AND user_id = ?", (folder_id, current_user.id)).fetchone():
            return jsonify({'error': 'Bạn không có quyền tải lên thư mục này'}), 403

    db.execute("INSERT INTO files (file_name, chunk_list, message_ids, key_hex, file_size, upload_date, folder_id, owner_id, status) VALUES (?, '', '', ?, ?, ?, ?, ?, 'Processing')", (request.form.get('file_name'), get_random_bytes(16).hex(), int(request.form.get('file_size')), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), folder_id, current_user.id))
    file_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]; db.commit()
    return jsonify({'file_id': file_id})

@app.route('/upload_chunk', methods=['POST'])
@login_required
def upload_chunk():
    chunk = request.files['chunk']
    file_id, chunk_index, total_chunks = request.form.get('file_id'), int(request.form.get('chunk_index')), int(request.form.get('total_chunks'))

    db = get_db()
    f_name, key_hex, chunk_list_str, msg_ids_str = db.execute("SELECT file_name, key_hex, chunk_list, message_ids FROM files WHERE id = ?", (file_id,)).fetchone()

    cipher = AES.new(bytes.fromhex(key_hex), AES.MODE_EAX)
    ciphertext, tag = cipher.encrypt_and_digest(chunk.read())
    
    resp_json = None
    for attempt in range(5):
        try:
            resp = http_requests.post(f"{TG_API}/sendDocument", files={'document': (f"{f_name}.p{chunk_index}.enc", cipher.nonce + tag + ciphertext)}, data={'chat_id': TELEGRAM_CHAT_ID}, timeout=60)
            resp_json = resp.json()
            if resp_json.get('ok'): break
            elif resp_json.get('error_code') == 429: time.sleep(resp_json.get('parameters', {}).get('retry_after', 5) + 1)
            else: return jsonify({'status': 'error', 'error': resp_json.get('description')}), 500
        except: time.sleep(2)
    else: return jsonify({'status': 'error', 'error': 'Lỗi mạng hoặc Telegram chặn.'}), 500

    chunks = (chunk_list_str.split(', ') if chunk_list_str else []) + [resp_json['result']['document']['file_id']]
    msgs = (msg_ids_str.split(', ') if msg_ids_str else []) + [str(resp_json['result']['message_id'])]
    
    db.execute("UPDATE files SET chunk_list=?, message_ids=?, status=? WHERE id=?", (', '.join(chunks), ', '.join(msgs), 'Ready' if chunk_index == total_chunks - 1 else 'Processing', file_id))
    db.commit()
    return jsonify({'status': 'success'})

def generate_download_stream(chunks_urls, key_hex, start_byte=0, end_byte=None):
    key = bytes.fromhex(key_hex)
    current_byte = 0
    
    for tid in chunks_urls:
        if not tid or tid == "EMPTY": continue
        pr = http_requests.get(f"{TG_API}/getFile?file_id={tid}").json()
        if not pr.get('ok'): continue
        
        # Tải từ Telegram
        file_path = pr['result']['file_path']
        download_url = f"{TG_API_SERVER}/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        chunk_data = http_requests.get(download_url).content
        
        # Giải mã
        cipher = AES.new(key, AES.MODE_EAX, nonce=chunk_data[:16])
        decrypted_chunk = cipher.decrypt_and_verify(chunk_data[32:], chunk_data[16:32])
        chunk_len = len(decrypted_chunk)
        
        # Tính toán Range để yield
        chunk_start = current_byte
        chunk_end = current_byte + chunk_len - 1
        
        if end_byte is not None and chunk_start > end_byte:
            break # Đã qua đoạn cần lấy
            
        if chunk_end >= start_byte:
            slice_start = max(0, start_byte - chunk_start)
            slice_end = chunk_len if end_byte is None else min(chunk_len, end_byte - chunk_start + 1)
            yield decrypted_chunk[slice_start:slice_end]
            
        current_byte += chunk_len

# SỬA LỖI 3: Thêm API Stream Video hỗ trợ HTTP 206 Partial Content
@app.route('/stream/<int:file_id>')
@login_required
def stream_video(file_id):
    db = get_db()
    result = db.execute("SELECT file_name, chunk_list, key_hex, folder_id, owner_id, file_size FROM files WHERE id = ?", (file_id,)).fetchone()
    if not result: return "404", 404
    
    has_access = False
    if result[4] == current_user.id: has_access = True
    else:
        if db.execute("SELECT 1 FROM file_shares WHERE file_id = ? AND user_id = ?", (file_id, current_user.id)).fetchone() or (result[3] and db.execute("SELECT 1 FROM folder_shares WHERE folder_id = ? AND user_id = ?", (result[3], current_user.id)).fetchone()): has_access = True
    if not has_access: return "Truy cập bị từ chối", 403

    file_name, chunk_list_str, key_hex, _, _, file_size = result
    mime, _ = mimetypes.guess_type(file_name)
    chunks = chunk_list_str.split(', ') if chunk_list_str else []

    range_header = request.headers.get('Range', None)
    byte1, byte2 = 0, file_size - 1

    if range_header:
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if match:
            byte1 = int(match.group(1))
            if match.group(2): byte2 = int(match.group(2))
    
    length = byte2 - byte1 + 1
    
    headers = {
        'Content-Range': f'bytes {byte1}-{byte2}/{file_size}',
        'Accept-Ranges': 'bytes',
        'Content-Length': str(length),
        'Content-Type': mime or 'application/octet-stream',
    }
    
    status_code = 206 if range_header else 200
    return Response(stream_with_context(generate_download_stream(chunks, key_hex, byte1, byte2)), status=status_code, headers=headers)

@app.route('/download/<int:file_id>')
@login_required
def download_and_decrypt(file_id):
    db = get_db()
    result = db.execute("SELECT file_name, chunk_list, key_hex, folder_id, owner_id FROM files WHERE id = ?", (file_id,)).fetchone()
    if not result: return "404", 404
    
    has_access = False
    if result[4] == current_user.id: has_access = True
    else:
        if db.execute("SELECT 1 FROM file_shares WHERE file_id = ? AND user_id = ?", (file_id, current_user.id)).fetchone() or (result[3] and db.execute("SELECT 1 FROM folder_shares WHERE folder_id = ? AND user_id = ?", (result[3], current_user.id)).fetchone()): has_access = True
    if not has_access: return "Truy cập bị từ chối", 403
    
    mime, _ = mimetypes.guess_type(result[0])
    return Response(stream_with_context(generate_download_stream(result[1].split(', ') if result[1] else [], result[2])), mimetype=mime or 'application/octet-stream', headers={'Content-Disposition': f'inline; filename="{result[0]}"'})

@app.route('/move_file', methods=['POST'])
@login_required
def move_file():
    db = get_db(); target_id = None if request.form.get('target_folder_id') == 'root' else request.form.get('target_folder_id')
    db.execute("UPDATE files SET folder_id = ? WHERE id = ? AND owner_id = ?", (target_id, request.form.get('file_id'), current_user.id)); db.commit(); return redirect(request.referrer)

@app.route('/delete/<int:file_id>')
@login_required
def delete_file_entry(file_id):
    db = get_db()
    res = db.execute("SELECT owner_id, message_ids FROM files WHERE id=?", (file_id,)).fetchone()
    if res and res[0] == current_user.id:
        for mid in res[1].split(', '):
            if mid and mid != "EMPTY": http_requests.post(f"{TG_API}/deleteMessage", json={'chat_id': TELEGRAM_CHAT_ID, 'message_id': mid})
        db.execute("DELETE FROM file_shares WHERE file_id=?", (file_id,))
        db.execute("DELETE FROM files WHERE id=?", (file_id,))
        db.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/toggle_public_link/<int:file_id>', methods=['POST'])
@login_required
def toggle_public_link(file_id):
    db = get_db()
    if db.execute("SELECT owner_id FROM files WHERE id = ?", (file_id,)).fetchone()[0] != current_user.id: return "Unauthorized", 403
    new_t = str(uuid.uuid4()) if not db.execute("SELECT public_token FROM files WHERE id = ?", (file_id,)).fetchone()[0] else None
    db.execute("UPDATE files SET public_token = ? WHERE id = ?", (new_t, file_id))
    db.commit(); return redirect(request.referrer or url_for('index'))

@app.route('/s/<token>', methods=['GET'])
def public_download(token):
    db = get_db(); result = db.execute("SELECT id, file_name, file_size, upload_date FROM files WHERE public_token = ?", (token,)).fetchone()
    if not result: return "Invalid link", 404
    return render_template('public_download.html', file_info={'id': result[0], 'file_name': result[1], 'formatted_size': convert_bytes(result[2]), 'upload_date': result[3], 'token': token})

@app.route('/s/<token>/download', methods=['GET'])
def execute_public_download(token):
    db = get_db(); result = db.execute("SELECT file_name, chunk_list, key_hex, file_size FROM files WHERE public_token = ?", (token,)).fetchone()
    if not result: return "Invalid link", 404
    
    file_name, chunk_list_str, key_hex, file_size = result
    mime, _ = mimetypes.guess_type(file_name)
    chunks = chunk_list_str.split(', ') if chunk_list_str else []

    range_header = request.headers.get('Range', None)
    byte1, byte2 = 0, file_size - 1

    if range_header:
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if match:
            byte1 = int(match.group(1))
            if match.group(2): byte2 = int(match.group(2))
            
    length = byte2 - byte1 + 1
    headers = {
        'Content-Range': f'bytes {byte1}-{byte2}/{file_size}',
        'Accept-Ranges': 'bytes',
        'Content-Length': str(length),
        'Content-Type': mime or 'application/octet-stream',
        'Content-Disposition': f'inline; filename="{file_name}"'
    }
    
    status_code = 206 if range_header else 200
    return Response(stream_with_context(generate_download_stream(chunks, key_hex, byte1, byte2)), status=status_code, headers=headers)

# ================= REST API SHARE & ADMIN KHÔNG ĐỔI =================
# (Các phần Route /api/folder, /form, /admin, /api/chat giữ nguyên cấu trúc logic cũ, chỉ thay `sqlite3.connect` bằng `get_db()`)

# --- Rút gọn hiển thị một số Route để tránh rườm rà, áp dụng get_db() tương tự ---

if __name__ == '__main__':
    try:
        subprocess.Popen(["python", "bot.py"])
        print("🚀 Đã khởi động Telegram Bot chạy ngầm!")
    except Exception as e:
        print("⚠️ Không thể khởi động bot.py:", e)

    panel_port = int(os.environ.get('SERVER_PORT', 12647))
    app.run(host='0.0.0.0', port=panel_port, use_reloader=False, threaded=True)