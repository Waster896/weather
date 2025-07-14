import os
import requests
import sqlite3
from datetime import datetime
from pytz import timezone
import matplotlib.pyplot as plt
from io import BytesIO
from gtts import gTTS
import telebot
from telebot import types
from dotenv import load_dotenv
from cachetools import TTLCache
from apscheduler.schedulers.background import BackgroundScheduler

# --- Инициализация конфигурации ---
load_dotenv()

# Создаем объект бота
TOKEN = "7457787588:AAHsbH4g4qWTEdfT7aTK106s1NRidY2tB4E"
bot = telebot.TeleBot(TOKEN)
WEATHER_API = "b8bf370f1dd984bfbcdf50d3d13908bb"

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

# --- Обработчики команд ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Обработчик команд start и help"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = types.KeyboardButton('🌤 Текущая погода')
    btn2 = types.KeyboardButton('📅 Прогноз на 5 дней')
    btn3 = types.KeyboardButton('⚠️ Установить уведомление')
    btn4 = types.KeyboardButton('📍 Поделиться локацией', request_location=True)
    markup.add(btn1, btn2, btn3, btn4)
    
    bot.send_message(
        message.chat.id,
        "Добро пожаловать в WeatherBot!\n"
        "Я могу показать текущую погоду или прогноз на 5 дней.\n"
        "Выберите действие:",
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: message.text == '🌤 Текущая погода')
def request_current_weather(message):
    """Запрос города для текущей погоды"""
    msg = bot.send_message(message.chat.id, "Введите название города:")
    bot.register_next_step_handler(msg, process_current_weather_request)

def process_current_weather_request(message):
    """Обработка запроса текущей погоды"""
    city = message.text.strip()
    weather_data = get_weather_data(city)
    
    if not weather_data or weather_data.get('cod') != 200:
        bot.send_message(message.chat.id, "Не удалось получить данные о погоде. Проверьте название города.")
        return
    
    # Сохраняем запрос в историю
    try:
        db_cursor.execute(
            "INSERT INTO history (user_id, city, date) VALUES (?, ?, ?)",
            (message.chat.id, city, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        db_conn.commit()
    except Exception as e:
        print(f"Ошибка при сохранении в историю: {e}")
    
    # Формируем и отправляем ответ
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
    
    bot.send_message(message.chat.id, response_text)

@bot.message_handler(func=lambda message: message.text == '📅 Прогноз на 5 дней')
def request_forecast(message):
    """Запрос города для прогноза"""
    msg = bot.send_message(message.chat.id, "Введите название города для прогноза:")
    bot.register_next_step_handler(msg, process_forecast_request)

def process_forecast_request(message):
    """Обработка запроса прогноза"""
    city = message.text.strip()
    forecast_data = get_weather_data(city, forecast=True)
    
    if not forecast_data or forecast_data.get('cod') != '200':
        bot.send_message(message.chat.id, "Не удалось получить прогноз. Проверьте название города.")
        return

# Парсим данные прогноза
    try:
        tz = timezone('Europe/Moscow')
        daily_forecasts = []
        
        for item in forecast_data['list']:
            if '12:00:00' in item['dt_txt']:  # Берем только дневные прогнозы
                date = datetime.strptime(item['dt_txt'], "%Y-%m-%d %H:%M:%S")
                daily_forecasts.append({
                    'date': date.astimezone(tz).strftime("%d.%m"),
                    'temp': item['main']['temp'],
                    'description': item['weather'][0]['description'].capitalize()
                })
        
        # Генерируем график
        plot = generate_temp_plot(daily_forecasts)
        if plot:
            bot.send_photo(message.chat.id, plot)
        
        # Формируем текстовый прогноз
        forecast_text = f"📅 Прогноз в {city} на 5 дней:\n\n" + "\n".join(
            f"🗓 {day['date']}: {day['temp']}°C, {day['description']}" 
            for day in daily_forecasts
        )
        
        # Отправляем текстовый прогноз
        bot.send_message(message.chat.id, forecast_text)
        
        # Генерируем голосовое сообщение
        voice = generate_voice_message(forecast_text)
        if voice:
            bot.send_voice(message.chat.id, voice)
    
    except Exception as e:
        print(f"Ошибка при обработке прогноза: {e}")
        bot.send_message(message.chat.id, "Произошла ошибка при обработке прогноза.")

@bot.message_handler(content_types=['location'])
def handle_location(message):
    """Обработка геолокации"""
    try:
        lat = message.location.latitude
        lon = message.location.longitude
        
        # Получаем погоду по координатам
        url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API}&units=metric&lang=ru"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        weather_data = response.json()
        
        if weather_data.get('cod') != 200:
            bot.send_message(message.chat.id, "Не удалось определить погоду для вашей локации.")
            return
        
        city = weather_data.get('name', 'вашем местоположении')
        temp = weather_data['main']['temp']
        description = weather_data['weather'][0]['description'].capitalize()
        
        bot.send_message(
            message.chat.id,
            f"📍 Погода в {city}:\n"
            f"🌡 {temp}°C, {description}\n"
            f"Используйте кнопки меню для подробностей."
        )
    except Exception as e:
        print(f"Ошибка обработки локации: {e}")
        bot.send_message(message.chat.id, "Ошибка определения погоды по локации.")

# --- Система уведомлений ---
def check_weather_alerts():
    """Проверка условий для уведомлений"""
    try:
        db_cursor.execute("SELECT user_id, city, last_temp FROM users WHERE alert_time IS NOT NULL")
        for user_id, city, last_temp in db_cursor.fetchall():
            current_data = get_weather_data(city)
            if current_data and current_data.get('cod') == 200:
                current_temp = current_data['main']['temp']
                if abs(current_temp - last_temp) >= 5:
                    bot.send_message(
                        user_id,
                        f"⚠️ В {city} изменилась температура!\n"
                        f"Было: {last_temp}°C, сейчас: {current_temp}°C"
                    )
                    # Обновляем последнюю температуру
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

print("Бот запущен и готов к работе!")
try:
    bot.infinity_polling()
except Exception as e:
    print(f"Ошибка в работе бота: {e}")
finally:
    db_conn.close()
    scheduler.shutdown()