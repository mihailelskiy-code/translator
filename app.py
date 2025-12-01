import logging
import os
import tempfile
import subprocess
from pathlib import Path
import json

import requests
import speech_recognition as sr
from gtts import gTTS

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile

from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


# ------------------------
# НАСТРОЙКИ
# ------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN")
if not OPENROUTER_API_KEY:
    raise RuntimeError("Не задан OPENROUTER_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"

WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")  # напр. https://translator-47k.onrender.com

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


# ------------------------
# АУДИО: конвертация и STT
# ------------------------

def convert_voice_to_wav(ogg_path: Path) -> Path:
    wav_path = ogg_path.with_suffix(".wav")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(ogg_path),
        str(wav_path),
    ]
    logging.info("ffmpeg: конвертация в wav")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return wav_path


def recognize_speech(wav_path: Path) -> str:
    recognizer = sr.Recognizer()
    with sr.AudioFile(str(wav_path)) as source:
        audio = recognizer.record(source)

    for lang in ("ru-RU", "de-DE"):
        try:
            text = recognizer.recognize_google(audio, language=lang)
            logging.info("STT ok, язык %s, текст: %s", lang, text)
            return text
        except sr.UnknownValueError:
            logging.warning("STT не распозналось на %s", lang)
        except Exception as e:
            logging.exception("STT ошибка на %s: %s", lang, e)

    return ""


def synthesize_speech(text: str, direction_flag: str) -> Path:
    tts_lang = "de" if "🇷🇺" in direction_flag else "ru"

    fd, path_str = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    out_path = Path(path_str)

    logging.info("gTTS: генерация, язык %s", tts_lang)
    tts = gTTS(text=text, lang=tts_lang)
    tts.save(str(out_path))

    return out_path


# ------------------------
# Перевод через OpenRouter
# ------------------------

def translate(text: str) -> tuple[str, str]:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://translator-bot.example",
        "X-Title": "Telegram Voice Translator",
    }

    system_prompt = (
        "You are a professional translator between Russian and German. "
        "Detect the language of the user's text. If it is Russian, "
        "translate to German. If it is German, translate to Russian. "
        "Answer ONLY as a JSON object with fields 'direction' and 'translation'. "
        "Field 'direction' must be either 'ru-de' or 'de-ru'. "
        "Do NOT add any extra text."
    )

    payload = {
        "model": OPENROUTER_MODEL,
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    }

    logging.info("Запрос в OpenRouter…")
    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
def main():
    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot, on_startup=on_startup)

    port = int(os.getenv("PORT", 10000))
    logging.info("Запуск сервера на 0.0.0.0:%d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
