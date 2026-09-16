import os
import re
import sqlite3
import concurrent.futures
from io import BytesIO
from PIL import Image
from flask import Flask, request, abort
from google import genai
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, MessagingApiBlob, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Thread pool xử lý nhiều ảnh song song
executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

# DATABASE LƯU SỐ TIỀN CỦA CÁC ẢNH
def init_db():
    conn = sqlite3.connect('totals.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS group_totals
                 (group_id TEXT, amount REAL)''')
    conn.commit()
    conn.close()

init_db()

def add_amount(group_id, amount):
    conn = sqlite3.connect('totals.db')
    c = conn.cursor()
    c.execute("INSERT INTO group_totals VALUES (?, ?)", (group_id, amount))
    conn.commit()
    conn.close()

def get_and_clear_total(group_id):
    conn = sqlite3.connect('totals.db')
    c = conn.cursor()
    c.execute("SELECT amount FROM group_totals WHERE group_id = ?", (group_id,))
    rows = c.fetchall()
    
    if not rows:
        conn.close()
        return None, 0

    amounts = [r[0] for r in rows]
    total_sum = sum(amounts)
    
    c.execute("DELETE FROM group_totals WHERE group_id = ?", (group_id,))
    conn.commit()
    conn.close()

    return amounts, total_sum

@app.route("/", methods=['GET'])
def index():
    return "Bot is running!"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

def process_single_image(message_id, reply_token, group_id):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            blob_api = MessagingApiBlob(api_client)
            
            image_bytes = blob_api.get_message_content(message_id=message_id)

            img = Image.open(BytesIO(image_bytes))
            img.thumbnail((500, 500))
            output = BytesIO()
            img.save(output, format="JPEG", quality=45)
            compressed_image_bytes = output.getvalue()

            prompt = (
                "Hãy phân tích và đọc toàn bộ chữ/số viết tay trong ảnh theo các quy tắc:\n"
                "1. BỎ HOÀN TOÀN thông tin ngày tháng năm ở đầu tờ giấy.\n"
                "2. KHÔNG ghi tiền tố 'Dòng 1:', 'Dòng 2:'... Chỉ liệt kê trực tiếp nội dung các mục từ trên xuống dưới.\n"
                "3. QUY TẮC ĐỀ GOM: Dạng '45-54=100k' nghĩa là tổng các số đó là 100k.\n"
                "4. Giữ nguyên số 0 đằng trước nếu có (01, 02...).\n"
                "5. BẮT BUỘC DÒNG CUỐI CÙNG PHẢI GHI ĐÚNG CÚ PHÁP: 'TỔNG: [con số tổng tiền cả ảnh]' (Ví dụ: TỔNG: 1800).\n"
                "6. Không viết lời chào hay giải thích thừa."
            )

            image_part = genai.types.Part.from_bytes(
                data=compressed_image_bytes,
                mime_type='image/jpeg'
            )

            response = ai_client.models.generate_content(
                model='gemini-flash-latest',
                contents=[image_part, prompt]
            )

            extracted_text = response.text if (response and response.text) else ""

            match = re.search(r'TỔNG:\s*(-?\d+(?:\.\d+)?)', extracted_text, re.IGNORECASE)
            if match:
                sub_total = float(match.group(1))
                add_amount(group_id, sub_total)
            else:
                numbers = re.findall(r'-?\d+(?:\.\d+)?', extracted_text)
                if numbers:
                    add_amount(group_id, float(numbers[-1]))

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=extracted_text)]
                )
            )
    except Exception as e:
        print(f"Lỗi xử lý ảnh: {e}")

@handler.add(MessageEvent)
def handle_message(event):
    group_id = getattr(event.source, 'group_id', None) or getattr(event.source, 'user_id', 'default_user')

    # 1. XỬ LÝ LỆNH TỔNG CHỮ HOẶC TAG BOT
    if isinstance(event.message, TextMessageContent):
        text_msg = event.message.text.lower().strip()
        
        if "tổng" in text_msg or "tong" in text_msg or "@" in text_msg:
            amounts, total_sum = get_and_clear_total(group_id)
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                
                if amounts is None:
                    reply_text = "Chưa có dữ liệu ảnh nào được gửi để tính tổng!"
                else:
                    detail_str = " + ".join([f"{a:g}" for a in amounts])
                    reply_text = (
                        f"📊 TỔNG CỘNG TẤT CẢ CÁC ẢNH:\n"
                        f"Chi tiết: {detail_str}\n"
                        f"👉 TỔNG TIỀN: {total_sum:g}"
                    )

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )
        return

    # 2. XỬ LÝ GỬI HÌNH ẢNH
    elif isinstance(event.message, ImageMessageContent):
        executor.submit(process_single_image, event.message.id, event.reply_token, group_id)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
