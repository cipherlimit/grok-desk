#!/usr/bin/env bash
# Restart the desk and the plan B publisher in the background.
cd "$(dirname "$0")"
pkill -f "\.venv/bin/python main.py" ; pkill -f "\.venv/bin/python publisher.py" ; sleep 1
mkdir -p logs
nohup .venv/bin/python main.py >> logs/main.out 2>&1 &  echo "main.py pid $!"
nohup .venv/bin/python publisher.py >> logs/publisher.out 2>&1 &  echo "publisher.py pid $!"
