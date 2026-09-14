import os
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

@handler.add(MessageEvent)
def handle_message(event):
    if isinstance(event.message, TextMessageContent):
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="Tôi đã nhận được tin nhắn! Hãy gửi cho tôi 1 tấm ảnh hóa đơn/sổ tay để đọc dữ liệu nhé.")]
                )
            )

    elif isinstance(event.message, ImageMessageContent):
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            try:
                blob_api = MessagingApiBlob(api_client)
                image_bytes = blob_api.get_message_content(message_id=event.message.id)

                # Nén và thu nhỏ ảnh để tránh tràn RAM trên Render Free Tier
                img = Image.open(BytesIO(image_bytes))
                img.thumbnail((1024, 1024))
                output = BytesIO()
                img.save(output, format="JPEG", quality=85)
                compressed_image_bytes = output.getvalue()

                # Prompt đã được tối ưu để chỉ lấy chữ/số viết tay
                prompt = (
                    "Chỉ trích xuất các phần chữ và số viết tay có trong ảnh. "
                    "Bỏ qua toàn bộ các chữ in sẵn (như 估價單, 品名, 數量, 單價, 金額, 合計...). "
                    "Trình bày kết quả dưới dạng bảng hoặc danh sách gọn gàng chỉ gồm dữ liệu viết tay."
                )

                response = ai_client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=[
                        genai.types.Part.from_bytes(
                            data=compressed_image_bytes,
                            mime_type='image/jpeg'
                        ),
                        prompt
                    ]
                )

                extracted_text = response.text if response.text else "Không thể đọc được dữ liệu từ ảnh."

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=extracted_text)]
                    )
                )
            except Exception as e:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"Lỗi xử lý ảnh: {str(e)}")]
                    )
                )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
