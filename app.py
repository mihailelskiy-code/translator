# app.py
import os
import logging

from fastapi import FastAPI, Request
import uvicorn
import httpx

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "ok", "message": "simple webhook bot running"}


@app.api_route("/webhook", methods=["GET", "POST"])
async def telegram_webhook(request: Request):
    # Telegram присылает апдейты только POST-запросами,
    # но GET оставим, чтобы браузер не видел 404.
    if request.method == "POST":
        data = await request.json()
        logging.info(f"Update from Telegram: {data}")

        message = data.get("message") or {}
        chat = message.get("chat") or {}
        text = message.get("text")

        chat_id = chat.get("id")

        if chat_id is not None:
            reply_text = "Братик, вебхук работает ✅"
            # если пришёл текст — добавим его в ответ, чтобы видеть эхо
            if text:
                reply_text += f"\nТы написал: {text}"

            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{TELEGRAM_API}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": reply_text,
                    },
                    timeout=10.0,
                )

    # Всегда отвечаем 200 OK, чтобы Telegram был доволен
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
