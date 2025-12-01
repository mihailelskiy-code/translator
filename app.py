import os
import logging
from pathlib import Path
import speech_recognition as sr
from pydub import AudioSegment
from gtts import gTTS
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# API ключи
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'YOUR_TELEGRAM_TOKEN_HERE')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', 'YOUR_OPENROUTER_KEY_HERE')

# OpenRouter настройки
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Создание папок
TEMP_DIR = Path('temp_audio')
TEMP_DIR.mkdir(exist_ok=True)

# Инициализация
recognizer = sr.Recognizer()

# Словарь языков пользователей
user_languages = {}
user_message_history = {}

# Эмодзи
FLAG_DE = "🇩🇪"
FLAG_RU = "🇷🇺"
MIC = "🎤"
SPEAKER = "🔊"
ARROW = "➡️"
TEXT_ICON = "💬"
VOICE_ICON = "🎙️"


def convert_to_wav(input_file: Path, output_file: Path) -> bool:
    """Конвертация в WAV"""
    try:
        audio = AudioSegment.from_file(str(input_file))
        audio.export(str(output_file), format='wav')
        return True
    except Exception as e:
        logger.error(f"Ошибка конвертации: {e}")
        return False


def recognize_speech(audio_file: Path, language: str = 'ru-RU') -> str:
    """Распознавание речи"""
    try:
        with sr.AudioFile(str(audio_file)) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language=language)
            return text
    except sr.UnknownValueError:
        return None
    except Exception as e:
        logger.error(f"Ошибка распознавания: {e}")
        return None


