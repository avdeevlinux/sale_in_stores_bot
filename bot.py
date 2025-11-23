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
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from download_video import download_all_videos  # Импорт функции для скачивания всех видео
from yookassa import Configuration, Payment
import time

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
        status TEXT DEFAULT 'pending',  -- pending, waiting_for_capture, succeeded, canceled
        amount REAL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        paid_at TIMESTAMP NULL
    )
""")
conn.commit()

async def is_user_paid(chat_id: int) -> bool:
    cursor.execute("SELECT 1 FROM payments WHERE chat_id = ? AND status = 'succeeded'", (chat_id,))
    return cursor.fetchone() is not None

async def create_payment(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    idempotency_key = f"course_{chat_id}_{int(time.time())}"
    try:
        chat = await context.bot.get_chat(chat_id)
        first_name = chat.first_name or ''
        last_name = getattr(chat, 'last_name', '') or ''
        username = f"@{chat.username}" if chat.username else ''
        description = f"Оплата курса 'Продажи в сториз' для Telegram {first_name} {last_name} {username} [{chat_id}]"
        metadata = {
            "telegram_chat_id": str(chat_id),
            "first_name": first_name,
            "last_name": last_name,
            "username": chat.username or None
        }
        payment = Payment.create({
            "amount": {
                "value": os.getenv('COURSE_PRICE', '1990.00'),
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
        """, (chat_id, payment.id, float(payment.amount.value), payment.description))
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /start: отправляет приветствие с фото админа и кнопкой начать курс.
    """
    try:
        if update.message and update.message.chat:
            # Получение ID чата
            chat_id = update.message.chat.id
            # Логирование ID пользователя
            logging.info(f"User ID: {chat_id}")
            photo_message = None

            # Получение фото админа
            photo = await get_admin_photo(context.bot, ADMIN_ID)  # Передача context.bot

            # Логирование начала курса
            logging.info(f"Запуск приветствия для chat_id {chat_id}")
            if photo:
                # Отправка фото админа
                photo_message = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo
                )
                # Попытка удалить команду /start
                # try:
                #     if update.message.message_id:
                #         await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
                # except Exception as e:
                #     # Логирование неудачи удаления
                #     logging.error(f"Не удалось удалить сообщение: {e}")

                # Сохранение ID сообщения с фото
                if photo_message:
                    context.user_data['photo_message_id'] = photo_message.message_id

            # Отправка приветственного текста с кнопкой
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
            paid = await is_user_paid(chat_id)
            if paid:
                extra_text = "\n\n✅ Вы уже оплатили курс!"
                button_text = "Начать курс 🎉"
                callback_data_b = 'start_course'
            else:
                extra_text = "\n\n💳 Доступ к курсу платный."
                button_text = f"Купить курс ({os.getenv('COURSE_PRICE', '1990')} ₽)"
                callback_data_b = 'buy_course'
            full_text = welcome_text + extra_text
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(button_text, callback_data=callback_data_b)]])
            welcome_message = await context.bot.send_message(
                chat_id=chat_id,
                text=full_text,
                reply_markup=keyboard
            )
            # Сохранение ID приветственного сообщения
            context.user_data['welcome_message_id'] = welcome_message.message_id

    except Exception as e:
        # Логирование ошибки и отправка сообщения
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

        if query.data == 'start_course':
            # Удаление фото и приветствия при старте курса
            # photo_msg_id = context.user_data.get('photo_message_id')
            welcome_msg_id = context.user_data.get('welcome_message_id')

            # if photo_msg_id:
            #     try:
            #         await context.bot.delete_message(chat_id=chat_id, message_id=photo_msg_id)
            #     except Exception as e:
            #         logging.error(f"Не удалось удалить фото: {e}")

            if welcome_msg_id:
                try:
                    # await context.bot.delete_message(chat_id=chat_id, message_id=welcome_msg_id)
                    await context.bot.edit_message_reply_markup(
                        chat_id=chat_id, message_id=welcome_msg_id, reply_markup=None
                    )
                    logging.info(f"Удалена кнопка с предыдущего сообщения {welcome_msg_id}")
                except Exception as e:
                    # logging.error(f"Не удалось удалить приветствие: {e}")
                    logging.error(f"Не удалось удалить кнопку 'Начать курс': {e}")

            # Удаление кнопки "Начать курс" после нажатия на неё

            # Очистка user_data
            context.user_data.pop('photo_message_id', None)
            context.user_data.pop('welcome_message_id', None)

            # Подтверждение нажатия кнопки (убирает индикатор загрузки)
            await query.answer()

            # Установка task_id = 1 для первого урока
            task_id = 1
        else:
            if not await is_user_paid(chat_id):
                await context.bot.send_message(chat_id=chat_id, text="Доступ к курсу платный. Нажмите /start для оплаты.")
                await query.answer()
                return
            # Парсинг task_id из callback_data
            try:
                task_id = int(query.data)
            except ValueError:
                # Логирование неверного ID и ошибка
                logging.error(f"Неверный task_id: {query.data}")
                await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")
                return

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
        # Сохранение ID текущего сообщения задачи
        context.user_data['last_task_message_id'] = task_message.message_id

        # Подтверждение нажатия
        await query.answer()

    except Exception as e:
        # Общее логирование ошибки
        logging.error(f"Ошибка в функции button: {e}")

async def send_video(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    """
    Отправляет видео для указанной задачи.
    """
    if not update.effective_chat:
        logging.error("Нет effective_chat")
        return

    chat_id = update.effective_chat.id
    video_filename = f'task_{task_id}.mp4'  # Имя файла видео
    video_path = os.path.join('./videos', video_filename)  # Полный путь к видео

    # Проверка существования файла видео
    if not os.path.exists(video_path):
        logging.error(f"Видео файл не найден: {video_path}")
        await context.bot.send_message(chat_id=chat_id, text=f"Видео для задачи {task_id} не найдено.")
        return

    try:
        # Получение размеров видео с помощью ffprobe
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', video_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode == 0:
            # Парсинг ширины и высоты
            width, height = map(int, result.stdout.strip().split('x'))
        else:
            # Fallback размеры если ffprobe failed
            logging.warning(f"Не удалось получить размеры видео {video_path}, используем дефолт")
            width, height = 640, 360

        # Отправка видео
        with open(video_path, 'rb') as video_file:
            await context.bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=f'Задание №{task_id}',  # Подпись к видео
                height=height,
                width=width,
                protect_content=True  # Защита контента от пересылки
            )
    except Exception as e:
        # Логирование и отправка ошибки
        logging.error(f"Ошибка отправки видео для задачи {task_id}: {str(e)}")
        await context.bot.send_message(chat_id=chat_id, text=f"Ошибка отправки видео для задачи {task_id}: {str(e)}")

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
