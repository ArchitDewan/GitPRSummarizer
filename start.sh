#!/bin/bash

# Navigate to the project directory
cd /Users/architdewan/Documents/GitPrSummarizer

# Activate the virtual environment
source .venv/bin/activate

# Run the application
uvicorn main:app --reload --port 8000

