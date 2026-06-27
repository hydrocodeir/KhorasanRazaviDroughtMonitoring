.PHONY: dev prod prod-detached prod-ps prod-logs prod-restart prod-import prod-precompute-trends prod-down down downv dev-down precompute-trends spi-discover spi-generate spi-import prediction-build-predictors prediction-download-terraclimate prediction-train prediction-train-smoke prediction-self-learn prediction-monthly-update prod-prediction-train prod-prediction-self-learn prod-prediction-monthly-update spi-host-env spi-discover-host spi-generate-host station-spi-discover station-spi-generate station-spi-import station-spi-discover-host station-spi-generate-host

SPI_HOST_VENV = .venv-spi
SPI_HOST_PYTHON_UNIX = $(SPI_HOST_VENV)/bin/python
SPI_HOST_PYTHON_WIN = $(SPI_HOST_VENV)/Scripts/python.exe
SPI_HOST_PYTHON = $(if $(wildcard $(SPI_HOST_PYTHON_WIN)),$(SPI_HOST_PYTHON_WIN),$(SPI_HOST_PYTHON_UNIX))
SPI_HOST_REQUIREMENTS = backend/scripts/spi_pipeline/requirements-host.txt
SPI_HOST_STAMP = $(SPI_HOST_VENV)/.requirements-installed
SPI_SELECT_ARGS = $(if $(SPI_SOURCE),--source $(SPI_SOURCE),) $(if $(SPI_BOUNDARY),--boundary $(SPI_BOUNDARY),)
SPI_SCALE_ARGS = $(if $(SPI_SCALE),--scale $(SPI_SCALE),)
STATION_SPI_SCALE_ARGS = $(if $(STATION_SPI_SCALE),--scale $(STATION_SPI_SCALE),)
PREDICTION_SELECT_ARGS = $(if $(PREDICTION_SOURCE),--source $(PREDICTION_SOURCE),) $(if $(PREDICTION_DATASET),--dataset $(PREDICTION_DATASET),) $(if $(PREDICTION_INDEX),--index $(PREDICTION_INDEX),) $(if $(PREDICTION_SCALE),--scale $(PREDICTION_SCALE),)
PREDICTION_INPUT_ARGS = $(if $(PREDICTION_INPUT),--input $(PREDICTION_INPUT),)
PREDICTION_ENSO_ARG = $(if $(PREDICTION_ENSO_FILE),--enso-file $(PREDICTION_ENSO_FILE),)
STATION_SPI_DATASET = $(if $(STATION_SPI_DATASET_KEY),$(STATION_SPI_DATASET_KEY),$(if $(STATION_SPI_SCALE),razavi_khorasan_station_spi$(STATION_SPI_SCALE),razavi_khorasan_station_spi3))

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
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.spi_pipeline.cli --config /app/backend/scripts/spi_pipeline/config.json --discover $(SPI_SELECT_ARGS) $(SPI_SCALE_ARGS)

spi-generate:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.spi_pipeline.cli --config /app/backend/scripts/spi_pipeline/config.json $(SPI_SELECT_ARGS) $(SPI_SCALE_ARGS)

spi-import:
	docker compose -f docker-compose.dev.yml exec backend python /app/import_data.py --generated-only --replace-dataset --skip-trends

prediction-build-predictors:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.prediction.download_predictors $(if $(PREDICTION_SOURCE),--source $(PREDICTION_SOURCE),--source terraclimate) $(PREDICTION_INPUT_ARGS) $(PREDICTION_ENSO_ARG)

prediction-download-terraclimate: prediction-build-predictors

prediction-train:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.prediction.train_lstm_attention $(PREDICTION_SELECT_ARGS)

prediction-train-smoke:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.prediction.train_lstm_attention --epochs 1 --final-epochs 1 --batch-size 64 $(if $(PREDICTION_SOURCE),--source $(PREDICTION_SOURCE),--source terraclimate) $(if $(PREDICTION_DATASET),--dataset $(PREDICTION_DATASET),) $(if $(PREDICTION_INDEX),--index $(PREDICTION_INDEX),$(if $(PREDICTION_SCALE),--scale $(PREDICTION_SCALE),--index spi3))

prediction-self-learn: prediction-train

prediction-monthly-update:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.prediction.monthly_update $(PREDICTION_SELECT_ARGS) $(if $(PREDICTION_INPUT),--predictor-input $(PREDICTION_INPUT),) $(PREDICTION_ENSO_ARG)

prod-prediction-train:
	$(PROD_COMPOSE) exec backend python -m scripts.prediction.train_lstm_attention $(PREDICTION_SELECT_ARGS)

prod-prediction-self-learn: prod-prediction-train

prod-prediction-monthly-update:
	$(PROD_COMPOSE) exec backend python -m scripts.prediction.monthly_update $(PREDICTION_SELECT_ARGS) $(if $(PREDICTION_INPUT),--predictor-input $(PREDICTION_INPUT),) $(PREDICTION_ENSO_ARG)

$(SPI_HOST_STAMP): $(SPI_HOST_REQUIREMENTS)
	uv venv --python python $(SPI_HOST_VENV)
	uv pip install --python $(SPI_HOST_PYTHON) -r $(SPI_HOST_REQUIREMENTS)
	@touch $(SPI_HOST_STAMP)

spi-host-env: $(SPI_HOST_STAMP)

spi-discover-host: $(SPI_HOST_STAMP)
	PYTHONPATH=backend $(SPI_HOST_PYTHON) -m scripts.spi_pipeline.cli --config backend/scripts/spi_pipeline/config.example.json --discover $(SPI_SELECT_ARGS) $(SPI_SCALE_ARGS)

spi-generate-host: $(SPI_HOST_STAMP)
	PYTHONPATH=backend $(SPI_HOST_PYTHON) -m scripts.spi_pipeline.cli --config backend/scripts/spi_pipeline/config.example.json $(SPI_SELECT_ARGS) $(SPI_SCALE_ARGS)

station-spi-discover:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.station_spi_pipeline.cli --config /app/backend/scripts/station_spi_pipeline/config.json --discover $(STATION_SPI_SCALE_ARGS)

station-spi-generate:
	docker compose -f docker-compose.dev.yml exec backend python -m scripts.station_spi_pipeline.cli --config /app/backend/scripts/station_spi_pipeline/config.json $(STATION_SPI_SCALE_ARGS)

station-spi-import:
	docker compose -f docker-compose.dev.yml exec backend python /app/import_data.py --replace-dataset --skip-trends --dataset $(STATION_SPI_DATASET)

station-spi-discover-host: $(SPI_HOST_STAMP)
	PYTHONPATH=backend $(SPI_HOST_PYTHON) -m scripts.station_spi_pipeline.cli --config backend/scripts/station_spi_pipeline/config.example.json --discover $(STATION_SPI_SCALE_ARGS)

station-spi-generate-host: $(SPI_HOST_STAMP)
	PYTHONPATH=backend $(SPI_HOST_PYTHON) -m scripts.station_spi_pipeline.cli --config backend/scripts/station_spi_pipeline/config.example.json $(STATION_SPI_SCALE_ARGS)
