# Iran Drought Monitoring

سامانه پایش خشکسالی ایران با `FastAPI`، `PostGIS`، `Redis` و نقشه تعاملی `Leaflet`.

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

## تولید SPI-3 پهنه‌ای از NetCDF

مولد مستقل در `backend/scripts/spi_pipeline` همه فایل‌های مکانی زیر
`geoBoundaries` را به‌صورت بازگشتی کشف می‌کند. هر منبع بارش در
`config.json` تعریف می‌شود؛ بنابراین افزودن منبع جدید فقط به یک مدخل تنظیمات
نیاز دارد.

روش محاسبه شامل تبدیل بارش به mm، تجمیع ماهانه، میانگین مساحت‌وزن با تقاطع
دقیق سلول و پلیگون، و سپس استانداردسازی ناپارامتری SDAT در مقیاس سه‌ماهه
است. خروجی سری زمانی و هندسه هر ترکیب منبع/مرز به‌صورت Parquet/GeoParquet
فشرده ذخیره می‌شود.
پلیگون‌هایی که کمتر از ۸۰٪ آن‌ها داخل پوشش شبکه بارش باشد حذف می‌شوند تا
SPI بر مبنای بخش کوچکی از یک ناحیه گزارش نشود.

در داده فعلی، TerraClimate جهانی است؛ فایل‌های FLDAS2 موجود فقط محدوده
تقریبی `42–64°E` و `24–41°N` را پوشش می‌دهند. همچنین اکتبر و دسامبر ۲۰۲۴
FLDAS2 روزهای مفقود دارند، بنابراین آن ماه‌ها و پنجره‌های SPI-3 وابسته به
آن‌ها به‌صورت `No Data` ذخیره می‌شوند.

کشف ورودی‌ها بدون محاسبه:

```bash
make spi-discover
```

تولید یا به‌روزرسانی همه خروجی‌ها:

```bash
make spi-generate
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
  SPI_BOUNDARY=administrative_country
```

بعد از اطمینان از خروجی، State و County یا FLDAS2 را جداگانه اجرا کنید.

اجرای انتخابی:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python -m scripts.spi_pipeline.cli \
  --config /app/backend/scripts/spi_pipeline/config.json \
  --source terraclimate \
  --boundary administrative_country
```

کش ماهانه و وزن‌های هندسی باعث می‌شود فایل‌های بدون تغییر دوباره محاسبه
نشوند. اضافه‌شدن فایل، تغییر فایل تاریخی، یا اضافه‌شدن shapefile جدید
خودکار تشخیص داده می‌شود.

پس از تولید، dataset تغییرکرده را وارد PostGIS کنید:

```bash
docker compose -f docker-compose.dev.yml exec backend \
  python /app/import_data.py \
  --dataset terraclimate_administrative_country_spi3 \
  --replace-dataset \
  --skip-trends
```

یا همه خروجی‌های تولیدشده توسط pipeline را بدون دست‌زدن به datasetهای دیگر
وارد کنید:

```bash
make spi-import
```

داشبورد dataset را از `/datasets` کشف می‌کند و آن را خودکار در فهرست انتخاب
نمایش می‌دهد.

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
- `GET /mapdata?level=<dataset_key>&index=spi3&date=YYYY-MM&bbox=minLon,minLat,maxLon,maxLat`
- `GET /overview?level=<dataset_key>&index=spi3&date=YYYY-MM`
- `GET /timeseries?region_id=<id>&level=<dataset_key>&index=spi3`
- `GET /kpi?region_id=<id>&level=<dataset_key>&index=spi3&date=YYYY-MM`

## نکته مهم

اگر داده‌ها را تغییر دادی، دوباره `import_data.py --replace` را اجرا کن تا ترندها و کش‌ها با داده جدید هماهنگ شوند.
