import io
import re
import os
import asyncio
import sqlite3
import logging
import subprocess
from typing import Optional
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler
from telegram.ext import filters
from download_video import download_all_videos  # Импорт функции для скачивания всех видео
from yookassa import Configuration, Payment
import time
from datetime import datetime
import re
import csv

# Загрузка переменных окружения из .env файла
load_dotenv()

# Получение токена бота из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("Не указан BOT_TOKEN в переменных окружения")  # Проверка наличия токена

# Получение ID администратора из переменных окружения
ADMIN_ID = os.getenv('ADMIN_ID')
if not ADMIN_ID:
    raise ValueError("Не указан ADMIN_ID в переменных окружения")  # Проверка наличия ID админа

Configuration.account_id = os.getenv('YOOKASSA_SHOP_ID')
Configuration.secret_key = os.getenv('YOOKASSA_SECRET_KEY')

# Настройка логирования для отслеживания событий и ошибок
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Подключение к базе данных SQLite (разрешение работы из разных потоков для polling)
conn = sqlite3.connect('sales_in_stories.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблицы tasks, если она не существует
cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        task_id INTEGER PRIMARY KEY,
        task_name TEXT NOT NULL,
        task_content TEXT NOT NULL,
        task_link TEXT
    )
""")
conn.commit()  # Фиксация создания таблицы

cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER UNIQUE,
        yookassa_payment_id TEXT UNIQUE,
        status TEXT DEFAULT 'pending',
        amount REAL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        paid_at TIMESTAMP NULL
    )
""")
conn.commit()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        phone TEXT,
        email TEXT UNIQUE,
        consent_agreed INTEGER DEFAULT 0,
        registered INTEGER DEFAULT 0,
        link_clicked INTEGER DEFAULT 0,
        promo_key TEXT,
        promo_price REAL
    )
""")
conn.commit()

# Migration: add username if missing
try:
    cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass  # Column already exists

cursor.execute("""
    CREATE TABLE IF NOT EXISTS promo (
        promo_id INTEGER PRIMARY KEY AUTOINCREMENT,
        promo_key TEXT UNIQUE NOT NULL,
        promo_price REAL NOT NULL,
        promo_start_period TEXT NOT NULL,
        promo_end_period TEXT NOT NULL
    )
