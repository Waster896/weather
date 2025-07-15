import os
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
import asyncio
from dotenv import load_dotenv
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
from gtts import gTTS
import sqlite3
from datetime import datetime

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEATHER_API = os.getenv("WEATHER_API_KEY")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# --- FSM для диалога о погоде ---
class WeatherStates(StatesGroup):
    waiting_for_city = State()

# --- Текущая погода ---
@dp.message(Command("weather"))
async def weather_command(message: Message, state: FSMContext):
    await message.answer("Введите название города:")
    await state.set_state(WeatherStates.waiting_for_city)

@dp.message(WeatherStates.waiting_for_city)
async def process_city(message: Message, state: FSMContext):
    city = message.text.strip()
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get('cod') != 200:
            await message.answer("Не удалось получить данные о погоде. Проверьте название города.")
            await state.clear()
            return
        temp = data['main']['temp']
        feels_like = data['main']['feels_like']
        humidity = data['main']['humidity']
        wind = data['wind']['speed']
        description = data['weather'][0]['description'].capitalize()
        response_text = (
            f"🌤 Погода в {city}:\n"
            f"🌡 Температура: {temp}°C (ощущается как {feels_like}°C)\n"
            f"💧 Влажность: {humidity}%\n"
            f"🌬 Ветер: {wind} м/с\n"
            f"☁️ {description}"
        )
        await message.answer(response_text)
        # Сохраняем запрос в историю
        try:
            db_cursor.execute(
                "INSERT INTO history (user_id, city, date) VALUES (?, ?, ?)",
                (message.from_user.id, city, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            db_conn.commit()
        except Exception as e:
            pass
    except Exception as e:
        await message.answer("Ошибка при запросе погоды.")
    await state.clear()

class ForecastStates(StatesGroup):
    waiting_for_city = State()

@dp.message(Command("forecast"))
async def forecast_command(message: Message, state: FSMContext):
    await message.answer("Введите название города для прогноза:")
    await state.set_state(ForecastStates.waiting_for_city)

@dp.message(ForecastStates.waiting_for_city)
async def process_forecast_city(message: Message, state: FSMContext):
    city = message.text.strip()
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get('cod') != "200":
            await message.answer("Не удалось получить прогноз. Проверьте название города.")
            await state.clear()
            return
        # Собираем прогноз на 5 дней (по 12:00)
        from datetime import datetime
        from pytz import timezone
        tz = timezone('Europe/Moscow')
        daily_forecasts = []
        for item in data['list']:
            if '12:00:00' in item['dt_txt']:
                date = datetime.strptime(item['dt_txt'], "%Y-%m-%d %H:%M:%S")
                daily_forecasts.append({
                    'date': date.astimezone(tz).strftime("%d.%m"),
                    'temp': item['main']['temp'],
                    'description': item['weather'][0]['description'].capitalize()
                })
        if not daily_forecasts:
            await message.answer("Не удалось найти прогноз на 5 дней.")
            await state.clear()
            return
        forecast_text = f"📅 Прогноз в {city} на 5 дней:\n\n" + "\n".join(
            f"🗓 {day['date']}: {day['temp']}°C, {day['description']}" 
            for day in daily_forecasts
        )
        await message.answer(forecast_text)
        # --- График ---
        try:
            plt.figure(figsize=(10, 5))
            dates = [day['date'] for day in daily_forecasts]
            temps = [day['temp'] for day in daily_forecasts]
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
            await message.answer_photo(img_buffer)
        except Exception as e:
            await message.answer("Ошибка при генерации графика.")
        # --- Голосовое сообщение ---
        try:
            tts = gTTS(text=forecast_text, lang='ru')
            voice_buffer = BytesIO()
            tts.write_to_fp(voice_buffer)
            voice_buffer.seek(0)
            await message.answer_voice(voice_buffer)
        except Exception as e:
            await message.answer("Ошибка при генерации голосового сообщения.")
        # Сохраняем запрос в историю
        try:
            db_cursor.execute(
                "INSERT INTO history (user_id, city, date) VALUES (?, ?, ?)",
                (message.from_user.id, city, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            db_conn.commit()
        except Exception as e:
            pass
    except Exception as e:
        await message.answer("Ошибка при запросе прогноза.")
    await state.clear()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет!\nНапиши /weather чтобы узнать погоду в городе.")

@dp.message(lambda m: m.location is not None)
async def handle_location(message: Message):
    lat = message.location.latitude
    lon = message.location.longitude
    url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API}&units=metric&lang=ru"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get('cod') != 200:
            await message.answer("Не удалось определить погоду для вашей локации.")
            return
        city = data.get('name', 'вашем местоположении')
        temp = data['main']['temp']
        description = data['weather'][0]['description'].capitalize()
        await message.answer(
            f"📍 Погода в {city}:\n"
            f"🌡 {temp}°C, {description}\n"
            f"Используйте /weather или /forecast для подробностей."
        )
    except Exception as e:
        await message.answer("Ошибка определения погоды по локации.")

# Временное хранилище для уведомлений (user_id: {city, last_temp, threshold})
user_alerts = {}
scheduler = AsyncIOScheduler()

@dp.message(Command("alert"))
async def alert_command(message: Message, state: FSMContext):
    await message.answer("Введите город для отслеживания температуры:")
    await state.set_state(AlertStates.waiting_for_city)

class AlertStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_threshold = State()

@dp.message(AlertStates.waiting_for_city)
async def process_alert_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await message.answer("Введите порог изменения температуры (например, 5):")
    await state.set_state(AlertStates.waiting_for_threshold)

@dp.message(AlertStates.waiting_for_threshold)
async def process_alert_threshold(message: Message, state: FSMContext):
    try:
        threshold = float(message.text.strip())
    except ValueError:
        await message.answer("Порог должен быть числом. Введите снова:")
        return
    data = await state.get_data()
    city = data["city"]
    # Получаем текущую температуру
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get('cod') != 200:
            await message.answer("Не удалось получить данные о погоде. Проверьте название города.")
            await state.clear()
            return
        last_temp = data['main']['temp']
        user_alerts[message.from_user.id] = {"city": city, "last_temp": last_temp, "threshold": threshold}
        await message.answer(f"Уведомление установлено! Я сообщу, если температура в {city} изменится более чем на {threshold}°C.")
    except Exception as e:
        await message.answer("Ошибка при установке уведомления.")
    await state.clear()

async def check_weather_alerts():
    for user_id, info in user_alerts.items():
        city = info["city"]
        last_temp = info["last_temp"]
        threshold = info["threshold"]
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
        try:
            response = requests.get(url, timeout=10)
            data = response.json()
            if data.get('cod') != 200:
                continue
            current_temp = data['main']['temp']
            if abs(current_temp - last_temp) >= threshold:
                await bot.send_message(user_id, f"⚠️ В {city} изменилась температура! Было: {last_temp}°C, сейчас: {current_temp}°C")
                user_alerts[user_id]["last_temp"] = current_temp
        except Exception:
            continue

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = types.Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return {"status": "ok"}

async def on_startup():
    await bot.delete_webhook()
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")

# Запуск планировщика при старте приложения
@app.on_event("startup")
async def startup_event():
    await on_startup()
    scheduler.add_job(check_weather_alerts, "interval", minutes=60)
    scheduler.start()

# --- Инициализация базы данных ---
def init_db():
    conn = sqlite3.connect('weather.db', check_same_thread=False)
    cursor = conn.cursor()
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))