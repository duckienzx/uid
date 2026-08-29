import os
import uuid
import re
import requests
import threading
from flask import Flask, request, Response
from flask_cors import CORS
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8525386643:AAFhrmnkUmamGJgWZg4MHNjt8znEfaqlU-E").strip()
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "7126654319").strip()
RENDER_DOMAIN = "https://uid-yskb.onrender.com"

app = Flask(__name__)
CORS(app)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_CHAT_ID:
        return
    msg = (
        "🤖 **BOT LẤY UDID THIẾT BỊ ĐANG HOẠT ĐỘNG**\n\n"
        f"🔗 **Link lấy UDID:** `{RENDER_DOMAIN}/get-udid-profile`\n\n"
        "Khi người dùng cài Profile từ Safari, mã UDID sẽ tự động gửi về đây!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

@app.route('/get-udid-profile', methods=['GET'])
def get_udid_profile():
    receive_url = f"{RENDER_DOMAIN}/receive-udid"
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
            <string>DEVICE_NAME</string>
        </array>
    </dict>
    <key>PayloadOrganization</key>
    <string>Xác minh thiết bị</string>
    <key>PayloadDisplayName</key>
    <string>Lấy UDID Thiết Bị iOS</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
    <key>PayloadUUID</key>
    <string>{uuid.uuid4()}</string>
    <key>PayloadIdentifier</key>
    <string>com.device.getudid</string>
    <key>PayloadDescription</key>
    <string>Cấu hình hỗ trợ đọc mã định danh UDID của thiết bị.</string>
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
            
            udid_match = re.search(r'<key>UDID</key>\s*<string>(.*?)</string>', plist_xml)
            product_match = re.search(r'<key>PRODUCT</key>\s*<string>(.*?)</string>', plist_xml)
            version_match = re.search(r'<key>VERSION</key>\s*<string>(.*?)</string>', plist_xml)
            name_match = re.search(r'<key>DEVICE_NAME</key>\s*<string>(.*?)</string>', plist_xml)
            
            udid = udid_match.group(1) if udid_match else "Không xác định"
            product = product_match.group(1) if product_match else "Không xác định"
            version = version_match.group(1) if version_match else "Không xác định"
            dev_name = name_match.group(1) if name_match else "iPhone"

            msg = (
                f"📱 **THIẾT BỊ MỚI VỪA GỬI UDID!**\n\n"
                f"🔑 **UDID:** `{udid}`\n"
                f"🏷️ **Tên máy:** `{dev_name}`\n"
                f"🍏 **Model:** `{product}`\n"
                f"⚙️ **iOS:** `{version}`"
            )
            
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
            )
            
            return "", 301, {"Location": f"{RENDER_DOMAIN}/success"}
    except Exception as e:
        print(f"Lỗi phân tích UDID: {e}")
        
    return "Lỗi xử lý UDID", 400

@app.route('/success', methods=['GET'])
def success_page():
    return """
    <html>
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Thành công</title></head>
    <body style="display:flex;justify-content:center;align-items:center;height:100vh;margin:0;font-family:-apple-system,sans-serif;background:#f8f9fa;text-align:center;padding:20px;">
        <div style="background:white;padding:40px;border-radius:20px;box-shadow:0 10px 30px rgba(0,0,0,0.08);max-width:350px;">
            <div style="font-size:50px;margin-bottom:15px;">✅</div>
            <h2 style="margin:0 0 10px;color:#1a1a1a;">Đã lấy UDID thành công!</h2>
            <p style="color:#666;font-size:14px;line-height:1.5;">Thông tin thiết bị đã được gửi về Telegram của quản trị viên.</p>
        </div>
    </body>
    </html>
    """, 200

@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return "Server lấy UDID đang chạy!", 200

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
    
    tg_app.add_handler(CommandHandler("start", start_command))
    tg_app.run_polling(drop_pending_updates=True, timeout=30)
