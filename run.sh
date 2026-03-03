#!/usr/bin/env bash
# Start the GitHub Repo Surveyor
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
  echo "❌  Virtual environment not found. Run ./setup.sh first."
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "❌  .env not found. Copy .env.example → .env and add your GITHUB_TOKEN."
  exit 1
fi

source .venv/bin/activate

PORT="${PORT:-8000}"
echo "🚀  Starting GitHub Surveyor at http://localhost:${PORT}"
echo "    Press Ctrl+C to stop."
echo ""

python -m uvicorn surveyor.main:app --host 0.0.0.0 --port "$PORT" --reload
