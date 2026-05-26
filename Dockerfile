FROM python:3.11-slim

# Системные зависимости (gcc нужен для сборки aiohttp wheel)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ENV — до COPY, чтобы слой кэшировался
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1

# Зависимости устанавливаем отдельным слоем — кэшируется пока requirements.txt не меняется
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Исходный код
COPY . .

# AGENT_NAME передаётся Railway как переменная окружения каждого сервиса.
# Shell-форма CMD нужна для раскрытия ${AGENT_NAME}.
CMD python main.py --agent ${AGENT_NAME}
