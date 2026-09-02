# PROJECT CONTEXT v2 — miran_bot (продажа VPN в Иране)

## 1. Цель
MVP Telegram-бот продажи VPN (панель 3x-UI, VLESS+Reality) для Ирана.
Минимализм: у клиента 2-3 кнопки, без автопродлений, бонусов, рефералок.

## 2. Инфраструктура
- **Сервер бота:** 83.243.86.111 (Ubuntu 24.04), проект `/root/miran_bot`, venv.
  Запуск пока вручную `python main.py` (systemd — в Блоке 8).
- **Панель 3x-UI:** https://45.159.151.26:27320 + webBasePath `/bIwwjwThZ7fpVh9dji/`.
  SSL самоподписанный → в коде `verify=False`. Новая версия панели (React-фронт).
- **GitHub:** https://github.com/Konstannttiinn1/miran_bot (код ЕЩЁ не запушен — TODO).
- **Локально:** PyCharm, `C:\Users\user\PycharmProjects\Iran_bot` — здесь РЕДАКТИРУЕМ код.
- **Workflow:** правим в PyCharm → `scp` изменённые файлы на сервер → рестарт бота.
  Сервер = источник истины по запуску; локально бот НЕ запускаем.

## 3. Аккаунты и ID
- Бот: @VPN_to_peopleBOT
- Супер-админ (TG ID): 803344511
- Тестовый клиент (TG ID): 8692108732
- Поддержка: @L_Konstantinn
- Дилер (будущий): @Orion_Ehsan

## 4. Стек
Python 3.12, aiogram 3, SQLAlchemy 2 async (сейчас sqlite vpn.db; Postgres — Блок 8),
pydantic-settings, httpx, FastAPI+uvicorn (вебхуки — Блок 8).

## 5. Роли и логика
### Клиент (fa/en, минимализм)
/start → выбор языка (если первый вход) → меню [📱 Мой VPN][🆘 Поддержка][🌐 Язык].
Нет подписки → тарифы (1m $5 / 3m $12 / 6m $20, файл app/utils/tariffs.py) →
способ оплаты: Heleket / Дилер / Stars.
Есть подписка → дата окончания, трафик, ссылка подключения.

### Дилер (Блок 6, спроектировано)
Клиент грузит чек → дилеру уведомление в бота с кнопками [✅][❌] →
при ✅: проверка dealer_balance → списание кредитов → grant_vpn → клиенту ссылка.
Кредиты дилеру начисляет только админ. Если 3x-UI упал — откат кредитов.

### Супер-админ (всегда ru)
Поиск юзера по TG ID → [+7 дней][-7 дней][Сброс ссылки][Блок];
начисление кредитов дилерам; логи действий. (Блок 7)

## 6. БД (app/database/models.py): users, subscriptions, orders, dealer_logs.
xui_email клиента = его telegram_id. Даты — naive UTC.

## 7. Текущая структура файлов
app/: bot.py, config.py,
database/ (engine, models), repositories/ (db_repo),
middlewares/ (i18n), keyboards/ (builders), handlers/ (start, client, states),
services/ (heleket, xui_api, subscription, payment_checker),
utils/ (tariffs, emojis), locales/ (fa, en, ru).
main.py — точка входа (polling + payment_checker_loop).

## 8. Ключевые решения
- i18n: все тексты в locales JSON, хендлеры получают t(), lang, db_user из I18nMiddleware.
- Логотип: строка `<b>VPN TO PEOPLE</b>` вшита в тексты локалей.
- Кастомные эмодзи: фундамент app/utils/emojis.py, флаг USE_CUSTOM_EMOJI (пока false).
- Крипта: Heleket, polling статусов раз в 30 сек (payment_checker), флаг HELEKET_ENABLED=false до получения ключа. Вебхук — после появления домена (Блок 8).
- Stars: опция; аккаунт-получатель НЕ иранский.
- 3x-UI: авторизация токен ИЛИ логин/пароль (xui_api.py); addClient с subId;
  ссылка клиента = XUI_SUB_URL + subId (subscription в панели НЕ включён — TODO!).

## 9. Переменные .env (сервер /root/miran_bot/.env)
BOT_TOKEN, ADMIN_IDS, SUPPORT_USERNAME, DB_URL,
XUI_HOST, XUI_BASE_PATH, XUI_USERNAME, XUI_PASSWORD, XUI_TOKEN, XUI_INBOUND_ID, XUI_SUB_URL,
HELEKET_API_KEY, HELEKET_WEBHOOK_URL, HELEKET_ENABLED,
DEALER_CARD_NUMBER, DEALER_CONTACT, AVAILABLE_LANGS, USE_CUSTOM_EMOJI.

## 10. Прогресс
- [x] Блок 1: скелет, /start
- [x] Блок 2: БД
- [x] Блок 3: i18n, меню
- [x] Блок 4 (+4.5 🌐 язык, +4.6 эмодзи/логотип): тарифы, выбор оплаты, FSM
- [🔄] Блок 5: деплой на сервер ГОТОВ; /testvpn существует;
  ОТКРЫТАЯ ПРОБЛЕМА: 3x-UI API авторизация — POST /login → 403, addClient с Bearer-токеном → 404.
  TODO: включить Subscription в панели → заполнить XUI_SUB_URL.
- [ ] Блок 6: дилер
- [ ] Блок 7: админка
- [ ] Блок 8: systemd, Postgres, домен+вебхук, уведомления за 3/1 дня,
  пуш в GitHub, убрать ru из AVAILABLE_LANGS в проде.

## 11. Возобновление в новом чате
Первым сообщением вставить этот файл + «продолжаем с блока N» + последние логи/ошибки.

