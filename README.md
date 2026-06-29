# Iran Drought Monitoring

سامانه پایش خشکسالی ایران با `FastAPI`، `PostGIS`، `Redis` و نقشه تعاملی `Leaflet`.

راهنمای مرحله‌به‌مرحله فارسی اجرای داده و Docker:
[DATA_WORKFLOW_GUIDE.fa.md](./DATA_WORKFLOW_GUIDE.fa.md)

## امکانات

- نمایش نقشه و پنل آماری برای هر لایه
- پشتیبانی از چند لایه داده مثل station / province / county
- بارگذاری داده فقط یک‌بار و ذخیره در PostGIS
- محاسبه ترندها با Mann-Kendall و Sen's slope
- کش Redis به همراه کش درون‌حافظه‌ای

## پیش‌نیازها

- Docker
- Docker Compose
- Git

## ساختار داده

داده‌ها باید داخل `data/import` قرار بگیرند.

### حالت تک‌لایه

```text
data/import/data.parquet   # اولویت اول
data/import/data.csv       # جایگزین
data/import/geoinfo.geojson
```

این حالت به صورت پیش‌فرض با `dataset_key=station` وارد می‌شود.

### حالت چندلایه

```text
data/import/<dataset_key>/data.parquet
data/import/<dataset_key>/data.csv
data/import/<dataset_key>/geoinfo.parquet  # اولویت اول
data/import/<dataset_key>/geoinfo.geojson # جایگزین
```

`dataset_key` فقط می‌تواند شامل حروف، عدد و `_` باشد.

### قالب CSV

CSV باید یکی از این حالت‌ها را داشته باشد:

- `date`
- `year` + `month`
- `yyyymm`

ستون شناسه می‌تواند یکی از این‌ها باشد:

- `feature_id`
- `station_id`
- `region_id`
- `id`
- `code`
- `gid`
- `fid`
- `name`

بقیه ستون‌ها به عنوان شاخص‌های زمانی ذخیره می‌شوند.

> پوشه `data/user_data` در نسخه فعلی فقط برای سازگاری قدیمی است و در runtime استفاده نمی‌شود.

## اجرای محلی با Docker

### اجرا

قبل از اولین اجرا، فایل تنظیمات dev را بساز:

```bash
cp .env.example .env
```

سپس در `.env` مقدار `DATASETS_ROOT` را به مسیر واقعی datasetها روی سیستم خودت
تغییر بده. اگر روی Windows و Docker Desktop هستی، معمولاً چیزی شبیه
`F:\Datasets` درست است.

```bash
make dev
```

یا:

```bash
docker compose -f docker-compose.dev.yml up --build
```

### آدرس‌ها

- Frontend: `http://localhost:8080`
- Backend health: `http://localhost:8000/health`
- Swagger: `http://localhost:8000/docs`

### توقف

```bash
make dev-down
```

### حذف کامل دیتابیس محلی

```bash
make downv
```

## وارد کردن داده‌ها

ابتدا `data.parquet` (با اولویت) یا `data.csv` به همراه `geoinfo.geojson` را در مسیر درست قرار بده، سپس:

```bash
docker compose -f docker-compose.dev.yml exec backend python /app/import_data.py --replace
```

در سرور:

```bash
make prod-import
```

اگر خواستی مسیر دیگری بدهی:

```bash
python import_data.py --data-dir /path/to/import
```

## تولید داینامیک SPI پهنه‌ای از NetCDF

مولد مستقل در `backend/scripts/spi_pipeline` همه فایل‌های مکانی زیر
`geoBoundaries` را به‌صورت بازگشتی کشف می‌کند. هر منبع بارش در
`config.json` تعریف می‌شود؛ بنابراین افزودن منبع جدید فقط به یک مدخل تنظیمات
نیاز دارد.

