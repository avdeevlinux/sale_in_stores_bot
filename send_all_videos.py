import os
import sqlite3
from dotenv import load_dotenv
from telegram import Update, InputMediaVideo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("Не указан BOT_TOKEN в переменных окружения")

async def send_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = sqlite3.connect('sales_in_stories.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT task_id, task_link FROM tasks ORDER BY task_id ASC")
    videos = cursor.fetchall()
    
    for task_id, task_link in videos:
        await update.message.chat.send_message(f"Video {task_id}: {task_link}")
    
    conn.close()


def main() -> None:
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler('video', send_videos))
    
    application.run_polling()

if __name__ == '__main__':
    main()