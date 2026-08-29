import os
import json
import uuid
import threading
import requests
import re
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes
from upstash_redis import Redis

VN_TZ = timezone(timedelta(hours=7))

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8525386643:AAFhrmnkUmamGJgWZg4MHNjt8znEfaqlU-E").strip()
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "7126654319").strip()
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "https://crucial-redfish-68584.upstash.io").strip()
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "gQAAAAAAAQvoAAIgcDE5MmE5MzU4ODUwZDY0MWM5OTMwNjQ1YzVlMTA1MGRiZg").strip()

redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)

def get_all_keys():
    try:
        keys_data = redis.get("dns_vip_keys")
        if not keys_data:
            return {}
        if isinstance(keys_data, str):
            return json.loads(keys_data)
        return keys_data
    except Exception:
        return {}

def save_all_keys(keys_dict):
    try:
        redis.set("dns_vip_keys", json.dumps(keys_dict, ensure_ascii=False))
    except Exception:
        pass

def get_remaining_time(key_info):
    expires_at = key_info.get("expires_at")
    if not expires_at:
        try:
            created_dt = datetime.strptime(key_info.get("created_at"), "%H:%M:%S - %d/%m/%Y").replace(tzinfo=VN_TZ)
            expires_at = (created_dt + timedelta(days=key_info.get("days", 0))).timestamp()
        except Exception:
            expires_at = datetime.now(VN_TZ).timestamp() + (key_info.get("days", 0) * 86400)

    rem = expires_at - datetime.now(VN_TZ).timestamp()
    if rem <= 0:
        return "🔴 Hết hạn", True
    
    rem_days = int(rem // 86400)
    rem_hours = int((rem % 86400) // 3600)
    rem_mins = int((rem % 3600) // 60)
    return f"🟢 Còn {rem_days} ngày {rem_hours} giờ {rem_mins} phút", False

orders = {}
app = Flask(__name__)
CORS(app)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID:
        return
    help_text = (
        "📖 **QUẢN LÝ BOT DNS**\n\n"
        "• `/genkey <tên> <ngày> <giá>`\n"
        "• `/editkey <tên> <ngày_mới> <giá_mới>`\n"
        "• `/delkey <tên>`\n"
        "• `/listkeys`"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def genkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID:
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("⚠️ Cú pháp: `/genkey <tên_key> <số_ngày> <giá>`", parse_mode="Markdown")
        return
    custom_key = args[0].upper()
    try:
        days, price = int(args[1]), int(args[2])
    except ValueError:
        return

    keys = get_all_keys()
    if custom_key in keys:
        await update.message.reply_text(f"❌ Mã `{custom_key}` đã tồn tại!", parse_mode="Markdown")
        return

    created_dt = datetime.now(VN_TZ)
    keys[custom_key] = {
        "days": days,
        "price": price,
        "created_at": created_dt.strftime("%H:%M:%S - %d/%m/%Y"),
        "expires_at": (created_dt + timedelta(days=days)).timestamp()
    }
    save_all_keys(keys)
    await update.message.reply_text(f"🎉 Đã tạo key: `{custom_key}` ({days} ngày)", parse_mode="Markdown")

async def editkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID:
        return
    args = context.args
    if len(args) < 3:
        return
    custom_key = args[0].upper()
    try:
        days, price = int(args[1]), int(args[2])
    except ValueError:
        return
    keys = get_all_keys()
    if custom_key not in keys:
        await update.message.reply_text("❌ Không tìm thấy mã!", parse_mode="Markdown")
        return

    keys[custom_key]["days"] = days
    keys[custom_key]["price"] = price
    keys[custom_key]["expires_at"] = (datetime.now(VN_TZ) + timedelta(days=days)).timestamp()
    save_all_keys(keys)
    await update.message.reply_text(f"✅ Đã cập nhật key: `{custom_key}`", parse_mode="Markdown")

async def delkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID:
        return
    if not context.args:
        return
    target_key = context.args[0].upper()
    keys = get_all_keys()
    if target_key in keys:
        del keys[target_key]
        save_all_keys(keys)
        await update.message.reply_text(f"🗑️ Đã xóa: `{target_key}`", parse_mode="Markdown")

async def listkeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID:
        return
    keys = get_all_keys()
    if not keys:
        await update.message.reply_text("📂 Chưa có mã nào!")
        return
    msg = "📋 **DANH SÁCH KEY:**\n\n"
    for k, v in keys.items():
        st, _ = get_remaining_time(v)
        msg += f"• `{k}` | {v.get('price', 0):,}đ\n  └ {st}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

@app.route('/create-order', methods=['POST'])
def create_order():
    try:
        data = request.json
        name = data.get('name')
        full_link = data.get('full_link', 'Không có link')
        amount = data.get('amount', 15000)
        used_key = data.get('key', '').strip().upper()
        if not name:
            return jsonify({"success": False, "message": "Thiếu tên!"}), 400

        keys = get_all_keys()
        if used_key:
            if used_key not in keys:
                return jsonify({"success": False, "message": "Mã Key không hợp lệ!"}), 400
            _, is_exp = get_remaining_time(keys[used_key])
            if is_exp:
                return jsonify({"success": False, "message": "Mã Key đã hết hạn!"}), 400
            del keys[used_key]
            save_all_keys(keys)
            amount = 0

        order_id = str(uuid.uuid4())[:8].upper()
        created_at = datetime.now(VN_TZ).strftime("%H:%M:%S - %d/%m/%Y")

        if amount == 0:
            orders[order_id] = {'name': name, 'full_link': full_link, 'status': 'APPROVED'}
            msg = f"🎁 **KHÁCH DÙNG KEY THÀNH CÔNG!**\n\n👤 `{name}`\n🔗 {full_link}\n🔑 `{used_key}`"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
            return jsonify({"success": True, "order_id": order_id, "auto_approved": True})

        orders[order_id] = {'name': name, 'full_link': full_link, 'status': 'PENDING'}
        msg = f"🔔 **ĐƠN MỚI:** `{order_id}`\n👤 `{name}`\n💵 `{amount:,}đ`"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ XÁC NHẬN", callback_data=f"approve_{order_id}")]])
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": kb.to_dict()})
        return jsonify({"success": True, "order_id": order_id, "auto_approved": False})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/check-key', methods=['POST'])
def check_key():
    data = request.json
    raw_key = data.get('key', '').strip().upper()
    keys = get_all_keys()
    if not raw_key or raw_key not in keys:
        return jsonify({"success": False, "message": "Mã không tồn tại!"}), 400
    _, is_exp = get_remaining_time(keys[raw_key])
    if is_exp:
        return jsonify({"success": False, "message": "Mã đã hết hạn!"}), 400
    return jsonify({"success": True, "key": raw_key, "days": keys[raw_key]['days'], "price": keys[raw_key].get('price', 0)})

@app.route('/check-status/<order_id>', methods=['GET'])
def check_status(order_id):
    order = orders.get(order_id)
    if not order:
        return jsonify({"status": "NOT_FOUND"})
    return jsonify({"status": order['status'], "name": order['name']})

@app.route('/get-udid-profile', methods=['GET'])
def get_udid_profile():
    receive_url = "https://bot-dns.onrender.com/receive-udid"
    xml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <dict>
        <key>URL</key>
        <string>{receive_url}</string>
        <key>DeviceAttributes</key>
        <array>
            <string>UDID</string>
            <string>VERSION</string>
            <string>PRODUCT</string>
        </array>
    </dict>
    <key>PayloadOrganization</key>
    <string>Duc Kien DNS</string>
    <key>PayloadDisplayName</key>
    <string>Xác minh thiết bị iPhone</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
    <key>PayloadUUID</key>
    <string>{uuid.uuid4()}</string>
    <key>PayloadIdentifier</key>
    <string>com.duckien.profileservice</string>
    <key>PayloadType</key>
    <string>Profile Service</string>
</dict>
</plist>'''
    return Response(xml_content, mimetype='application/x-apple-asymmetric-key-exchange')

@app.route('/receive-udid', methods=['POST'])
def receive_udid():
    try:
        raw_data = request.get_data().decode('latin1')
        match = re.search(r'<\?xml.*?</plist>', raw_data, re.DOTALL)
        if match:
            plist_xml = match.group(0)
            udid = re.search(r'<key>UDID</key>\s*<string>(.*?)</string>', plist_xml).group(1)
            product = re.search(r'<key>PRODUCT</key>\s*<string>(.*?)</string>', plist_xml).group(1)
            msg = f"📱 **XÁC MINH UDID:**\n🔑 `{udid}`\n🍏 Thiết bị: `{product}`"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
            return "", 301, {"Location": "https://bot-dns.onrender.com/"}
    except Exception:
        pass
    return "OK", 200

@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return "Server Online", 200

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("approve_"):
        order_id = query.data.split("_")[1]
        if order_id in orders:
            orders[order_id]['status'] = 'APPROVED'
            await query.edit_message_text(f"✅ Đã duyệt đơn: `{order_id}`", parse_mode="Markdown")

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    tg_app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(60.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
        .pool_timeout(60.0)
        .build()
    )
    tg_app.add_handler(CommandHandler("start", help_command))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("genkey", genkey_command))
    tg_app.add_handler(CommandHandler("editkey", editkey_command))
    tg_app.add_handler(CommandHandler("delkey", delkey_command))
    tg_app.add_handler(CommandHandler("listkeys", listkeys_command))
    tg_app.add_handler(CallbackQueryHandler(button_callback))
    
    tg_app.run_polling(drop_pending_updates=True, timeout=30)