روش محاسبه شامل تبدیل بارش به mm، تجمیع ماهانه، میانگین مساحت‌وزن با تقاطع
دقیق سلول و پلیگون، و سپس استانداردسازی ناپارامتری SDAT در مقیاس زمانی
دلخواه است. خروجی سری زمانی و هندسه هر ترکیب منبع/مرز/scale به‌صورت
Parquet/GeoParquet فشرده ذخیره می‌شود.
پلیگون‌هایی که کمتر از ۸۰٪ آن‌ها داخل پوشش شبکه بارش باشد حذف می‌شوند تا
SPI بر مبنای بخش کوچکی از یک ناحیه گزارش نشود.

در داده فعلی، TerraClimate جهانی است؛ فایل‌های FLDAS2 موجود فقط محدوده
تقریبی `42–64°E` و `24–41°N` را پوشش می‌دهند. همچنین اکتبر و دسامبر ۲۰۲۴
FLDAS2 روزهای مفقود دارند، بنابراین آن ماه‌ها و پنجره‌های SPI وابسته به
آن‌ها به‌صورت `No Data` ذخیره می‌شوند.

کشف ورودی‌ها بدون محاسبه:

```bash
make spi-discover
```

برای یک scale مشخص:

```bash
make spi-discover SPI_SCALE=6
```

تولید یا به‌روزرسانی همه خروجی‌ها:

```bash
make spi-generate
```

برای یک scale مشخص:

```bash
make spi-generate SPI_SCALE=6
```

اگر Docker Desktop روی Windows استفاده می‌شود، در Settings بخش
Resources/File Sharing دسترسی درایوی که `DATASETS_ROOT` روی آن قرار دارد
(برای این پروژه درایو `F:`) باید فعال باشد. در غیر این صورت bind mount
`/datasets` داخل کانتینر خالی دیده می‌شود. اجرای مستقیم روی WSL/Linux می‌تواند
از `config.example.json` و متغیرهای محیطی `DATASETS_ROOT` و `SPI_CACHE_ROOT`
استفاده کند:

```bash
make spi-host-env
make spi-discover-host
make spi-generate-host
```

دستورهای host از محیط مجزای `.venv-spi` استفاده می‌کنند و به محیط Conda
فعال (`base`) وابسته نیستند. در اولین اجرا وابستگی‌های مخصوص pipeline
خودکار نصب می‌شوند. خروجی host در `data/import` و cache در
`data/spi_cache` پروژه نوشته می‌شود.

برای اولین اجرا بهتر است یک ترکیب کوچک را انتخاب کنید:

```bash
make spi-generate-host \
  SPI_SOURCE=terraclimate \
  SPI_BOUNDARY=administrative_country \
  SPI_SCALE=6
```

بعد از اطمینان از خروجی، State و County یا FLDAS2 را جداگانه اجرا کنید.

اجرای انتخابی:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.spi_pipeline.cli \
  --config /app/backend/scripts/spi_pipeline/config.json \
  --source terraclimate \
  --boundary administrative_country \
  --scale 6
```

کش ماهانه و وزن‌های هندسی باعث می‌شود فایل‌های بدون تغییر دوباره محاسبه
نشوند. اضافه‌شدن فایل، تغییر فایل تاریخی، یا اضافه‌شدن shapefile جدید
خودکار تشخیص داده می‌شود.

پس از تولید، dataset تغییرکرده را وارد PostGIS کنید:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python /app/import_data.py \
  --dataset terraclimate_administrative_country_spi6 \
  --replace-dataset \
  --skip-trends
```

برای تولید چند scale به‌صورت پیش‌فرض، در فایل‌های
`backend/scripts/spi_pipeline/config.json` و
`backend/scripts/station_spi_pipeline/config.json` از کلید `scales` استفاده
کنید:

```json
"scales": [3, 6, 12]
```

اگر `--scale` در CLI بدهید، فقط همان scaleها تولید می‌شوند و روی `scales`
تنظیم‌شده در config override اعمال می‌شود.

## تولید داینامیک SPI ایستگاهی

pipeline ایستگاهی هم دیگر روی `SPI-3` قفل نیست و می‌تواند با scale دلخواه
خروجی بسازد. نام dataset و ستون شاخص به‌صورت خودکار با scale هماهنگ می‌شود.

کشف ورودی:

```bash
make station-spi-discover
make station-spi-discover STATION_SPI_SCALE=6
```

تولید خروجی:

