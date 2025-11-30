# app.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "simple app running"}

@app.post("/webhook")
async def webhook():
    return {"ok": True}

