import os
import logging
from fastapi import FastAPI, Request
import uvicorn
import httpx

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")  # <<< сюда вставляется HF ключ
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

HF_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3-turbo"

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "ok", "message": "HF speech bot running"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    logging.info(f"Update: {data}")

    message = data.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    # голосовое сообщение
    voice = message.get("voice")
    if not voice:
        return {"ok": True}

    file_id = voice.get("file_id")

    # 1. Получаем файл через Telegram API
    async with httpx.AsyncClient() as client:
        file_info = await client.get(f"{TELEGRAM_API}/getFile?file_id={file_id}")
        file_path = file_info.json()["result"]["file_path"]

        voice_bytes = await client.get(
            f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        )

    # 2. Отправляем на Whisper HF
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}

    async with httpx.AsyncClient(timeout=200) as client:
        r = await client.post(
            HF_URL,
            headers=headers,
            content=voice_bytes.content
        )

    if r.status_code != 200:
        logging.error(r.text)
        text = f"Ошибка распознавания 😢\n{r.text}"
    else:
        result = r.json()
        text = result.get("text", "Не удалось распознать...")

    # 3. Отправляем ответ
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text}
        )

    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
