# Платежи в клубе «Любящие Бога»: провайдеры, валюты, учёт в ₽

Инструкция для переноса логики на другой сервер/проект.  
Источник: `/home/appuser/dev/kostya/club` (бот + PostgreSQL).  
Срез: сентябрь 2026.

---

## 1. Картина целиком (30 секунд)

```
Пользователь в Telegram → выбирает тариф → выбирает валюту RUB|USD
  → создаём orders (pending) + payments (pending)
  → по валюте выбираем провайдера (env):
        RUB → YooKassa (по умолчанию)
        USD → BZB / Bezebee (по умолчанию)
  → отдаём checkout URL
  → вебхуков НЕТ: фоновый PaymentChecker опрашивает API провайдера
  → при succeeded: курс ЦБ РФ → amount_rub + exchange_rate
  → атомарно payments→succeeded, orders→paid
  → выдача лицензии / подарок / angel_pool
```

**Важно для учёта:**

- В БД оригинал = поля `amount` + `currency` (колонки `amount_orig` нет).
- Единая сумма для отчётов = `payments.amount_rub` (и дубль на `orders.amount_rub`).
- Курс = **официальный XML ЦБ РФ на день**, не курс провайдера.
- Курс **замораживается при успехе**; пересчёта задним числом нет.
- Stripe / crypto в основном потоке подписки **нет**.
- Гео/страна пользователя **не** влияет на провайдера — только валюта заказа.

Отдельный канал «сбор на ружьё» (speargun) — не выручка клуба; ниже кратко, чтобы не смешать.

---

## 2. Файловая карта

| Путь | Роль |
|------|------|
| `club/bot/payments/payment_provider_router.py` | RUB/USD → имя провайдера + DI сервиса |
| `club/bot/payments/yookassa_service.py` | Создание/статус YooKassa (всегда RUB в API) |
| `club/bot/payments/bzb_service.py` | Создание/статус BZB (RUB или USD) |
| `club/bot/payments/currency_converter.py` | ЦБ XML → `convert_payment_amount` |
| `club/bot/payments/payment_checker.py` | Poll pending + finalize + deliver |
| `club/bot/payments/fulfillment.py` | `compute_rub_amount`, доставка продукта |
| `club/bot/features/payment.py` | UI тарифов / валюты / создание платежа |
| `club/storage/db/payments.py` | CRUD + `try_finalize_pending_payment_success` |
| `club/storage/db/orders.py` | Заказы |
| `club/storage/db/tariffs.py` | Тарифы + цены по валютам |
| `club/storage/db/licenses.py` | Лицензия + `license_history` (идемпотентность) |
| `club/bot/services/promo_campaign_service.py` | Скидка % на обе валюты |
| `club/bot/core.py` / `club/bot/base_app.py` | Сборка DI, старт checker |
| `club/config.py` | Env |
| `club/scripts/reconcile_pending_payments.py` | Ручная догонка pending |

---

## 3. Переменные окружения

```bash
# YooKassa (обычно RUB)
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...

# BZB / Bezebee (обычно USD; может и RUB)
BZB_API_KEY=...
BZB_API_URL=https://public-api.bezebee.com   # опционально

# Маршрутизация по валюте (можно поменять местами)
PAYMENT_PROVIDER_RUB=yookassa
PAYMENT_PROVIDER_USD=bzb

# Poll
PAYMENT_CHECK_LOOP_SEC=60
PAYMENT_CHECK_MAX_AGE_DAYS=30
PAYMENT_CHECK_API_PAUSE_SEC=0.25

# Рекуррент сейчас по умолчанию выключен
SUBSCRIPTION_RECURRING_ENABLED=0
```

Проверка YooKassa в коде: `config.has_yookassa` = оба поля shop+secret непустые.  
Ключа ЦБ нет — публичный XML.

---

## 4. Схема БД (минимальный набор)

### 4.1. Тарифы

```sql
-- упрощённо; в проде таблицы уже были до миграций репо
CREATE TABLE tariffs (
  id            serial PRIMARY KEY,
  name          text NOT NULL,
  duration_days int  NOT NULL,
  type          text NOT NULL DEFAULT 'base',  -- base | promo_...
  active        boolean NOT NULL DEFAULT true
);

CREATE TABLE tariff_prices (
  tariff_id  int REFERENCES tariffs(id),
  currency   varchar(10) NOT NULL,          -- 'RUB' | 'USD'
  amount     numeric(10,2) NOT NULL,
  old_amount numeric(10,2),                 -- для зачёркнутой цены в UI
  PRIMARY KEY (tariff_id, currency)
);
```

Типичные цены (ориентир): 30д 1200₽ / $15; 90д 2990₽ / $39; 183д 5490₽ / $69; 365д 9990₽ / $129.

Загрузка в боте — `TariffsMixin.get_active_tariffs`: `json_agg` цен в массив `prices: [{currency, amount, old_amount}, ...]`.

### 4.2. Заказы

```sql
CREATE TABLE orders (
  id              bigserial PRIMARY KEY,
  user_id         bigint NOT NULL,
  tariff_id       int REFERENCES tariffs(id),
  currency        varchar(10) NOT NULL,
  amount          numeric(10,2) NOT NULL,   -- сумма в currency
  amount_rub      numeric(10,2),             -- заполняется при оплате
  status          text NOT NULL,            -- pending | paid
  paid_at         timestamptz,
  is_gift         boolean DEFAULT false,
  gift_recipient_user_id bigint,
  gift_recipient_username text,
  is_angel_pool   boolean DEFAULT false,
  angel_pool_slots int,
  promo_campaign_guid text,
  created_at      timestamptz DEFAULT now()
);
```

### 4.3. Платежи (ядро учёта)

```sql
CREATE TABLE payments (
  id                   bigserial PRIMARY KEY,
  user_id              bigint NOT NULL,
  amount               numeric(10,2) NOT NULL,  -- ОРИГИНАЛ в currency
  currency             varchar(10) NOT NULL DEFAULT 'RUB',
  payment_type         varchar(50),             -- subscription | angel_pool | ...
  payment_provider     varchar(20) NOT NULL DEFAULT 'yookassa',  -- yookassa | bzb
  provider_payment_id  varchar,
  provider_checkout_url text,
  status               varchar NOT NULL,        -- pending|succeeded|canceled|failed|expired
  amount_rub           numeric(10,2),           -- УЧЁТ в ₽
  exchange_rate        numeric(10,4),           -- ₽ за 1 единицу currency
  converted_at         timestamptz,
  order_id             bigint REFERENCES orders(id),
  completed_at         timestamptz,
  last_checked_at      timestamptz,             -- для деградирующего poll
  created_at           timestamptz DEFAULT now(),
  updated_at           timestamptz DEFAULT now()
);
```

Миграция poll: `club/migrations/027_payments_last_checked_at.sql`.

### 4.4. Лицензия

После успеха: одна строка `license` на пользователя (`expires_at`, `origin`, …) + запись в `license_history` с `payment_id` / `source='subscription_payment'` — **ключ идемпотентности доставки**.

---

## 5. Выбор валюты пользователем

Не автодетект по IP/стране. После выбора тарифа бот показывает кнопки:

- `payment_currency_rub_{tariff_id}`
- `payment_currency_usd_{tariff_id}`

В `PaymentFeature._handle_currency_selection`:

1. Берёт цену: `next(p for p in tariff['prices'] if p['currency'].lower() == currency)`.
2. Считает `amount`, `currency_code = 'RUB'|'USD'`.
3. Опционально применяет промо-% к amount (одинаковый % к обеим валютам).
4. Дальше роутер провайдера.

Поддерживаемые валюты клуба для подписки: **только RUB и USD**.

---

## 6. Роутинг провайдера по валюте

Файл: `club/bot/payments/payment_provider_router.py`

