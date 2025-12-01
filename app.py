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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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
        async with httpx.AsyncClient(timeout=20.0) as client:
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
    """Упрощённый вызов Telegram Bot API (JSON POST)."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(f"{TELEGRAM_API}/{method}", json=payload)
        if r.status_code != 200:
            logging.error(f"Telegram API {method} failed: {r.status_code} {r.text}")
        return r


async def tg_send_audio(chat_id: int, audio_bytes: bytes, caption: str):
    """Отправка mp3/ogg как audio в Telegram."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        files = {
            "audio": ("translation.mp3", audio_bytes, "audio/mpeg"),
        }
        data = {
            "chat_id": str(chat_id),
            "caption": caption,
        }
        r = await client.post(f"{TELEGRAM_API}/sendAudio", data=data, files=files)
        if r.status_code != 200:
            logging.error(f"sendAudio failed: {r.status_code} {r.text}")
        return r


async def openai_transcribe(audio_bytes: bytes) -> str | None:
    """Whisper STT: аудио → текст."""
    if not OPENAI_API_KEY:
        return None

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }

    files = {
        "file": ("audio.ogg", audio_bytes, "audio/ogg"),
    }
    data = {
        "model": "whisper-1",
        # язык можно не указывать, Whisper сам поймет
    }

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
            )
            if r.status_code != 200:
                logging.error(f"OpenAI STT error: {r.status_code} {r.text}")
                return None
            j = r.json()
            return j.get("text")
    except Exception as e:
        logging.exception(f"OpenAI STT exception: {e}")
        return None


async def openai_tts(text: str) -> bytes | None:
    """OpenAI TTS: текст → mp3 байты."""
    if not OPENAI_API_KEY:
        return None

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    json_payload = {
        "model": "gpt-4o-mini-tts",  # при необходимости можешь сменить модель
        "voice": "alloy",
        "input": text,
        "format": "mp3",
    }

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers=headers,
                json=json_payload,
            )
            if r.status_code != 200:
                logging.error(f"OpenAI TTS error: {r.status_code} {r.text}")
                return None
            return r.content
    except Exception as e:
        logging.exception(f"OpenAI TTS exception: {e}")
        return None


# ----------------- HTTP-МАРШРУТЫ ----------------- #

@app.get("/")
async def root():
    return {"status": "ok", "message": "translator bot running"}


@app.api_route("/webhook", methods=["GET", "POST"])
async def telegram_webhook(request: Request):
    if request.method == "GET":
        return {"ok": True}

    data = await request.json()
    logging.info(f"Update from Telegram: {data}")

    # 1) CALLBACK QUERY (кнопки режимов)
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

            await tg_request(
                "answerCallbackQuery",
                {
                    "callback_query_id": cq_id,
                    "text": f"Режим перевода: {mode}",
                    "show_alert": False,
                },
            )

            await tg_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "✅ Режим перевода обновлён.\n"
                            "Напиши текст или отправь голос – я переведу.",
                    "reply_markup": build_mode_keyboard(current=mode),
                },
            )

        return {"ok": True}

    # 2) СООБЩЕНИЯ
    message = data.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text")
    voice = message.get("voice")
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
            "2️⃣ Отправь текст ИЛИ голосовое – я верну перевод.\n\n"
            "По умолчанию включён режим 🤖 Auto: "
            "если текст/голос на русском → перевожу на немецкий, "
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

    mode = user_modes.get(chat_id, "auto")

    # ----- VOICE: голос → текст → перевод → TTS -----
    if voice:
        if not OPENAI_API_KEY:
            await tg_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Для работы с голосом нужен OPENAI_API_KEY в переменных окружения 🚨",
                },
            )
            return {"ok": True}

        file_id = voice["file_id"]

        # 1) Получаем file_path через getFile
        file_res = await tg_request("getFile", {"file_id": file_id})
        file_json = file_res.json()
        file_path = file_json.get("result", {}).get("file_path")
        if not file_path:
            await tg_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Не смог получить файл от Telegram 😔",
                },
            )
            return {"ok": True}

        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

        # 2) Скачиваем аудио
        async with httpx.AsyncClient(timeout=60.0) as client:
            audio_resp = await client.get(file_url)
            if audio_resp.status_code != 200:
                logging.error(f"Download voice failed: {audio_resp.status_code} {audio_resp.text}")
                await tg_request(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": "Не удалось скачать голосовое из Telegram 😔",
                    },
                )
                return {"ok": True}
            audio_bytes = audio_resp.content

        # 3) STT: аудио → текст
        recognized = await openai_transcribe(audio_bytes)
        if not recognized:
            await tg_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Не удалось распознать речь 😔",
                },
            )
            return {"ok": True}

        # 4) Переводим распознанный текст
        translated = await translate_text(recognized, mode)

        # 5) TTS: перевод → голос
        tts_audio = await openai_tts(translated)

        # 6) Отправляем текст + (если получилось) аудио
        orig_safe = html.escape(recognized)
        tr_safe = html.escape(translated)

        reply_text = f"<b>Оригинал (из голоса):</b>\n{orig_safe}\n\n<b>Перевод:</b>\n{tr_safe}"

        await tg_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": reply_text,
                "parse_mode": "HTML",
                "reply_markup": build_mode_keyboard(current=mode),
            },
        )

        if tts_audio:
            await tg_send_audio(
                chat_id,
                tts_audio,
                caption="🎧 Озвученный перевод",
            )

        return {"ok": True}

    # ----- ТЕКСТ: как раньше -----
    if text:
        translated = await translate_text(text, mode)

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
