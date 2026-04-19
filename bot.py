import telebot
import sqlite3
import os
from werkzeug.security import check_password_hash
from datetime import datetime

# Thay bằng Token thật của bạn
TELEGRAM_BOT_TOKEN = 'Nhập vào đây'
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

DATABASE_FILE = 'data/file_data.db'

def get_db():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def format_bytes(size):
    if not size: return "0 B"
    power = 2**10
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

def get_user_by_tg_id(tg_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (str(tg_id),)).fetchone()
    conn.close()
    return user

# ================= 0. HƯỚNG DẪN (HELP/START) =================
@bot.message_handler(commands=['start', 'help'])
def help_command(message):
    user = get_user_by_tg_id(message.chat.id)
    
    msg = "🤖 *HƯỚNG DẪN SỬ DỤNG BOT TELEGRAM DRIVE*\n\n"
    
    if not user:
        msg += "⚠️ *Bạn chưa đăng nhập.*\nĐể sử dụng Bot, hãy liên kết với tài khoản trên Web của bạn:\n\n"
        msg += "👉 `/login <username> <mật_khẩu>`\n"
        msg += "_(Ví dụ: /login admin 123456)_"
    else:
        msg += f"👋 Chào mừng *{user['username']}* ({user['role'].capitalize()})\n\n"
        msg += "📂 *LỆNH CƠ BẢN:*\n"
        msg += "🔹 `/info` - Xem dung lượng Drive và thông tin tài khoản\n"
        msg += "🔹 `/logout` - Đăng xuất khỏi Bot\n\n"
        
        if user['role'] == 'admin':
            msg += "🛡️ *LỆNH QUẢN TRỊ VIÊN (ADMIN):*\n"
            msg += "🔹 `/chats` - Xem danh sách khách đang chờ hỗ trợ\n"
            msg += "🔹 `/reply <ID_Khách> <Nội dung>` - Chat với khách trên web\n"
            msg += "🔹 `/close <ID_Khách>` - Đóng phiên hỗ trợ\n"
            msg += "🔹 `/ban <Username> <Lý do>` - Khóa tài khoản người dùng vi phạm\n"
            
    bot.reply_to(message, msg, parse_mode="Markdown")

