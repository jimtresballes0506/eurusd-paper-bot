FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py cloud_start.py config.json ./
RUN useradd --create-home worker && chown worker /app
USER worker
ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py", "run"]
