# app.py
import os
import re
import base64
import logging
from typing import Dict, Optional, Any, List

from fastapi import FastAPI, Request
import uvicorn
import httpx

logging.basicConfig(level=logging.INFO)

# ==== КОНФИГ ==== #

TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в переменных окружения")

if not OPENROUTER_API_KEY:
    logging.warning("⚠ OPENROUTER_API_KEY не задан — распознавание голоса работать не будет")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

# Модель для распознавания речи через OpenRouter
OPENROUTER_MODEL = "google/gemini-flash-1.5-8b"

# Режимы перевода
MODE_AUTO = "auto"
MODE_DE_RU = "de_ru"
MODE_RU_DE = "ru_de"

MODE_LABELS = {
    MODE_AUTO: "Auto 🇩🇪↔🇷🇺",
    MODE_DE_RU: "🇩🇪 → 🇷🇺",
    MODE_RU_DE: "🇷🇺 → 🇩🇪",
}

# Память режимов на одного процесса (хватает для нашего бота)
user_modes: Dict[int, str] = {}

app = FastAPI()


# ==== ХЕЛПЕРЫ ДЛЯ TELEGRAM ==== #

async def tg_request(method: str, payload: Dict[str, Any]) -> httpx.Response:
    """Отправка метода в Telegram Bot API."""
    url = f"{TELEGRAM_API}/{method}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code != 200:
        logging.error("Telegram API error %s %s: %s",
                      method, resp.status_code, resp.text)
    return resp


def build_mode_keyboard(selected: str) -> Dict[str, Any]:
    """Инлайн-клавиатура выбора режима перевода."""
    def btn(mode: str) -> Dict[str, str]:
        prefix = "✅ " if mode == selected else ""
        return {"text": prefix + MODE_LABELS[mode], "callback_data": mode}

    return {
        "inline_keyboard": [
            [btn(MODE_AUTO)],
            [btn(MODE_DE_RU), btn(MODE_RU_DE)],
        ]
    }


def is_russian(text: str) -> bool:
    """Очень простой детектор русского языка по кириллице."""
    return bool(re.search(r"[А-Яа-яЁё]", text))


# ==== ПЕРЕВОД (MyMemory) ==== #

async def translate_text(text: str, mode: str) -> str:
    """
    Перевод через MyMemory:
    - auto: RU→DE или DE→RU по языку входного текста
    - ru_de: RU→DE
    - de_ru: DE→RU
    """
    text = text.strip()
    if not text:
        return "Пустой текст, нечего переводить 🤷‍♂️"

    if mode == MODE_RU_DE:
        src, tgt = "ru", "de"
    elif mode == MODE_DE_RU:
        src, tgt = "de", "ru"
    else:  # auto
        if is_russian(text):
            src, tgt = "ru", "de"
        else:
            src, tgt = "de", "ru"

    params = {
        "q": text,
        "langpair": f"{src}|{tgt}",
    }

    url = "https://api.mymemory.translated.net/get"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params)
        data = resp.json()
        translated = data.get("responseData", {}).get("translatedText")
        if not translated:
            raise ValueError("Нет translatedText в ответе")
        return translated
    except Exception as e:
        logging.exception("Ошибка перевода через MyMemory: %s", e)
        return "❌ Не удалось сделать перевод, попробуй ещё раз позже."


# ==== РАСПОЗНАВАНИЕ ГОЛОСА ЧЕРЕЗ OPENROUTER ==== #

async def download_telegram_file(file_id: str) -> Optional[bytes]:
    """Скачиваем файл голосового сообщения из Telegram."""
    try:
        # 1) Получаем путь к файлу
        get_file_resp = await tg_request("getFile", {"file_id": file_id})
        data = get_file_resp.json()
        file_path = data.get("result", {}).get("file_path")
        if not file_path:
            logging.error("Не найден file_path в ответе getFile: %s", data)
            return None

        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

        # 2) Качаем файл
        async with httpx.AsyncClient(timeout=60.0) as client:
            file_resp = await client.get(file_url)
        if file_resp.status_code != 200:
            logging.error("Ошибка скачивания файла %s: %s %s",
                          file_url, file_resp.status_code, file_resp.text)
            return None
        return file_resp.content
    except Exception as e:
        logging.exception("Ошибка при скачивании файла из Telegram: %s", e)
        return None