```bash
make station-spi-generate
make station-spi-generate STATION_SPI_SCALE=6
```

نمونه اجرای مستقیم:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.station_spi_pipeline.cli \
  --config /app/backend/scripts/station_spi_pipeline/config.json \
  --scale 6
```

import خروجی ایستگاهی:

```bash
make station-spi-import
make station-spi-import STATION_SPI_SCALE=6
```

برای scale غیرپیش‌فرض، dataset به صورت خودکار با suffix جدید ساخته می‌شود؛
مثلاً `razavi_khorasan_station_spi6`.

یا همه خروجی‌های تولیدشده توسط pipeline را بدون دست‌زدن به datasetهای دیگر
وارد کنید:

```bash
make spi-import
```

داشبورد dataset را از `/datasets` کشف می‌کند و آن را خودکار در فهرست انتخاب
نمایش می‌دهد.

## سیستم پیش‌بینی LSTM + Attention

بخش پیش‌بینی برای همه datasetهای غیرایستگاهی فعال می‌شود؛ یعنی هر لایه‌ای که
`boundary_key=station` نباشد، چه الان وجود داشته باشد و چه بعداً اضافه شود.
منطق مدل مشابه داشبورد UCI است: ۱۸ ماه گذشته به مدل داده می‌شود، LSTM وابستگی
زمانی را یاد می‌گیرد، attention روی ماه‌های مهم‌تر وزن می‌دهد، و خروجی به‌صورت
autoregressive برای ۱۲ ماه آینده تولید می‌شود.

خروجی‌ها در جدول‌های `prediction_*` ذخیره می‌شوند و API در زمان درخواست فقط
forecast و معیارهای ارزیابی آماده را می‌خواند؛ بنابراین آموزش مدل در مسیر
runtime داشبورد انجام نمی‌شود.

### داده‌های مورد نیاز برای پیش‌بینی

مدل پیش‌بینی برای هر dataset غیرایستگاهی دو نوع داده لازم دارد:

1. **سری هدف داخل دیتابیس داشبورد**
   همان شاخصی که قرار است پیش‌بینی شود؛ مثل `spi3`, `spi6`, `spei3` یا `ssi3`.
   این داده از جدول‌های `ts_<dataset_key>` خوانده می‌شود و با pipeline/import
   اصلی پروژه ساخته شده است.

2. **predictorهای ماهانه کمکی برای هر منبع داده**
   این داده‌ها برای همه boundaryهای یک `source_key` مشترک هستند. برای مثال همه
   datasetهای TerraClimate، چه County باشند چه Basin، از یک فایل کمکی استفاده
   می‌کنند:

   ```text
   data/prediction/features/<source_key>/monthly_predictors.parquet
   ```

فایل predictor باید حداقل ستون `date` داشته باشد. ستون `source_key` اختیاری
است ولی توصیه می‌شود. بقیه ستون‌های عددی به‌عنوان ورودی LSTM استفاده می‌شوند.
اگر این فایل هنوز وجود نداشته باشد، script آموزش همچنان از تاریخچه خود شاخص
خشکسالی، lagها و seasonality استفاده می‌کند؛ اما برای مدل کامل چندمتغیره،
predictorها باید دانلود و ساخته شوند.

ورودی‌های مدل adaptive هستند:

- ورودی پایه همیشه وجود دارد: مقدار خود شاخص هدف در پنجره ۱۸ ماهه،
  `y_lag_1`, `y_lag_3`, `y_lag_6`, `month_sin`, `month_cos`.
- اگر همه predictorهای کمکی آماده باشند، مدل چندمتغیره کامل آموزش می‌بیند.
- اگر فقط بخشی از predictorها آماده باشد، همان ستون‌های موجود و دارای پوشش
  کافی استفاده می‌شوند.
- اگر هیچ predictor کمکی آماده نباشد، مدل همچنان با تاریخچه شاخص، lagها و
  seasonality forecast تولید می‌کند.
- ستون کمکی که پوشش زمانی خیلی کم داشته باشد، خودکار کنار گذاشته می‌شود تا
  کیفیت training با مقدارهای مصنوعی خراب نشود.

### نیازمندی داده برای datasetهای فعلی

| Datasetها | `source_key` | فایل predictor لازم | داده‌هایی که باید دانلود/آماده شود |
| --- | --- | --- | --- |
| `terraclimate_administrative_razavikhorasan_state_spi3`, `terraclimate_administrative_razavikhorasan_county_spi3`, `terraclimate_hydrological_hozeh30_spi3`, `terraclimate_hydrological_mahdoude_spi3` | `terraclimate` | `data/prediction/features/terraclimate/monthly_predictors.parquet` | TerraClimate `ppt`, `tmin`, `tmax`, `soil`, `pet` + ENSO/Nino3.4 |
| `agera5_administrative_razavikhorasan_state_spi3`, `agera5_administrative_razavikhorasan_county_spi3`, `agera5_hydrological_hozeh30_spi3`, `agera5_hydrological_mahdoude_spi3` | `agera5` | `data/prediction/features/agera5/monthly_predictors.parquet` | AgERA5 precipitation و temperature از فایل‌های NetCDF محلی + ENSO/Nino3.4 |
| `fldas2_administrative_razavikhorasan_state_spi3`, `fldas2_administrative_razavikhorasan_county_spi3`, `fldas2_hydrological_hozeh30_spi3`, `fldas2_hydrological_mahdoude_spi3` | `fldas2` | `data/prediction/features/fldas2/monthly_predictors.parquet` | FLDAS2 precipitation، رطوبت خاک لایه‌های سطحی/میانی، دمای هوا از فایل‌های NetCDF محلی + ENSO/Nino3.4 |
| `razavi_khorasan_station_spi3`, `razavi_khorasan_station_spi6`, ... | `razavi_khorasan_stations` | ندارد | فعلاً prediction برای datasetهای ایستگاهی اجرا نمی‌شود. |

### predictorهای پیشنهادی

برای نزدیک‌شدن به منطق UCI، بهتر است برای هر `source_key` این متغیرها آماده
شوند:

- `precip_anom`: anomaly بارش ماهانه نسبت به دوره مرجع
- `tmean_anom`: anomaly دمای میانگین ماهانه نسبت به دوره مرجع
- `soil_moisture_anom`: anomaly رطوبت خاک؛ برای FLDAS2 بهتر است لایه‌های
  `0-10 cm` و `10-40 cm` یا نزدیک‌ترین لایه‌های موجود استفاده شوند
- `pet_anom`: anomaly تبخیر-تعرق پتانسیل، اگر منبع آن را داشته باشد
- `enso_nino34`: شاخص ENSO/Nino3.4 ماهانه
- `month_sin` و `month_cos` لازم نیست دانلود شوند؛ script آموزش آن‌ها را
  خودکار برای seasonality می‌سازد.

دوره مرجع پیش‌فرض برای anomaly در script برابر `1981-01-01` تا
`2010-12-31` است. اگر برای منبعی دوره مرجع دیگری مناسب‌تر است، می‌توان با
`--baseline-start` و `--baseline-end` تغییرش داد.

### آماده‌سازی TerraClimate

برای TerraClimate هم مثل AgERA5 و FLDAS2 فرض بر این است که فایل‌های NetCDF خام
را قبلاً دانلود کرده‌اید و فقط مسیر پوشه یا glob را به script می‌دهید. فایل‌ها
باید الگوی نامی‌ای شبیه `TerraClimate_ppt_YYYY.nc`، `TerraClimate_tmin_YYYY.nc`،
`TerraClimate_tmax_YYYY.nc`، `TerraClimate_soil_YYYY.nc` و
`TerraClimate_pet_YYYY.nc` داشته باشند.

نمونه اجرا با target جدید:

```bash
make prediction-build-predictors \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_INPUT="/datasets/TerraClimate"
```

یا مستقیم:

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

خروجی:

```text
data/prediction/features/terraclimate/monthly_predictors.parquet
```

اگر بخواهید برای هر helper مسیر جدا تعریف کنید یا بعضی helperها را خاموش
کنید، از فایل config استفاده کنید:

```bash
make prediction-build-predictors \
  PREDICTION_SOURCE=terraclimate \
  PREDICTOR_CONFIG="/app/backend/scripts/prediction/predictor_config.example.json"
