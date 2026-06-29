# راهنمای کامل داده و اجرا با Docker

این راهنما کل مسیر کار پروژه را از صفر توضیح می‌دهد: آماده‌سازی محیط، قرار دادن داده‌ها، تولید لایه ایستگاهی، تولید لایه‌های پهنه‌ای، import به پایگاه داده، و در نهایت ساخت و به‌روزرسانی پیش‌بینی‌ها. ترتیب مراحل عمداً کاملاً شماره‌ای نوشته شده تا بتوانید قدم‌به‌قدم جلو بروید.

## 1. هدف این پروژه چیست؟

این پروژه یک داشبورد پایش خشکسالی است که داده‌ها را مستقیم از CSV و GeoJSON در زمان اجرا نمی‌خواند. مسیر درست کار این است:

1. داده خام را در مسیرهای مشخص قرار می‌دهید.
2. با pipelineها خروجی‌های استاندارد پروژه را می‌سازید.
3. خروجی‌ها را با `import_data.py` وارد PostGIS می‌کنید.
4. داشبورد و API داده را از دیتابیس می‌خوانند.
5. برای datasetهای غیرایستگاهی، مدل پیش‌بینی LSTM + Attention هم جداگانه آموزش داده می‌شود.

## 2. پیش‌نیازهای لازم

قبل از هر کاری این موارد باید روی سیستم نصب باشد:

1. `Docker Desktop` یا `Docker Engine`
2. `Docker Compose`
3. `Git`
4. برای Windows:
   از داخل Docker Desktop باید درایوی که داده‌های خام روی آن قرار دارند Share شده باشد.

## 3. ساختار کلی مسیرها را بشناسید

در این پروژه سه مسیر مهم داریم:

1. مسیر داده‌های خام بیرون از پروژه که با متغیر `DATASETS_ROOT` معرفی می‌شود.
2. مسیر cache مربوط به pipeline پهنه‌ای که با `SPI_CACHE_ROOT` معرفی می‌شود.
3. مسیر داده‌های تولیدشده داخل خود پروژه:

```text
./data/import
```

نگاشت این مسیرها داخل کانتینر backend به این شکل است:

```text
${DATASETS_ROOT}  ->  /datasets
${SPI_CACHE_ROOT} ->  /spi-cache
./data            ->  /app/data
```

پس هر وقت در دستورها `/datasets` یا `/spi-cache` می‌بینید، منظور همان مسیرهایی است که شما در `.env` تعریف کرده‌اید.

## 4. فایل تنظیمات Docker را آماده کنید

در ریشه پروژه این دستور را اجرا کنید:

```bash
cp .env.example .env
```

اگر روی PowerShell هستید:

```powershell
Copy-Item .env.example .env
```

سپس فایل `.env` را باز کنید و حداقل این دو مقدار را درست بگذارید:

```env
DATASETS_ROOT=F:\Datasets
SPI_CACHE_ROOT=F:\Datasets\DroughtCache\polygon_spi
```

نمونه بالا فقط مثال است. شما باید مسیر واقعی سیستم خودتان را بگذارید.

## 5. سرویس‌ها را با Docker بالا بیاورید

برای توسعه، دستور اصلی این پروژه این است:

```bash
make dev
```

اگر `make` در دسترس نبود:

```bash
docker compose -f docker-compose.dev.yml up --build
```

بعد از بالا آمدن سرویس‌ها این آدرس‌ها باید در دسترس باشند:

1. فرانت: `http://localhost:8080`
2. health بک‌اند: `http://localhost:8000/health`
3. Swagger: `http://localhost:8000/docs`

## 6. نوع داده‌هایی که پروژه می‌شناسد

این پروژه از سه جریان اصلی داده استفاده می‌کند:

1. داده ایستگاهی برای ساخت dataset ایستگاه‌ها
2. داده بارش و مرزبندی برای ساخت dataset پهنه‌ها
3. داده‌های کمکی ماهانه برای بخش پیش‌بینی

نکته مهم:

1. دیگر تولید فقط روی `SPI-3` ثابت نیست.
2. می‌توانید scale را مثل `1`، `3`، `6`، `12` یا هر عدد ماهانه معتبر دیگر تعیین کنید.
3. همین منطق در بخش prediction هم پشتیبانی می‌شود.

هر کدام مسیر و دستور مخصوص خودشان را دارند.

## 7. داده خام ایستگاهی باید کجا باشد؟

فایل تنظیمات ایستگاهی در این مسیر است:

```text
backend/scripts/station_spi_pipeline/config.json
```

