# Voicebox: чеклист с голой Ubuntu → боты

Отдельный GPU-сервер с [jamiepine/voicebox](https://github.com/jamiepine/voicebox).
Боты на `144.124.239.159` ходят к нему по HTTP (`:17493`).

Эталон того, что уже поднимали: **NVIDIA L4 24 GB**, код в `/opt/voicebox`,
пользователь `deploy`, UFW только с бот-сервера.

---

## 0. Перед выключением GPU (пока сервер ещё жив)

Сохранить профили голоса, иначе придётся заново заливать sample:

```bash
# на GPU-сервере
docker volume ls | grep voicebox

sudo tar -czf ~/voicebox-data-backup.tgz \
  -C /var/lib/docker/volumes voicebox_voicebox-data \
  -C /var/lib/docker/volumes voicebox_huggingface-cache
# скачать архив к себе / на бот-сервер
```

Записать:

| Что | Значение (рабочий эталон) |
|-----|---------------------------|
| Имя профиля | `КостяМолитвы` |
| Profile ID | `16be89c3-b2e3-429d-a191-2377241e09d6` |
| Движок | `qwen` / `1.7B` |
| Tempo | `VOICEBOX_ATEMPO=0.92` |

Пока GPU выключен — в biblia (prod + dev):

```env
VOICEBOX_ENABLED=0
```

Молитвы уйдут на SpeechKit. После подъёма вернуть `VOICEBOX_ENABLED=1`.

---

## 1. Заказать машину

| Параметр | Значение |
|----------|----------|
| GPU | **1× NVIDIA L4 24 GB** |
| CPU | 4–8 core |
| RAM | **16 GB** (лучше 24) |
| Диск | **≥120 GB** |
| ОС | **Ubuntu 22.04/24.04 + NVIDIA GPU** |
| Сеть | публичный IPv4 |

---

## 2. Первый вход (с Mac)

```bash
ssh root@IP_GPU
nvidia-smi          # должна быть L4 ~23034 MiB
free -h && df -h
```

Если `nvidia-smi` нет — сначала драйвер NVIDIA у провайдера, без этого дальше нет смысла.

---

## 3. Базовые пакеты + Docker

```bash
apt update
apt install -y git curl ca-certificates ufw

curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
docker version
```

---

## 4. NVIDIA Container Toolkit

По [официальной инструкции NVIDIA](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
для вашей Ubuntu, затем проверка:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Должна показаться та же L4.

---

## 5. Пользователь `deploy`

```bash
adduser deploy
usermod -aG sudo,docker deploy
# ключ с бот-сервера (~/.ssh/id_ed25519_github.pub)
# → /home/deploy/.ssh/authorized_keys
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
```

Дальше удобнее под `deploy` / через `sudo`.

С бот-сервера:

```bash
ssh -i ~/.ssh/id_ed25519_github deploy@IP_GPU
```

---

## 6. Поставить Voicebox

```bash
cd /opt
git clone https://github.com/jamiepine/voicebox.git
cd /opt/voicebox
```

В `docker-compose.yml` (как в рабочем сетапе):

- порт: `"0.0.0.0:17493:17493"`
- volumes: `./output` → generations, `voicebox-data`, `huggingface-cache`
- лимиты: `cpus: "8"`, `memory: 16G`
- GPU: `driver: nvidia`, `count: 1`, `capabilities: [gpu]`
- `restart: unless-stopped`
- `container_name: voicebox`

Пример ключевых блоков:

```yaml
services:
  voicebox:
    build: .
    container_name: voicebox
    restart: unless-stopped
    ports:
      - "0.0.0.0:17493:17493"
    volumes:
      - ./output:/app/data/generations
      - voicebox-data:/app/data
      - huggingface-cache:/home/voicebox/.cache/huggingface
    deploy:
      resources:
        limits:
          cpus: "8"
          memory: 16G
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

Запуск (первый раз долго — сборка + модели):

```bash
cd /opt/voicebox
docker compose up -d --build
docker compose logs -f
```

Проверка на самом GPU:

```bash
curl -s http://127.0.0.1:17493/health
curl -s http://127.0.0.1:17493/profiles
```

---

## 7. Firewall (обязательно)

API Voicebox **без пароля**. Не открывать `17493` на весь интернет.

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow from 144.124.239.159 to any port 22 proto tcp comment 'bot SSH'
ufw allow from 144.124.239.159 to any port 17493 comment 'bots Voicebox API'
# опционально SSH с вашего VPN / дома:
# ufw allow from ВАШ_IP to any port 22 proto tcp comment 'Mac SSH'
ufw enable
ufw status numbered
```

UI в браузере с Mac лучше не держать постоянно; профиль создаёте через avatar
(`/voice_sample`).

---

## 8. (Опционально) Восстановить бэкап volumes

Если есть `voicebox-data-backup.tgz` — восстановить docker volumes
**до** или вместо пустого старта, затем `docker compose up -d`.
Тогда старый `profile_id` может сохраниться.

Иначе — шаг 9.

---

## 9. Создать голос через avatar

В `avatar_kostya/.env`:

```env
VOICEBOX_ENABLED=1
VOICEBOX_BASE_URL=http://НОВЫЙ_IP:17493
VOICEBOX_ENGINE=qwen
VOICEBOX_MODEL_SIZE=1.7B
VOICEBOX_LANGUAGE=ru
```

Рестарт:

```bash
cd /home/appuser/dev/kostya/avatar_kostya && ./scripts/deploy_prod.sh
# или: sudo supervisorctl restart avatar:avatar_kostya
```

В Telegram:

1. `/voice_sample` — 15–30 сек чистой речи → профиль **`КостяМолитвы`**
2. при желании `/voice_add_sample` — ещё 1–2 sample
3. `/voice_models` — скопировать **новый profile id**

---

## 10. Подключить Biblia

В `/home/appuser/biblia/.env` и `/home/appuser/dev/kostya/biblia/.env`:

```env
VOICEBOX_ENABLED=1
VOICEBOX_BASE_URL=http://НОВЫЙ_IP:17493
VOICEBOX_PROFILE_ID=<новый_id_из_шага_9>
VOICEBOX_ENGINE=qwen
VOICEBOX_MODEL_SIZE=1.7B
VOICEBOX_LANGUAGE=ru
VOICEBOX_ATEMPO=0.92
VOICEBOX_INSTRUCT=Warm natural prayerful speech, gentle rhythm, slight emotional variation, not monotone and not robotic. Soft unhurried pace. The final word амИнь: stress on capital И (a-MÍN), clear and solemn.
```

Рестарт biblia:

```bash
sudo supervisorctl restart bots:biblia_bot
# или полный деплой:
# cd /home/appuser/dev/kostya/biblia && sudo ./scripts/deploy_prod.sh
```

Проверка с бот-сервера:

```bash
curl -s --connect-timeout 5 http://НОВЫЙ_IP:17493/health
curl -s http://НОВЫЙ_IP:17493/profiles
# затем /prayer в боте Библия
```

---

## 11. Smoke после подъёма

| Проверка | Ожидание |
|----------|----------|
| `nvidia-smi` | L4, процесс docker |
| `docker ps` | `voicebox` Up, `0.0.0.0:17493` |
| `ufw status` | 17493 только с `144.124.239.159` |
| curl `/health` с бот-сервера | ok |
| `/voice_models` в avatar | виден `КостяМолитвы` |
| `/prayer` в biblia | голос + atempo 0.92 |

---

## Когда выключаете «на паузу»

1. `VOICEBOX_ENABLED=0` в biblia (+ рестарт)
2. (желательно) бэкап volumes — шаг 0
3. Снос / стоп GPU у провайдера

При возврате — с п.1 этого чеклиста. Код ботов переписывать не нужно:
меняются только **IP**, **profile_id** и флаг **ENABLED** в `.env`.

---

## Что уже есть в коде (не трогать при пересоздании)

| Проект | Назначение |
|--------|------------|
| `avatar_kostya/bot/features/voicebox_admin.py` | `/voice_sample`, `/voice_add_sample`, `/voice_models`, `/voice_test` |
| `avatar_kostya/bot/services/voicebox_client.py` | HTTP-клиент API |
| `biblia/bot/services/voicebox_tts.py` | TTS молитв + atempo → OGG |
| `biblia/bot/features/personal_prayer.py` | `/prayer` → Voicebox (fallback SpeechKit) |
