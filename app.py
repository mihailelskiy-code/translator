import os
import logging
import base64
import httpx

from fastapi import FastAPI, Request
import uvicorn

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

HF_TOKEN = os.getenv("HF_TOKEN")
HF_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3"

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "ok", "message": "free whisper bot running"}


@app.api_route("/webhook", methods=["GET", "POST"])
async def telegram_webhook(request: Request):
    if request.method != "POST":
        return {"ok": True}

    data = await request.json()
    logging.info(f"Update: {data}")

    message = data.get("message")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]

    # если voice-сообщение
    if "voice" in message:
        file_id = message["voice"]["file_id"]

        # 1. Скачать OGG
        async with httpx.AsyncClient() as client:
            file_info = await client.get(f"{TELEGRAM_API}/getFile?file_id={file_id}")
            file_path = file_info.json()["result"]["file_path"]

            file_bytes = await client.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}")
            audio_bytes = file_bytes.content

        # 2. Отправить в бесплатный HuggingFace Whisper
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                HF_URL,
                headers={
                    "Authorization": f"Bearer {HF_TOKEN}",
                },
                data=audio_bytes,
                timeout=120
            )

        if resp.status_code != 200:
            text = f"Братик, HF Whisper error: {resp.text}"
        else:
            result = resp.json()
            text = result.get("text", "Братик, не смог распознать речь 😔")

        # 3. Отправить ответ в Telegram
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )

        return {"ok": True}

    # обычный текст
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": "Братик, отправь голосовое 💬"},
        )

    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
