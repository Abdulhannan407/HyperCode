#!/bin/bash

echo "🚀 Installing Hyper Code dependencies..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! command -v brew &> /dev/null; then
        echo "Homebrew not found. Please install it first to get portaudio."
    else
        echo "📦 Installing portaudio via Homebrew..."
        brew install portaudio
    fi
fi

# Install python dependencies
pip3 install -r requirements.txt

# Install playwright browsers
playwright install

echo "✅ Installation complete! You can now run: python3 crack.py"
