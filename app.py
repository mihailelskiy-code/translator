import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Update, Message
from fastapi import FastAPI, Request
import uvicorn
import os

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # типа https://translator-4fkx.onrender.com/webhook

bot = Bot(TOKEN)
dp = Dispatcher()
app = FastAPI()


# ---------- Хендлеры ----------

@dp.message(F.text)
async def echo(message: Message):
    await message.answer("Бот работает! Напиши голосовое — добавим позже.")


# ---------- Webhook обработчик ----------

@app.post("/webhook")
async def webhook_handler(request: Request):
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}


# ---------- Render Startup ----------

@app.on_event("startup")
async def on_start():
    print("Setting webhook to:", WEBHOOK_URL)
    await bot.set_webhook(WEBHOOK_URL)


@app.on_event("shutdown")
async def on_stop():
    await bot.delete_webhook()


# ---------- Run ----------

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
