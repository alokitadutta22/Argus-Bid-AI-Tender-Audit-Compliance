FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Hugging Face Spaces requires apps to run on port 7860
EXPOSE 7860

# Run the Streamlit app
CMD ["streamlit", "run", "tender_audit_platform.py", "--server.port", "7860", "--server.address", "0.0.0.0"]
