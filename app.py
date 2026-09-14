import os
import time
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

# Khởi tạo client Gemini
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
    # Bỏ qua tin nhắn dạng chữ (Bot im lặng, không trả lời)
    if isinstance(event.message, TextMessageContent):
        return

    # Chỉ xử lý khi tin nhắn là hình ảnh
    elif isinstance(event.message, ImageMessageContent):
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            try:
                blob_api = MessagingApiBlob(api_client)
                image_bytes = blob_api.get_message_content(message_id=event.message.id)

                # Nén ảnh nhẹ hơn để gửi nhanh (600x600, quality 65)
                img = Image.open(BytesIO(image_bytes))
                img.thumbnail((600, 600))
                output = BytesIO()
                img.save(output, format="JPEG", quality=65)
                compressed_image_bytes = output.getvalue()

                # Prompt tổng hợp tất cả quy tắc đọc ảnh chuẩn xác
                prompt = (
                    "Hãy phân tích và đọc toàn bộ dữ liệu chữ và số viết tay trong ảnh theo các quy tắc sau:\n"
                    "1. BẮT BUỘC giữ nguyên các số 0 đằng trước (ví dụ: 01, 02, 03, không được tự ý đổi thành 1, 2, 3).\n"
                    "2. Nếu là dạng bảng gom chung đơn giá (như gom x 40): Hãy đọc theo thứ tự TỪ TRÊN XUỐNG DƯỚI CHO TỪNG CỘT (Đọc hết cột trái từ trên xuống, rồi đến cột giữa từ trên xuống, rồi đến cột phải từ trên xuống). Nối các số phân cách bằng dấu phẩy và kết thúc bằng 'x [đơn giá]'.\n"
                    "3. Nếu là dạng sổ ghi chép có chữ hoặc phép tính riêng từng dòng (như 'Đề', 'Đầu 1 x 100', 'Đít 1 x 100'): Hãy xuống dòng và ghi lại chính xác nội dung từng dòng từ trên xuống dưới.\n"
                    "4. Dòng 'Tổng cộng:': Lấy chính xác con số tổng viết tay tại ô 合計 hoặc ở góc dưới cùng tờ giấy (ví dụ: 1200 hoặc 4000). Đây là tổng đơn giá cộng lại, TUYỆT ĐỐI KHÔNG TÍNH PHÉP NHÂN.\n"
                    "5. QUY TẮC TUYỆT ĐỐI: Không viết lời chào, lời dẫn hay giải thích thừa."
                )

                image_part = genai.types.Part.from_bytes(
                    data=compressed_image_bytes,
                    mime_type='image/jpeg'
                )

                # Cơ chế tự động chọn model và thử lại
                models_to_try = ['gemini-3.6-flash', 'gemini-2.5-flash', 'gemini-1.5-flash']
                response = None
                last_error = None

                for model_name in models_to_try:
                    for attempt in range(2):
                        try:
                            response = ai_client.models.generate_content(
                                model=model_name,
                                contents=[image_part, prompt]
                            )
                            if response and response.text:
                                break
                        except Exception as err:
                            last_error = err
                            if "503" in str(err) and attempt == 0:
                                time.sleep(1.5)
                                continue
                            break
                    if response and response.text:
                        break

                if response and response.text:
                    extracted_text = response.text
                else:
                    raise last_error if last_error else Exception("Không thể kết nối đến Gemini API.")

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