```

در این config می‌توانید برای هر helper جداگانه `enabled: true/false` و مسیر
پوشه‌ی خودش را بدهید؛ مثلاً بارش در یک فولدر، رطوبت خاک در فولدر دیگر، و
`ENSO` در فایل مستقل.

### آماده‌سازی AgERA5

برای AgERA5 فرض بر این است که فایل‌های NetCDF خام را قبلاً در `DATASETS_ROOT`
یا مسیر مشابه دانلود کرده‌اید. script خودش از روی NetCDFها predictor ماهانه
می‌سازد. mapping پیش‌فرض:

- `Precipitation_Flux=precip`
- `Temperature_Air_2m_Mean_24h=tmean`

نمونه اجرا:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.prediction.download_predictors \
  --source agera5 \
  --input "/datasets/AgERA5/*.nc"
```

اگر نام variableها در فایل‌های شما فرق دارد، mapping را صریح بدهید:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.prediction.download_predictors \
  --source agera5 \
  --input "/datasets/AgERA5/*.nc" \
  --var-map Precipitation_Flux=precip \
  --var-map Temperature_Air_2m_Mean_24h=tmean
```

خروجی:

```text
data/prediction/features/agera5/monthly_predictors.parquet
```

### آماده‌سازی FLDAS2

برای FLDAS2 هم فایل‌های NetCDF خام باید از قبل در مسیر datasetها موجود باشند.
mapping پیش‌فرض:

- `Rainf_tavg=precip`
- `SoilMoi0_10cm_inst=soil_moisture_top`
- `SoilMoi10_40cm_inst=soil_moisture_10_40`
- `Tair_f_tavg=tair`

نمونه اجرا:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.prediction.download_predictors \
  --source fldas2 \
  --input "/datasets/FLDAS/*.nc" \
  --var-map Rainf_tavg=precip \
  --var-map SoilMoi10_40cm_inst=soil_moisture
```

