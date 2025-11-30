# app.py
from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "simple app running"}

# ОДИН маршрут, который принимает И GET, И POST на /webhook
@app.api_route("/webhook", methods=["GET", "POST"])
async def webhook(request: Request):
    # просто читаем тело для POST, чтобы не было ошибок
    if request.method == "POST":
        try:
            await request.json()
        except Exception:
            pass
    return {"ok": True}