""")
conn.commit()

def get_user(chat_id: int):
    """Get user data from DB."""
    cursor.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    if row:
        cols = [desc[0] for desc in cursor.description]
        return dict(zip(cols, row))
    return None

def ensure_user(chat_id: int):
    """Ensure user record exists."""
    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    conn.commit()

def update_user_fields(chat_id: int, **kwargs):
    """Update or insert user fields."""
    ensure_user(chat_id)
    fields = ', '.join(f"{k}=?" for k in kwargs.keys())
    values = list(kwargs.values()) + [chat_id]
    cursor.execute(f"UPDATE users SET {fields} WHERE chat_id = ?", values)
    conn.commit()

def validate_email(email: str) -> bool:
    pattern = r'^[a-zA-Z][a-zA-Z0-9_.+-]*@[a-zA-Z][a-zA-Z0-9-]*\.[a-zA-Z][a-zA-Z0-9-.]+$'
    return bool(re.match(pattern, email))

def validate_phone(phone: str) -> bool:
    pattern = r'^\+?[\d\s\-\(\)]{10,15}$'
    return bool(re.match(pattern, phone))

def validate_promo(promo_key: str) -> Optional[float]:
    """Validate promo and return price if valid."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        SELECT promo_price FROM promo
        WHERE promo_key = ? AND promo_start_period <= ? AND promo_end_period >= ?
    """, (promo_key, now, now))
    row = cursor.fetchone()
    return row[0] if row else None

def is_consent_and_registered(chat_id: int) -> bool:
    """Check if user has consented and registered."""
    user = get_user(chat_id)
    return bool(user and user.get('consent_agreed', 0) == 1 and user.get('registered', 0) == 1)

async def is_user_paid(chat_id: int) -> bool:
    cursor.execute("SELECT 1 FROM payments WHERE chat_id = ? AND status = 'succeeded'", (chat_id,))
    return cursor.fetchone() is not None

async def create_payment(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    user = get_user(chat_id)
    if not user or not is_consent_and_registered(chat_id):
        logging.error(f"User not registered/consented: {chat_id}")
        return None
    idempotency_key = f"course_{chat_id}_{int(time.time())}"
    try:
        first_name = user.get('first_name', '') or ''
        last_name = user.get('last_name', '') or ''
        email = user.get('email', '')
        phone = user.get('phone', '')
        promo_price = user.get('promo_price') or float(os.getenv('COURSE_PRICE', '1990.00'))
        amount_value = f"{promo_price:.2f}"
        description = f"Оплата курса 'Продажи в сториз' для {first_name} {last_name} ({email}, {phone}) [{chat_id}]"
        metadata = {
            "telegram_chat_id": str(chat_id),
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
            "promo_key": user.get('promo_key'),
            "promo_price": amount_value
        }
        chat = await context.bot.get_chat(chat_id)
        username = f"@{chat.username}" if chat.username else ''
        metadata["username"] = username

        payment = Payment.create({
            "amount": {
                "value": amount_value,
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://yookassa.ru/my/test"  # Or your site/TG link
            },
            "capture": True,
            "description": description,
            "metadata": metadata
        }, idempotency_key)
        
        # Store pending
        cursor.execute("""
            INSERT OR REPLACE INTO payments (chat_id, yookassa_payment_id, status, amount, description)
            VALUES (?, ?, 'pending', ?, ?)
        """, (chat_id, payment.id, promo_price, description))
        conn.commit()
        
        return payment.confirmation.confirmation_url
    except Exception as e:
        logging.error(f"Payment creation failed: {e}")
        return None

async def check_payment(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cursor.execute("SELECT yookassa_payment_id FROM payments WHERE chat_id = ? AND status = 'pending'", (chat_id,))
    row = cursor.fetchone()
    if not row:
        return False
    payment_id = row[0]
    try:
        payment = Payment.find_one(payment_id)
        if payment.status == 'succeeded':
            cursor.execute("""
                UPDATE payments SET status = 'succeeded', paid_at = CURRENT_TIMESTAMP
                WHERE chat_id = ?
            """, (chat_id,))
            conn.commit()
            return True
        elif payment.status in ['canceled', 'rejected']:
            cursor.execute("UPDATE payments SET status = ? WHERE chat_id = ?", (payment.status, chat_id))
            conn.commit()
    except Exception as e:
        logging.error(f"Payment check failed: {e}")
    return False


async def get_admin_photo(bot: Bot, admin_id: str) -> Optional[InputFile]:
    """
    Получает фото профиля администратора для приветственного сообщения.
    """
    try:
        # Получение информации о чате администратора
        admin_chat = await bot.get_chat(admin_id)
    except Exception as e:
        # Логирование ошибки получения чата
        logging.error(f"Ошибка получения чата админа {admin_id}: {e}")
        return None  # Возврат None при ошибке

    if admin_chat and admin_chat.photo:
        try:
            # Логирование попытки получения фото
            logging.info(f"Попытка получить фото профиля администратора {admin_id}")
            # Получение файла фото
            photo = await bot.get_file(admin_chat.photo.big_file_id)
            logging.info(f"Получена информация о фото: {photo}")

            if photo and photo.file_path:
                # Логирование загрузки фото
                logging.info(f"Загрузка фото по пути: {photo.file_path}")
                # Скачивание фото как байтовый массив
                photo_bytes = await photo.download_as_bytearray()

                if photo_bytes:
                    # Логирование размера фото
                    logging.info(f"Размер загруженного фото: {len(photo_bytes)} байт")
                    # Возврат фото как InputFile
                    return InputFile(io.BytesIO(photo_bytes), filename='admin_photo.jpg')
                else:
                    # Предупреждение о пустом фото
                    logging.warning("Не удалось загрузить фото: photo_bytes пуст")
            else:
                # Предупреждение об отсутствии пути к файлу
                logging.warning("Не удалось получить file_path фото")
        except Exception as e:
            # Логирование ошибки обработки фото
            logging.error(f"Ошибка при работе с фото профиля: {e}")

    # Возврат None, если фото недоступно
    return None

async def list_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /list_videos: показывает список видео файлов в директории videos.
    """
    if not update.message:
        # Логирование ошибки отсутствия сообщения
        logging.error("update.message is None")
        return

    try:
        # Чтение списка файлов в директории videos
        video_files = os.listdir('./videos')
        # Отправка списка файлов пользователю
        await update.message.reply_text(f"Видео файлы: {video_files}")
    except Exception as e:
        # Логирование и отправка ошибки
        logging.error(f"Ошибка листинга видео файлов: {e}")
        await update.message.reply_text("Ошибка при получении списка видео файлов")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /help: показывает справку по боту.
    """
    if not update.message:
        return

    help_text = """