خروجی:

```text
data/prediction/features/fldas2/monthly_predictors.parquet
```

### اضافه‌کردن predictor برای datasetهای آینده

اگر بعداً dataset جدیدی اضافه شود، کافی است `metadata.json` آن `source_key`
درست داشته باشد. prediction به‌صورت خودکار datasetهای غیرایستگاهی را پیدا
می‌کند. سپس یکی از این دو حالت لازم است:

- اگر `source_key` قبلاً predictor دارد، همان فایل مشترک استفاده می‌شود.
- اگر `source_key` جدید است، یک فایل زیر مسیر زیر بسازید:

```text
data/prediction/features/<new_source_key>/monthly_predictors.parquet
```

قالب حداقلی:

| ستون | توضیح |
| --- | --- |
| `date` | تاریخ ماهانه؛ بهتر است روز اول ماه باشد، مثل `2024-01-01` |
| `source_key` | نام منبع؛ مثل `terraclimate` یا `agera5` |
| ستون‌های عددی | هر predictor عددی مانند `precip_anom`, `tmean_anom`, `soil_moisture_anom`, `enso_nino34` |

نام ستون‌ها آزاد است، اما بهتر است از نام‌های شفاف و پایدار استفاده شود. همه
ستون‌های عددی به مدل داده می‌شوند.

### آموزش و انتشار forecast

اکنون سه روش prediction پشتیبانی می‌شوند:

- `lstm_attention`
- `random_forest`
- `garch`

روش `garch` فقط از خود سری زمانی شاخص استفاده می‌کند و helper نمی‌خواهد.
روش‌های `lstm_attention` و `random_forest` می‌توانند helperها را با
`PREDICTION_USE_HELPERS=yes|no` استفاده یا نادیده بگیرند.

همه منابع و همه شاخص‌های drought غیرایستگاهی:

```bash
make prediction-train
```

اجرای انتخابی:

```bash
make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

اگر خواستی index را صریح بدهی هم این حالت معتبر است:

```bash
make prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_INDEX=spi3
```

اجرای روش مشخص:

```bash
make prediction-train \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_SCALE=6 \
  PREDICTION_METHOD=garch
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

