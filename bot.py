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

# Múi giờ Việt Nam (UTC+7)
VN_TZ = timezone(timedelta(hours=7))

# Cấu hình Token & Admin Telegram (ĐÃ ĐƯỢC CẬP NHẬT)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8525386643:AAFhrmnkUmamGJgWZg4MHNjt8znEfaqlU-E").strip()
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "7126654319").strip()

# Cấu hình Upstash Redis
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
    except Exception as e:
        print(f"Lỗi đọc dữ liệu từ Redis: {e}")
        return {}

def save_all_keys(keys_dict):
    try:
        redis.set("dns_vip_keys", json.dumps(keys_dict, ensure_ascii=False))
    except Exception as e:
        print(f"Lỗi lưu dữ liệu lên Redis: {e}")

# HÀM TÍNH TOÁN THỜI GIAN ĐẾM NGƯỢC
def get_remaining_time(key_info):
    expires_at = key_info.get("expires_at")
    
    if not expires_at:
        try:
            created_dt = datetime.strptime(key_info.get("created_at"), "%H:%M:%S - %d/%m/%Y").replace(tzinfo=VN_TZ)
            expires_at = (created_dt + timedelta(days=key_info.get("days", 0))).timestamp()
        except:
            expires_at = datetime.now(VN_TZ).timestamp() + (key_info.get("days", 0) * 86400)

    now_ts = datetime.now(VN_TZ).timestamp()
    rem = expires_at - now_ts
    
    if rem <= 0:
        return "🔴 Hết hạn", True
    
    rem_days = int(rem // 86400)
    rem_hours = int((rem % 86400) // 3600)
    rem_mins = int((rem % 3600) // 60)
    return f"🟢 Còn {rem_days} ngày {rem_hours} giờ {rem_mins} phút", False

orders = {}
app = Flask(__name__)
CORS(app)

# ==================== TELEGRAM BOT COMMANDS ====================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID: return
    help_text = (
        "📖 **BẢNG HƯỚNG DẪN QUẢN LÝ BOT DUC KIEN DNS**\n\n"
        "🔑 **1. Tạo mã Key:**\n"
        "• `/genkey <tên_key> <số_ngày> <giá>`\n\n"
        "✏️ **2. Sửa mã Key:**\n"
        "• `/editkey <tên_key> <số_ngày_mới> <giá_mới>`\n\n"
        "🗑️ **3. Xóa mã Key:**\n"
        "• `/delkey <tên_key>`\n\n"
        "📋 **4. Xem danh sách mã:**\n"
        "• `/listkeys`"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def genkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID: return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("⚠️ **Cú pháp:** `/genkey <tên_key> <số_ngày> <giá>`", parse_mode="Markdown")
        return

    custom_key = args[0].upper()
    try:
        days, price = int(args[1]), int(args[2])
    except ValueError:
        await update.message.reply_text("⚠️ Số ngày và Giá tiền phải là số!")
        return

    keys = get_all_keys()
    if custom_key in keys:
        await update.message.reply_text(f"❌ Mã `{custom_key}` đã tồn tại!", parse_mode="Markdown")
        return

    created_dt = datetime.now(VN_TZ)
    expires_dt = created_dt + timedelta(days=days)

    keys[custom_key] = {
        "days": days,
        "price": price,
        "created_at": created_dt.strftime("%H:%M:%S - %d/%m/%Y"),
        "expires_at": expires_dt.timestamp()
    }
    save_all_keys(keys)
    await update.message.reply_text(
        f"🎉 **ĐÃ TẠO MÃ KEY THÀNH CÔNG!**\n\n🔑 **Mã Key:** `{custom_key}`\n⏳ **Thời hạn:** `{days} ngày`\n💵 **Giá:** `{price:,} VNĐ`",
        parse_mode="Markdown"
    )

async def editkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID: return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("⚠️ **Cú pháp:** `/editkey <tên_key> <số_ngày_mới> <giá_mới>`", parse_mode="Markdown")
        return

    custom_key = args[0].upper()
    try:
        days, price = int(args[1]), int(args[2])
    except ValueError:
        return

    keys = get_all_keys()
    if custom_key not in keys:
        await update.message.reply_text(f"❌ Không tìm thấy mã `{custom_key}`!", parse_mode="Markdown")
        return

    created_str = keys[custom_key].get("created_at")
    try:
        created_dt = datetime.strptime(created_str, "%H:%M:%S - %d/%m/%Y").replace(tzinfo=VN_TZ)
    except:
        created_dt = datetime.now(VN_TZ)
        
    expires_dt = created_dt + timedelta(days=days)

    keys[custom_key]["days"] = days
    keys[custom_key]["price"] = price
    keys[custom_key]["expires_at"] = expires_dt.timestamp()
    save_all_keys(keys)
    await update.message.reply_text(
        f"✅ **ĐÃ SỬA MÃ KEY THÀNH CÔNG!**\n\n🔑 **Mã Key:** `{custom_key}`\n⏳ **Thời hạn mới:** `{days} ngày`\n💵 **Giá mới:** `{price:,} VNĐ`",
        parse_mode="Markdown"
    )

async def delkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID: return
    if not context.args:
        return

    target_key = context.args[0].upper()
    keys = get_all_keys()

    if target_key in keys:
        del keys[target_key]
        save_all_keys(keys)
        await update.message.reply_text(f"🗑️ Đã xóa mã `{target_key}` thành công!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Không tìm thấy mã `{target_key}`!", parse_mode="Markdown")

async def listkeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID: return
    keys = get_all_keys()
    if not keys:
        await update.message.reply_text("📂 Hiện chưa có mã kích hoạt nào!")
        return

    msg = "📋 **DANH SÁCH MÃ KEY ĐANG CÓ:**\n\n"
    for k, v in keys.items():
        status_text, _ = get_remaining_time(v)
        price = v.get('price', 0)
        msg += f"• Mã: `{k}` | Giá: `{price:,}đ`\n  └ {status_text}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

# ==================== WEB API ROUTE ====================

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
                return jsonify({"success": False, "message": "Mã Key không hợp lệ hoặc đã được sử dụng!"}), 400
            
            _, is_expired = get_remaining_time(keys[used_key])
            if is_expired:
                return jsonify({"success": False, "message": "Mã Key này đã hết hạn!"}), 400
            
            del keys[used_key]
            save_all_keys(keys)
            amount = 0

        order_id = str(uuid.uuid4())[:8].upper()
        created_at = datetime.now(VN_TZ).strftime("%H:%M:%S - %d/%m/%Y")

        if amount == 0:
            orders[order_id] = {
                'name': name, 'full_link': full_link, 'status': 'APPROVED',
                'created_at': created_at, 'amount': amount, 'message_id': None
            }
            key_text = f"🔑 **Mã Key áp dụng:** `{used_key}`\n" if used_key else ""
            msg = (
                f"🎁 **CÓ KHÁCH SỬ DỤNG KEY THÀNH CÔNG!**\n\n"
                f"👤 **Username:** `{name}`\n🔗 **Link Locket:** {full_link}\n🆔 **Mã đơn:** `{order_id}`\n"
                f"{key_text}⏰ **Thời gian:** `{created_at}`\n\n👉 *Khách đã được tự động duyệt.*"
            )
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
            return jsonify({"success": True, "order_id": order_id, "auto_approved": True})

        orders[order_id] = {
            'name': name, 'full_link': full_link, 'status': 'PENDING',
            'created_at': created_at, 'amount': amount, 'message_id': None
        }

        msg = (
            f"🔔 **ĐƠN HÀNG MỚI!**\n\n"
            f"👤 **Username:** `{name}`\n🔗 **Link Locket:** {full_link}\n🆔 **Mã đơn:** `{order_id}`\n"
            f"💵 **Số tiền:** `{amount:,} VNĐ`\n📝 **Nội dung CK:** `LOCKET {order_id}`\n⏰ **Thời gian:** `{created_at}`"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ XÁC NHẬN ĐÃ NHẬN TIỀN", callback_data=f"approve_{order_id}")]])
        res = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard.to_dict()}).json()

        if res.get("ok"):
            orders[order_id]['message_id'] = res['result']['message_id']

        return jsonify({"success": True, "order_id": order_id, "auto_approved": False})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/check-key', methods=['POST'])
def check_key():
    try:
        data = request.json
        raw_key = data.get('key', '').strip().upper()
        keys = get_all_keys()

        if not raw_key or raw_key not in keys:
            return jsonify({"success": False, "message": "Mã không hợp lệ hoặc đã được sử dụng!"}), 400

        key_info = keys[raw_key]
        _, is_expired = get_remaining_time(key_info)
        if is_expired:
            return jsonify({"success": False, "message": "Mã này đã hết hạn sử dụng!"}), 400

        return jsonify({"success": True, "key": raw_key, "days": key_info['days'], "price": key_info.get('price', 0)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/cancel-order', methods=['POST'])
def cancel_order():
    try:
        data = request.json
        order_id = data.get('order_id')
        if order_id in orders:
            orders[order_id]['status'] = 'CANCELLED'
            message_id = orders[order_id].get('message_id')
            if message_id:
                msg = f"❌ **ĐƠN HÀNG ĐÃ HỦY!**\n\n🆔 **Mã đơn:** `{order_id}`"
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json={"chat_id": ADMIN_CHAT_ID, "message_id": message_id, "text": msg, "parse_mode": "Markdown"})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/check-status/<order_id>', methods=['GET'])
def check_status(order_id):
    order = orders.get(order_id)
    if not order: return jsonify({"status": "NOT_FOUND"})
    return jsonify({"status": order['status'], "name": order['name']})

# ==================== API XỬ LÝ LẤY MÃ UDID CỦA IPHONE ====================

@app.route('/get-udid-profile', methods=['GET'])
def get_udid_profile():
    # URL nhận dữ liệu UDID (Thay bằng URL của Render nếu cần)
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
            <string>IMEI</string>
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
    <key>PayloadDescription</key>
    <string>Cài đặt cấu hình này để trích xuất UDID.</string>
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
            version = re.search(r'<key>VERSION</key>\s*<string>(.*?)</string>', plist_xml).group(1)
            
            msg = (
                f"📱 **CÓ THIẾT BỊ VỪA XÁC MINH UDID!**\n\n"
                f"🔑 **UDID:** `{udid}`\n"
                f"🍏 **Thiết bị:** `{product}`\n"
                f"⚙️ **iOS:** `{version}`\n\n"
                f"*(Lưu mã UDID này lại để cấp cấu hình DNS)*"
            )
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
            
            success_url = "https://bot-dns.onrender.com/"
            return "", 301, {"Location": success_url}
    except Exception as e:
        print(f"Lỗi phân tích UDID: {e}")
        
    return "Lỗi xác minh thiết bị", 400

# ==================== KẾT THÚC API UDID ====================

@app.route('/download-profile/<dns_id>/<username>.mobileconfig', methods=['GET'])
def download_profile(dns_id, username):
    xml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>PayloadDisplayName</key>
    <string>NextDNS ({dns_id}) · {username}</string>
    <key>PayloadDescription</key>
    <string>Cấu hình DNS Locket dành riêng cho {username}. Vận hành bởi Duc Kien DNS.</string>
    <key>PayloadIdentifier</key>
    <string>io.nextdns.{dns_id}.profile</string>
    <key>PayloadOrganization</key>
    <string>Duc Kien DNS</string>
    <key>PayloadScope</key>
    <string>System</string>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{uuid.uuid4()}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
    <key>PayloadContent</key>
    <array>
      <dict>
        <key>DNSSettings</key>
        <dict>
          <key>DNSProtocol</key>
          <string>HTTPS</string>
          <key>ServerURL</key>
          <string>https://apple.dns.nextdns.io/{dns_id}/{username}</string>
        </dict>
        <key>OnDemandRules</key>
        <array>
          <dict>
            <key>Action</key>
            <string>EvaluateConnection</string>
            <key>ActionParameters</key>
            <array>
              <dict>
                <key>DomainAction</key>
                <string>NeverConnect</string>
                <key>Domains</key>
                <array>
                  <string>captive.apple.com</string>
                  <string>3gppnetwork.org</string>
                  <string>dav.orange.fr</string>
                  <string>vvm.mobistar.be</string>
                  <string>vvm.mstore.msg.t-mobile.com</string>
                  <string>tma.vvm.mone.pan-net.eu</string>
                  <string>vvm.ee.co.uk</string>
                </array>
              </dict>
            </array>
          </dict>
          <dict>
            <key>Action</key>
            <string>Connect</string>
          </dict>
        </array>
        <key>PayloadType</key>
        <string>com.apple.dnsSettings.managed</string>
        <key>PayloadIdentifier</key>
        <string>io.nextdns.{dns_id}.profile.dnsSettings.managed</string>
        <key>PayloadUUID</key>
        <string>{uuid.uuid4()}</string>
        <key>PayloadDisplayName</key>
        <string>NextDNS ({dns_id}) · {username}</string>
        <key>PayloadOrganization</key>
        <string>Duc Kien DNS</string>
        <key>PayloadVersion</key>
        <integer>1</integer>
      </dict>
    </array>
  </dict>
</plist>'''
    return Response(xml_content, mimetype='application/x-apple-asymmetric-key-exchange')

@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return "Server DNS Locket đang hoạt động bình thường!", 200

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("approve_"):
        order_id = query.data.split("_")[1]
        if order_id in orders:
            orders[order_id]['status'] = 'APPROVED'
            await query.edit_message_text(text=f"✅ **ĐÃ XÁC NHẬN ĐƠN HÀNG: {order_id}**", parse_mode="Markdown")

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    
    tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", help_command))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("genkey", genkey_command))
    tg_app.add_handler(CommandHandler("editkey", editkey_command))
    tg_app.add_handler(CommandHandler("delkey", delkey_command))
    tg_app.add_handler(CommandHandler("listkeys", listkeys_command))
    tg_app.add_handler(CallbackQueryHandler(button_callback))
    
    # Đã thêm drop_pending_updates=True chống lỗi Conflict trên Render
    tg_app.run_polling(drop_pending_updates=True)
