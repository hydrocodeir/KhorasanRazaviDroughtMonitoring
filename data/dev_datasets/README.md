This folder is the safe default `DATASETS_ROOT` for local Docker development.

Why it exists:

- Some Windows + Docker Desktop setups fail to mount external drive paths such as `F:\Datasets`.
- Pointing `DATASETS_ROOT` here allows `make dev` to start reliably.

If you want to run the SPI pipelines with real raw datasets:

1. Place the expected raw files under this folder, or
2. Change `DATASETS_ROOT` in `.env` to your real datasets path after confirming Docker Desktop can mount it.

Expected examples:

- `data/dev_datasets/RazaviKhorasanStations.csv`
- `data/dev_datasets/geoBoundaries/`
- `data/dev_datasets/TerraClimate/PPT/`
- `data/dev_datasets/FLDAS2/Rainf_tavg/`
- `data/dev_datasets/AgERA5/precipitation/`
