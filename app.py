# app.py
import os
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update
from fastapi import FastAPI, Request
import uvicorn

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://translator-4f7k.onrender.com/webhook

bot = Bot(TOKEN)
dp = Dispatcher()
app = FastAPI()


# ---------- Хендлеры бота ----------

@dp.message(F.text)
async def echo_handler(message: Message):
    await message.answer("Братик, я на Render и живой 😊")


# ---------- HTTP маршруты ----------

@app.get("/")
async def root():
    return {"status": "ok", "message": "translator bot running"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """Сюда Telegram шлёт апдейты."""
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}


# ---------- События запуска/остановки ----------

@app.on_event("startup")
async def on_startup():
    logging.info(f"Setting webhook to {WEBHOOK_URL!r}")
    await bot.set_webhook(WEBHOOK_URL)


@app.on_event("shutdown")
async def on_shutdown():
    logging.info("Deleting webhook")
    await bot.delete_webhook()


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )

