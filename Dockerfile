FROM python:3.12-slim

WORKDIR /app

# Залежності окремим шаром — щоб не перевстановлювались при зміні коду
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["./docker/start.sh"]
