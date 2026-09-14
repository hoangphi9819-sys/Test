import os
from flask import Flask, request, abort
import google.generativeai as genai
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
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel('gemini-1.5-flash')

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

# Lắng nghe tất cả MessageEvent
@handler.add(MessageEvent)
def handle_message(event):
    # Nếu là tin nhắn văn bản
    if isinstance(event.message, TextMessageContent):
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="Tôi đã nhận được tin nhắn! Hãy gửi cho tôi 1 tấm ảnh hóa đơn/sổ tay để đọc dữ liệu nhé.")]
                )
            )

    # Nếu là tin nhắn hình ảnh
    elif isinstance(event.message, ImageMessageContent):
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            line_bot_api = MessagingApi(api_client)
            
            image_bytes = blob_api.get_message_content(message_id=event.message.id)

            prompt = """Hãy đọc ảnh này và xuất văn bản thuần túy giữ nguyên cấu trúc toán học."""

            image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]
            response = model.generate_content([prompt, image_parts[0]])
            extracted_text = response.text if response.text else "Không thể đọc được ảnh."

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=extracted_text)]
                )
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