```python
def payment_provider_for_currency(currency: str) -> str:
    cur = (currency or "").strip().upper()
    if cur == "RUB":
        return (config.PAYMENT_PROVIDER_RUB or "yookassa").strip().lower()
    if cur == "USD":
        return (config.PAYMENT_PROVIDER_USD or "bzb").strip().lower()
    raise ValueError(f"Unsupported payment currency: {currency!r}")


def resolve_payment_service(currency, *, yookassa_service, bzb_service):
    provider = payment_provider_for_currency(currency)
    return resolve_payment_service_by_name(
        provider, yookassa_service=yookassa_service, bzb_service=bzb_service
    )


def resolve_payment_service_by_name(provider, *, yookassa_service, bzb_service):
    """При проверке статуса — по СОХРАНЁННОМУ payment_provider, не по текущему env."""
    name = (provider or "").strip().lower()
    if name == "yookassa":
        if yookassa_service is None or not config.has_yookassa:
            raise RuntimeError("YooKassa не настроена")
        return yookassa_service, "yookassa"
    if name == "bzb":
        if bzb_service is None:
            raise RuntimeError("BZB не настроена")
        return bzb_service, "bzb"
    raise RuntimeError(f"Unknown payment provider: {provider!r}")
```

**Правило:** при poll всегда резолвить сервис по `payments.payment_provider` из строки платежа. Иначе смена env сломает старые pending.

---

## 7. Создание платежа у провайдеров

### 7.1. YooKassa (`yookassa_service.py`)

- SDK `yookassa`: `Payment.create` / `Payment.find_one` через `asyncio.to_thread`.
- В API **всегда** `"currency": "RUB"`.
- Есть заглушка чека 54-ФЗ (`receipt` с email `user@example.com`).
- `return_url = https://t.me/{bot_username}`.
- Идемпотентный ключ: `uuid.uuid4()` на create.
- Возврат: `(confirmation_url, payment_id, payment_method_id)`.

Фрагмент payload:

```python
payment_data = {
    "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
    "confirmation": {"type": "redirect", "return_url": return_url},
    "capture": True,
    "description": description,
    "metadata": {
        "telegram_user_id": str(user_id),
        "payment_type": payment_type,
        "bot_platform": "telegram",
    },
    "receipt": receipt_data,  # 54-ФЗ
}
payment = await self._run_blocking(Payment.create, payment_data, str(uuid.uuid4()))
```

### 7.2. BZB (`bzb_service.py`)

- HTTP: `POST {base}/api/v1/payments`, заголовок `X-API-Key`.
- `currency` из kwargs (`RUB` или `USD`).
- `type`: `ONE_TIME` (по умолчанию); `RECURRING` только если флаг рекуррента.
- Статусы при check: `confirmed`→`succeeded`, `cancelled`→`canceled`, `pending`→`pending`.
- Возврат: `(payment_url, payment_id, metadata)`.

```python
payload = {
    "amount": float(amount),
    "currency": currency,          # RUB | USD
    "title": title,
    "description": description[:500],
    "back_url": f"https://t.me/{bot_username}",
    "type": "ONE_TIME",
}
# POST /api/v1/payments → 201 → id + payment_url
```

### 7.3. Запись в нашу БД после create

Порядок в `PaymentFeature`:

1. `create_order(...)` → `order_id`
2. `service.create_payment(...)` → url + provider_payment_id
3. `create_payment(..., provider=..., currency=..., amount=..., order_id=..., provider_checkout_url=url)` со статусом `pending`

```python
# storage/db/payments.py — create_payment
INSERT INTO payments
  (user_id, amount, currency, payment_type, subscription_id,
   payment_provider, provider_payment_id, status, user_telegram_data,
   order_id, provider_checkout_url)
VALUES (..., 'pending', ...)
RETURNING id
```

Пользователю — кнопка URL на `provider_checkout_url`.

---

## 8. Подтверждение оплаты: poll, не webhook

HTTP-вебхуков от YooKassa/BZB в клубе **нет**.

`PaymentChecker` (стартует из `base_app`):

- цикл каждые `PAYMENT_CHECK_LOOP_SEC` (мин. 15с);
- выбирает pending моложе `PAYMENT_CHECK_MAX_AGE_DAYS`;
- деградирующий интервал по возрасту ссылки (`poll_interval_for_age`):
  - &lt;1ч → раз в 1 мин;
  - &lt;6ч → 5 мин;
  - … до раз в сутки после ~15 дней;
