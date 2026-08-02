# MM2 Values API

Публичный API значений оружия и питомцев MM2 с сайта [Supreme Values](https://supremevalues.com/). Быстрые публичные ответы обслуживает Python-функция, а почасовое обновление запускает Puppeteer Core с серверным Chromium (`@sparticuz/chromium`) в отдельной Vercel-функции.

## Возможности

- Оружие из категорий: Uniques, Ancients, Vintages, Chromas, Godlies, Legendaries, Rares, Uncommons, Commons.
- Питомцы из категории Pets.
- Полные названия и отображаемый `Value` без Sets, Misc, Evos и Untradables.
- JSON API и простые строки `name=value`.
- Поиск и фильтрация в веб-интерфейсе.
- Почасовой Vercel Cron через Puppeteer Core и `@sparticuz/chromium`.
- Блокировка изображений, шрифтов и видео для экономии памяти и ускорения рендера.
- Резервная встроенная выборка, если источник временно блокирует serverless IP.

## Развёртывание на Vercel

1. Загрузите содержимое этой папки в новый GitHub-репозиторий.
2. В Vercel выберите **Add New → Project**, импортируйте репозиторий и нажмите **Deploy**. Framework Preset можно оставить `Other`.
3. Добавьте из Vercel Marketplace интеграцию **Upstash Redis** к проекту. Обычно она автоматически создаёт `UPSTASH_REDIS_REST_URL` и `UPSTASH_REDIS_REST_TOKEN`. Также поддерживаются старые имена `KV_REST_API_URL` и `KV_REST_API_TOKEN`.
4. В **Settings → Environment Variables** создайте `CRON_SECRET` со случайной длинной строкой (не менее 16 символов).
5. Убедитесь, что для проекта используется Node.js 22 или новее. Chromium-функции выделено до 2 ГБ памяти и до 300 секунд; доступность этих лимитов зависит от тарифа Vercel.
6. Выполните повторный Deploy, затем один раз вызовите cron вручную с авторизацией:

```bash
curl -H "Authorization: Bearer ВАШ_CRON_SECRET" https://ВАШ-ДОМЕН.vercel.app/api/cron
```

После первого успешного запуска данные сохранятся в Redis. Встроенная резервная выборка используется до этого момента и при отсутствии Redis.

> Важно: Vercel Cron на Hobby-плане может ограничивать частоту запусков. Если Vercel не принимает почасовое расписание, нужен Pro-план либо внешний cron-сервис, вызывающий `/api/cron` с тем же заголовком авторизации.

## API

### Все предметы

```http
GET /api/values
```

Ответ:

```json
{
  "updatedAt": "2026-08-02T18:00:00+00:00",
  "source": "https://supremevalues.com",
  "cache": "persistent",
  "count": 825,
  "items": [
    {
      "name": "Nightsky",
      "value": "5",
      "valueNumber": 5,
      "type": "weapon",
      "category": "Godlies"
    }
  ]
}
```

### Фильтры

```http
GET /api/values?type=weapon
GET /api/values?type=pet
GET /api/values?q=Nightsky
GET /api/values?type=weapon&q=Night
```

- `type`: `weapon` или `pet`.
- `q`: поиск без учёта регистра по полному названию.

### Текст `name=value`

```http
GET /api/text
GET /api/text?type=pet
GET /api/text?q=Nightsky
```

Пример ответа:

```text
Nightsky=5
```

### Обновление

```http
GET /api/cron
Authorization: Bearer CRON_SECRET
```

Vercel вызывает этот адрес автоматически по расписанию из `vercel.json`. Если `CRON_SECRET` задан, ручной запрос без правильного заголовка вернёт `401`.

## Локальная проверка

Требуются Node.js 22+, npm и Python 3.10+.

```bash
npm install
python -m py_compile api/index.py lib/parser.py lib/store.py
vercel dev
```

После запуска вызовите Chromium-обновление:

```bash
curl -H "Authorization: Bearer ВАШ_CRON_SECRET" http://localhost:3000/api/cron
```

## Надёжность и ограничения

Supreme Values использует защиту от автоматических запросов. Chromium загружает обычный DOM, но проект намеренно не решает CAPTCHA/challenge, не подделывает защитные cookies и не пытается скрывать автоматизацию. Если появляется страница защиты, обновление использует предыдущие успешные категории и не заменяет целый кэш повреждёнными данными. `/api/cron` возвращает диагностируемую ошибку, а публичный API продолжает отдавать последний снимок. Не уменьшайте интервал — частые запросы повышают вероятность блокировки и нагрузку на источник.

Данные принадлежат соответствующим владельцам. Проект не связан с Roblox или Supreme Values.

## Публичное скачивание

Любой посетитель может нажать **«Запросить данные»** на главной странице либо скачать готовую выборку без регистрации:

```http
GET /api/download?format=json
GET /api/download?format=csv
GET /api/download?format=txt
GET /api/download?format=csv&type=weapon&q=Night
GET /api/health
```

Публичные чтения обслуживаются из CDN-кэша и последнего успешного снимка, поэтому не создают новый запрос к Supreme Values на каждого посетителя. Обновление источника выполняется только защищённым почасовым заданием. CORS открыт для чтения с любых сайтов.

## О защите источника

Проект не взламывает и не обходит Incapsula: не решает challenge, не подделывает cookies и не маскирует автоматизацию. Вместо этого он снижает вероятность блокировки корректным способом — редкие обновления, малая параллельность, экспоненциальная пауза, сохранение успешных категорий и выдача последней целой выборки при временном отказе источника. Для гарантированного обновления используйте официальный API или письменное разрешение владельца Supreme Values.
