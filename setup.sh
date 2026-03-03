#!/usr/bin/env bash
# GitHub Repo Surveyor — one-time setup
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "==> Creating Python virtual environment..."
python3 -m venv .venv

echo "==> Activating venv and installing dependencies..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Copy the example env file:"
echo "       cp .env.example .env"
echo "  2. Edit .env and paste your GitHub token:"
echo "       GITHUB_TOKEN=ghp_your_actual_token_here"
echo "  3. Run the app:"
echo "       ./run.sh"
echo ""
echo "To get a GitHub token:"
echo "  → https://github.com/settings/tokens/new"
echo "  → Click 'Generate new token (classic)'"
echo "  → Give it a name like 'surveyor'"
echo "  → Select scope: public_repo  (read-only is enough)"
echo "  → Click 'Generate token' and copy it to .env"
