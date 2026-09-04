.PHONY: setup run-backend run-frontend test test-integration clean

setup:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -q --upgrade pip
	backend/.venv/bin/pip install -q -r backend/requirements.txt
	python3 -m venv frontend/.venv
	frontend/.venv/bin/pip install -q --upgrade pip
	frontend/.venv/bin/pip install -q -r frontend/requirements.txt
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo ""; \
		echo "Created .env - add your GITHUB_TOKEN before running (see README)."; \
	fi

run-backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

run-frontend:
	cd frontend && .venv/bin/streamlit run streamlit_app.py

test:
	cd backend && .venv/bin/python -m pytest tests/ -m "not integration" -v

test-integration:
	cd backend && .venv/bin/python -m pytest tests/ -m integration -v

clean:
	rm -rf backend/.venv frontend/.venv backend/.pytest_cache data/
