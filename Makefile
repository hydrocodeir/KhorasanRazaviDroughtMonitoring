.PHONY: dev prod prod-detached prod-ps prod-logs prod-restart prod-import prod-precompute-trends prod-down down downv dev-down precompute-trends spi-discover spi-generate spi-import spi-host-env spi-discover-host spi-generate-host station-spi-discover station-spi-generate station-spi-import station-spi-discover-host station-spi-generate-host

SPI_HOST_VENV = .venv-spi
SPI_HOST_PYTHON_UNIX = $(SPI_HOST_VENV)/bin/python
SPI_HOST_PYTHON_WIN = $(SPI_HOST_VENV)/Scripts/python.exe
SPI_HOST_PYTHON = $(if $(wildcard $(SPI_HOST_PYTHON_WIN)),$(SPI_HOST_PYTHON_WIN),$(SPI_HOST_PYTHON_UNIX))
SPI_HOST_REQUIREMENTS = backend/scripts/spi_pipeline/requirements-host.txt
SPI_HOST_STAMP = $(SPI_HOST_VENV)/.requirements-installed
SPI_SELECT_ARGS = $(if $(SPI_SOURCE),--source $(SPI_SOURCE),) $(if $(SPI_BOUNDARY),--boundary $(SPI_BOUNDARY),)

PROD_COMPOSE = docker compose --env-file .env.prod -f docker-compose.prod.yml

dev:
	@printf "\nFrontend: http://localhost:8080\n"
	@printf "Backend health: http://localhost:8000/health\n"
	@printf "Note: Docker may show 0.0.0.0:8080; open localhost or 127.0.0.1 in your browser.\n\n"
	docker compose -f docker-compose.dev.yml up --build

prod:
	$(PROD_COMPOSE) up --build

prod-detached:
	$(PROD_COMPOSE) up --build -d

prod-ps:
	$(PROD_COMPOSE) ps

prod-logs:
	$(PROD_COMPOSE) logs -f --tail=100

prod-restart:
	$(PROD_COMPOSE) up --build -d

prod-import:
	$(PROD_COMPOSE) exec backend python /app/import_data.py --replace

prod-precompute-trends:
	$(PROD_COMPOSE) exec backend python /app/backend/scripts/precompute_trends.py

down:
	docker compose down

downv:
	docker compose down -v

dev-down:
	docker compose -f docker-compose.dev.yml down

prod-down:
	$(PROD_COMPOSE) down

precompute-trends:
	docker compose -f docker-compose.dev.yml exec backend python /app/backend/scripts/precompute_trends.py

spi-discover:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.spi_pipeline.cli --config /app/backend/scripts/spi_pipeline/config.json --discover $(SPI_SELECT_ARGS)

spi-generate:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.spi_pipeline.cli --config /app/backend/scripts/spi_pipeline/config.json $(SPI_SELECT_ARGS)

spi-import:
	docker compose -f docker-compose.dev.yml exec backend python /app/import_data.py --generated-only --replace-dataset --skip-trends

$(SPI_HOST_STAMP): $(SPI_HOST_REQUIREMENTS)
	uv venv --python python $(SPI_HOST_VENV)
	uv pip install --python $(SPI_HOST_PYTHON) -r $(SPI_HOST_REQUIREMENTS)
	@touch $(SPI_HOST_STAMP)

spi-host-env: $(SPI_HOST_STAMP)

spi-discover-host: $(SPI_HOST_STAMP)
	PYTHONPATH=backend $(SPI_HOST_PYTHON) -m scripts.spi_pipeline.cli --config backend/scripts/spi_pipeline/config.example.json --discover $(SPI_SELECT_ARGS)

spi-generate-host: $(SPI_HOST_STAMP)
	PYTHONPATH=backend $(SPI_HOST_PYTHON) -m scripts.spi_pipeline.cli --config backend/scripts/spi_pipeline/config.example.json $(SPI_SELECT_ARGS)

station-spi-discover:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.station_spi_pipeline.cli --config /app/backend/scripts/station_spi_pipeline/config.json --discover

station-spi-generate:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.station_spi_pipeline.cli --config /app/backend/scripts/station_spi_pipeline/config.json

station-spi-import:
	docker compose -f docker-compose.dev.yml exec backend python /app/import_data.py --replace-dataset --skip-trends --dataset razavi_khorasan_station_spi3

station-spi-discover-host: $(SPI_HOST_STAMP)
	PYTHONPATH=backend $(SPI_HOST_PYTHON) -m scripts.station_spi_pipeline.cli --config backend/scripts/station_spi_pipeline/config.example.json --discover

station-spi-generate-host: $(SPI_HOST_STAMP)
	PYTHONPATH=backend $(SPI_HOST_PYTHON) -m scripts.station_spi_pipeline.cli --config backend/scripts/station_spi_pipeline/config.example.json
