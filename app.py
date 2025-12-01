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
# КОНФИГ
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
if not BASE_WEBHOOK_URL:
    raise RuntimeError("Не задан BASE_WEBHOOK_URL в Environment Render")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"

WEBHOOK_PATH = "/webhook"

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


# -------------------------------------------------
# АУДИО (Конвертация, STT)
# -------------------------------------------------

def convert_voice_to_wav(ogg_path: Path) -> Path:
    wav_path = ogg_path.with_suffix(".wav")
    cmd = ["ffmpeg", "-y", "-i", str(ogg_path), str(wav_pat]()