در سرور:

```bash
make prod-prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

یا:

```bash
make prod-prediction-train PREDICTION_SOURCE=terraclimate PREDICTION_INDEX=spi3
```

اجرای کوچک برای اطمینان از کل مسیر، با epoch کم:

```bash
make prediction-train-smoke PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

یا:

```bash
make prediction-train-smoke PREDICTION_SOURCE=terraclimate PREDICTION_INDEX=spi3
```

اگر کانتینر backend قبل از اضافه‌شدن `torch` ساخته شده باشد، اول rebuild کنید:

```bash
docker compose -f docker-compose.dev.yml up --build -d backend celery_worker
```

مدل‌ها به‌صورت pooled برای هر `source_key + index` آموزش داده می‌شوند و برای
هر dataset همان source forecast جدا ذخیره می‌شود. از این به بعد forecastها به
تفکیک `method_name` هم ذخیره می‌شوند و داشبورد می‌تواند چند روش را هم‌زمان روی
نمودار نمایش دهد. ارزیابی با backtest روی ماه‌های انتهایی انجام می‌شود و
معیارهای `MAE`, `RMSE`, `bias`, `R²`, `correlation` و دقت کلاس خشکسالی برای
leadهای ۱ تا ۱۲ در داشبورد نمایش داده می‌شوند.

### نسخه‌بندی مدل‌ها

هر اجرای training دو artifact می‌سازد:

```text
data/prediction/models/<model_key>_<YYYYMMDDTHHMMSSZ>.pt
data/prediction/models/<model_key>.pt
```

فایل تاریخ‌دار برای audit و rollback است. فایل بدون تاریخ، نسخه latest است و
برای warm-start در اجرای بعدی استفاده می‌شود. جدول
`prediction_model_versions` همه نسخه‌ها را با مسیر artifact، زمان training،
ستون‌های ورودی، پارامترهای training و metricهای خلاصه نگه می‌دارد.

### وضعیت تازگی forecast در داشبورد

پنل Prediction برای هر feature این موارد را نشان می‌دهد:

- `Fresh` یا `Needs refresh`
- ماه issue مدل
- آخرین ماه مشاهده‌شده در داده واقعی
- تعداد نسخه‌های مدل
- تعداد helperهایی که مدل واقعاً استفاده کرده است
- metricهای backtest و realized feedback

اگر `observed_max_month` از `issue_month` جلوتر باشد، یعنی داده واقعی جدیدتر
وارد شده ولی forecast هنوز دوباره train نشده است؛ در این حالت status به
`Needs refresh` تغییر می‌کند.

### عدم قطعیت forecast

برای هر ماه forecast، علاوه بر مقدار مرکزی، بازه عدم قطعیت هم ذخیره می‌شود:

```text
value, lower_value, upper_value
```

روش محاسبه:

1. برای هر lead ماهانه، residualهای backtest محاسبه می‌شوند.
2. اگر برای همان dataset و همان lead نمونه کافی وجود داشته باشد، نیم‌عرض بازه
   از quantile خطای مطلق همان residualها گرفته می‌شود.
3. اگر نمونه dataset کافی نباشد، residualهای pooled همان `source_key + index`
   استفاده می‌شود.
4. اگر residual کافی وجود نداشته باشد، fallback محافظه‌کارانه از پراکندگی
   تاریخی شاخص هدف ساخته می‌شود.

بنابراین بازه uncertainty برای همه حالت‌ها وجود دارد: چه مدل با همه متغیرهای
کمکی آموزش دیده باشد، چه با چند متغیر، چه فقط با lagهای خود شاخص. در نمودار
داشبورد، forecast با خط dashed و بازه عدم قطعیت با band کم‌رنگ اطراف forecast
نمایش داده می‌شود؛ tooltip هر ماه هم `lower` و `upper` همان ماه را نشان می‌دهد.

### خودیادگیری مدل بعد از ورود داده واقعی

