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


# -------------------------------------------------
# CONFIG
# -------------------------------------------------

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
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


# -------------------------------------------------
# AUDIO UTILITIES
# -------------------------------------------------

def convert_voice_to_wav(ogg_path: Path) -> Path:
    wav_path = ogg_path.with_suffix(".wav")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(ogg_path),
        str(wav_path)
    ]
    logging.info("ffmpeg: converting ogg → wav")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return wav_path


def recognize_speech(wav_path: Path) -> str:
    recognizer = sr.Recognizer()
    with sr.AudioFile(str(wav_path)) as source:
        audio = recognizer.record(source)

    for lang in ("ru-RU", "de-DE"):
        try:
            text = recognizer.recognize_google(audio, language=lang)
            logging.info("STT OK (%s): %s", lang, text)
            return text
        except Exception:
            logging.warning("STT failed for %s", lang)

    return ""


def synthesize_speech(text: str, direction_flag: str) -> Path:
    lang = "de" if "🇷🇺" in direction_flag else "ru"
    fd, path_str = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)

    out = Path(path_str)
    tts = gTTS(text, lang=lang)
    tts.save(str(out))
    return out


# -------------------------------------------------
# TRANSLATION VIA OPENROUTER
# -------------------------------------------------

def translate(text: str) -> tuple[str, str]:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "translator-bot",
    }

    system_prompt = (
        "Detect language (RU/DE). If RU → translate to DE. "
        "If DE → translate to RU. Respond strictly as JSON: "
        "{\"direction\": \"ru-de\"|\"de-ru\", \"translation\": \"...\"}"
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

    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=40)
    resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"]

    try:
        obj = json.loads(raw)
        direction = obj["direction"]
        translation = obj["translation"]
    except:
        direction = "ru-de"
        translation = raw

    flag = "🇷🇺→🇩🇪" if direction == "ru-de" else "🇩🇪→🇷🇺"
    return translation, flag


# -------------------------------------------------
# TELEGRAM HANDLERS
# -------------------------------------------------

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🎧 Привет! Я переводчик RU ⇄ DE.\n"
        "Отправь голосовое — распознаю, переведу и озвучу."
    )


@dp.message(F.voice)
async def handle_voice(message: Message):

    note = await message.answer("⏳ Обрабатываю...")

    ogg = None
    wav = None
    tts = None

    try:
        # Download
        fd, tmpogg = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        ogg = Path(tmpogg)

        await bot.download(message.voice.file_id, ogg)

        # Convert
        wav = convert_voice_to_wav(ogg)

        # STT
        text = recognize_speech(wav)
        await note.edit_text(f"🗣 Распознано: {text}")

        # Translate
        tr, flag = translate(text)
        await message.answer(f"{flag}\n{tr}")

        # TTS
        tts = synthesize_speech(tr, flag)
        await message.answer_audio(FSInputFile(str(tts)))

    finally:
        for f in [ogg, wav, tts]:
            if f and f.exists():
                try:
                    f.unlink()
                except:
                    pass


# -------------------------------------------------
# WEBHOOK STARTUP
# -------------------------------------------------

async def on_startup(app: web.Application):
    if BASE_WEBHOOK_URL:
        url = BASE_WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
        await bot.set_webhook(url=url, secret_token=WEBHOOK_SECRET)
        logging.info("Webhook set: %s", url)


def main():
    app = web.Application()

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot, on_startup=on_startup)

    port = int(os.getenv("PORT", 10000))
    logging.info("Server running on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
