.PHONY: setup data test lint eval run docker ci compose mcp

setup:        ## install the package with dev tools
	pip install -e ".[dev,postgres,mcp]"

data:         ## build data/market.db (fictional companies, synthetic prices)
	python scripts/generate_data.py

test: data    ## unit, API and red-team tests
	pytest --cov=market_assistant --cov-report=term-missing

lint:         ## style and types
	ruff check .
	mypy market_assistant

eval: data    ## golden-dataset evaluation (fails under 95%)
	python -m market_assistant.eval --min-score 0.95

run: data     ## dashboard + chat widget on http://127.0.0.1:8000
	uvicorn market_assistant.api.app:app --reload

docker:
	docker build -t bilingual-market-assistant .
	docker run --rm -p 8000:8000 --env-file .env bilingual-market-assistant

ci: lint test eval

compose:      ## PostgreSQL stack: database, loader, API on the read-only role
	docker compose up --build

mcp:          ## MCP server over stdio
	python -m market_assistant.api.mcp_server
