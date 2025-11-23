# Берем легкую версию Python
FROM python:3.13-slim

# Устанавливаем рабочую папку внутри контейнера
WORKDIR /app/

# Устанавливаем FFmpeg для ffprobe
RUN apt install -y ffmpeg && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip

# Копируем список зависимостей и ставим их
COPY requirements.txt .
RUN python -m pip install -r requirements.txt

# Копируем наши файлы в контейнер
COPY ./ .

# Запускаем основной файл
CMD ["python", "bot.py"]