مدل‌ها به‌صورت online داخل requestهای داشبورد تغییر نمی‌کنند؛ این کار برای
پایداری و قابل ردگیری بودن مناسب نیست. در عوض، self-learning به‌صورت دوره‌ای
انجام می‌شود:

1. مدل برای ۱۲ ماه آینده forecast تولید می‌کند و forecastها در
   `prediction_forecasts` ذخیره می‌شوند.
2. بعد از گذشت زمان، داده واقعی همان ماه‌ها وارد dataset اصلی می‌شود؛ یعنی
   جدول `ts_<dataset_key>` با مقدار واقعی `spi<scale>` یا شاخص هدف به‌روز می‌شود.
3. هنگام اجرای دوباره training، قبل از جایگزینی forecastهای قدیمی، سیستم
   forecastهای قبلی را با مقدار واقعی واردشده مقایسه می‌کند.
4. خطاهای واقعی در جدول `prediction_feedback` ذخیره می‌شوند:
   `predicted_value`, `actual_value`, `error`, `absolute_error`,
   `squared_error`, `lead_month`.
5. مدل با کل تاریخچه جدید، شامل ماه‌های تازه مشاهده‌شده، دوباره آموزش می‌بیند.
   اگر artifact قبلی با معماری فعلی سازگار باشد، آموزش نهایی از وزن‌های قبلی
   شروع می‌شود و fine-tune می‌شود؛ اگر سازگار نباشد، از صفر آموزش می‌بیند.
6. forecast جدید برای ۱۲ ماه آینده منتشر می‌شود و داشبورد همان خروجی تازه را
   نشان می‌دهد.

این یعنی مدل با هر بار ورود داده واقعی، حافظه‌ی خطای گذشته را نگه می‌دارد و
با سری زمانی کامل‌تر دوباره یاد می‌گیرد.

دستور پیشنهادی بعد از import داده‌های ماهانه:

```bash
make prediction-self-learn PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

در سرور:

```bash
make prod-prediction-self-learn PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

اگر predictorهای کمکی هم ماه جدید دارند، قبل از self-learning آن‌ها را هم
به‌روز کنید:

```bash
make prediction-build-predictors \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_INPUT="/datasets/TerraClimate"
make prediction-self-learn PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

برای اجرای کامل ماهانه، ترتیب پیشنهادی این است:

1. دانلود/تولید داده خام جدید sourceها
2. اجرای pipeline تولید SPI/SPEI و import datasetها
3. به‌روزرسانی predictorهای کمکی
4. اجرای `prediction-self-learn`
5. بررسی معیارهای backtest و feedback در داشبورد

برای cron ماهانه در سرور می‌توان همین دستور را بعد از import قرار داد:

```bash
make prod-prediction-self-learn PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

یا از workflow یکپارچه استفاده کنید:

```bash
make prediction-monthly-update PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

در سرور:

```bash
make prod-prediction-monthly-update PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6
```

این target کارهای زیر را انجام می‌دهد:

1. اگر `PREDICTION_INPUT` داده شده باشد predictorها را از فایل‌های محلی rebuild می‌کند.
2. خروجی‌های generated را import می‌کند.
3. مدل را self-learn/retrain می‌کند.
4. کش API را invalid می‌کند.

برای همه sourceها از جمله TerraClimate، اگر بخواهید predictorها در همین مرحله
بازسازی شوند، مسیر ورودی را هم بدهید:

```bash
make prediction-monthly-update \
  PREDICTION_SOURCE=terraclimate \
  PREDICTION_SCALE=6 \
  PREDICTION_INPUT="/datasets/TerraClimate"
```

اگر predictorها را قبلاً ساخته‌اید، می‌توانید `prediction-monthly-update` را
بدون `PREDICTION_INPUT` اجرا کنید تا مرحله predictor فقط skip شود.

نمونه cron ماهانه:

```cron
30 3 5 * * cd /path/to/KhorasanRazaviDroughtMonitoring && make prod-prediction-monthly-update PREDICTION_SOURCE=terraclimate PREDICTION_SCALE=6 PREDICTION_INPUT="/datasets/TerraClimate" >> data/prediction/monthly_update.log 2>&1
```

### تست end-to-end پیش‌بینی

تست prediction یک dataset موقت داخل PostGIS می‌سازد، forecast همراه
`lower/upper` ثبت می‌کند، endpoint را می‌خواند و در پایان همه چیز را پاک
می‌کند:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest -q /app/backend/tests/test_prediction_api.py
```