# ================= 1. ĐĂNG NHẬP =================
@bot.message_handler(commands=['login'])
def login_command(message):
    tg_id = str(message.chat.id)
    user = get_user_by_tg_id(tg_id)
    if user:
        bot.reply_to(message, f"✅ Đã đăng nhập: *{user['username']}*\nBạn có thể gõ /help để xem danh sách lệnh.", parse_mode="Markdown")
        return

    args = message.text.split()
    if len(args) != 3:
        bot.reply_to(message, "⚠️ Cú pháp:\n`/login <username> <mật_khẩu>`", parse_mode="Markdown")
        return
    
    username, password = args[1], args[2]
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    
    if user and check_password_hash(user['password'], password):
        conn.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (tg_id, user['id']))
        conn.commit()
        bot.reply_to(message, f"🎉 Đăng nhập thành công! Chào mừng *{username}*.\n\n👉 Hãy gõ `/help` để bắt đầu.", parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ Sai tên đăng nhập hoặc mật khẩu!")
    conn.close()

# ================= 2. KIỂM TRA THÔNG TIN =================
@bot.message_handler(commands=['info', 'me'])
def info_command(message):
    user = get_user_by_tg_id(message.chat.id)
    if not user: return bot.reply_to(message, "⚠️ Bạn chưa đăng nhập. Dùng `/login` hoặc `/help`")
    
    conn = get_db()
    total_size = conn.execute("SELECT SUM(file_size) FROM files WHERE owner_id = ?", (user['id'],)).fetchone()[0] or 0
    file_count = conn.execute("SELECT COUNT(*) FROM files WHERE owner_id = ?", (user['id'],)).fetchone()[0]
    folder_count = conn.execute("SELECT COUNT(*) FROM folders WHERE owner_id = ?", (user['id'],)).fetchone()[0]
    conn.close()

    msg = (f"👤 *Tài khoản:* `{user['username']}`\n"
           f"👑 *Quyền:* {user['role'].capitalize()}\n"
           f"☁️ *Dung lượng:* {format_bytes(total_size)}\n"
           f"📁 *Tệp:* {file_count} | 📂 *Thư mục:* {folder_count}")
    bot.reply_to(message, msg, parse_mode="Markdown")

# ================= 3. QUẢN TRỊ VIÊN: LIVE CHAT & BAN =================
@bot.message_handler(commands=['ban'])
def ban_command(message):
    admin_user = get_user_by_tg_id(message.chat.id)
    if not admin_user or admin_user['role'] != 'admin': return
    
    args = message.text.split(' ', 2)
    if len(args) < 3: return bot.reply_to(message, "⚠️ Cú pháp: `/ban <username> <lý do>`", parse_mode="Markdown")
    
    target, reason = args[1], args[2]
    conn = get_db()
    t_user = conn.execute("SELECT * FROM users WHERE username = ?", (target,)).fetchone()
    if not t_user: bot.reply_to(message, "❌ Không tìm thấy user này.")
    else:
        conn.execute("UPDATE users SET is_banned = 1, ban_reason = ? WHERE id = ?", (reason, t_user['id']))
        conn.commit()
        bot.reply_to(message, f"🔨 Đã khóa *{target}*\n📝 Lý do: _{reason}_", parse_mode="Markdown")
    conn.close()

@bot.message_handler(commands=['chats'])
def list_chats(message):
    admin_user = get_user_by_tg_id(message.chat.id)
    if not admin_user or admin_user['role'] != 'admin': return
    
    conn = get_db()
    chats = conn.execute("SELECT id FROM support_chats WHERE status = 'open' ORDER BY updated_at DESC").fetchall()
    if not chats: return bot.reply_to(message, "✅ Hiện không có khách nào cần hỗ trợ.")
        
    msg = "💬 *DANH SÁCH KHÁCH CHỜ:*\n\n"
    for c in chats:
        last = conn.execute("SELECT sender, msg FROM chat_msgs WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (c['id'],)).fetchone()
        icon = "👤" if last['sender'] == 'user' else "🎧"
        msg += f"🔹 *ID:* `{c['id']}`\n   {icon} _{last['msg']}_\n\n"
    msg += "👉 Để trả lời, gõ: `/reply <ID> <Nội dung>`"
    bot.reply_to(message, msg, parse_mode="Markdown")
    conn.close()

@bot.message_handler(commands=['reply'])
def reply_chat(message):
    admin_user = get_user_by_tg_id(message.chat.id)
    if not admin_user or admin_user['role'] != 'admin': return
    
    args = message.text.split(' ', 2)
    if len(args) < 3: return bot.reply_to(message, "⚠️ Cú pháp: `/reply <ID_Khách> <Nội dung>`", parse_mode="Markdown")
    
    chat_id, reply_msg = args[1], args[2]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    conn = get_db()
    if not conn.execute("SELECT id FROM support_chats WHERE id = ?", (chat_id,)).fetchone():
        conn.close(); return bot.reply_to(message, "❌ ID Khách không tồn tại hoặc đã đóng.")
        
    conn.execute("INSERT INTO chat_msgs (chat_id, sender, msg, timestamp) VALUES (?, 'admin', ?, ?)", (chat_id, reply_msg, now))
    conn.execute("UPDATE support_chats SET status = 'open', updated_at = ? WHERE id = ?", (now, chat_id))
    conn.commit(); conn.close()
    
    bot.reply_to(message, f"✅ Đã gửi tới Khách `{chat_id}`", parse_mode="Markdown")

@bot.message_handler(commands=['close'])
def close_chat(message):
    admin_user = get_user_by_tg_id(message.chat.id)
    if not admin_user or admin_user['role'] != 'admin': return
    args = message.text.split(' ', 1)
    if len(args) < 2: return bot.reply_to(message, "⚠️ Cú pháp: `/close <ID_Khách>`", parse_mode="Markdown")
    
    conn = get_db()
    conn.execute("UPDATE support_chats SET status = 'closed' WHERE id = ?", (args[1],))
    conn.commit(); conn.close()
    bot.reply_to(message, f"🔒 Đã đóng phiên chat với Khách `{args[1]}`", parse_mode="Markdown")

# ================= 4. ĐĂNG XUẤT =================
@bot.message_handler(commands=['logout'])
def logout_command(message):
    conn = get_db()
    conn.execute("UPDATE users SET telegram_id = NULL WHERE telegram_id = ?", (str(message.chat.id),))
    conn.commit(); conn.close()
    bot.reply_to(message, "👋 Đã đăng xuất khỏi Bot. Cảm ơn bạn đã sử dụng!")

if __name__ == "__main__":
    print("🚀 Telegram Bot đang chạy độc lập...")
    bot.infinity_polling()