طبق تنظیم فعلی، pipeline ایستگاهی انتظار دارد فایل خام اینجا باشد:

```text
/datasets/RazaviKhorasanStations.csv
```

یعنی روی سیستم شما باید فایلی با این نام در این مسیر قرار بگیرد:

```text
<DATASETS_ROOT>/RazaviKhorasanStations.csv
```

مثال:

```text
F:\Datasets\RazaviKhorasanStations.csv
```

## 8. فایل خام ایستگاهی چه ستون‌هایی باید داشته باشد؟

طبق `config.json` فعلی، این ستون‌ها باید در CSV وجود داشته باشند:

1. `station_id`
2. `station_name`
3. `lon`
4. `lat`
5. `station_elevation`
6. `date`
7. `rrr24`

معنی ستون‌ها:

1. `station_id`: شناسه یکتای ایستگاه
2. `station_name`: نام ایستگاه
3. `lon`: طول جغرافیایی
4. `lat`: عرض جغرافیایی
5. `station_elevation`: ارتفاع
6. `date`: تاریخ
7. `rrr24`: بارش روزانه

## 9. چگونه فقط ورودی ایستگاهی را بررسی کنیم؟

برای اینکه فقط ببینید فایل پیدا می‌شود و pipeline آماده اجرا هست یا نه:

```bash
make station-spi-discover
```

اگر بخواهید برای یک scale مشخص تست کنید:

```bash
make station-spi-discover STATION_SPI_SCALE=6
```

این دستور چیزی را تولید نهایی نمی‌کند و فقط کشف ورودی را تست می‌کند.

## 10. چگونه dataset ایستگاهی را تولید کنیم؟

برای تولید خروجی ایستگاهی:

```bash
make station-spi-generate
```

برای یک scale مشخص:

```bash
make station-spi-generate STATION_SPI_SCALE=6
```

معادل مستقیم Docker:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.station_spi_pipeline.cli \
  --config /app/backend/scripts/station_spi_pipeline/config.json \
  --scale 6
```

## 11. خروجی pipeline ایستگاهی کجا ساخته می‌شود؟

طبق تنظیم فعلی، اگر scale پیش‌فرض اجرا شود، خروجی در این مسیر ساخته می‌شود:

```text
/app/data/import/razavi_khorasan_station_spi3
```

که روی سیستم شما معادل این مسیر است:

```text
data/import/razavi_khorasan_station_spi3
```

معمولاً فایل‌های زیر در آن ساخته می‌شوند:

```text
data/import/razavi_khorasan_station_spi3/data.parquet یا data.csv
data/import/razavi_khorasan_station_spi3/geoinfo.parquet یا geoinfo.geojson
data/import/razavi_khorasan_station_spi3/metadata.json
```

اگر مثلاً scale برابر `6` باشد، dataset و پوشه خروجی به این شکل می‌شود:

```text
data/import/razavi_khorasan_station_spi6
```

## 12. چگونه dataset ایستگاهی را وارد دیتابیس کنیم؟

بعد از تولید خروجی، آن را import کنید:

```bash
make station-spi-import
```

برای import scale مشخص:

```bash
make station-spi-import STATION_SPI_SCALE=6
```

معادل مستقیم:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python /app/import_data.py \
  --replace-dataset \
  --skip-trends \
  --dataset razavi_khorasan_station_spi6
```

## 13. داده خام پهنه‌ای باید کجا باشد؟

فایل تنظیمات پهنه‌ای در این مسیر است:

```text
backend/scripts/spi_pipeline/config.json
```

طبق این فایل، pipeline پهنه‌ای از این ورودی‌ها استفاده می‌کند:

1. مرزها از:

```text
/datasets/geoBoundaries
```

2. داده TerraClimate از:

```text
/datasets/TerraClimate/PPT
```

3. داده FLDAS2 از:

```text
/datasets/FLDAS2/Rainf_tavg
```

4. داده AgERA5 از:

```text
/datasets/AgERA5/precipitation
```

یعنی روی سیستم شما باید ساختاری شبیه این وجود داشته باشد:

```text
<DATASETS_ROOT>/
├── geoBoundaries/
├── TerraClimate/
│   └── PPT/
├── FLDAS2/
│   └── Rainf_tavg/
└── AgERA5/
    └── precipitation/
```

## 14. مرزهای پهنه‌ای باید کجا باشند؟

پروژه مرزها را به صورت بازگشتی از زیرشاخه‌های `geoBoundaries` پیدا می‌کند. پس فایل‌های مرزی را باید زیر این مسیر بگذارید:

```text
<DATASETS_ROOT>/geoBoundaries
```

این مرزها می‌توانند شامل مرزهای اداری و هیدرولوژیک باشند. نام دقیق dataset نهایی بر اساس source و boundary ساخته می‌شود.

## 15. قبل از تولید پهنه‌ها، فقط کشف ورودی را اجرا کنید

برای دیدن اینکه چه sourceها و boundaryها پیدا شده‌اند:

```bash
make spi-discover
```

اگر فقط یک source یا یک boundary خاص را می‌خواهید تست کنید:

```bash
make spi-discover SPI_SOURCE=terraclimate SPI_BOUNDARY=administrative_country
```

اگر بخواهید فقط یک scale مشخص را هم بررسی کنید:

```bash
make spi-discover SPI_SOURCE=terraclimate SPI_BOUNDARY=administrative_country SPI_SCALE=6
```

## 16. چگونه کل پهنه‌ها را تولید کنیم؟

برای تولید همه sourceها و همه boundaryهای قابل کشف:

```bash
make spi-generate
```

اگر فقط یک ترکیب مشخص می‌خواهید:

```bash
make spi-generate SPI_SOURCE=terraclimate SPI_BOUNDARY=administrative_country
```

اگر بخواهید scale مشخص تولید شود:

```bash
make spi-generate SPI_SOURCE=terraclimate SPI_BOUNDARY=administrative_country SPI_SCALE=6
```

معادل مستقیم Docker:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.spi_pipeline.cli \
  --config /app/backend/scripts/spi_pipeline/config.json \
  --source terraclimate \
  --boundary administrative_country \
  --scale 6
```

## 17. خروجی pipeline پهنه‌ای کجا ساخته می‌شود؟

خروجی پهنه‌ای هم داخل پروژه و زیر `data/import` نوشته می‌شود:

```text
data/import/<dataset_key>/
```

برای هر dataset تولیدشده معمولاً این فایل‌ها را خواهید داشت:

```text
data/import/<dataset_key>/data.parquet یا data.csv
data/import/<dataset_key>/geoinfo.parquet یا geoinfo.geojson
data/import/<dataset_key>/metadata.json
```

نمونه datasetهایی که همین حالا آثارشان در مخزن دیده می‌شود:

1. `terraclimate_administrative_razavikhorasan_state_spi3`
2. `terraclimate_administrative_razavikhorasan_county_spi3`
3. `terraclimate_hydrological_hozeh30_spi3`
4. `terraclimate_hydrological_mahdoude_spi3`
5. `agera5_administrative_razavikhorasan_state_spi3`
6. `agera5_administrative_razavikhorasan_county_spi3`
7. `agera5_hydrological_hozeh30_spi3`
8. `agera5_hydrological_mahdoude_spi3`
9. `fldas2_administrative_razavikhorasan_state_spi3`
10. `fldas2_administrative_razavikhorasan_county_spi3`
11. `fldas2_hydrological_hozeh30_spi3`
12. `fldas2_hydrological_mahdoude_spi3`

اگر scale دیگری مثل `6` یا `12` را اجرا کنید، همین datasetها با suffix جدید ساخته می‌شوند؛ مثلاً:

1. `terraclimate_administrative_razavikhorasan_state_spi6`
2. `agera5_hydrological_hozeh30_spi12`

## 18. چگونه datasetهای پهنه‌ای را وارد دیتابیس کنیم؟

بعد از تولید خروجی‌ها، import را اجرا کنید:

```bash
make spi-import
```

معادل مستقیم:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python /app/import_data.py \
  --generated-only \
  --replace-dataset \
  --skip-trends
```

این دستور فقط datasetهایی را import می‌کند که `metadata.json` دارند؛ یعنی همان خروجی‌های pipelineها.

## 19. اگر بخواهیم همه datasetها را یکجا import کنیم چه کنیم؟

اگر داده‌های آماده داخل `data/import` قرار دارند و می‌خواهید همه را import کنید:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python /app/import_data.py --replace
```

اگر فقط بعضی datasetها را می‌خواهید:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python /app/import_data.py \
  --replace-dataset \
  --dataset razavi_khorasan_station_spi6 \
  --dataset terraclimate_administrative_razavikhorasan_state_spi6
```

## 20. قالب datasetهای import-ready چیست؟

اسکریپت `import_data.py` دو حالت را می‌شناسد.

حالت تک dataset:

```text
data/import/data.parquet
data/import/data.csv
data/import/geoinfo.parquet
data/import/geoinfo.geojson
```

حالت چند dataset:

```text
data/import/<dataset_key>/data.parquet
data/import/<dataset_key>/data.csv
data/import/<dataset_key>/geoinfo.parquet
data/import/<dataset_key>/geoinfo.geojson
```

نکته مهم:

1. `data.parquet` نسبت به `data.csv` اولویت دارد.
2. `geoinfo.parquet` نسبت به `geoinfo.geojson` اولویت دارد.
3. پوشه `data/user_data` در نسخه فعلی مسیر runtime نیست و نباید مبنای کار جدید باشد.

## 21. فرمت جدول زمانی برای import باید چگونه باشد؟

فایل `data.csv` یا `data.parquet` باید یکی از این حالت‌های زمانی را داشته باشد:

1. ستون `date`
2. ستون‌های `year` و `month`
3. ستون `yyyymm`

ستون شناسه نیز می‌تواند یکی از این‌ها باشد:

1. `feature_id`
2. `station_id`
3. `region_id`
4. `id`
5. `code`
6. `gid`
7. `fid`
8. `name`

بقیه ستون‌ها به عنوان indexهای زمانی وارد دیتابیس می‌شوند.

## 22. بخش پیش‌بینی برای کدام datasetها فعال است؟

پیش‌بینی فقط برای datasetهای غیرایستگاهی فعال است. یعنی:

1. datasetهای station در prediction وارد نمی‌شوند.
2. datasetهای polygon و boundaryهای غیر station وارد prediction می‌شوند.

پس datasetهای ایستگاهی مثل `razavi_khorasan_station_spi3` یا `razavi_khorasan_station_spi6` فعلاً پیش‌بینی ندارند، ولی datasetهای TerraClimate و AgERA5 و FLDAS2 روی پهنه‌ها می‌توانند forecast داشته باشند.

## 22.1. ترندها برای حالت بدون پیش‌بینی و با پیش‌بینی چگونه هستند؟

در نسخه فعلی پروژه باید بین دو چیز تفاوت بگذاریم:

1. `trend_stats` که در دیتابیس ذخیره می‌شود
2. خط نمایشی `Trend + Forecast` که فقط در نمودار فرانت دیده می‌شود

نکته مهم:

1. ترندهای ذخیره‌شده در جدول `trend_stats` فقط با داده‌های observed و full-history همان dataset محاسبه می‌شوند.
2. این ترندها در import یا با `make precompute-trends` از روی جدول‌های `ts_<dataset_key>` ساخته می‌شوند.
3. forecastهای بخش prediction داخل `trend_stats` وارد نمی‌شوند.
4. وقتی در نمودار فرانت خط `Trend + Forecast` می‌بینید، این یک خط ترکیبی برای visualization است و به معنی ذخیره‌شدن یک trend دوم در دیتابیس نیست.

پس پاسخ کوتاه این است:

1. بله، در UI هر دو نمایش وجود دارد: `Observed Trend` و `Trend + Forecast`.
2. نه، در لایه backend/database فقط trend observed به صورت رسمی precompute و ذخیره می‌شود.

## 23. داده‌های کمکی پیش‌بینی باید کجا ذخیره شوند؟

فایل‌های predictor ماهانه باید در این مسیر باشند:

```text
data/prediction/features/<source_key>/monthly_predictors.parquet
```

نمونه‌ها:

```text
data/prediction/features/terraclimate/monthly_predictors.parquet
data/prediction/features/agera5/monthly_predictors.parquet
data/prediction/features/fldas2/monthly_predictors.parquet
```

## 24. predictor فایل چه ستون‌هایی باید داشته باشد؟

حداقل این ستون لازم است:

1. `date`

بهتر است این ستون هم باشد:

1. `source_key`

و بقیه ستون‌های عددی می‌توانند predictor باشند، مثل:

1. `precip_anom`
2. `tmean_anom`
3. `soil_moisture_anom`
4. `pet_anom`
5. `enso_nino34`

## 25. چگونه predictorهای TerraClimate را بسازیم؟

برای TerraClimate فرض بر این است که کاربر فایل‌های NetCDF خام را از قبل
دانلود کرده و فقط مسیر پوشه یا glob را به پروژه می‌دهد. فایل‌ها بهتر است
الگوی نامی‌ای شبیه این داشته باشند:

1. `TerraClimate_ppt_YYYY.nc`
2. `TerraClimate_tmin_YYYY.nc`
3. `TerraClimate_tmax_YYYY.nc`
4. `TerraClimate_soil_YYYY.nc`
5. `TerraClimate_pet_YYYY.nc`

