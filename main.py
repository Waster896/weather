import os
import sqlite3
from datetime import datetime
from pytz import timezone
import matplotlib.pyplot as plt
from io import BytesIO
from gtts import gTTS
import httpx
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
import asyncio
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

# --- Инициализация конфигурации ---
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEATHER_API = os.getenv("WEATHER_API")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL") + WEBHOOK_PATH

@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    env_vars = {k: v for k, v in os.environ.items()}
    await bot.set_webhook(WEBHOOK_URL)
    scheduler.start()
    yield
    await bot.delete_webhook()
    db_conn.close()
    scheduler.shutdown()

# --- FastAPI и aiogram ---
app = FastAPI(lifespan=lifespan)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- Инициализация базы данных ---
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
async def get_weather_data(city, forecast=False):
    start_time = time.monotonic()
    async with httpx.AsyncClient() as client:
        if forecast:
            url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
        else:
            url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
        print(f"[get_weather_data] city={city}, forecast={forecast}, url={url}")
        try:
            req_start = time.monotonic()
            response = await client.get(url, timeout=10)
            req_time = time.monotonic() - req_start
            print(f"[get_weather_data] HTTP status: {response.status_code}, request_time={req_time:.3f}s")
            response.raise_for_status()
            data = response.json()
            print(f"[get_weather_data] Response keys: {list(data.keys())}")
            return data
        except Exception as e:
            print(f"[get_weather_data] Ошибка при запросе погоды: {e}")
            return None
        finally:
            total_time = time.monotonic() - start_time
            print(f"[get_weather_data] Total time: {total_time:.3f}s")

def generate_temp_plot(data):
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
    try:
        tts = gTTS(text=text, lang='ru')
        voice_buffer = BytesIO()
        tts.write_to_fp(voice_buffer)
        voice_buffer.seek(0)
        return voice_buffer
    except Exception as e:
        print(f"Ошибка при генерации голоса: {e}")
        return None

# --- Хендлеры ---
class WeatherStates(StatesGroup):
    waiting_for_city_current = State()
    waiting_for_city_forecast = State()

@dp.message(F.content_type == types.ContentType.LOCATION)
async def handle_location(message: types.Message):
    print(f"[LOCATION] Handler triggered. message.location={getattr(message, 'location', None)}")
    try:
        lat = message.location.latitude
        lon = message.location.longitude
        async with httpx.AsyncClient() as client:
            url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API}&units=metric&lang=ru"
            response = await client.get(url, timeout=10)
            response.raise_for_status()
            weather_data = response.json()
        if weather_data.get('cod') != 200:
            await message.answer("Не удалось определить погоду для вашей локации.")
            return
        city = weather_data.get('name', 'вашем местоположении')
        temp = weather_data['main']['temp']
        description = weather_data['weather'][0]['description'].capitalize()
        await message.answer(
            f"📍 Погода в {city}:\n"
            f"🌡 {temp}°C, {description}\n"
            f"Используйте кнопки меню для подробностей."
        )
    except Exception as e:
        print(f"[LOCATION] Ошибка обработки локации: {e}")
        await message.answer("Ошибка определения погоды по локации.")

@dp.message(F.text.in_(["/start", "/help"]))
async def send_welcome(message: types.Message):
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text='🌤 Текущая погода'),
                KeyboardButton(text='📅 Прогноз на 5 дней'),
                KeyboardButton(text='📍 Поделиться локацией', request_location=True)
            ]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "Добро пожаловать в WeatherBot!\n"
        "Я могу показать текущую погоду или прогноз на 5 дней.\n"
        "Выберите действие:",
        reply_markup=markup
    )

@dp.message(F.text == '🌤 Текущая погода')
async def request_current_weather(message: types.Message, state: FSMContext):
    await message.answer("Введите название города:")
    await state.set_state(WeatherStates.waiting_for_city_current)

@dp.message(WeatherStates.waiting_for_city_current)
async def process_current_weather_request(message: types.Message, state: FSMContext):
    city = message.text.strip()
    weather_data = await get_weather_data(city)
    if not weather_data or weather_data.get('cod') != 200:
        await message.answer("Не удалось получить данные о погоде. Проверьте название города.")
        await state.clear()
        return
    try:
        db_cursor.execute(
            "INSERT INTO history (user_id, city, date) VALUES (?, ?, ?)",
            (message.chat.id, city, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
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
    await message.answer(response_text)
    await state.clear()

@dp.message(F.text == '📅 Прогноз на 5 дней')
async def request_forecast(message: types.Message, state: FSMContext):
    await message.answer("Введите название города для прогноза:")
    await state.set_state(WeatherStates.waiting_for_city_forecast)

@dp.message(WeatherStates.waiting_for_city_forecast)
async def process_forecast_request(message: types.Message, state: FSMContext):
    city = message.text.strip()
    forecast_data = await get_weather_data(city, forecast=True)
    if not forecast_data or forecast_data.get('cod') != '200':
        await message.answer("Не удалось получить прогноз. Проверьте название города.")
        await state.clear()
        return
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
            input_file = BufferedInputFile(plot.getvalue(), filename="plot.png")
            await message.answer_photo(input_file)
        forecast_text = f"📅 Прогноз в {city} на 5 дней:\n\n" + "\n".join(
            f"🗓 {day['date']}: {day['temp']}°C, {day['description']}" for day in daily_forecasts
        )
        await message.answer(forecast_text)
        voice = generate_voice_message(forecast_text)
        if voice:
            input_voice = BufferedInputFile(voice.getvalue(), filename="voice.ogg")
            await message.answer_voice(input_voice)
    except Exception as e:
        print(f"Ошибка при обработке прогноза: {e}")
        await message.answer("Произошла ошибка при обработке прогноза.")
    await state.clear()

# --- Система уведомлений ---
async def check_weather_alerts():
    try:
        db_cursor.execute("SELECT user_id, city, last_temp FROM users WHERE alert_time IS NOT NULL")
        for user_id, city, last_temp in db_cursor.fetchall():
            current_data = await get_weather_data(city)
            if current_data and current_data.get('cod') == 200:
                current_temp = current_data['main']['temp']
                if abs(current_temp - last_temp) >= 5:
                    await bot.send_message(
                        user_id,
                        f"⚠️ В {city} изменилась температура!\n"
                        f"Было: {last_temp}°C, сейчас: {current_temp}°C"
                    )
                    db_cursor.execute(
                        "UPDATE users SET last_temp = ? WHERE user_id = ?",
                        (current_temp, user_id)
                    )
                    db_conn.commit()
    except Exception as e:
        print(f"Ошибка проверки уведомлений: {e}")

scheduler = AsyncIOScheduler()
scheduler.add_job(check_weather_alerts, 'interval', hours=1)
# scheduler.start()  # УБРАТЬ отсюда

# --- FastAPI webhook endpoint ---
@app.get("/")
async def root():
    return {"status": "ok"}

@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    data = await request.json()
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000))) 