*Доступные команды:*

/start - Запустить курс "Продажи в сториз за 12 дней"
/help - Показать эту справку

*Как пользоваться:*
1. Нажмите /start для приветствия и оплаты (3990 ₽ через YooKassa).
2. Купите курс → "Проверить оплату".
3. После оплаты: "Начать курс 🎉" → уроки с видео + текстом.
4. "Следующий урок" для перехода.

Курс защищён оплатой. Тестовые карты YooKassa: 4111 1111 1111 1111.
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def send_video(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    chat_id = update.effective_chat.id
    video_path = f"./videos/task_{task_id}.mp4"
    if not os.path.exists(video_path):
        logging.error(f"Видео файл не найден: {video_path}")
        return
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', video_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode == 0:
            width, height = map(int, result.stdout.strip().split('x'))
        else:
            logging.warning(f"Не удалось получить размеры видео {video_path}, используем дефолт")
            width, height = 640, 360

        with open(video_path, 'rb') as video_file:
            await context.bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=f'Задание №{task_id}',
                height=height,
                width=width,
                protect_content=True
            )
    except Exception as e:
        logging.error(f"Ошибка отправки видео для задачи {task_id}: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /start: отправляет приветствие с фото админа и кнопкой начать курс.
    """
    try:
        chat_id = update.effective_chat.id
        logging.info(f"User ID: {chat_id}")

        if str(chat_id) == ADMIN_ID:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Подготовить отчет", callback_data='prepare_report')],
                [InlineKeyboardButton("Список пользователей", callback_data='list_users')],
                [InlineKeyboardButton("Удалить пользователя", callback_data='delete_user')]
            ])
            await context.bot.send_message(chat_id=chat_id, text="Меню администратора.", reply_markup=keyboard)
            return
        else:
            logging.info(f"Запуск приветствия для chat_id {chat_id}")

            ensure_user(chat_id)
            user = get_user(chat_id)
            paid = await is_user_paid(chat_id)
            photo = await get_admin_photo(context.bot, ADMIN_ID) if ADMIN_ID else None

        if is_consent_and_registered(chat_id):
            welcome_text = (
                "Рада приветствовать вас на моём авторском курсе 'Продажи в сториз за 12 дней'\n\n"
                "Ольга Авдеева — наставник по продажам и эксперт в создании стратегий для роста бизнеса.\n\n"
                "Более 5 лет я помогаю самозанятым, экспертам и предпринимателям привлекать клиентов из соцсетей, "
                "выстраивать систему продаж и масштабировать свои проекты.\n\n"
                "Моя миссия — помочь вам найти точку роста, увидеть свою уникальность и превратить это в стратегию "
                "действий, которая работает.\n\n"
                "Форматы работы:\n\n"
                "⭕️Видео — описание условий для успешного выполнения задачи, объяснение способа выполнения.\n\n"
                "⭕️Текст — текстовое описание, инфоповод и важные особенности для достижения успешного результата.\n\n"
                "Этот бот создан как помощник для обучения экспертов и предпринимателей без выгорания."
            )
            if paid:
                extra_text = "\n\n✅ Вы уже оплатили курс!"
                button_text = "Начать курс 🎉"
                callback_data_b = 'start_course'
            else:
                promo_price = user.get('promo_price')
                price_str = f"{promo_price:.2f}" if promo_price is not None else os.getenv('COURSE_PRICE', '1990.00')
                extra_text = f"\n\n💳 Купить курс ({price_str} ₽)"
                button_text = f"Купить курс ({price_str} ₽)"
                callback_data_b = 'buy_course'
            full_text = welcome_text + extra_text
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(button_text, callback_data=callback_data_b)]])
            if photo:
                photo_message = await context.bot.send_photo(chat_id=chat_id, photo=photo)
                if photo_message and context.user_data is not None:
                    context.user_data['photo_message_id'] = photo_message.message_id
            welcome_message = await context.bot.send_message(chat_id=chat_id, text=full_text, reply_markup=keyboard)
            if context.user_data is not None:
                context.user_data['welcome_message_id'] = welcome_message.message_id
        else:
            consent_text = (
                "Перед доступом к курсу необходимо ознакомиться с документами по обработке персональных данных "
                "и дать согласие.\n\n"
                "Нажимая кнопку 'Согласен' я даю своё согласие на:\n"
                "- на обработку и хранение персональных данных(которые сделали при старте),\n"
                "- на фото и видео съёмку и использование этих материалов в целях продвижения и привлечения участников.\n\n"
                "Документы доступны по ссылке:"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Открыть документы", callback_data='open_docs')],
                [InlineKeyboardButton("Согласен ✅", callback_data='consent_yes'),
                InlineKeyboardButton("Не согласен ❌", callback_data='consent_no')]
            ])
            if photo:
                await context.bot.send_photo(chat_id=chat_id, photo=photo)
            await context.bot.send_message(chat_id=chat_id, text=consent_text, reply_markup=keyboard)

    except Exception as e:
        logging.error(f"Ошибка в функции start: {e}")
        if update.message:
            await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте снова.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик нажатий кнопок: переход по урокам, отправка видео и текста задачи.
    """
    try:
        # Получение callback_query
        query = update.callback_query
        if query is None:
            # Логирование и отправка ошибки
            logging.error("query is None для callback кнопки")
            if update.effective_chat:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")
            return

        chat_id = update.effective_chat.id  # ID чата для отправки

        if query.data == 'buy_course':
            if not is_consent_and_registered(chat_id):
                await context.bot.send_message(chat_id=chat_id, text="Сначала завершите регистрацию. Нажмите /start.")
                await query.answer()
                return
            url = await create_payment(chat_id, context)
            if url:
                check_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Проверить оплату", callback_data='check_pay')]])
                await context.bot.send_message(chat_id=chat_id, text=f"Перейдите по ссылке для оплаты:\n{url}", reply_markup=check_keyboard)
            else:
                await context.bot.send_message(chat_id=chat_id, text="Ошибка создания платежа. Попробуйте позже.")
            await query.answer()
            return

        elif query.data == 'check_pay':
            paid = await check_payment(chat_id, context)
            if paid:
                await context.bot.send_message(chat_id=chat_id, text="Оплата подтверждена! 🎉 Начинаем курс:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Начать курс", callback_data='start_course')]]))
            else:
                await context.bot.send_message(chat_id=chat_id, text="Оплата не подтверждена. Перейдите по ссылке и попробуйте снова.")
            await query.answer()
            return

        elif query.data == 'open_docs':
            update_user_fields(chat_id, link_clicked=1)
            url = "https://disk.yandex.ru/d/GpPCV_3ozvydig"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📄 Ознакомиться с документами", url=url)]])
            await query.edit_message_text("Ознакомьтесь с документами по ссылке ниже:\n(для продолжения нажмите /start)", reply_markup=keyboard)
            await query.answer("Документы открыты для просмотра")

        elif query.data == 'consent_yes':
            update_user_fields(chat_id, consent_agreed=1)
            context.user_data['reg_state'] = 'name'
            await query.edit_message_text("✅ Согласие на обработку персональных данных получено!\n\nТеперь зарегистрируйтесь,\nвведите ваше имя:")
            await query.answer("Начинаем регистрацию")

        elif query.data == 'consent_no':
            update_user_fields(chat_id, consent_agreed=0)
            await context.bot.send_message(chat_id=chat_id, text="❌ К сожалению, без согласия на обработку персональных данных доступ к курсу невозможен.\nНажмите /start для новой попытки.")
            await query.answer("Согласие отказано")

        elif query.data == 'has_promo_yes':
            context.user_data['reg_state'] = 'promo_code'
            await query.edit_message_text("Введите промокод:")
            await query.answer()

        elif query.data == 'has_promo_no':
            update_user_fields(chat_id, promo_key=None, promo_price=None, registered=1)
            if context.user_data is not None:
                context.user_data.pop('reg_state', None)
            default_price = os.getenv('COURSE_PRICE', '1990.00')
            await query.edit_message_text(f"✅ Регистрация завершена! Цена курса: {default_price} ₽\nНажмите /start для покупки.")
            await query.answer()

        elif query.data == 'prepare_report':
            query_str = """
                SELECT 
                    ROW_NUMBER() OVER (ORDER BY u.created_at) as "Номер п/п",
                    u.last_name as "Фамилия",
                    u.first_name as "Имя",
                    u.phone as "Номер телефона",
                    u.email as "email",
                    u.created_at as "Дата заявки",
                    p.paid_at as "Дата оплаты",
                    p.amount as "Бюджет",
                    CASE WHEN p.status = 'succeeded' THEN 'Оплачено' ELSE 'Не оплачено' END as "Оплата",
                    COALESCE(u.promo_key, 'Нет') as "Промокод"
                FROM users u 
                LEFT JOIN payments p ON u.chat_id = p.chat_id 
                ORDER BY u.created_at
            """
            cursor.execute(query_str)
            rows = cursor.fetchall()
            cols = [desc[0] for desc in cursor.description]

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(cols)
            writer.writerows(rows)
            csv_content = output.getvalue().encode('utf-8')
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = f'report_{timestamp}.csv'
            bio = io.BytesIO(csv_content)
            bio.name = filename

            await context.bot.send_document(chat_id=chat_id, document=InputFile(bio, filename=filename))
            await query.answer("Отчет отправлен")
            return

        elif query.data == 'list_users':
            cursor.execute("SELECT COUNT(*) FROM users")
            total_registered = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM payments WHERE status = 'succeeded'")
            total_paid = cursor.fetchone()[0]
            stats_text = f"👥 Зарегистрировано всего пользователей: {total_registered}\n💰 Оплатили: {total_paid}\n\n"

            cursor.execute("""
                SELECT DISTINCT chat_id FROM users
                UNION
                SELECT chat_id FROM payments
                ORDER BY chat_id
            """)
            all_chat_ids = [row[0] for row in cursor.fetchall()]

            list_text = ""
            for cid in all_chat_ids:
                user = get_user(cid)
                if user:
                    fn = user.get('first_name', '') or ''
                    ln = user.get('last_name', '') or ''
                    reg_status = 'зарегистрирован'
                else:
                    reg_status = 'не зарегистрирован'
                    try:
                        chat_obj = await context.bot.get_chat(cid)
                        fn = chat_obj.first_name or ''
                        ln = chat_obj.last_name or ''
                    except Exception as e:
                        logging.error(f"Failed to fetch chat {cid}: {e}")
                        fn = ln = ''
                name = f"{fn} {ln}".strip()
                if not name:
                    name = f"User {cid}"
                cursor.execute("SELECT 1 FROM payments WHERE chat_id = ? AND status = 'succeeded'", (cid,))
                pay_row = cursor.fetchone()
                pay_status = 'оплатил' if pay_row else 'не оплатил'
                list_text += f"{name} - {reg_status} - {pay_status}\n"

            full_text = stats_text + list_text.rstrip('\n')
            await query.edit_message_text(full_text)
            await query.answer("Список пользователей")
            return

        elif query.data == 'delete_user':
            cursor.execute("SELECT chat_id, first_name, last_name FROM users WHERE first_name IS NOT NULL ORDER BY created_at DESC LIMIT 10")
            users = cursor.fetchall()
            if not users:
                await query.edit_message_text("Нет пользователей для удаления.")
                await query.answer()
                return
            keyboard = []
            for user in users:
                chat_id_u, first, last = user
                name = f"{first or ''} {last or ''}".strip() or f"User {chat_id_u}"
                keyboard.append([InlineKeyboardButton(name, callback_data=f'delete_confirm_{chat_id_u}')])
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data='admin_menu')])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Выберите пользователя для удаления:", reply_markup=reply_markup)
            await query.answer()
            return

        elif query.data == 'admin_menu':
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Подготовить отчет", callback_data='prepare_report')],
                [InlineKeyboardButton("Список пользователей", callback_data='list_users')],
                [InlineKeyboardButton("Удалить пользователя", callback_data='delete_user')]
            ])
            await query.edit_message_text("Меню администратора.", reply_markup=keyboard)
            await query.answer()
            return

        elif query.data.startswith('delete_confirm_'):
            try:
                del_id = int(query.data.split('_', 2)[2])
                user = get_user(del_id)
                name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or f"User {del_id}"
                cursor.execute("DELETE FROM users WHERE chat_id = ?", (del_id,))
                cursor.execute("DELETE FROM payments WHERE chat_id = ?", (del_id,))
                conn.commit()
                await query.edit_message_text(f"Пользователь {name} ({del_id}) удалён из базы данных.")
            except (ValueError, IndexError):
                await query.edit_message_text("Ошибка удаления.")
            except Exception as e:
                await query.edit_message_text(f"Ошибка: {str(e)}")
            await query.answer("Удалено")
            return

        else:
            if str(chat_id) != ADMIN_ID:
                if not is_consent_and_registered(chat_id):
                    await query.answer()
                    return
                if not await is_user_paid(chat_id):
                    await context.bot.send_message(chat_id=chat_id, text="Доступ к курсу платный. Нажмите /start для оплаты.")
                    await query.answer()
                    return

            # Handle start_course or numeric lesson
            if query.data == 'start_course':
                task_id = 1
            else:
                task_id = int(query.data)

        # Запрос данных задачи из БД
        cursor.execute("SELECT task_name, task_content, task_link FROM tasks WHERE task_id = ?", (task_id,))
        task = cursor.fetchone()

        if not task:
            # Отправка ошибки если задача не найдена
            await context.bot.send_message(chat_id=chat_id, text=f"Задача {task_id} не найдена.")
            return

        task_name, task_content, task_link = task

        # Получение общего количества задач
        cursor.execute("SELECT COUNT(*) FROM tasks")
        total_tasks = cursor.fetchone()[0]

        # Подготовка кнопки следующей задачи, если не последняя
        next_task_id = task_id + 1
        if task_id < total_tasks:
            keyboard = [[InlineKeyboardButton("Следующий урок", callback_data=str(next_task_id))]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            reply_markup = None

        # Удаление кнопки с предыдущего сообщения задачи
        if context.user_data:
            previous_msg_id = context.user_data.get('last_task_message_id')
            if previous_msg_id:
                try:
                    await context.bot.edit_message_reply_markup(
                        chat_id=chat_id, message_id=previous_msg_id, reply_markup=None
                    )
                    logging.info(f"Удалена кнопка с предыдущего сообщения {previous_msg_id}")
                except Exception as e:
                    logging.error(f"Не удалось отредактировать предыдущее сообщение: {e}")

        # Отправка видео для задачи
        try:
            await send_video(update, context, task_id)
        except Exception as e:
            # Логирование ошибки видео и отправка текста без видео
            logging.error(f"Ошибка отправки видео: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"Ошибка обработки видео: {str(e)}")

        # Отправка текста задачи
        task_text = f"{task_name}\n{task_content}"
        if task_link:
            task_text += f"\n\nСсылка: {task_link}"  # Добавление ссылки если есть
        task_message = await context.bot.send_message(
            chat_id=chat_id, text=task_text, reply_markup=reply_markup
        )
        # Сохранение ID текущего сообщения для удаления кнопки в следующий раз
        if context.user_data is not None:
            context.user_data['last_task_message_id'] = task_message.message_id

    except Exception as e:
        # Общее логирование ошибки
        logging.error(f"Ошибка в функции button: {e}")

async def register_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text inputs during registration."""
    if not update.message:
        return
    chat_id = update.message.chat.id
    if context.user_data is None:
        return
    reg_state = context.user_data.get('reg_state')
    if not reg_state:
        return  # Ignore if not in reg state
    text = (update.message.text or '').strip()

    if reg_state == 'name':
        if len(text) < 2:
            await update.message.reply_text("Имя слишком короткое. Введите имя (минимум 2 символа):")
            return
        if not text.isalpha():
            await update.message.reply_text("Имя должно содержать только буквы.")
            return
        update_user_fields(chat_id, first_name=text)
        context.user_data['reg_state'] = 'surname'
        await update.message.reply_text("Введите фамилию:")
    elif reg_state == 'surname':
        if len(text) < 2:
            await update.message.reply_text("Фамилия слишком короткая. Введите фамилию (минимум 2 символа):")
            return
        if not text.isalpha():
            await update.message.reply_text("Фамилия должна содержать только буквы.")
            return
        update_user_fields(chat_id, last_name=text)
        context.user_data['reg_state'] = 'email'
        await update.message.reply_text("Введите email:")
    elif reg_state == 'email':
        if not validate_email(text):
            await update.message.reply_text("Неверный формат email. Пример: example@mail.com\nВведите email:")
            return
        cursor.execute("SELECT 1 FROM users WHERE email = ? AND chat_id != ?", (text, chat_id))
        if cursor.fetchone():
            await update.message.reply_text("Этот email уже зарегистрирован. Введите другой:")
            return
        update_user_fields(chat_id, email=text)
        context.user_data['reg_state'] = 'phone'
        await update.message.reply_text("Введите номер телефона (например, +7 (999) 123-45-67):")
    elif reg_state == 'phone':
        if not validate_phone(text):
            await update.message.reply_text("Неверный формат телефона. Пример: +79991234567\nВведите номер телефона:")
            return
        update_user_fields(chat_id, phone=text)
        context.user_data['reg_state'] = 'username'
        await update.message.reply_text("Введите username из учетной записи telegram:")

    elif reg_state == 'username':
        username_input = text.strip()
        if not username_input:
            await update.message.reply_text("Username не может быть пустым. Введите username из учетной записи telegram:")
            return
        update_user_fields(chat_id, username=username_input)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Да ✅", callback_data='has_promo_yes'),
             InlineKeyboardButton("Нет ❌", callback_data='has_promo_no')]
        ])
        await update.message.reply_text("У Вас есть промокод?", reply_markup=keyboard)

    elif reg_state == 'promo_code':
        promo_price = validate_promo(text)
        if promo_price is None:
            await update.message.reply_text("Неверный промокод или срок действия истек.\nВведите промокод:")
            return
        update_user_fields(chat_id, promo_key=text, promo_price=promo_price, registered=1)
        if context.user_data is not None:
            context.user_data.pop('reg_state', None)
        await update.message.reply_text(f"✅ Промокод применен! Цена курса: {promo_price:.2f} ₽\nРегистрация завершена. Нажмите /start для покупки.")

def main() -> None:
    """
    Основная функция: настройка и запуск бота.
    """
    if BOT_TOKEN is None:
        raise ValueError("BOT_TOKEN не установлен в переменных окружения.")

    # Создание приложения Telegram бота
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Добавление обработчиков команд и callback
    application.add_handler(CommandHandler("start", start))  # /start
    application.add_handler(CommandHandler("list_videos", list_videos))  # /list_videos
    application.add_handler(CommandHandler("help", help_command))  # /help
    application.add_handler(CallbackQueryHandler(button))  # Кнопки
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, register_text_handler))

    # Запуск polling для получения обновлений
    application.run_polling()

if __name__ == '__main__':
    # Асинхронная функция для скачивания всех видео при запуске
    async def download_all_videos_async():
        download_all_videos()  # Вызов синхронной функции скачивания
        logging.info("Завершено скачивание всех видео.")

    # Скачивание видео перед запуском бота
    asyncio.run(download_all_videos_async())
    # Запуск бота
    main()
