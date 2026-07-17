#!/bin/bash
set -e

echo "=== AI Chief of Staff Setup ==="

echo ""
echo "1. Setting up Python backend..."
cd backend
python3 -m venv venv 2>/dev/null || python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..

echo ""
echo "2. Setting up Node.js frontend..."
cd frontend
npm install
cd ..

echo ""
echo "3. Creating .env files..."
[ ! -f backend/.env ] && cp backend/.env.example backend/.env
echo "   Done."

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "To run the project:"
echo ""
echo "  1. Start PostgreSQL (or use Docker):"
echo "     docker compose up -d postgres"
echo ""
echo "  2. Start Ollama (or use Docker):"
echo "     docker compose up -d ollama"
echo "     docker compose exec ollama ollama pull qwen:7b"
echo ""
echo "  3. Run the backend:"
echo "     cd backend && source venv/bin/activate && python run.py"
echo ""
echo "  4. Run the frontend (in another terminal):"
echo "     cd frontend && npm run dev"
echo ""
echo "  Or use Docker Compose for everything:"
echo "     docker compose up --build"
echo ""