### نتیجه import

- ایجاد جدول‌های `datasets` و `features`
- ساخت جدول زمانی `ts_<dataset_key>`
- ثبت `min_date` و `max_date`
- پاک‌سازی کش‌ها
- پیش‌محاسبه ترندها

## محاسبه ترند

ترندها به صورت full-history برای هر `feature` و هر `index` محاسبه می‌شوند.

- روش: Mann-Kendall
- شیب: Sen's slope
- ذخیره: جدول `trend_stats`

### اجرای دستی

```bash
docker compose -f docker-compose.dev.yml exec backend python /app/backend/scripts/precompute_trends.py
```

فقط یک لایه:

```bash
docker compose -f docker-compose.dev.yml exec backend python /app/backend/scripts/precompute_trends.py --level station
```

فقط یک شاخص:

```bash
docker compose -f docker-compose.dev.yml exec backend python /app/backend/scripts/precompute_trends.py --level station --index spi3
```

برای scale دیگر:

```bash
docker compose -f docker-compose.dev.yml exec backend python /app/backend/scripts/precompute_trends.py --level station --index spi6
```

## اجرای روی سرور

### 1) نصب وابستگی‌ها

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx git
sudo systemctl enable --now docker nginx
```

### 2) دریافت پروژه

```bash
cd /opt
sudo git clone https://github.com/HydroCodeIR/KhorasanRazaviDroughtMonitoring.git
sudo chown -R "$USER:$USER" KhorasanRazaviDroughtMonitoring
cd KhorasanRazaviDroughtMonitoring
cp .env.prod.example .env.prod
```

### 3) تنظیم `.env.prod`

مقادیر مهم:

- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `CORS_ORIGINS`
- `DATASETS_ROOT`
- `SPI_CACHE_ROOT`

نمونه:

```env
POSTGRES_DB=drought
POSTGRES_USER=drought
POSTGRES_PASSWORD=change-me
DATABASE_URL=postgresql+psycopg2://drought:change-me@db:5432/drought
CORS_ORIGINS=https://drought.werifum.ir,http://drought.werifum.ir
DATASETS_ROOT=/mnt/f/Datasets
SPI_CACHE_ROOT=/mnt/f/Datasets/DroughtCache/polygon_spi
```

### 4) اجرای سرویس‌ها

```bash
make prod-detached
```

### 5) تنظیم Nginx

فایل `deploy/nginx-drought.werifum.ir.conf` را فعال کن:

```bash
sudo cp deploy/nginx-drought.werifum.ir.conf /etc/nginx/sites-available/drought.werifum.ir
sudo ln -s /etc/nginx/sites-available/drought.werifum.ir /etc/nginx/sites-enabled/drought.werifum.ir
```

سپس:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 6) فعال‌سازی SSL

```bash
sudo certbot --nginx -d drought.werifum.ir
```

### 7) بارگذاری داده‌ها

```bash
make prod-import
```

### 8) محاسبه مجدد ترندها

```bash
make prod-precompute-trends
```

## API های اصلی

- `GET /health`
- `GET /datasets`
- `GET /meta?level=<dataset_key>`
- `GET /regions?level=<dataset_key>`
- `GET /mapdata?level=<dataset_key>&index=spi6&date=YYYY-MM&bbox=minLon,minLat,maxLon,maxLat`
- `GET /overview?level=<dataset_key>&index=spi6&date=YYYY-MM`
- `GET /timeseries?region_id=<id>&level=<dataset_key>&index=spi6`
- `GET /kpi?region_id=<id>&level=<dataset_key>&index=spi6&date=YYYY-MM`

## نکته مهم

اگر داده‌ها را تغییر دادی، دوباره `import_data.py --replace` را اجرا کن تا ترندها و کش‌ها با داده جدید هماهنگ شوند.
