# Master Data - common tasks.
#
# Every target has a plain `docker compose` equivalent shown in the README, for
# anyone without make installed (which on Windows is most people).

.DEFAULT_GOAL := help
.PHONY: help up down logs seed-demo reset-demo migrate roles test test-backend \
        test-frontend test-e2e lint typecheck audit build shell-db worker-job

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Levanta toda la plataforma (db + api + worker + web)
	docker compose up -d --build
	@echo ""
	@echo "  Web:  http://localhost:3000"
	@echo "  API:  http://localhost:8000/docs"
	@echo ""

down: ## Apaga todo (los datos se conservan)
	docker compose down

logs: ## Muestra los logs de todos los servicios
	docker compose logs -f --tail=100

migrate: ## Aplica las migraciones de base de datos
	docker compose run --rm migrate

roles: ## Crea/actualiza los roles de base de datos
	docker compose exec -T api python -m scripts.setup_roles

seed-demo: ## Carga el workspace de demostración (3 países con datos reales)
	docker compose exec -T api python -m scripts.seed_demo --reset

reset-demo: ## Borra la demo y la vuelve a cargar
	docker compose exec -T api python -m scripts.seed_demo --reset

check-effi: ## Analiza reportes reales de Effi sin cargarlos a tu workspace: make check-effi FILES="ruta/*.xlsx"
	.venv/Scripts/python.exe -m scripts.check_real_effi $(FILES)

worker-job: ## Ejecuta un job del worker: make worker-job JOB=refresh_fx
	docker compose exec -T worker python -m worker.main $(JOB)

shell-db: ## Abre psql contra la base de datos
	docker compose exec db psql -U $${POSTGRES_USER:-norte} -d $${POSTGRES_DB:-norte}

test: test-backend test-frontend test-extension ## Corre todos los tests

test-backend: ## Tests de Python (pipeline, API, RLS, NL->SQL)
	.venv/Scripts/python.exe -m pytest -q || python -m pytest -q

test-frontend: ## Tests unitarios del frontend
	cd web && npm test

test-extension: ## Prueba del capturador de Effi (solo node, sin instalar nada)
	node tools/effi-capture/prueba-worker.mjs

test-e2e: ## Tests end-to-end con Playwright (requiere la plataforma corriendo)
	cd web && npx playwright test

lint: ## Lint de backend y frontend
	.venv/Scripts/python.exe -m ruff check . || python -m ruff check .
	cd web && npx eslint .

typecheck: ## Verificación de tipos
	.venv/Scripts/python.exe -m mypy api pipeline worker ai --ignore-missing-imports || true
	cd web && npx tsc --noEmit

audit: ## Auditoría de dependencias y secretos
	.venv/Scripts/python.exe -m pip_audit || pip-audit || echo "pip-audit no instalado"
	cd web && npm audit --omit=dev

build: ## Construye las imágenes sin levantarlas
	docker compose build
