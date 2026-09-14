import os
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, MessagingApiBlob, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, ImageMessageContent
import google.generativeai as genai

app = Flask(__name__)

LINE_ACCESS_TOKEN = os.environ.get('LINE_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

configuration = Configuration(access_token=LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel('gemini-2.5-flash')

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent)
def handle_message(event):
    if not isinstance(event.message, ImageMessageContent):
        return

    with ApiClient(configuration) as api_client:
        blob_api = MessagingApiBlob(api_client)
        image_bytes = blob_api.get_message_content(message_id=event.message.id)

    prompt = """
    Hãy đọc hình ảnh hóa đơn/sổ ghi chép này và xuất ra văn bản để copy.
    Quy tắc quan trọng:
    1. Nếu có nét gạch dọc kéo dài từ một đơn giá (ví dụ: 200, 40, 100...) xuống các dòng bên dưới, điều đó có nghĩa là TẤT CẢ các dòng nằm trong phạm vi nét gạch dọc đó đều có cùng đơn giá nhân ở trên.
    2. Hãy tự động nhân/gán đơn giá đó cho từng dòng tương ứng.
    3. Giữ cấu trúc rõ ràng, xuất ra văn bản thuần để tôi dễ copy.
    """

    image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]
    response = model.generate_content([prompt, image_parts[0]])
    extracted_text = response.text if response.text else "Không thể đọc được ảnh."

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=extracted_text)]
            )
        )

if __name__ == "__main__":
    app.run(port=5000)
