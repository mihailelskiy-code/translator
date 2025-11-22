async def handle_voice(message: Message) -> None:
    note = await message.answer("🎧 Обрабатываю голосовое сообщение…")

    ogg_file = Path(tempfile.mkstemp(suffix=".ogg")[1])
    wav_file: Path | None = None
    voice_path: Path | None = None

    try:
        # Скачивание voice (aiogram v3)
        await message.bot.download(
            message.voice.file_id,
            destination=ogg_file
        )

        # Конвертация ogg → wav
        wav_file = convert_voice_to_wav(ogg_file)

        # Распознавание речи
        recognized_text = recognize_speech(wav_file)
        await note.edit_text(f"🗣 Распознано: {recognized_text}")

        # Перевод
        translated, direction = translate(recognized_text)
        await message.answer(f"{direction}\n{translated}")

        # Озвучка результата
        target_lang = "de" if direction == "🇷🇺→🇩🇪" else "ru"
        voice_path = synthesize_speech(translated, target_lang)
        await message.answer_voice(voice=FSInputFile(voice_path))

    except sr.UnknownValueError:
        await note.edit_text("😔 Не удалось распознать речь. Попробуй ещё раз.")

    except Exception as e:
        logger.exception("Error while handling voice message")
        await note.edit_text("⚠️ Произошла ошибка при обработке голосового сообщения.")

    finally:
        ogg_file.unlink(missing_ok=True)
        if wav_file:
            wav_file.unlink(missing_ok=True)
        if voice_path:
            voice_path.unlink(missing_ok=True)