## 12. РЕШЕНИЕ ПО 3x-UI v3.6 (ЗАРАБОТАЛО 02.09.2026)
- Панель v3.6.0: старый маршрут inbounds/addClient НЕ существует.
- Новый API: /panel/api/clients/add (тело {"client": {...}, "inboundIds": [1]}),
  update/{email}, del/{email}, bulkAdjust (±дни), resetTraffic/{email}, links/{email}.
- Эндпоинты clients/* закрыты замком: БЕЗ токена отдают 404!
  inbounds/list отвечает без авторизации (не ориентир).
- Токен: на сервере панели `x-ui setting -getApiToken true` → свежий apiToken →
  в .env XUI_TOKEN → xui_api шлёт `Authorization: Bearer`.
- Ссылка клиента: https://45.159.151.26:2096/sub/{subId} (XUI_SUB_URL).
- /testvpn — админская команда полной проверки выдачи.
- Прогресс: Блок 4 ✅, Блок 5 ✅ (Heleket на флаге), далее Блок 6.
# PROJECT CONTEXT v3 — miran_bot / VPN TO PEOPLE (MVP в продакшне)

## 1. Цель
Telegram-бот продажи VPN (3x-UI, VLESS+Reality) для Ирана. Минимализм: у клиента 2-3 кнопки.

## 2. Инфраструктура
- Сервер бота: 83.243.86.111 (Ubuntu 24.04), /root/miran_bot, venv, systemd: miran-bot.
- Панель 3x-UI v3.6.0: https://45.159.151.26:27320 + webBasePath /bIwwjwThZ7fpVh9dji/
  Подписка: https://45.159.151.26:2096/sub/{subId} (порт 2096, путь /sub/).
- БД: Postgres (miran_bot / user miran) на сервере бота.
- GitHub: github.com/Konstannttiinn1/miran_bot
- Локально: PyCharm, C:\Users\user\PycharmProjects\Iran_bot (редактируем; на сервере НЕ запускаем).
- Workflow: правим в PyCharm → scp на сервер → systemctl restart miran-bot.

## 3. Аккаунты
- Бот: @VPN_to_peopleBOT
- Супер-админ: 803344511
- Тест-клиент: 8692108732
- Поддержка: @L_Konstantinn | Дилер: @Orion_Ehsan

## 4. Роли
- Клиент (fa/en): /start → язык → [Мой VPN][Поддержка][Язык].
  Нет подписки: тарифы (+🎁 тест 1 раз). Есть: дата, трафик, 🔗 ссылка, 🛒 докупка.
- Дилер: меню [Баланс][История]; получает чек фото → ✅/❌; ✅ = списание кредитов + выдача; откат при сбое 3x-UI.
- Админ (ru): /testvpn /setdealer /topup + меню [Пользователи(пагинация,поиск)][Дилеры][Логи];
  карточка: ±7 дней, сброс ссылки, блок/разблок, удалить. Уведомления админу: чек, подтверждение, отказ, крипто.

## 5. Тарифы (app/utils/tariffs.py)
test: 1д/1ГБ/0$ | 1m: 30д/30ГБ/$5 | 3m: 90д/90ГБ/$12 | 6m: 180д/180ГБ/$20.

## 6. 3x-UI API v3.6 (экспериментально)
- Авторизация: Bearer {XUI_TOKEN}; без токена clients/* отдают 404!
- add: POST /panel/api/clients/add {"client":{...},"inboundIds":[1]}; email=telegram_id;
  если email занят — update (срок+трафик).
- get: GET /panel/api/clients/get/{email} → obj={"client":{...},"inboundIds":[...]}.
- update: POST /panel/api/clients/update/{email}; ОБЯЗАТЕЛЬНО: email в теле;
  sanitize: allowedIPs строка→список; id должен быть str(uuid), иначе Go-ошибка.
- del: POST /panel/api/clients/del/{email}; links: GET /panel/api/clients/links/{email}.
- Токен: на сервере панели `x-ui setting -getApiToken true`.

## 7. .env (сервер)
BOT_TOKEN, ADMIN_IDS, SUPPORT_USERNAME, DB_URL(postgres), XUI_HOST, XUI_BASE_PATH,
XUI_USERNAME, XUI_PASSWORD, XUI_TOKEN, XUI_INBOUND_ID=1, XUI_SUB_URL,
HELEKET_API_KEY, HELEKET_WEBHOOK_URL, HELEKET_ENABLED=false,
DEALER_CARD_NUMBER, DEALER_CONTACT, AVAILABLE_LANGS, USE_CUSTOM_EMOJI=false.

## 8. Серверные команды
- Логи: journalctl -u miran-bot -f | -n 100 --no-pager
- Рестарт/стоп/статус: systemctl restart|stop|status miran-bot
- Дроп БД: sudo -u postgres psql -d miran_bot -c "DROP TABLE IF EXISTS dealer_logs, orders, subscriptions, users CASCADE;"
- Деплой: scp -r app main.py root@83.243.86.111:/root/miran_bot/

## 9. Готово (блоки 1-8)
Скелет, БД, i18n, тарифы/FSM, 3x-UI интеграция, дилер-цепочка, админка,
тест-тариф, sub-ссылки, напоминания 3/1 дня, Postgres, systemd.

## 10. Отложено
1) Покупка кредитов дилером за деньги (после курса). 2) Telegram Stars.
3) Домен + вебхук Heleket (вместо polling 30с). 4) Белый список IP в панели (только 83.243.86.111).

## 11. Возобновление в новом чате
Вставить этот файл + «продолжаем с ...» + свежие логи/скрины.