ساده‌ترین حالت:

```bash
make prediction-build-predictors \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_INPUT="/datasets/TerraClimate"
```

معادل مستقیم:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.prediction.download_predictors \
  --source terraclimate \
  --input "/datasets/TerraClimate"
```

اگر فایل ENSO را هم محلی نگه می‌دارید:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.prediction.download_predictors \
  --source terraclimate \
  --input "/datasets/TerraClimate" \
  --enso-file "/datasets/climate_indices/enso_nino34.csv"
```

اگر `--enso-file` ندهید، اسکریپت خودش به طور پیش‌فرض شاخص Nino 3.4 را از این منبع رسمی NOAA PSL می‌خواند:

```text
https://psl.noaa.gov/data/correlation/nina34.data
```

اگر بخواهید فایل را خودتان دانلود و آرشیو کنید، یک مسیر پیشنهادی این است:

```text
<DATASETS_ROOT>/climate_indices/enso_nino34.csv
```

و فایل محلی باید حداقل این دو ستون را داشته باشد:

1. `date`
2. `enso_nino34`

اگر بخواهید برای هر helper مسیر جدا، و برای هر helper حالت `yes/no` تعریف
کنید، از فایل config استفاده کنید:

```bash
make prediction-build-predictors \
  PREDICTION_SOURCE=terraclimate \
  PREDICTOR_CONFIG="/app/backend/scripts/prediction/predictor_config.example.json"
```

در این فایل می‌توانید برای هر helper مثل بارش، رطوبت خاک، `PET`، `TMIN` و
`TMAX` فولدر جداگانه بدهید.

## 26. چگونه predictorهای AgERA5 را بسازیم؟

برای AgERA5 باید فایل‌های NetCDF خام از قبل زیر `DATASETS_ROOT` وجود داشته باشند. سپس اجرا کنید:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.prediction.download_predictors \
  --source agera5 \
  --input "/datasets/AgERA5/*.nc"
```

اگر variableها در فایل شما با نام دیگری هستند، mapping بدهید:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.prediction.download_predictors \
  --source agera5 \
  --input "/datasets/AgERA5/*.nc" \
  --var-map Precipitation_Flux=precip \
  --var-map Temperature_Air_2m_Mean_24h=tmean
```

## 27. چگونه predictorهای FLDAS2 را بسازیم؟

برای FLDAS2 هم فایل‌های NetCDF خام باید از قبل حاضر باشند:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.prediction.download_predictors \
  --source fldas2 \
  --input "/datasets/FLDAS/*.nc" \
  --var-map Rainf_tavg=precip \
  --var-map SoilMoi10_40cm_inst=soil_moisture
```

اگر mapping پیش‌فرض مناسب باشد، می‌توانید `--var-map` را کامل‌تر یا مطابق فایل‌های خودتان تنظیم کنید.

## 28. چگونه مدل پیش‌بینی را آموزش دهیم؟

سه روش prediction پشتیبانی می‌شوند:

1. `lstm_attention`
2. `random_forest`
3. `xgboost`

نکته مهم:

1. روش‌های `lstm_attention`، `random_forest` و `xgboost` می‌توانند helperها را
   استفاده کنند یا نکنند.
2. برای این سه روش می‌توانید `PREDICTION_USE_HELPERS=yes` یا
   `PREDICTION_USE_HELPERS=no` بدهید.
برای آموزش همه sourceهای غیرایستگاهی:

```bash
make prediction-train
```

برای آموزش بر اساس scale بدون نوشتن مستقیم index:

```bash
make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

اگر بخواهید index را صریح بدهید هم این حالت معتبر است:

```bash
make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_INDEX=spi3
```

برای آموزش با روش مشخص:

```bash
make prediction-train \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_SCALE=6 \
  PREDICTION_METHOD=xgboost
```

یا:

```bash
make prediction-train \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_SCALE=6 \
  PREDICTION_METHOD=random_forest \
  PREDICTION_USE_HELPERS=yes
```

اگر بخواهید helperها عمداً استفاده نشوند:

```bash
make prediction-train \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_SCALE=6 \
  PREDICTION_METHOD=random_forest \
  PREDICTION_USE_HELPERS=no
```

برای اجرای smoke test سبک:

```bash
make prediction-train-smoke PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

یا:

```bash
make prediction-train-smoke PREDICTION_SOURCE=terraclimate PREDICTION_INDEX=spi3
```

## 29. خروجی مدل‌های پیش‌بینی کجا ذخیره می‌شود؟

artifactهای مدل در این مسیر ذخیره می‌شوند:

```text
data/prediction/models
```

ساختار معمول:

```text
data/prediction/models/<model_key>.pt|pkl|json
data/prediction/models/<model_key>_<timestamp>.pt|pkl|json
```

علاوه بر فایل مدل، forecast و evaluation هم در دیتابیس ذخیره می‌شود.

## 30. ترتیب درست اجرای ماهانه از داده خام تا forecast چیست؟

ترتیب پیشنهادی کار این است:

1. داده خام جدید را در `DATASETS_ROOT` قرار دهید.
2. اگر داده ایستگاهی دارید، `make station-spi-generate` را اجرا کنید.
3. اگر داده پهنه‌ای دارید، `make spi-generate` را اجرا کنید.
4. داده‌های تولیدشده را با `make station-spi-import` و `make spi-import` وارد دیتابیس کنید.
5. predictorهای کمکی را به‌روز کنید.
6. `make prediction-self-learn PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6` را اجرا کنید.
7. داشبورد و endpointهای prediction را بررسی کنید.

## 31. اگر بخواهیم workflow ماهانه prediction را یکجا اجرا کنیم چه کنیم؟

برای workflow یکپارچه:

```bash
make prediction-monthly-update \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_SCALE=6 \
  PREDICTION_INPUT="/datasets/TerraClimate"
```

این workflow به صورت کلی این کارها را انجام می‌دهد:

1. predictorها را در صورت امکان به‌روزرسانی می‌کند.
2. datasetهای generated را import می‌کند.
3. مدل prediction را train یا self-learn می‌کند.
4. cache API را پاک می‌کند.

اگر `PREDICTION_INPUT` ندهید، مرحله predictor skip می‌شود و workflow فقط import،
train و cache clear را انجام می‌دهد.

## 32. وضعیت سیستم را بعد از اجرا چگونه چک کنیم؟

این موارد را بررسی کنید:

1. سلامت backend:

```bash
curl http://localhost:8000/health
```

2. فهرست datasetها:

```bash
curl http://localhost:8000/datasets
```

3. Swagger:

```text
http://localhost:8000/docs
```

4. داشبورد:

```text
http://localhost:8080
```

## 33. اگر لازم شد ترندها را جداگانه محاسبه کنیم

برای محاسبه مجدد trendها:

```bash
make precompute-trends
```

یا مستقیم:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python /app/backend/scripts/precompute_trends.py
```

نکته مهم:

1. workflow پیش‌بینی (`prediction-train` و `prediction-monthly-update`) خودش trend جدید در `trend_stats` تولید نمی‌کند.
2. در `prediction-monthly-update` مرحله import با `--skip-trends` اجرا می‌شود تا workflow ماهانه سبک‌تر بماند.
3. اگر بعد از import داده‌های جدید بخواهید trendهای observed هم تازه شوند، `make precompute-trends` را جداگانه اجرا کنید.

## 33.1. داده‌های ENSO را دقیقاً از کجا دانلود کنم؟

منبع پیش‌فرض پروژه این URL رسمی NOAA PSL است:

```text
https://psl.noaa.gov/data/correlation/nina34.data
```

اگر بخواهید پروژه خودش دانلود کند، کافی است `--enso-file` را ندهید.

اگر بخواهید فایل را دستی دانلود و نگهداری کنید:

1. محتوای همین URL را بگیرید.
2. آن را به CSV یا Parquet با ستون‌های `date` و `enso_nino34` تبدیل کنید.
3. سپس در دستور predictor از `--enso-file` استفاده کنید.

نمونه:

```bash
make prediction-build-predictors \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_INPUT="/datasets/TerraClimate" \
  PREDICTION_ENSO_FILE="/datasets/climate_indices/enso_nino34.csv"
```

## 34. اگر بخواهیم سرویس‌ها را متوقف کنیم

برای محیط توسعه:

```bash
make dev-down
```

برای حذف volume دیتابیس محلی:

```bash
make downv
```

## 35. یک چک‌لیست کوتاه نهایی

اگر بخواهیم از صفر تا نتیجه فقط سریع چک کنیم، این لیست کافی است:

1. `.env` را بسازید و `DATASETS_ROOT` و `SPI_CACHE_ROOT` را تنظیم کنید.
2. `make dev` را اجرا کنید.
3. فایل خام ایستگاهی را در `<DATASETS_ROOT>/RazaviKhorasanStations.csv` بگذارید.
4. مرزها را در `<DATASETS_ROOT>/geoBoundaries` بگذارید.
5. فایل‌های TerraClimate و FLDAS2 و AgERA5 را در زیرشاخه‌های تعریف‌شده قرار دهید.
6. `make station-spi-discover` و `make spi-discover` را برای تست ورودی‌ها اجرا کنید.
7. `make station-spi-generate` و `make spi-generate` را اجرا کنید.
8. `make station-spi-import` و `make spi-import` را اجرا کنید.
9. `make prediction-build-predictors PREDICTION_SOURCE=terraclimate PREDICTION_INPUT="/datasets/TerraClimate"` یا دستور مشابه sourceهای دیگر را اجرا کنید.
10. `make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6` را اجرا کنید.
11. `http://localhost:8080` و `http://localhost:8000/docs` را بررسی کنید.

## 36. مهم‌ترین نکات خطاگیری

اگر جایی به مشکل خوردید، اول این موارد را چک کنید:

1. آیا `DATASETS_ROOT` درست تنظیم شده است؟
2. آیا در Windows درایو داده‌ها در Docker Desktop Share شده است؟
3. آیا backend داخل کانتینر واقعاً مسیر `/datasets` را می‌بیند؟
4. آیا فایل‌ها دقیقاً با نام‌ها و مسیرهای تعریف‌شده در `config.json` قرار گرفته‌اند؟
5. آیا بعد از تولید خروجی، مرحله `import_data.py` را هم اجرا کرده‌اید؟
6. آیا برای prediction، فایل `monthly_predictors.parquet` در مسیر درست ساخته شده است؟

اگر بخواهید، در مرحله بعدی می‌توانم همین فایل را به `README.md` لینک کنم یا یک نسخه کوتاه‌تر عملیاتی برای تیم اجرا هم از روی آن بسازم.





























































قبل از شروع این را بدان:
- `make downv` دیتابیس Docker را کامل پاک می‌کند.
- اگر پوشه‌های generated داخل پروژه را هم پاک کنی، خروجی‌های قبلی pipeline و predictorها هم از بین می‌روند.
- داده‌های خام داخل `DATASETS_ROOT` را پاک نکن، مگر این‌که واقعاً بخواهی از صفر مطلق حتی بدون raw data شروع کنی.

**مرحله 1: تنظیم `.env`**
فایل `.env` باید حداقل این‌ها را درست داشته باشد:
- `DATASETS_ROOT`
- `SPI_CACHE_ROOT`

نمونه:
```env
DATASETS_ROOT=F:\Datasets
SPI_CACHE_ROOT=F:\Datasets\DroughtCache\polygon_spi
```

اگر روی ویندوز هستی، مطمئن شو آن درایوها در Docker Desktop share شده‌اند.

**مرحله 2: خاموش‌کردن و پاک‌کردن دیتابیس**
از ریشه پروژه اجرا کن:

```powershell
make dev-down
make downv
```

اگر `make` نداری:
```powershell
docker compose -f docker-compose.dev.yml down
docker compose down -v
```

این مرحله دیتابیس و volumeهایش را پاک می‌کند.

**مرحله 3: پاک‌کردن خروجی‌های تولیدشده داخل پروژه**
اگر می‌خواهی generated data و predictorها و model artifactها هم پاک شوند، این پوشه‌ها را پاک کن:

```powershell
Remove-Item -Recurse -Force .\data\import\*
Remove-Item -Recurse -Force .\data\prediction\features\*
Remove-Item -Recurse -Force .\data\prediction\models\*
```

اگر بعضی پوشه‌ها وجود نداشتند، اشکال ندارد.

اگر می‌خواهی cache پهنه‌ای را هم صفر کنی و `SPI_CACHE_ROOT` روی سیستم‌ات مسیر واقعی است، پوشه cache همان مسیر را هم دستی خالی کن.

**مرحله 4: بالا آوردن سرویس‌ها**
```powershell
make dev
```

بعد این‌ها را چک کن:
- فرانت: `http://localhost:8080`
- بک‌اند: `http://localhost:8000/health`
- Swagger: `http://localhost:8000/docs`

**مرحله 5: اول فقط ایستگاهی را اضافه کن**
فایل خام ایستگاهی باید اینجا باشد:
```text
<DATASETS_ROOT>\RazaviKhorasanStations.csv
```

اول discovery:
```powershell
make station-spi-discover
```

بعد generate:
```powershell
make station-spi-generate
```

بعد import:
```powershell
make station-spi-import
```

بعد trend observed:
```powershell
make precompute-trends
```

نکته:
- برای station prediction نداریم.
- trend فقط observed است.