async def transcribe_with_openrouter(audio_bytes: bytes, lang_hint: Optional[str] = None) -> Optional[str]:
    """
    Распознаём речь через OpenRouter (модель Gemini).
    Отправляем audio как base64 + content type input_audio.
    """
    if not OPENROUTER_API_KEY:
        return None

    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")

    # Подсказка для модели, на каком языке говорим
    hint_text = ""
    if lang_hint == "ru":
        hint_text = "The audio is in Russian. Transcribe it in the original language."
    elif lang_hint == "de":
        hint_text = "The audio is in German. Transcribe it in the original language."
    else:
        hint_text = "Transcribe this Telegram voice message to plain text, keep the original language."

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": hint_text,
                    },
                    {
                        "type": "input_audio",
                        "inputAudio": {
                            "data": b64_audio,
                            # Telegram voice обычно OGG/OPUS, но многие модели принимают "mp3"/"wav".
                            # Если будут проблемы — позже можно перекодировать через ffmpeg.
                            "format": "ogg",
                        },
                    },
                ],
            }
        ],
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Не обязательно, но рекомендуется для OpenRouter
        "HTTP-Referer": "https://github.com/mihailelskiy-code/translator",
        "X-Title": "Telegram Translator Bot",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
            )

        if resp.status_code != 200:
            logging.error("OpenRouter STT error %s: %s", resp.status_code, resp.text)
            return None

        data = resp.json()
        choice = data["choices"][0]["message"]["content"]

        # content может быть строкой или списком частей
        if isinstance(choice, str):
            text = choice.strip()
        elif isinstance(choice, list):
            parts: List[str] = []
            for part in choice:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            text = " ".join(parts).strip()
        else:
            text = ""

        if not text:
            logging.error("Пустой текст из OpenRouter STT: %s", data)
            return None

        return text
    except Exception as e:
        logging.exception("Ошибка при обращении к OpenRouter STT: %s", e)
        return None


# ==== FASTAPI HANDLERS ==== #

@app.get("/")
async def root() -> Dict[str, Any]:
    return {"status": "ok", "message": "translator bot running"}


@app.api_route("/webhook", methods=["GET", "POST"])
async def telegram_webhook(request: Request) -> Dict[str, Any]:
    if request.method == "GET":
        # Чтобы браузер не видел 404, если зайти на URL вебхука
        return {"ok": True, "message": "webhook endpoint"}

    data = await request.json()
    logging.info("Update from Telegram: %s", data)

    # --- Обработка callback-кнопок (смена режима) --- #
    if "callback_query" in data:
        cq = data["callback_query"]
        chat = cq.get("message", {}).get("chat", {}) or {}
        chat_id = chat.get("id")
        mode_from_btn = cq.get("data")

        if chat_id and mode_from_btn in MODE_LABELS:
            user_modes[chat_id] = mode_from_btn
            kb = build_mode_keyboard(mode_from_btn)

            # Обновим подпись под сообщением
            await tg_request(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": cq["message"]["message_id"],
                    "reply_markup": kb,
                },
            )
            await tg_request(
                "answerCallbackQuery",
                {
                    "callback_query_id": cq["id"],
                    "text": f"Режим: {MODE_LABELS[mode_from_btn]}",
                    "show_alert": False,
                },
            )

        return {"ok": True}

    # --- Обычное сообщение --- #
    message = data.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}

    text = message.get("text")
    voice = message.get("voice")

    # Режим по умолчанию
    mode = user_modes.get(chat_id, MODE_AUTO)
    kb = build_mode_keyboard(mode)

    # /start
    if text and text.startswith("/start"):
        user_modes[chat_id] = MODE_AUTO
        kb = build_mode_keyboard(MODE_AUTO)
        start_text = (
            "Привет, Братик! 👋\n\n"
            "Я бот-переводчик 🇩🇪↔🇷🇺.\n\n"
            "• Пиши текст — я переведу.\n"
            "• Отправляй голосовые — я распознаю и переведу.\n\n"
            "Ниже можешь выбрать режим перевода:"
        )
        await tg_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": start_text,
                "reply_markup": kb,
            },
        )
        return {"ok": True}

    # --- Текст для перевода --- #
    if text:
        translated = await translate_text(text, mode)
        reply = (
            f"🌐 Режим: {MODE_LABELS[mode]}\n\n"
            f"📝 Оригинал:\n{text}\n\n"
            f"🔁 Перевод:\n{translated}"
        )
        await tg_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": reply,
                "reply_markup": kb,
            },
        )
        return {"ok": True}

    # --- Голосовое --- #
    if voice:
        if not OPENROUTER_API_KEY:
            await tg_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ Распознавание голоса временно недоступно (нет ключа OpenRouter).",
                    "reply_markup": kb,
                },
            )
            return {"ok": True}

        await tg_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "🎧 Обрабатываю голосовое, секунду...",
            },
        )

        file_id = voice.get("file_id")
        audio_bytes = await download_telegram_file(file_id)
        if not audio_bytes:
            await tg_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ Не удалось скачать аудио из Telegram.",
                    "reply_markup": kb,
                },
            )
            return {"ok": True}

        # Подсказка модели, на каком языке говоришь
        lang_hint = None
        if mode == MODE_RU_DE:
            lang_hint = "ru"
        elif mode == MODE_DE_RU:
            lang_hint = "de"

        text_stt = await transcribe_with_openrouter(audio_bytes, lang_hint=lang_hint)
        if not text_stt:
            await tg_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ Не удалось распознать речь. Попробуй записать ещё раз.",
                    "reply_markup": kb,
                },
            )
            return {"ok": True}

        translated = await translate_text(text_stt, mode)
        reply_voice = (
            f"🎙 Распознал:\n{text_stt}\n\n"
            f"🌐 Режим: {MODE_LABELS[mode]}\n\n"
            f"🔁 Перевод:\n{translated}"
        )
        await tg_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": reply_voice,
                "reply_markup": kb,
            },
        )
        return {"ok": True}

    # Если ничего из интересного — просто ок
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
