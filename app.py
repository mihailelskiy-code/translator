# app.py
import os
import logging
import html
import re

from fastapi import FastAPI, Request
import uvicorn
import httpx

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

app = FastAPI()

# Память режимов по chat_id: auto / de_ru / ru_de
user_modes: dict[int, str] = {}

# ----------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------- #

def build_mode_keyboard(current: str | None = None) -> dict:
    """Инлайн-клавиатура выбора режима перевода."""
    def mark(mode: str, text: str) -> str:
        return f"✅ {text}" if current == mode else text

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": mark("auto", "🤖 Auto 🇩🇪/🇷🇺"),
                    "callback_data": "mode:auto",
                }
            ],
            [
                {
                    "text": mark("de_ru", "🇩🇪 → 🇷🇺"),
                    "callback_data": "mode:de_ru",
                },
                {
                    "text": mark("ru_de", "🇷🇺 → 🇩🇪"),
                    "callback_data": "mode:ru_de",
                },
            ],
        ]
    }
    return keyboard


def detect_ru(text: str) -> bool:
    """Очень простая детекция: есть кириллица → считаем, что текст русский."""
    return bool(re.search(r"[А-Яа-яЁё]", text))


async def translate_text(text: str, mode: str) -> str:
    """
    Перевод с помощью бесплатного API MyMemory.
    mode: auto / de_ru / ru_de
    """
    if mode == "de_ru":
        src, dst = "DE", "RU"
    elif mode == "ru_de":
        src, dst = "RU", "DE"
    else:  # auto
        if detect_ru(text):
            src, dst = "RU", "DE"
        else:
            src, dst = "DE", "RU"

    params = {
        "q": text,
        "langpair": f"{src}|{dst}",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.mymemory.translated.net/get", params=params)
            data = r.json()
            translated = data.get("responseData", {}).get("translatedText")
            if not translated:
                raise RuntimeError("No translatedText in response")
            return translated
    except Exception as e:
        logging.exception(f"Translation error: {e}")
        return "Не удалось перевести текст 😔"


async def tg_request(method: str, payload: dict):
    """Упрощённый вызов Telegram Bot API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{TELEGRAM_API}/{method}", json=payload)
        if r.status_code != 200:
            logging.error(f"Telegram API {method} failed: {r.status_code} {r.text}")
        return r


# ----------------- HTTP-МАРШРУТЫ ----------------- #

@app.get("/")
async def root():
    return {"status": "ok", "message": "translator bot running"}


@app.api_route("/webhook", methods=["GET", "POST"])
async def telegram_webhook(request: Request):
    if request.method == "GET":
        # чтобы браузер не видел 404
        return {"ok": True}

    # POST от Telegram
    data = await request.json()
    logging.info(f"Update from Telegram: {data}")

    # 1) CALLBACK QUERY (нажатие на кнопки)
    if "callback_query" in data:
        cq = data["callback_query"]
        cq_id = cq["id"]
        message = cq.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        cb_data = cq.get("data", "")

        if chat_id is not None and cb_data.startswith("mode:"):
            mode = cb_data.split(":", 1)[1]
            if mode not in {"auto", "de_ru", "ru_de"}:
                mode = "auto"

            user_modes[chat_id] = mode

            # ответ на нажатие
            await tg_request(
                "answerCallbackQuery",
                {
                    "callback_query_id": cq_id,
                    "text": f"Режим перевода: {mode}",
                    "show_alert": False,
                },
            )

            # отправляем обновлённую клавиатуру
            await tg_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "✅ Режим перевода обновлён.\n"
                            "Напиши текст на русском или немецком – я переведу.",
                    "reply_markup": build_mode_keyboard(current=mode),
                },
            )

        return {"ok": True}

    # 2) СООБЩЕНИЯ
    message = data.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text")

    chat_id = chat.get("id")

    if chat_id is None:
        return {"ok": True}

    # команда /start
    if text == "/start":
        user_modes[chat_id] = "auto"

        welcome = (
            "Привет, Братик! 🧠\n\n"
            "Я перевожу между 🇩🇪 немецким и 🇷🇺 русским.\n\n"
            "1️⃣ Выбери режим перевода на кнопках ниже.\n"
            "2️⃣ Просто отправь текст – я верну перевод.\n\n"
            "По умолчанию включён режим 🤖 Auto: "
            "если текст на русском → перевожу на немецкий, "
            "если на немецком → на русский."
        )

        await tg_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": welcome,
                "reply_markup": build_mode_keyboard(current="auto"),
            },
        )
        return {"ok": True}

    # обычный текст → переводим
    if text:
        mode = user_modes.get(chat_id, "auto")
        translated = await translate_text(text, mode)

        # чуть-чуть форматирования
        orig_safe = html.escape(text)
        tr_safe = html.escape(translated)

        reply = f"<b>Оригинал:</b>\n{orig_safe}\n\n<b>Перевод:</b>\n{tr_safe}"

        await tg_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": reply,
                "parse_mode": "HTML",
                "reply_markup": build_mode_keyboard(current=mode),
            },
        )

    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
