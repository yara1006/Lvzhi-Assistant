.PHONY: dev test build clean docker-up docker-down lint install

# Local development
dev:
	@echo "Starting backend..."
	cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run tests
test:
	cd backend && pytest -v

# Docker
docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

# Clean up
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf backend/.pytest_cache

# Install dependencies
install:
	cd backend && pip install -e ".[dev]"

# Code linting
lint:
	cd backend && python -m ruff check .
