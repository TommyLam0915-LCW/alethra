#!/bin/bash
set -e

echo "=== Starting Alethra Acceptance Environment ==="

# Start PostgreSQL
echo "1. Starting PostgreSQL..."
docker run -d --name alethra-acceptance-db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=ai_investment_lab_acceptance \
  -p 5433:5432 \
  postgres:16-alpine

echo "Waiting for PostgreSQL to be ready..."
sleep 5

# Run migrations
echo "2. Running database migrations..."
cd backend
source venv/bin/activate
export DATABASE_URL="postgresql://postgres:postgres@localhost:5433/ai_investment_lab_acceptance"
export JWT_SECRET_KEY="acceptance-test-secret-minimum-32-chars-long"
export JWT_ALGORITHM="HS256"
export AI_PROVIDER="mock"
export DATA_PROVIDER="demo"
export APP_ENV="development"

python -m alembic upgrade head

# Start backend
echo "3. Starting backend server..."
nohup uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/alethra-backend.log 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > /tmp/alethra-backend.pid

echo "Backend started with PID $BACKEND_PID"
echo "Logs: /tmp/alethra-backend.log"

# Build and start frontend
echo "4. Building frontend for production..."
cd ../frontend
npm run build

echo "5. Starting frontend server..."
nohup npm start -- -p 3001 > /tmp/alethra-frontend.log 2>&1 &
FRONTEND_PID=$!
echo $FRONTEND_PID > /tmp/alethra-frontend.pid

echo "Frontend started with PID $FRONTEND_PID"
echo "Logs: /tmp/alethra-frontend.log"

echo ""
echo "=== Acceptance Environment Ready ==="
echo "Backend: http://localhost:8001"
echo "Frontend: http://localhost:3001"
echo "Database: localhost:5433"
echo ""
echo "To stop: ./stop_acceptance_env.sh"
