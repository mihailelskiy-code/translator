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
# НАСТРОЙКИ И ПЕРЕМЕННЫЕ
# ------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN в переменных окружения")
if not OPENROUTER_API_KEY:
    raise RuntimeError("Не задан OPENROUTER_API_KEY в переменных окружения")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"

WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")  # напр. https://translator-47k.onrender.com

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


# ------------------------
# УТИЛИТЫ ДЛЯ АУДИО
# ------------------------

def convert_voice_to_wav(ogg_path: Path) -> Path:
    """
    Конвертируем OGG (Opus) от Telegram в WAV через ffmpeg.
    """
    wav_path = ogg_path.with_suffix(".wav")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(ogg_path),
        str(wav_path),
    ]
    logging.info("Запуск ffmpeg для конвертации в wav")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return wav_path


def recognize_speech(wav_path: Path) -> str:
    """
    Распознаём речь из WAV через speech_recognition.
    Сначала пробуем русский, если не получилось — немецкий.
    """
    recognizer = sr.Recognizer()
    with sr.AudioFile(str(wav_path)) as source:
        audio = recognizer.record(source)

    for lang in ("ru-RU", "de-DE"):
        try:
            text = recognizer.recognize_google(audio, language=lang)
            logging.info("STT успешно, язык: %s, текст: %s", lang, text)
            return text
        except sr.UnknownValueError:
            logging.warning("STT: не распозналось на языке %s", lang)
        except Exception as e:
            logging.exception("STT ошибка на языке %s: %s", lang, e)

    return ""


def synthesize_speech(text: str, direction_flag: str) -> Path:
    """
    Озвучка текста через gTTS.
    direction_flag: '🇷🇺→🇩🇪' или '🇩🇪→🇷🇺'
    """
    tts_lang = "de" if "🇷🇺" in direction_flag else "ru"

    fd, path_str = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    out_path = Path(path_str)

    logging.info("Запуск gTTS, язык %s", tts_lang)
    tts = gTTS(text=text, lang=tts_lang)
    tts.save(str(out_path))

    return out_path


# ------------------------
# ПЕРЕВОД ЧЕРЕЗ OPENROUTER
# ------------------------

def translate(text: str) -> tuple[str, str]:
    """
    Перевод текста через OpenRouter (openai/gpt-4o-mini).
    Возвращает (перевод, направление_флагом).
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Можно указать свои, но не обязательно:
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

    logging.info("Отправка запроса в OpenRouter для перевода")
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
        # если вдруг модель прислала невалидный json — считаем, что это просто текст
        logging.warning("Не удалось распарсить JSON, вернул сырой текст")
        direction = "ru-de"
        translation = content.strip()

    flag = "🇷🇺→🇩🇪" if direction == "ru-de" else "🇩🇪→🇷🇺"
    return translation, flag


# ------------------------
# ХЕНДЛЕРЫ БОТА
# ------------------------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! 🎧\n"
        "Я бот-переводчик голосовых.\n\n"
        "Отправь мне голосовое сообщение на русском или немецком — "
        "я распознаю, переведу и пришлю текст + озвучку."
    )


@dp.message(F.voice)
async def handle_voice(message: Message):
    note = await message.answer("🎧 Обрабатываю голосовое сообщение…")

    ogg_file: Path | None = None
    wav_file: Path | None = None
    tts_file: Path | None = None

    try:
        # 1. Скачиваем voice из Telegram
        fd, ogg_path_str = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        ogg_file = Path(ogg_path_str)

        await bot.download(message.voice.file_id, destination=ogg_file)
        logging.info("Файл голосового скачан: %s", ogg_file)

        # 2. Конвертация в WAV
        wav_file = convert_voice_to_wav(ogg_file)

        # 3. Распознавание речи
        recognized_text = recognize_speech(wav_file)

        if not recognized_text:
            await note.edit_text("❌ Не удалось распознать речь. Попробуй ещё раз.")
            return

        await note.edit_text(f"🗣 Распознано:\n{recognized_text}")

        # 4. Перевод через OpenRouter
        translated, direction_flag = translate(recognized_text)

        if not translated:
            await message.answer("❌ Не удалось получить перевод.")
            return

        await message.answer(f"{direction_flag}\n{translated}")

        # 5. Озвучка перевода
        tts_file = synthesize_speech(translated, direction_flag)
        voice = FSInputFile(str(tts_file))
        await message.answer_audio(voice, caption="🔊 Озвучка перевода")

    except Exception as e:
        logging.exception("Ошибка при обработке голосового: %s", e)
        try:
            await note.edit_text("❌ Произошла ошибка при обработке. Попробуй ещё раз.")
        except Exception:
            pass
    finally:
        # Чистим временные файлы
        for f in (ogg_file, wav_file, tts_file):
            if f and f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass


# ------------------------
# WEBHOOK / AIOHTTP SERVER
# ------------------------

async def on_startup(app: web.Application):
    if not BASE_WEBHOOK_URL:
        logging.warning("BASE_WEBHOOK_URL не задан — вебхук не будет установлен автоматически.")
        return

    webhook_url = BASE_WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
    logging.info("Устанавливаем webhook: %s", webhook_url)
    await bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )


def main():
    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot, on_startup=on_startup)

    port = int(os.getenv("PORT", 10000))
    logging.info("Запуск aiohttp-сервера на порту %d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
