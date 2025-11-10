import os
import sqlite3
import asyncio
import requests
from telegram import Update, InputFile
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("BOT_TOKEN not set; bot functionality disabled.")
    # Continue without raising an exception; bot will not start.

async def get_m3u8_url(video_id):
    """
    Получить URL .m3u8 для заданного video_id.
    Возвращает None, если URL не найден или запрос завершился ошибкой.
    """
    url = f'https://rutube.ru/api/play/options/{video_id}/'
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        # Ошибка сети / 404 – просто возвращаем None, чтобы вызывающий код мог обработать её
        print(f"Ошибка при запросе к Rutube API: {e}")
        return None

    data = resp.json()
    # Ищем ключи, которые могут содержать m3u8
    if isinstance(data, dict) and 'video' in data and isinstance(data['video'], dict) and 'hls' in data['video']:
        hls_streams = data['video']['hls']
        # Среди разрешений выбрать максимальное (например, по битрейту)
        best_url = None
        best_bitrate = 0
        for resolution, streams in hls_streams.items():
            if isinstance(streams, dict):
                for bitrate_str, stream_url in streams.items():
                    try:
                        bitrate = int(bitrate_str)
                    except ValueError:
                        continue
                    if bitrate > best_bitrate:
                        best_bitrate = bitrate
                        best_url = stream_url
        if best_url:
            return best_url
    # Если не удалось найти URL – возвращаем None
    return None

async def download_rutube_video(video_id, output_file):
    """
    Скачивает видео по video_id и сохраняет в output_file.
    При отсутствии m3u8 URL генерирует RuntimeError.
    """
    m3u8_url = await get_m3u8_url(video_id)
    if not m3u8_url:
        raise RuntimeError("Не удалось получить m3u8 URL для данного video_id")

    ffmpeg_cmd = [
        'ffmpeg',
        '-i', m3u8_url,
        '-c', 'copy',
        output_file
    ]
    # Запускаем ffmpeg асинхронно
    proc = await asyncio.create_subprocess_exec(*ffmpeg_cmd)
    await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg error: не удалось скачать видео")

# Функция обработчик команды /video
async def send_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /video.
    При ошибках скачивания отправляет пользователю сообщение об ошибке.
    """
    chat = update.effective_chat
    if chat is None:
        return
    chat_id = chat.id
    video_id = 'b8a30c7ea8d9a0e87c874dfe79967a12'
    output_file = 'local_video.mp4'
    try:
        await download_rutube_video(video_id, output_file)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"Не удалось скачать видео: {e}")
        return

    # Отправляем видео, если файл успешно скачан
    try:
        with open(output_file, 'rb') as video_file:
            await context.bot.send_video(chat_id=chat_id, video=InputFile(video_file))
    finally:
        # Удаляем временный файл независимо от результата отправки
        if os.path.exists(output_file):
            os.remove(output_file)
    

def main() -> None:
    # If BOT_TOKEN is not set, skip starting the bot (useful for debugging)
    if not BOT_TOKEN:
        print("BOT_TOKEN not set; bot execution skipped.")
        return
    # Введите токен вашего бота
    TOKEN = BOT_TOKEN
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    # Регистрируем обработчик команды /video
    application.add_handler(CommandHandler('video', send_videos))

    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()


# Подключение к базе данных
# conn = sqlite3.connect('sales_in_stories.db')
# cursor = conn.cursor()

    # conn.close()
# async def send_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     conn = sqlite3.connect('sales_in_stories.db')
#     cursor = conn.cursor()
    
#     cursor.execute("SELECT task_id, task_link FROM tasks ORDER BY task_id ASC")
#     videos = cursor.fetchall()
    
#     for task_id, task_link in videos:
#         await update.message.chat.send_message(f"Video {task_id}: {task_link}")
    
#     conn.close()


# def main() -> None:
#     application = ApplicationBuilder().token(BOT_TOKEN).build()
    
#     application.add_handler(CommandHandler('video', send_videos))
    
#     application.run_polling()

# if __name__ == '__main__':
#     main()

# from telegram.ext import Updater, CommandHandler, CallbackContext


    # cursor.execute('SELECT task_link FROM tasks ORDER BY task_id ASC')
    # videos = cursor.fetchall()
    # # for task_id, task_link in videos:

    # #     await update.message.chat.send_message(f"Video {task_id}: {task_link}")
    
    # for task_link in videos:
    #     resp = requests.get(task_link)
    #     data = resp.json()
    #     m3u8_url = data['video_balancer']['m3u8']

    #     # Скачиваем m3u8 плейлист и сегменты, собираем в файл через ffmpeg
    #     # Тут используется команда ffmpeg для скачивания и объединения
    #     ffmpeg_cmd = [
    #         'ffmpeg',
    #         '-i', m3u8_url,
    #         '-c', 'copy',
    #         'local_video.mp4'
    #     ]
    #     subprocess.run(ffmpeg_cmd, check=True)
