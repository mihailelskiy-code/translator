# app.py
import os
import logging

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update
import uvicorn

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://translator-4f7k.onrender.com/webhook

bot = Bot(TOKEN)
dp = Dispatcher()
app = FastAPI()


# ---------- хендлеры бота ----------

@dp.message(F.text)
async def echo_handler(message: Message):
    await message.answer("Братик, я жив и получаю апдейты через вебхук ✅")


# ---------- HTTP-маршруты ----------

@app.get("/")
async def root():
    return {"status": "ok", "message": "translator bot running"}


# /webhook принимает и GET, и POST (на всякий случай)
@app.api_route("/webhook", methods=["GET", "POST"])
async def telegram_webhook(request: Request):
    if request.method == "POST":
        data = await request.json()
        update = Update(**data)
        await dp.feed_update(bot, update)
    return {"ok": True}


# ---------- события запуска/остановки ----------

@app.on_event("startup")
async def on_startup():
    logging.info(f"Setting webhook to {WEBHOOK_URL!r}")
    if WEBHOOK_URL:
        try:
            await bot.set_webhook(WEBHOOK_URL)
            logging.info("Webhook set successfully")
        except Exception as e:
            logging.exception(f"Failed to set webhook: {e}")


@app.on_event("shutdown")
async def on_shutdown():
    try:
        await bot.delete_webhook()
    except Exception:
        pass


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
