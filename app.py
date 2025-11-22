import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Tuple
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message
from deep_translator import GoogleTranslator
from gtts import gTTS
from pydub import AudioSegment
import speech_recognition as sr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен берём из переменной окружения BOT_TOKEN
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

translator_ru_to_de = GoogleTranslator(source="ru", target="de")
translator_de_to_ru = GoogleTranslator(source="de", target="ru")
recognizer = sr.Recognizer()


def detect_language(text: str) -> str:
    """Очень простой детектор языка: есть кириллица → ru, иначе → de."""
    return "ru" if re.search(r"[\u0400-\u04FF]", text) else "de"


def translate(text: str) -> Tuple[str, str]:
    """Перевод RU ⇄ DE. Возвращает (перевод, направление_строкой)."""
    source_lang = detect_language(text)
    translator = translator_ru_to_de if source_lang == "ru" else translator_de_to_ru
    translated = translator.translate(text)
    direction = "🇷🇺→🇩🇪" if source_lang == "ru" else "🇩🇪→🇷🇺"
    return translated, direction


def synthesize_speech(text: str, lang: str) -> Path:
    """TTS через gTTS → mp3 → ogg/opus для voice-кружка."""
    mp3_file = Path(tempfile.mkstemp(suffix=".mp3")[1])
    ogg_file = Path(tempfile.mkstemp(suffix=".ogg")[1])

    gTTS(text=text, lang=lang).save(str(mp3_file))

    audio = AudioSegment.from_mp3(mp3_file)
    audio = audio.set_frame_rate(48000).set_channels(1)
    audio.export(ogg_file, format="ogg", codec="libopus")

    mp3_file.unlink(missing_ok=True)
    return ogg_file


def convert_voice_to_wav(source_path: Path) -> Path:
    """Телеграм-voice (ogg/opus) → WAV для SpeechRecognition."""
    wav_path = Path(tempfile.mkstemp(suffix=".wav")[1])
    audio = AudioSegment.from_file(source_path)
    audio = audio.set_frame_rate(16000).set_channels(1)
    audio.export(wav_path, format="wav")
    return wav_path


def recognize_speech(audio_path: Path) -> str:
    """Пробуем распознать сначала RU, потом DE."""
    with sr.AudioFile(str(audio_path)) as source:
        audio = recognizer.record(source)
    for language_code in ("ru-RU", "de-DE"):
        try:
            return recognizer.recognize_google(audio, language=language_code)
        except sr.UnknownValueError:
            continue
    raise sr.UnknownValueError("Speech could not be recognized in supported languages")


async def handle_start(message: Message) -> None:
    help_text = (
        "👋 Я переводчик между русским и немецким.\n\n"
        "• Отправь текст на русском или немецком — переведу на противоположный язык.\n"
        "• Отправь voice — распознаю, переведу и пришлю озвучку перевода кружком.\n"
        "Работаю только в режиме 🇷🇺⇄🇩🇪."
    )
    await message.answer(help_text)


async def handle_text(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    translated, direction = translate(text)
    await message.answer(f"{direction}\n{translated}")

    target_lang = "de" if direction == "🇷🇺→🇩🇪" else "ru"
    voice_path = synthesize_speech(translated, target_lang)
    try:
        await message.answer_voice(voice=FSInputFile(voice_path))
    finally:
        voice_path.unlink(missing_ok=True)


async def handle_voice(message: Message) -> None:
    note = await message.answer("🎧 Обрабатываю голосовое сообщение…")

    ogg_file = Path(tempfile.mkstemp(suffix=".ogg")[1])
    wav_file: Path | None = None
    voice_path: Path | None = None

    try:
        await message.voice.download(destination=ogg_file)

        wav_file = convert_voice_to_wav(ogg_file)

        recognized_text = recognize_speech(wav_file)
        await note.edit_text(f"🗣 Распознано: {recognized_text}")

        translated, direction = translate(recognized_text)
        await message.answer(f"{direction}\n{translated}")

        target_lang = "de" if direction == "🇷🇺→🇩🇪" else "ru"
        voice_path = synthesize_speech(translated, target_lang)
        await message.answer_voice(voice=FSInputFile(voice_path))

    except sr.UnknownValueError:
        await note.edit_text("😔 Не удалось распознать речь. Попробуй ещё раз.")
    except Exception:
        logger.exception("Error while handling voice message")
        await note.edit_text("⚠️ Произошла ошибка при обработке голосового сообщения.")
    finally:
        ogg_file.unlink(missing_ok=True)
        if wav_file:
            wav_file.unlink(missing_ok=True)
        if voice_path:
            voice_path.unlink(missing_ok=True)


def register_handlers(dp: Dispatcher) -> None:
    dp.message.register(handle_start, CommandStart())
    dp.message.register(handle_voice, F.voice)
    dp.message.register(handle_text, F.text)


async def main() -> None:
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    register_handlers(dp)
    print("✅ Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
