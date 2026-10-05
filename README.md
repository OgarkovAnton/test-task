# Сервис платежей

Принимает платеж, кладет событие в outbox той же транзакцией и отвечает 202. Дальше консьюмер забирает сообщение из RabbitMQ, пару секунд изображает платежный шлюз и дергает webhook.

Шлюз — заглушка: спит 2–5 секунд, примерно в 9 случаях из 10 ставит `succeeded`, иначе `failed`. На оба исхода уходит webhook.

## Запуск

Нужны Docker и Compose.

```
docker compose up --build
```

- API: http://localhost:8000, swagger на `/docs`
- RabbitMQ: http://localhost:15672, логин `payments` / `payments`
- Postgres с хоста: `localhost:5433`, пользователь и база `payments`
- ключ API: заголовок `X-API-Key: test-key`

Остановить: `docker compose down`.

## Как это связано

Платеж и строка outbox пишутся одним коммитом. Цикл в консьюмере публикует неопубликованные строки в exchange `payments` (routing key `payments.new`). Если публикация не удалась, `published_at` пустой и строка уйдет в очередь со следующей попытки.

Обработчик один. Если webhook (или база) падает, есть еще две попытки: пауза 1 секунда, потом 2. После третьей неудачи сообщение публикуется в `payments.dlq` через exchange `payments.dlx`. Очередь `payments.new` тоже настроена на dead-letter, если процесс просто умрет посередине.

Повторный `POST` с тем же `Idempotency-Key` и тем же телом вернет уже созданный платеж и его текущий статус. Другое тело с тем же ключом — 409.

## Примеры

Создать:

```
curl -s -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-key" \
  -H "Idempotency-Key: 4f3c0a4e-1b2c-4d5e-8f90-112233445566" \
  -d '{"amount":"199.90","currency":"RUB","description":"заказ 15","metadata":{"order_id":"15"},"webhook_url":"https://webhook.site/your-id"}'
```

Ответ `202`:

```
{"payment_id":"...","status":"pending","created_at":"..."}
```

Посмотреть:

```
curl -s http://localhost:8000/api/v1/payments/<payment_id> \
  -H "X-API-Key: test-key"
```

Пока консьюмер не закончит, статус `pending`. Потом `succeeded` или `failed`, и на `webhook_url` уходит POST с `payment_id`, `status`, `amount`, `currency`.

Webhook с контейнера на свою машину удобно слать на `http://host.docker.internal:<порт>/...`.

Логи обработки: `docker compose logs -f consumer`. Застрявшие сообщения лежат в очереди `payments.dlq`.
