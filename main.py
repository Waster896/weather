import os
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import uvicorn
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Инициализация FastAPI и Telegram Application
app = FastAPI()
application = Application.builder().token(TOKEN).build()

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет")

application.add_handler(CommandHandler("start", start))

# Webhook endpoint для Telegram
@app.post("/webhook")
async def telegram_webhook(request: Request):
    json_data = await request.json()
    update = Update.de_json(json_data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

if __name__ == "__main__":
    # Удаляем предыдущий вебхук и устанавливаем новый
    application.bot.delete_webhook()
    application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    # Запуск FastAPI
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        workers=1
    )