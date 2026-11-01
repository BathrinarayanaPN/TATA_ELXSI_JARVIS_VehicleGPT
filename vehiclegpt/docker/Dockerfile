# Multi-arch (linux/amd64 + linux/arm64) image for the VehicleGPT app stack.
# docker buildx build --platform linux/arm64,linux/amd64 -t vehiclegpt -f docker/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x run_demo.sh
EXPOSE 8501
CMD ["./run_demo.sh"]
