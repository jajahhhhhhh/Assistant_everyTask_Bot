FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Create data directories
RUN mkdir -p data exports

# Run the bot
CMD ["python", "bot.py"]