**مرحله 6: حالا sourceهای پهنه‌ای را یکی‌یکی اضافه کن**
پیشنهاد من این ترتیب است:
1. `terraclimate`
2. `agera5`
3. `fldas2`

برای هر source این چرخه را تکرار کن.

**مرحله 6.1: Terraclimate**
ورودی‌ها:
- مرزها: `<DATASETS_ROOT>\geoBoundaries`
- داده بارش/NetCDFهای source

اول discovery:
```powershell
make spi-discover SPI_SOURCE=terraclimate
```

اگر خواستی scale مشخص:
```powershell
make spi-discover SPI_SOURCE=terraclimate SPI_SCALE=3
```

بعد generate:
```powershell
make spi-generate SPI_SOURCE=terraclimate
```

بعد import:
```powershell
make spi-import
```

بعد trend:
```powershell
make precompute-trends
```

**مرحله 6.2: AgERA5**
```powershell
make spi-discover SPI_SOURCE=agera5
make spi-generate SPI_SOURCE=agera5
make spi-import
make precompute-trends
```

**مرحله 6.3: FLDAS2**
```powershell
make spi-discover SPI_SOURCE=fldas2
make spi-generate SPI_SOURCE=fldas2
make spi-import
make precompute-trends
```

**مرحله 7: بعد از هر import این‌ها را چک کن**
```powershell
curl http://localhost:8000/datasets
curl http://localhost:8000/meta?level=<dataset_key>
```

و روی داشبورد:
- dataset باز می‌شود؟
- نقشه می‌آید؟
- trend observed نمایش دارد؟

**مرحله 8: predictorهای prediction را بساز**
این بخش فقط برای datasetهای غیرایستگاهی است.

بهترین روش این است که برای هر source یک config جدا داشته باشی. بعد برای هر source اجرا کن.

مثلاً برای `terraclimate`:
```powershell
make prediction-build-predictors PREDICTION_SOURCE=terraclimate PREDICTOR_CONFIG="/app/backend/scripts/prediction/predictor_config.terraclimate.json"
```

برای `agera5`:
```powershell
make prediction-build-predictors PREDICTION_SOURCE=agera5 PREDICTOR_CONFIG="/app/backend/scripts/prediction/predictor_config.agera5.json"
```

برای `fldas2`:
```powershell
make prediction-build-predictors PREDICTION_SOURCE=fldas2 PREDICTOR_CONFIG="/app/backend/scripts/prediction/predictor_config.fldas2.json"
```

اگر `ENSO` را محلی داری:
```powershell
... PREDICTION_ENSO_FILE="/datasets/climate_indices/enso_nino34.csv"
```

**مرحله 9: مدل‌های prediction را train کن**
پیشنهاد می‌کنم اول smoke test، بعد train واقعی.

نمونه برای `terraclimate`:
```powershell
make prediction-train-smoke PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=3
make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=3
```

برای چند method:
```powershell
make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=3 PREDICTION_METHOD=lstm_attention
make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=3 PREDICTION_METHOD=random_forest
make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=3 PREDICTION_METHOD=xgboost
```

همین را برای `agera5` و `fldas2` هم تکرار کن.

**مرحله 10: prediction را در داشبورد چک کن**
بعد از train:
- بخش `Prediction system` باید ظاهر شود.
- method selector باید دیده شود.
- `Uncertainty band` selector هم برای مدل‌های جدید باید دیده شود.
- اگر forecast نباشد، trend forecast و uncertainty نباید نمایش داده شود.

**مرحله 11: اگر بخواهی یک workflow تمیز و مرحله‌ای داشته باشی**
من این ترتیب را پیشنهاد می‌کنم:

1. پاک‌سازی کامل
2. `make dev`
3. station generate/import/trend
4. terraclimate generate/import/trend
5. terraclimate predictors
6. terraclimate prediction train
7. agera5 generate/import/trend
8. agera5 predictors
9. agera5 prediction train
10. fldas2 generate/import/trend
11. fldas2 predictors
12. fldas2 prediction train
13. بررسی نهایی داشبورد

**چک‌لیست نهایی**
اگر جایی چیزی نمایش نداد، این‌ها را چک کن:
- آیا dataset واقعاً import شده؟
- آیا `make precompute-trends` بعد از import اجرا شده؟
- آیا predictor parquet ساخته شده؟
- آیا prediction train برای همان `source + scale/index` اجرا شده؟
- آیا dataset ایستگاهی را اشتباهی برای prediction چک نمی‌کنی؟
- آیا forecastهای جدید بعد از تغییر uncertainty/retrain دوباره ساخته شده‌اند؟
