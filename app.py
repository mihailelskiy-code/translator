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
# НАСТРОЙКИ
# -------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN")
if not OPENROUTER_API_KEY:
    raise RuntimeError("Не задан OPENROUTER_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"

WEBHOOK_PATH = "/webhook"

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


# -------------------------------------------------
# АУДИО: конвертация и распознавание
# -------------------------------------------------

def convert_voice_to_wav(ogg_path: Path) -> Path:
    """Конвертация .ogg → .wav через ffmpeg"""
    wav_path = ogg_path.with_suffix(".wav")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(ogg_path),
        str(wav_path),
    ]
    logging.info("ffmpeg: конвертация %s → %s", ogg_path, wav_path)
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return wav_path


def recognize_speech(wav_path: Path) -> str:
    """Распознаём речь сначала как RU, потом как DE."""
    recognizer = sr.Recognizer()
    with sr.AudioFile(str(wav_path)) as source:
        audio = recognizer.record(source)

    for lang in ("ru-RU", "de-DE"):
        try:
            text = recognizer.recognize_google(audio, language=lang)
            logging.info("STT OK (%s): %s", lang, text)
            return text
        except sr.UnknownValueError:
            logging.warning("STT: не удалось распознать на %s", lang)
        except Exception as e:
            logging.exception("STT ошибка на %s: %s", lang, e)

    return ""


def synthesize_speech(text: str, direction_flag: str) -> Path:
    """Озвучиваем перевод через gTTS."""
    tts_lang = "de" if "🇷🇺" in direction_flag else "ru"

    fd, path_str = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    out_path = Path(path_str)

    logging.info("gTTS: генерация голосового (%s)", tts_lang)
    tts = gTTS(text=text, lang=tts_lang)
    tts.save(str(out_path))

    return out_path


# -------------------------------------------------
# ПЕРЕВОД через OpenRouter
# -------------------------------------------------

def translate(text: str) -> tuple[str, str]:
    """
    Отправляем текст в OpenRouter.
    Возвращаем (перевод, флаг-направление).
    """
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
    data = resp.json()

    content = data["choices"][0]["message"]["content"]
    logging.info("Ответ OpenRouter (raw): %s", content)

    try:
        obj = json.loads(content)
        direction = obj.get("direction", "ru-de")
        translation = obj.get("translation", "").strip()
    except json.JSONDecodeError:
        logging.warning("JSON не распарсился, беру сырой текст")
        direction = "ru-de"
        translation = content.strip()

    flag = "🇷🇺→🇩🇪" if direction == "ru-de" else "🇩🇪→🇷🇺"
    return translation, flag


# -------------------------------------------------
# ХЕНДЛЕРЫ TELEGRAM
# -------------------------------------------------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! 🎧\n"
        "Я бот-переводчик голосовых (RU ⇄ DE).\n\n"
        "Отправь мне голосовое на русском или немецком — "
        "я распознаю, переведу и пришлю текст + озвучку."
    )


@dp.message(F.voice)
async def handle_voice(message: Message):
    note = await message.answer("🎧 Обрабатываю голосовое…")

    ogg_file: Path | None = None
    wav_file: Path | None = None
    tts_file: Path | None = None

    try:
        # 1. Скачиваем voice
        fd, ogg_path_str = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        ogg_file = Path(ogg_path_str)

        await bot.download(message.voice.file_id, destination=ogg_file)
        logging.info("Голосовое скачано: %s", ogg_file)

        # 2. Конвертация
        wav_file = convert_voice_to_wav(ogg_file)

        # 3. STT
        recognized_text = recognize_speech(wav_file)
        if not recognized_text:
            await note.edit_text("❌ Не удалось распознать речь. Попробуй ещё раз.")
            return

        await note.edit_text(f"🗣 Распознано:\n{recognized_text}")

        # 4. Перевод
        translated, direction_flag = translate(recognized_text)
        if not translated:
            await message.answer("❌ Не удалось получить перевод.")
            return

        await message.answer(f"{direction_flag}\n{translated}")

        # 5. Озвучка
        tts_file = synthesize_speech(translated, direction_flag)
        voice = FSInputFile(str(tts_file))
        await message.answer_audio(voice, caption="🔊 Озвучка перевода")

    except Exception as e:
        logging.exception("Ошибка при обработке голосового: %s", e)
        try:
            await note.edit_text("❌ Произошла ошибка. Попробуй ещё раз.")
        except Exception:
            pass
    finally:
        for f in (ogg_file, wav_file, tts_file):
            if f and f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass


# -------------------------------------------------
# WEBHOOK + AIOHTTP
# -------------------------------------------------

async def on_startup(app: web.Application):
    """Вызывается при старте приложения – ставим webhook в Telegram."""
    if not BASE_WEBHOOK_URL:
        logging.warning("BASE_WEBHOOK_URL не задан — webhook не будет установлен")
        return

    url = BASE_WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
    logging.info("Устанавливаем webhook: %s", url)
    await bot.set_webhook(
        url=url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )
    logging.info("Webhook set: %s", url)


def main():
    app = web.Application()

    # Регистрируем обработчик webhook'а
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)

    # Подключаем aiogram к aiohttp + on_startup
    setup_application(app, dp, bot=bot, on_startup=on_startup)

    port = int(os.getenv("PORT", 10000))
    logging.info("Server running on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
