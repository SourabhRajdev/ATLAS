#!/bin/bash
# Install voice interface dependencies for ATLAS

set -e

echo "🎤 Installing ATLAS Voice Interface Dependencies"
echo "================================================"
echo ""

# Detect OS
OS="$(uname -s)"

# Install PortAudio (required for pyaudio)
echo "📦 Installing PortAudio..."
if [[ "$OS" == "Darwin" ]]; then
    # macOS
    if command -v brew &> /dev/null; then
        brew install portaudio
    else
        echo "❌ Homebrew not found. Please install: https://brew.sh"
        exit 1
    fi
elif [[ "$OS" == "Linux" ]]; then
    # Linux
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y portaudio19-dev python3-pyaudio
    elif command -v yum &> /dev/null; then
        sudo yum install -y portaudio-devel
    else
        echo "❌ Package manager not found. Please install portaudio manually."
        exit 1
    fi
else
    echo "⚠️  Unknown OS: $OS"
    echo "Please install PortAudio manually."
fi

echo ""
echo "📦 Installing Python packages..."

# Core dependencies (required)
pip install pyaudio SpeechRecognition pyttsx3

# Optional: Best quality (recommended)
echo ""
echo "📦 Installing optional high-quality voice packages..."
pip install deepgram-sdk elevenlabs keyboard || echo "⚠️  Optional packages failed (non-critical)"

echo ""
echo "✅ Voice interface dependencies installed!"
echo ""
echo "Next steps:"
echo "1. Add API keys to .env (optional, for best quality):"
echo "   ATLAS_DEEPGRAM_API_KEY=your_key"
echo "   ATLAS_ELEVENLABS_API_KEY=your_key"
echo ""
echo "2. Start ATLAS and type: /voice"
echo ""
echo "3. Press SPACE to talk, Q to quit"
echo ""
echo "See VOICE.md for complete documentation."
