.PHONY: setup data test lint eval run docker ci

setup:        ## install the package with dev tools
	pip install -e ".[dev]"

data:         ## build data/market.db (fictional companies, synthetic prices)
	python data/generate.py

test: data    ## unit, API and red-team tests
	pytest --cov=market_assistant --cov-report=term-missing

lint:         ## style and types
	ruff check .
	mypy market_assistant

eval: data    ## golden-dataset evaluation (fails under 95%)
	python eval/run_eval.py --min-score 0.95

run: data     ## dashboard + chat widget on http://127.0.0.1:8000
	uvicorn market_assistant.api:app --reload

docker:
	docker build -t bilingual-market-assistant .
	docker run --rm -p 8000:8000 --env-file .env bilingual-market-assistant

ci: lint test eval