def translate_with_openrouter(text: str, source_lang: str, target_lang: str, user_id: int) -> str:
    """Перевод через OpenRouter API"""
    try:
        # Определяем названия языков
        lang_names = {
            'ru': 'Russian',
            'de': 'German'
        }
        
        source_lang_name = lang_names.get(source_lang, source_lang)
        target_lang_name = lang_names.get(target_lang, target_lang)
        
        # Получаем историю для контекста
        if user_id not in user_message_history:
            user_message_history[user_id] = []
        
        history = user_message_history[user_id][-3:]  # Последние 3 сообщения
        
        # Формируем промпт
        system_prompt = f"""You are a professional translator. Translate the following text from {source_lang_name} to {target_lang_name}.
Rules:
1. Provide ONLY the translation, no explanations
2. Maintain the original tone and style
3. Keep proper nouns unchanged
4. If it's a casual conversation, use appropriate informal language
5. For formal text, use formal language"""

        # Подготавливаем сообщения
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # Добавляем контекст из истории
        for msg in history:
            messages.append({"role": "user", "content": f"Translate: {msg['original']}"})
            messages.append({"role": "assistant", "content": msg['translated']})
        
        # Добавляем текущий запрос
        messages.append({"role": "user", "content": f"Translate: {text}"})
        
        # Делаем запрос к OpenRouter
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/your-repo",  # Опционально
            "X-Title": "Telegram Translator Bot"  # Опционально
        }
        
        payload = {
            "model": "anthropic/claude-3.5-sonnet",  # Или другая модель
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1000
        }
        
        response = requests.post(
            OPENROUTER_API_URL,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            translation = result['choices'][0]['message']['content'].strip()
            
            # Сохраняем в историю
            user_message_history[user_id].append({
                'original': text,
                'translated': translation
            })
            
            # Ограничиваем историю
            if len(user_message_history[user_id]) > 10:
                user_message_history[user_id] = user_message_history[user_id][-10:]
            
            return translation
        else:
            logger.error(f"OpenRouter API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Ошибка перевода: {e}")
        return None


def text_to_speech(text: str, lang: str, output_file: Path) -> bool:
    """Синтез речи"""
    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        tts.save(str(output_file))
        return True
    except Exception as e:
        logger.error(f"Ошибка TTS: {e}")
        return False


def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Главная клавиатура"""
    current_lang = user_languages.get(user_id, 'ru')
    
    if current_lang == 'ru':
        direction_text = f"{FLAG_RU} Русский {ARROW} Немецкий"
    else:
        direction_text = f"{FLAG_DE} Немецкий {ARROW} Русский"
    
    keyboard = [
        [InlineKeyboardButton(f"🔄 {direction_text}", callback_data="toggle_lang")],
        [
            InlineKeyboardButton(f"{TEXT_ICON} Текст", callback_data="mode_text"),
            InlineKeyboardButton(f"{VOICE_ICON} Голос", callback_data="mode_voice")
        ],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    
    return InlineKeyboardMarkup(keyboard)


def start(update: Update, context: CallbackContext) -> None:
    """Команда /start"""
    user_id = update.effective_user.id
    user_languages[user_id] = 'ru'
    
    welcome_text = f"""
{MIC} <b>Добро пожаловать в переводчик DE ↔ RU!</b>

🤖 Я помогу вам переводить:
• {FLAG_RU} С русского на немецкий
• {FLAG_DE} С немецкого на русский

<b>Возможности:</b>
✅ Текстовый перевод
✅ Голосовой перевод
✅ Контекстная память разговора
✅ Качественный AI-перевод

<b>Быстрый старт:</b>
1. Выберите направление перевода
2. Отправьте текст или голосовое
3. Получите перевод!

Используйте кнопки ниже для управления:
"""
    
    update.message.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(user_id),
        parse_mode='HTML'
    )


def help_command(update: Update, context: CallbackContext) -> None:
    """Команда /help"""
    help_text = """
<b>📖 Как пользоваться ботом</b>

<b>Текстовый перевод:</b>
1. Выберите направление (🔄 кнопка)
2. Просто отправьте текст
3. Получите перевод

<b>Голосовой перевод:</b>
1. Выберите направление
2. Запишите голосовое сообщение
3. Получите текст + голосовой перевод

<b>Команды:</b>
/start - Начать работу
/help - Эта справка
/language - Сменить направление
/stats - Статистика переводов
/clear - Очистить историю

<b>Кнопки:</b>
🔄 - Переключить направление перевода
💬 - Режим текстового перевода
🎙️ - Режим голосового перевода
📊 - Ваша статистика
❓ - Справка

<b>Доступные модели перевода:</b>
• Claude 3.5 Sonnet (по умолчанию)
• GPT-4 (можно настроить)
• Llama 3 (быстрый)

<b>Совет:</b> Бот помнит контекст последних 10 сообщений для более точного перевода диалогов!
"""
    
    if update.message:
        update.message.reply_text(help_text, parse_mode='HTML')
    else:
        update.callback_query.message.reply_text(help_text, parse_mode='HTML')


def stats_command(update: Update, context: CallbackContext) -> None:
    """Статистика пользователя"""
    user_id = update.effective_user.id
    history_count = len(user_message_history.get(user_id, []))
    current_lang = user_languages.get(user_id, 'ru')
    
    if current_lang == 'ru':
        direction = f"{FLAG_RU} Русский → Немецкий"
    else:
        direction = f"{FLAG_DE} Немецкий → Русский"
    
    stats_text = f"""
<b>📊 Ваша статистика</b>

<b>Текущее направление:</b>
{direction}

<b>Переведено сообщений:</b>
{history_count}

<b>Контекст:</b>
Последние {min(history_count, 10)} сообщений в памяти

<b>Используемая модель:</b>
Claude 3.5 Sonnet (OpenRouter)
"""
    
    if update.message:
        update.message.reply_text(stats_text, parse_mode='HTML')
    else:
        update.callback_query.message.edit_text(stats_text, parse_mode='HTML')


def clear_history(update: Update, context: CallbackContext) -> None:
    """Очистить историю"""
    user_id = update.effective_user.id
    
    if user_id in user_message_history:
        del user_message_history[user_id]
    
    update.message.reply_text(
        "✅ История переводов очищена!\n\nКонтекст разговора сброшен.",
        parse_mode='HTML'
    )


def language_command(update: Update, context: CallbackContext) -> None:
    """Сменить язык"""
    user_id = update.effective_user.id
    current_lang = user_languages.get(user_id, 'ru')
    
    # Переключаем язык
    new_lang = 'de' if current_lang == 'ru' else 'ru'
    user_languages[user_id] = new_lang
    
    if new_lang == 'ru':
        text = f"✅ {FLAG_RU} Выбрано: Русский → Немецкий"
    else:
        text = f"✅ {FLAG_DE} Gewählt: Deutsch → Russisch"
    
    update.message.reply_text(text, reply_markup=get_main_keyboard(user_id))


def button_callback(update: Update, context: CallbackContext) -> None:
    """Обработка кнопок"""
    query = update.callback_query
    query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "toggle_lang":
        # Переключение языка
        current_lang = user_languages.get(user_id, 'ru')
        new_lang = 'de' if current_lang == 'ru' else 'ru'
        user_languages[user_id] = new_lang
        
        if new_lang == 'ru':
            text = f"✅ {FLAG_RU} <b>Выбрано:</b> Русский → Немецкий\n\nТеперь отправьте текст или голосовое на русском."
        else:
            text = f"✅ {FLAG_DE} <b>Gewählt:</b> Deutsch → Russisch\n\nJetzt senden Sie eine Nachricht auf Deutsch."
        
        query.edit_message_text(text, reply_markup=get_main_keyboard(user_id), parse_mode='HTML')
    
    elif data == "mode_text":
        query.edit_message_text(
            f"{TEXT_ICON} <b>Режим текстового перевода</b>\n\nОтправьте текстовое сообщение для перевода.",
            reply_markup=get_main_keyboard(user_id),
            parse_mode='HTML'
        )
    
    elif data == "mode_voice":
        query.edit_message_text(
            f"{VOICE_ICON} <b>Режим голосового перевода</b>\n\nЗапишите и отправьте голосовое сообщение.",
            reply_markup=get_main_keyboard(user_id),
            parse_mode='HTML'
        )
    
    elif data == "stats":
        stats_command(update, context)
    
    elif data == "help":
        help_command(update, context)


def handle_text(update: Update, context: CallbackContext) -> None:
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Проверяем выбор языка
    if user_id not in user_languages:
        update.message.reply_text(
            "⚠️ Сначала выберите направление перевода: /start",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    # Показываем статус
    status_msg = update.message.reply_text(f"{ARROW} Перевожу...")
    
    try:
        # Определяем направление
        source_lang = user_languages[user_id]
        target_lang = 'de' if source_lang == 'ru' else 'ru'
        
        if source_lang == 'ru':
            src_flag = FLAG_RU
            tgt_flag = FLAG_DE
        else:
            src_flag = FLAG_DE
            tgt_flag = FLAG_RU
        
        # Переводим через OpenRouter
        translated = translate_with_openrouter(text, source_lang, target_lang, user_id)
        
        if not translated:
            status_msg.edit_text(
                "❌ Ошибка перевода. Проверьте:\n"
                "• API ключ OpenRouter\n"
                "• Интернет соединение\n"
                "• Лимиты API"
            )
            return
        
        # Формируем ответ
        result_text = f"""
{src_flag} <b>Оригинал:</b>
{text}

{tgt_flag} <b>Перевод:</b>
{translated}
"""
        
        status_msg.delete()
        
        # Отправляем перевод с кнопками
        keyboard = [
            [InlineKeyboardButton(f"{SPEAKER} Озвучить", callback_data=f"tts_{target_lang}_{user_id}")],
            [InlineKeyboardButton("🔄 Новый перевод", callback_data="toggle_lang")]
        ]
        
        context.user_data['last_translation'] = {
            'text': translated,
            'lang': target_lang
        }
        
        update.message.reply_text(
            result_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Ошибка обработки текста: {e}")
        status_msg.edit_text("❌ Произошла ошибка при переводе")


def handle_voice(update: Update, context: CallbackContext) -> None:
    """Обработка голосовых сообщений"""
    user_id = update.effective_user.id
    
    if user_id not in user_languages:
        update.message.reply_text(
            "⚠️ Выберите направление перевода: /start",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    status_msg = update.message.reply_text(f"{MIC} Обрабатываю голосовое...")
    
    try:
        # Получаем файл
        if update.message.voice:
            file_id = update.message.voice.file_id
        elif update.message.audio:
            file_id = update.message.audio.file_id
        else:
            return
        
        file_name = f"voice_{user_id}_{update.message.message_id}"
        
        # Скачиваем
        new_file = context.bot.get_file(file_id)
        input_file = TEMP_DIR / f"{file_name}.ogg"
        wav_file = TEMP_DIR / f"{file_name}.wav"
        
        new_file.download(str(input_file))
        
        # Конвертируем
        status_msg.edit_text(f"{MIC} Конвертирую аудио...")
        if not convert_to_wav(input_file, wav_file):
            status_msg.edit_text("❌ Ошибка конвертации аудио")
            return
        
        # Определяем языки
        source_lang = user_languages[user_id]
        
        if source_lang == 'ru':
            recog_lang = 'ru-RU'
            src_code = 'ru'
            dest_code = 'de'
            tts_lang = 'de'
            src_flag = FLAG_RU
            dest_flag = FLAG_DE
        else:
            recog_lang = 'de-DE'
            src_code = 'de'
            dest_code = 'ru'
            tts_lang = 'ru'
            src_flag = FLAG_DE
            dest_flag = FLAG_RU
        
        # Распознаем
        status_msg.edit_text(f"{MIC} Распознаю речь...")
        recognized = recognize_speech(wav_file, recog_lang)
        
        if not recognized:
            status_msg.edit_text(
                "❌ Не удалось распознать речь.\n\n"
                "Советы:\n"
                "• Говорите четче\n"
                "• Уменьшите фоновый шум\n"
                "• Запишите более длинное сообщение"
            )
            return
        
        # Переводим
        status_msg.edit_text(f"{ARROW} Перевожу через OpenRouter...")
        translated = translate_with_openrouter(recognized, src_code, dest_code, user_id)
        
        if not translated:
            status_msg.edit_text("❌ Ошибка перевода")
            return
        
        # Синтезируем голос
        status_msg.edit_text(f"{SPEAKER} Создаю голосовой ответ...")
        output_audio = TEMP_DIR / f"output_{user_id}_{update.message.message_id}.mp3"
        
        if not text_to_speech(translated, tts_lang, output_audio):
            status_msg.edit_text("❌ Ошибка синтеза речи")
            return
        
        # Отправляем результат
        result_text = f"""
{src_flag} <b>Оригинал:</b>
{recognized}

{dest_flag} <b>Перевод:</b>
{translated}
"""
        
        status_msg.delete()
        update.message.reply_text(result_text, parse_mode='HTML')
        update.message.reply_voice(voice=open(output_audio, 'rb'))
        
        # Удаляем файлы
        input_file.unlink(missing_ok=True)
        wav_file.unlink(missing_ok=True)
        output_audio.unlink(missing_ok=True)
        
    except Exception as e:
        logger.error(f"Ошибка обработки голоса: {e}")
        status_msg.edit_text("❌ Произошла ошибка")


def main():
    """Запуск бота"""
    if TELEGRAM_TOKEN == 'YOUR_TELEGRAM_TOKEN_HERE':
        print("❌ Установите TELEGRAM_TOKEN!")
        return
    
    if OPENROUTER_API_KEY == 'YOUR_OPENROUTER_KEY_HERE':
        print("❌ Установите OPENROUTER_API_KEY!")
        print("Получите ключ на: https://openrouter.ai/keys")
        return
    
    # Создаем updater
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Регистрируем обработчики
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("language", language_command))
    dp.add_handler(CommandHandler("stats", stats_command))
    dp.add_handler(CommandHandler("clear", clear_history))
    dp.add_handler(CallbackQueryHandler(button_callback))
    dp.add_handler(MessageHandler(Filters.voice | Filters.audio, handle_voice))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
    
    # Запускаем
    logger.info("🚀 Бот запущен с OpenRouter API!")
    print("\n✅ Бот успешно запущен!")
    print(f"📡 Используется OpenRouter API")
    print(f"🤖 Модель: Claude 3.5 Sonnet")
    print("\nДля остановки нажмите Ctrl+C\n")
    
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