- пауза между API-вызовами `PAYMENT_CHECK_API_PAUSE_SEC`;
- пишет `last_checked_at` даже при ошибке API (чтобы не долбить каждую минуту).

Дополнительно: ручной reconcile — `scripts/reconcile_pending_payments.py`.

Старые pending &gt; max_age → локально `expired`.

---

## 9. Пересчёт в ₽ (самое важное для учёта)

Файл: `club/bot/payments/currency_converter.py`.

### 9.1. Источник курса

```
GET https://www.cbr.ru/scripts/XML_daily.asp?date_req=DD/MM/YYYY
```

- Парсим `<Valute><CharCode>`, `<Value>`, `<Nominal>` → `rate = Value/Nominal` (рублей за 1 единицу валюты).
- `RUB = 1.0`.
- Кэш в памяти: TTL 1 час, до 40 дат.
- Если на день нет листа (выходной) — lookback до **7** предыдущих дней.

### 9.2. Дата для курса

```python
def resolve_payment_datetime_for_rates(payment) -> datetime:
    for key in ("completed_at", "updated_at", "created_at"):
        v = payment.get(key)
        if isinstance(v, datetime):
            return v
    return datetime.now()
```

На первом finalize обычно берётся **`created_at`** (день выставления счёта), т.к. `completed_at` ещё нет.

### 9.3. Конвертация

```python
async def convert_payment_amount(self, amount, currency, payment_date) -> Optional[float]:
    if amount < 0:
        return None
    currency_u = currency.strip().upper()
    if currency_u == "RUB":
        return float(amount)
    rate = await self.get_rate_to_rub(currency_u, payment_date.date())
    if rate is None:
        return None
    return float(amount) * rate
```

**Если ЦБ недоступен / нет курса → `None` → finalize НЕ делается**, платёж остаётся `pending` (лучше недозачесть, чем зачесть криво).

### 9.4. Где вызывается

В `PaymentChecker._check_single_payment` при `status == "succeeded"`:

```python
order = await self.user_storage.get_order(payment["order_id"])
rub_amount = await self.order_fulfillment.compute_rub_amount(order, payment)
if not rub_amount:
    return  # ждём следующий poll
exchange_rate = rub_amount / float(order["amount"])  # ₽ за 1 USD/RUB

finalized = await self.order_fulfillment.finalize_pending_payment_or_none(
    payment_id=...,
    provider_payment_id=...,
    rub_amount=rub_amount,
    exchange_rate=exchange_rate,
)
# затем deliver_after_successful_payment_row
```

`compute_rub_amount` внутри зовёт `CurrencyConverterService.convert_payment_amount`.

### 9.5. Атомарный finalize

`PaymentsMixin.try_finalize_pending_payment_success`:

```sql
-- только если ещё pending (идемпотентность)
UPDATE payments
   SET status = 'succeeded',
       completed_at = NOW(),
       provider_payment_id = $2,
       amount_rub = $3,
       exchange_rate = $4,
       converted_at = NOW()
 WHERE id = $1 AND status = 'pending'
 RETURNING *;

UPDATE orders
   SET status = 'paid',
       paid_at = NOW(),
       amount_rub = $1
 WHERE id = $2;
```

Оба UPDATE в одной транзакции. Если строка уже не `pending` → `None` (второй poll не задвоит деньги).

### 9.6. Формулы для отчётов

```sql
-- Выручка клуба в ₽
SELECT COALESCE(SUM(amount_rub), 0)
FROM payments
WHERE status = 'succeeded'
  AND amount_rub IS NOT NULL;

-- Разрез по провайдеру / валюте оригинала
SELECT payment_provider, currency,
       COUNT(*), SUM(amount) AS orig, SUM(amount_rub) AS rub
FROM payments
WHERE status = 'succeeded'
GROUP BY 1, 2;
```

Все клубные воронки/дайджесты суммируют именно **`payments.amount_rub`**.

---

## 10. Доставка продукта после оплаты

`PaidOrderFulfillment.deliver_after_successful_payment_row`:

1. Проверка идемпотентности (`license_history` с этим `payment_id` / gift-by-order).
2. По типу заказа: продлить `license`, выдать подарочный код, angel_pool слоты.
3. Потребить промо, если было.
4. Админ-уведомления в топик оплат.

