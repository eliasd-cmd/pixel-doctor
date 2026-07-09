# 🩺 Pixel Doctor — imagen para Railway/Render/Fly/VPS
FROM python:3.12-slim

WORKDIR /app

# Dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium + librerías de sistema que necesita Playwright
RUN playwright install --with-deps chromium

COPY . .

ENV PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Railway inyecta $PORT; en local usa 8501
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true"]
