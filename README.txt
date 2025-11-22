# Telegram RU⇄DE Translator Bot with Voice (Render-ready)

Этот проект содержит Telegram-бота-переводчика между русским и немецким языками
с поддержкой:
- текстовых сообщений
- голосовых сообщений (voice)
- озвучки перевода (TTS) в формате voice-кружка

## Локальный запуск

1. Создать и активировать виртуальное окружение (опционально):
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   source .venv/bin/activate  # Linux/macOS
   ```

2. Установить зависимости:
   ```bash
   pip install -r requirements.txt
   ```

3. Установить ffmpeg в систему (обязательно для pydub).

4. Установить переменную окружения BOT_TOKEN с токеном вашего Telegram-бота.

5. Запустить:
   ```bash
   python app.py
   ```

## Деплой на Render (через веб-интерфейс)

1. Создайте репозиторий на GitHub и загрузите в него файлы `app.py` и `requirements.txt`.
2. Зайдите на https://render.com и создайте новый **Web Service** из этого репозитория.
3. В разделе **Environment** добавьте переменную:
   - `BOT_TOKEN` = ваш токен бота от BotFather
4. В качестве команды запуска укажите:
   ```bash
   python app.py
   ```
5. Нажмите Deploy и дождитесь запуска сервиса.