Повторный poll после успеха безопасен: finalize no-op + delivery audit exists.

---

## 11. Промо

`promo_campaigns.discount_percent` → `apply_promo_to_tariffs`: умножает **каждую** цену в `prices[]` на `(1 - pct/100)`.  
В order/payment пишется уже скидочный `amount`. GUID промо — на заказе.

---

## 12. Краевые случаи (обязательно скопировать поведение)

| Случай | Поведение клуба |
|--------|-----------------|
| Pending | Poll с деградацией |
| Succeeded | FX → finalize → deliver |
| Canceled/failed у провайдера | `payments.status` обновляется, без RUB и без лицензии |
| Старше max_age | `expired` локально |
| CBR недоступен | finalize откладывается |
| Повторный poll успеха | идемпотентно |
| Refund | **не реализован** |
| Платёж без `order_id` (редкий донат) | status→succeeded, thank-you; **amount_rub может не писаться** |
| Смена env провайдера | старые pending ходят по сохранённому `payment_provider` |
| Рекуррент | флаг выключен; задел в коде есть |

---

## 13. Скелет переноса (чеклист для агента)

1. **БД:** `tariffs`, `tariff_prices`, `orders`, `payments` (+ license/history).
2. **Env:** YooKassa + BZB + `PAYMENT_PROVIDER_RUB/USD`.
3. **Интерфейс провайдера** (общий контракт):
   - `create_payment(...) -> (url, provider_id, meta)`
   - `check_payment_status(provider_id) -> (status, details)`  
   статусы нормализовать к: `pending|succeeded|canceled|failed`.
4. **Router** по валюте + resolve by stored name.
5. **Bot flow:** тариф → валюта → order → provider create → payment row → URL.
6. **Checker loop** без вебхуков.
7. **CurrencyConverter** на ЦБ XML (как выше).
8. **Finalize** атомарно пишет `amount_rub`/`exchange_rate`/`converted_at` + order paid.
9. **Deliver** с идемпотентностью по `payment_id`.
10. Отчёты только по `amount_rub WHERE status='succeeded'`.

Минимальный псевдокод цикла успеха:

```python
status, _ = await provider.check(payment.provider_payment_id)
if status != "succeeded":
    update_status_if_terminal(status); return

order = get_order(payment.order_id)
pay_dt = resolve_payment_datetime_for_rates(payment)
rub = await converter.convert_payment_amount(order.amount, order.currency, pay_dt)
if rub is None:
    return  # retry later
rate = rub / float(order.amount)

row = await try_finalize_pending_payment_success(
    payment.id, payment.provider_payment_id, rub, rate
)
# row is None → already finalized
await deliver_idempotent(payment.id)
```

---

## 14. Что НЕ копировать как «клубную выручку»

**Speargun fund** (`bot/features/speargun_fund.py`):

- отдельная схема/таблица `speargun.donations`;
- USD/EUR через BZB;
- оценка в ₽ на создании через **фиксированные** env `SPEARGUN_USD_TO_RUB` / `SPEARGUN_EUR_TO_RUB`, не ЦБ;
- не суммировать с `payments.amount_rub` клуба.

---

## 15. Зависимости Python (ориентир)

```
yookassa
aiohttp
asyncpg
aiogram  # UI
```

ЦБ — только `aiohttp` + stdlib `xml.etree`.

---

## 16. Тест-план после переноса

1. Создать тариф с ценами RUB+USD.
2. Платёж RUB → YooKassa sandbox/live → pending → poll → `amount_rub == amount`, `exchange_rate ≈ 1`.
3. Платёж USD → BZB → success → `amount_rub ≈ amount * cbr_usd`, оба поля на payment и order заполнены.
4. Повторный poll того же id — лицензия не удваивается.
5. Выключить сеть к ЦБ на момент success — платёж остаётся pending; после восстановления — дозачёт.
6. Отчёт `SUM(amount_rub)` совпадает с ручным пересчётом по `exchange_rate`.

---

*Конец инструкции. При расхождении с кодом приоритет у файлов из раздела 2 в репозитории `kostya/club`.*
