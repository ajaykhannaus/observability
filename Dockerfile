FROM mcr.microsoft.com/azure-functions/python:4-python3.11

WORKDIR /home/site/wwwroot

# Install Python dependencies first (layer-cached until requirements change)
COPY function_app/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy function app entry point and host config
COPY function_app/function_app.py function_app.py
COPY function_app/host.json host.json

# Copy the generator package so it is importable from the container root
COPY generator/ generator/

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    PYTHONUNBUFFERED=1
