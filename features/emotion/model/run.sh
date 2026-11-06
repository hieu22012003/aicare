#!/bin/bash
# Launch the facial-expression-recognition webcam demo.

cd "$(dirname "$0")"

if [ -d "venv" ]; then
    ./venv/bin/python -m main
else
    python -m main
fi
