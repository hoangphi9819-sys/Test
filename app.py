import os
import re
import sqlite3
import queue
import threading
from io import BytesIO
from PIL import Image
from flask import Flask, request, abort
from google import genai
from google.genai import types
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

# HÀNG ĐỢI XỬ LÝ ẢNH CHUẨN THỨ TỰ
task_queue = queue.Queue()

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

def queue_worker():
    while True:
        task = task_queue.get()
        if task is None:
            break
        
        compressed_bytes, reply_token, group_id = task
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)

                # PROMPT ĐỌC NGUYÊN BẢN 100% KHÔNG TỰ SUY LUẬN
                prompt_text = (
                    "Hãy trích xuất chính xác toàn bộ chữ và số viết tay xuất hiện trong ảnh theo các quy tắc:\n"
                    "1. GHI NGUYÊN BẢN: Trong ảnh có chữ gì/số gì thì ghi ra đúng chính xác như vậy (ví dụ 'L:16=5đ/ Đ;45 -54=100k'). KHÔNG tự ý suy luận, KHÔNG tách dòng, KHÔNG diễn giải thành từ khác.\n"
                    "2. BỎ HOÀN TOÀN ngày tháng năm ở đầu tờ giấy (nếu có).\n"
                    "3. DÒNG CUỐI CÙNG BẮT BUỘC: Nếu trong ảnh có con số tổng kết quả (ví dụ 1800, 215...) thì ghi dòng cuối dạng 'TỔNG: [con số đó]'. Nếu không có, hãy tự cộng tổng các giá trị tiền trong ảnh và ghi 'TỔNG: [con số]'.\n"
                    "4. Không viết lời chào hay bất kỳ văn bản giải thích nào thêm."
                )

                response = ai_client.models.generate_content(
                    model='gemini-flash-latest',
                    contents=types.Content(
                        parts=[
                            types.Part.from_bytes(data=compressed_bytes, mime_type='image/jpeg'),
                            types.Part.from_text(text=prompt_text)
                        ]
                    )
                )

                extracted_text = response.text if (response and response.text) else ""

                match = re.search(r'TỔNG:\s*(-?\d+(?:\.\d+)?)', extracted_text, re.IGNORECASE)
                if match:
                    add_amount(group_id, float(match.group(1)))
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
            print(f"Lỗi worker: {e}")
        finally:
            task_queue.task_done()

threading.Thread(target=queue_worker, daemon=True).start()

def prepare_image(message_id, reply_token, group_id):
    try:
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            image_bytes = blob_api.get_message_content(message_id=message_id)

            img = Image.open(BytesIO(image_bytes))
            img.thumbnail((450, 450))
            output = BytesIO()
            img.save(output, format="JPEG", quality=40)
            
            task_queue.put((output.getvalue(), reply_token, group_id))
    except Exception as e:
        print(f"Lỗi tải ảnh: {e}")

@handler.add(MessageEvent)
def handle_message(event):
    group_id = getattr(event.source, 'group_id', None) or getattr(event.source, 'user_id', 'default_user')

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

    elif isinstance(event.message, ImageMessageContent):
        threading.Thread(target=prepare_image, args=(event.message.id, event.reply_token, group_id)).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
