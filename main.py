import os
import requests
import sqlite3
from datetime import datetime
from pytz import timezone
import matplotlib
matplotlib.use('Agg')  # Важно для работы без GUI
import matplotlib.pyplot as plt
from io import BytesIO
from gtts import gTTS
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext, ConversationHandler
from dotenv import load_dotenv
from cachetools import TTLCache
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
import uvicorn
from threading import Thread

# --- Инициализация FastAPI ---
app = FastAPI()

# --- Инициализация конфигурации ---
load_dotenv()

# Конфигурация
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEATHER_API = os.getenv("WEATHER_API_KEY")

# Инициализация бота
application = Application.builder().token(TOKEN).build()

# Настройка кэширования
cache = TTLCache(maxsize=100, ttl=300)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('weather.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        city TEXT,
        alert_time TEXT,
        last_temp REAL
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        city TEXT,
        date TEXT
    )
    ''')
    
    conn.commit()
    return conn, cursor

db_conn, db_cursor = init_db()

# --- Основные функции ---
def get_weather_data(city, forecast=False):
    """Получение данных о погоде с OpenWeatherMap"""
    try:
        if forecast:
            url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
        else:
            url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Ошибка при запросе погоды: {e}")
        return None

def generate_temp_plot(data):
    """Генерация графика температуры"""
    try:
        plt.figure(figsize=(10, 5))
        dates = [day['date'] for day in data]
        temps = [day['temp'] for day in data]
        
        plt.plot(dates, temps, marker='o', linestyle='-', color='b')
        plt.title('Прогноз температуры на 5 дней')
        plt.xlabel('Дата')
        plt.ylabel('Температура (°C)')
        plt.grid(True)
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        img_buffer = BytesIO()
        plt.savefig(img_buffer, format='png')
        img_buffer.seek(0)
        plt.close()
        return img_buffer
    except Exception as e:
        print(f"Ошибка при генерации графика: {e}")
        return None

def generate_voice_message(text):
    """Создание голосового сообщения"""
    try:
        tts = gTTS(text=text, lang='ru')
        voice_buffer = BytesIO()
        tts.write_to_fp(voice_buffer)
        voice_buffer.seek(0)
        return voice_buffer
    except Exception as e:
        print(f"Ошибка при генерации голоса: {e}")
        return None

# --- Обработчики команд и сообщений ---

# States for ConversationHandler
CURRENT_WEATHER_CITY, FORECAST_CITY = range(2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton('🌤 Текущая погода')],
        [KeyboardButton('📅 Прогноз на 5 дней')],
        [KeyboardButton('⚠️ Установить уведомление')],
        [KeyboardButton('📍 Поделиться локацией', request_location=True)]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Добро пожаловать в WeatherBot!\n"
        "Я могу показать текущую погоду или прогноз на 5 дней.\n"
        "Выберите действие:",
        reply_markup=markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def current_weather_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите название города:")
    return CURRENT_WEATHER_CITY

async def process_current_weather_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    weather_data = get_weather_data(city)
    
    if not weather_data or weather_data.get('cod') != 200:
        await update.message.reply_text("Не удалось получить данные о погоде. Проверьте название города.")
        return ConversationHandler.END
    
    # Сохраняем запрос в историю
    try:
        db_cursor.execute(
            "INSERT INTO history (user_id, city, date) VALUES (?, ?, ?)",
            (update.message.chat.id, city, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        db_conn.commit()
    except Exception as e:
        print(f"Ошибка при сохранении в историю: {e}")
    
    temp = weather_data['main']['temp']
    feels_like = weather_data['main']['feels_like']
    humidity = weather_data['main']['humidity']
    wind = weather_data['wind']['speed']
    description = weather_data['weather'][0]['description'].capitalize()
    
    response_text = (
        f"🌤 Погода в {city}:\n"
        f"🌡 Температура: {temp}°C (ощущается как {feels_like}°C)\n"
        f"💧 Влажность: {humidity}%\n"
        f"🌬 Ветер: {wind} м/с\n"
        f"☁️ {description}"
    )
    
    await update.message.reply_text(response_text)
    return ConversationHandler.END

async def forecast_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите название города для прогноза:")
    return FORECAST_CITY

async def process_forecast_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    forecast_data = get_weather_data(city, forecast=True)
    
    if not forecast_data or forecast_data.get('cod') != '200':
        await update.message.reply_text("Не удалось получить прогноз. Проверьте название города.")
        return ConversationHandler.END
    try:
        tz = timezone('Europe/Moscow')
        daily_forecasts = []
        for item in forecast_data['list']:
            if '12:00:00' in item['dt_txt']:
                date = datetime.strptime(item['dt_txt'], "%Y-%m-%d %H:%M:%S")
                daily_forecasts.append({
                    'date': date.astimezone(tz).strftime("%d.%m"),
                    'temp': item['main']['temp'],
                    'description': item['weather'][0]['description'].capitalize()
                })
        plot = generate_temp_plot(daily_forecasts)
        if plot:
            await update.message.reply_photo(plot)
        forecast_text = f"📅 Прогноз в {city} на 5 дней:\n\n" + "\n".join(
            f"🗓 {day['date']}: {day['temp']}°C, {day['description']}" 
            for day in daily_forecasts
        )
        await update.message.reply_text(forecast_text)
        voice = generate_voice_message(forecast_text)
        if voice:
            await update.message.reply_voice(voice)
    except Exception as e:
        print(f"Ошибка при обработке прогноза: {e}")
        await update.message.reply_text("Произошла ошибка при обработке прогноза.")
    return ConversationHandler.END

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API}&units=metric&lang=ru"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        weather_data = response.json()
        if weather_data.get('cod') != 200:
            await update.message.reply_text("Не удалось определить погоду для вашей локации.")
            return
        city = weather_data.get('name', 'вашем местоположении')
        temp = weather_data['main']['temp']
        description = weather_data['weather'][0]['description'].capitalize()
        await update.message.reply_text(
            f"📍 Погода в {city}:\n"
            f"🌡 {temp}°C, {description}\n"
            f"Используйте кнопки меню для подробностей."
        )
    except Exception as e:
        print(f"Ошибка обработки локации: {e}")
        await update.message.reply_text("Ошибка определения погоды по локации.")

# --- Система уведомлений ---
def check_weather_alerts():
    try:
        db_cursor.execute("SELECT user_id, city, last_temp FROM users WHERE alert_time IS NOT NULL")
        for user_id, city, last_temp in db_cursor.fetchall():
            current_data = get_weather_data(city)
            if current_data and current_data.get('cod') == 200:
                current_temp = current_data['main']['temp']
                if abs(current_temp - last_temp) >= 5:
                    # Use application.bot.send_message in a thread-safe way
                    Thread(target=lambda: application.bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ В {city} изменилась температура!\nБыло: {last_temp}°C, сейчас: {current_temp}°C"
                    )).start()
                    db_cursor.execute(
                        "UPDATE users SET last_temp = ? WHERE user_id = ?",
                        (current_temp, user_id)
                    )
                    db_conn.commit()
    except Exception as e:
        print(f"Ошибка проверки уведомлений: {e}")

# Инициализация планировщика уведомлений
scheduler = BackgroundScheduler()
scheduler.add_job(check_weather_alerts, 'interval', hours=1)
scheduler.start()

# --- Webhook обработчик для FastAPI ---
@app.post('/webhook')
async def webhook(request: Request):
    json_data = await request.json()
    update = Update.de_json(json_data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

# --- Регистрация обработчиков ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))

# Conversation for current weather
current_weather_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^🌤 Текущая погода$'), current_weather_request)],
    states={
        CURRENT_WEATHER_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_current_weather_request)]
    },
    fallbacks=[]
)
application.add_handler(current_weather_conv)

# Conversation for forecast
forecast_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^📅 Прогноз на 5 дней$'), forecast_request)],
    states={
        FORECAST_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_forecast_request)]
    },
    fallbacks=[]
)
application.add_handler(forecast_conv)

# Location handler
application.add_handler(MessageHandler(filters.LOCATION, handle_location))

# --- Запуск бота ---
def start_bot():
    # Удаляем предыдущие вебхуки
    application.bot.delete_webhook()
    # Устанавливаем новый вебхук
    application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    print("Бот запущен в режиме вебхука")

if __name__ == "__main__":
    # Установка вебхука
    application.bot.delete_webhook()
    application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    # Запуск FastAPI
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        workers=1
    )