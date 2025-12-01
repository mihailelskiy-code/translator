import logging
import os
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from deep_translator import GoogleTranslator
import speech_recognition as sr
from pydub import AudioSegment

# === Настройка ===
BOT_TOKEN = os.environ["BOT_TOKEN"]
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Функции ===

async def recognize_speech_from_ogg(ogg_path: str, lang: str) -> str:
    """Распознаёт речь из .ogg файла через Google Web Speech API."""
    try:
        # Конвертация в WAV
        wav_path = ogg_path.replace(".ogg", ".wav")
        audio = AudioSegment.from_ogg(ogg_path)
        audio.export(wav_path, format="wav")

        # Распознавание
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
        text = recognizer.recognize_google(audio_data, language=lang)
        return text
    except Exception as e:
        logger.error(f"Ошибка распознавания: {e}")
        return None
    finally:
        for p in [ogg_path, wav_path]:
            if os.path.exists(p):
                os.remove(p)

def translate_text(text: str, src: str, dest: str) -> str:
    try:
        return GoogleTranslator(source=src, target=dest).translate(text)
    except Exception as e:
        logger.error(f"Ошибка перевода: {e}")
        return "⚠️ Не удалось перевести."

# === Обработчики ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇩🇪 → 🇷🇺", callback_data="de-ru")],
        [InlineKeyboardButton("🇷🇺 → 🇩🇪", callback_data="ru-de")],
    ]
    await update.message.reply_text(
        "Привет! Выберите направление перевода:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["direction"] = query.data
    src, dest = query.data.split("-")
    lang_names = {"de": "немецкий", "ru": "русский"}
    await query.edit_message_text(
        f"Выбрано: {lang_names[src]} → {lang_names[dest]}\n"
        "Отправьте текст или голосовое сообщение!"
    )

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "direction" not in context.user_data:
        await update.message.reply_text("Сначала выберите направление перевода командой /start.")
        return

    direction = context.user_data["direction"]
    src_lang_code = direction.split("-")[0]
    speech_lang = "de-DE" if src_lang_code == "de" else "ru-RU"

    # Скачиваем голосовое во временный файл
    voice_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    await voice_file.download_to_drive(tmp_path)

    # Распознаём
    recognized = await recognize_speech_from_ogg(tmp_path, speech_lang)
    if not recognized:
        await update.message.reply_text("❌ Не удалось распознать речь.")
        return

    # Переводим
    dest_lang_code = "ru" if src_lang_code == "de" else "de"
    translation = translate_text(recognized, src_lang_code, dest_lang_code)

    await update.message.reply_text(
        f"🔹 Распознано:\n{recognized}\n\n"
        f"🔹 Перевод:\n{translation}"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "direction" not in context.user_data:
        await update.message.reply_text("Сначала выберите направление перевода командой /start.")
        return

    text = update.message.text
    src, dest = context.user_data["direction"].split("-")
    translation = translate_text(text, src, dest)
    await update.message.reply_text(f"🔹 Перевод:\n{translation}")

# === Запуск ===

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(direction_selected))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
