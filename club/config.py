# app/config.py
import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@dataclass(frozen=True)
class Config:
    """Конфигурация приложения."""

    MIRON_BOT_TOKEN: str
    OPENAI_API_KEY: str
    #: DeepSeek API (openai-совместимый) — аналитическое заключение по /churn; пусто — только цифровой отчёт.
    DEEPSEEK_API_KEY: Optional[str] = None

    WORKFLOW_ID: str = ""

    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""

    BZB_API_KEY: str = ""
    BZB_API_URL: str = ""

    #: Провайдер для RUB (yookassa | bzb). `.env`: ``PAYMENT_PROVIDER_RUB``
    PAYMENT_PROVIDER_RUB: str = "yookassa"
    #: Провайдер для USD (yookassa | bzb). `.env`: ``PAYMENT_PROVIDER_USD``
    PAYMENT_PROVIDER_USD: str = "bzb"
    #: Автопродление / save_payment_method (YooKassa). Пока выкл.; позже — и per-tariff.
    SUBSCRIPTION_RECURRING_ENABLED: bool = False

    DB_HOST: str = ""
    DB_PORT: str = ""
    DB_NAME: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""

    #: Read-only доступ к БД бота Библии для сквозного отчёта кампаний (опционально).
    BIBLIA_DB_HOST: str = ""
    BIBLIA_DB_PORT: str = "5432"
    BIBLIA_DB_NAME: str = ""
    BIBLIA_DB_USER: str = ""
    BIBLIA_DB_PASSWORD: str = ""

    ADMIN_CHANNEL_ID: Optional[str] = None
    #: Telegram user_id супер-админа, который может управлять таблицей admins.
    SUPER_ADMIN_ID: int = 0
    #: Явный chat id админ-супергруппы для консольных хендлеров. 0 — попытаться взять из ADMIN_CHANNEL_ID (если это число).
    ADMIN_GROUP_ID: int = 0
    #: Топик «диалог агента / продажи» (legacy 947): пересылки из MessagingFeature и ответы отдела продаж в admin_console.
    ADMIN_DIALOG_THREAD_ID: int = 947
    #: Супергруппа-форум для персональных топиков диалогов (один топик на пользователя).
    #: Если 0 — пересылка идёт в ADMIN_CHANNEL_ID / ADMIN_DIALOG_THREAD_ID по-старому.
    #: `.env`: `DIALOG_FORUM_GROUP_ID`
    DIALOG_FORUM_GROUP_ID: int = 0
    PAYMENT_THREAD_ID: int = 0
    SUPPORT_THREAD_ID: int = 0
    MEDIA_ID_TOPIC_ID: int = 0

    CLUB_GROUP_ID: int = 0
    #: Ссылка на пост закрытой группы (формат t.me/c/...), для уже вступивших.
    #: Задаётся в .env: CLUB_POST_LINK
    CLUB_POST_LINK: str = ""
    CLUB_INVITE_TTL_HOURS: int = 24
    #: Час UTC (0–23), когда раз в сутки гонять отзывы инвайтов и аудит членов клуба без лицензии.
    CLUB_GROUP_AUDIT_HOUR_UTC: int = 21
    #: После окончания подписки столько полных суток пользователь ещё может остаться в чате при ночном аудите.
    #: `.env`: `CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS`
    CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS: int = 3
    #: Ночной аудит группы (состав + лицензия). Для ``BOT_VARIANT=nastya`` по умолчанию выкл.
    CLUB_GROUP_NIGHTLY_AUDIT_ENABLED: bool = True
    #: Цепочка напоминаний об окончании подписки (9:00 МСК). Для nastya по умолчанию выкл.
    SUBSCRIPTION_REMINDER_ENABLED: bool = True
    #: Блоки DeepSeek в ежедневном отчёте v2. Для nastya по умолчанию выкл.
    CLUB_REPORT_INCLUDE_DEEPSEEK: bool = True
    #: Ежедневный клубный отчёт в ADMIN_CHANNEL_ID: час (0–23), как legacy Adm REPORT_HOUR. Час пояс — Europe/Moscow.
    REPORT_HOUR: int = 0
    #: Ежедневный клубный отчёт: минута (0–59), как legacy Adm REPORT_MINUTE.
    REPORT_MINUTE: int = 1
    #: Если админский чат — супергруппа с топиками: `message_thread_id` для отчёта; 0 — без указания топика.
    #: `.env`: `CLUB_REPORT_THREAD_ID`
    CLUB_REPORT_THREAD_ID: int = 0
    #: Параллельно слать legacy-отчёт вместе с v2 (переходный период).
    REPORT_LEGACY_ENABLED: bool = True
    #: Доска желаний (благотворительные просьбы). По умолчанию выкл.; см. wish_board_active.
    WISH_BOARD_ENABLED: bool = False
    #: Топик админ-канала для модерации заявок (approve/reject).
    WISH_BOARD_ADMIN_TOPIC_ID: int = 0
    #: Топик закрытого клуба — дайджест новых просьб.
    WISH_BOARD_DIGEST_TOPIC_ID: int = 0
    WISH_BOARD_DEFAULT_EXPIRE_DAYS: int = 30
    WISH_BOARD_TAKEN_TIMEOUT_DAYS: int = 7
    WISH_BOARD_DIGEST_HOUR: int = 10
    WISH_BOARD_DIGEST_MINUTE: int = 0
    #: Напоминание в группу об «зависших» открытых просьбах (МСК).
    WISH_BOARD_GROUP_REMINDER_HOUR: int = 17
    WISH_BOARD_GROUP_REMINDER_MINUTE: int = 0
    WISH_BOARD_GROUP_REMINDER_OPEN_DAYS: int = 2
    WISH_BOARD_GROUP_REMINDER_GAP_DAYS: int = 3
    WISH_BOARD_GROUP_REMINDER_MAX: int = 3
    WISH_BOARD_MAX_ACTIVE_PER_REQUESTER: int = 2
    #: URL legacy admin-БД (старый Adm) для миграции исторических club_snapshots.
    LEGACY_ADMIN_DB_URL: Optional[str] = None
    WELCOME_TOPIC_ID: int = 0
    #: Путь к JSONL-файлу отладки входа в группу (``chat_member`` + join-сообщения). Пусто / ``0`` — выкл.
    CLUB_JOIN_DEBUG_LOG: str = ""
    REACT_TOPIC_ID: int = 0
    #: Срок жизни ссылки активации подарочной подписки (`.env`: `GIFT_LINK_VALIDITY_DAYS`).
    #: Пишется в `gifts.expires_at`; тексты дарителю берут это же число (+ `russian_days_phrase`).
    GIFT_LINK_VALIDITY_DAYS: int = 30

    #: Username основного бота без «@». Запасной вариант, если ``get_me()`` недоступен (сети, rate limit).
    TELEGRAM_BOT_USERNAME: Optional[str] = None
    #: PDF оферты в оплате: Telegram file_id (перекрывает ``media_file_ids.py``). У каждого бота свой id.
    PUBLIC_OFFER_PDF_FILE_ID: Optional[str] = None
    #: PDF политики конфиденциальности (экран согласия и кнопка «Политика»).
    PRIVACY_POLICY_PDF_FILE_ID: Optional[str] = None
    #: PDF согласия на обработку персональных данных (экран согласия).
    PERSONAL_DATA_CONSENT_PDF_FILE_ID: Optional[str] = None

    #: ``club`` — основной бот; ``nastya`` — временный сценарий /start без ИИ-агента.
    BOT_VARIANT: str = "club"

    LOG_LEVEL: str = "INFO"
    #: Временная отладка: одна JSON-запись в лог на каждый запрос менеджера в LLM
    #: (полный ``messages``, RAG-чанки, флаги). ``.env``: ``LLM_AGENT_REQUEST_DUMP=1``
    LLM_AGENT_REQUEST_DUMP: bool = False
    MAX_WORKERS: int = 5

    #: Локальный каталог архива входящих медиафайлов (от корня проекта, если относительный).
    #: Пусто / 0 / false / off — архивирование выключено.
    MEDIA_INBOUND_ARCHIVE_DIR: str = "data/media_inbound"

    #: При ``True`` один раз после старта бота прогоняется вся цепочка сообщений о подписке в указанные ЛС (см. ``SUBSCRIPTION_CHAIN_TEST_USER_IDS``).
    SUBSCRIPTION_CHAIN_TEST: bool = False
    #: Пауза перед первым сообщением и между шагами (сек).
    SUBSCRIPTION_CHAIN_TEST_DELAY_SEC: int = 30
    #: Список ``user_id`` для тестовой цепочки; если тест включён и список в .env пуст — используются дефолтные id разработчиков.
    SUBSCRIPTION_CHAIN_TEST_USER_IDS: Tuple[int, ...] = ()

    #: Внешний prod-RAG из avatar_kostya (только чтение Chroma, без индексации в клубе).
    #: ``.env``: ``RAG_ENABLED``, ``RAG_CHROMA_PERSIST_DIR`` (каталог ``chroma_data`` того проекта).
    RAG_ENABLED: bool = False
    RAG_CHROMA_PERSIST_DIR: str = ""
    #: Имена коллекций и модель embeddings — как в avatar_kostya (менять только при смене prod).
    RAG_EXPERT_COLLECTION: str = "expert_materials"
    RAG_GOLDEN_COLLECTION: str = "golden_examples"
    RAG_EMBEDDING_MODEL: str = "text-embedding-3-small"
    #: Глубина RAG-поиска (качество важнее скорости; см. openai_client.rag_search_planner).
    RAG_PLANNER_MAX_QUERIES: int = 8
    RAG_TOP_K_PER_QUERY: int = 8
    RAG_MAX_CHUNKS: int = 24
    RAG_METADATA_MAX_CHUNKS: int = 24
    RAG_GOLDEN_TOP_K: int = 3
    RAG_GOLDEN_QUERY_COUNT: int = 4

    #: Read-only HTTP API для удалённого semantic search (вариант C).
    RAG_READ_API_HOST: str = "127.0.0.1"
    RAG_READ_API_PORT: int = 8790
    RAG_READ_API_TOKEN: str = ""

    #: Member-агент в личке для участников с активной лицензией (иначе — sales AgentsClient).
    MEMBER_AGENT_ENABLED: bool = True
    #: Двухуровневая проверка ответа member-агента (генератор + верификатор).
    MEMBER_AGENT_VERIFIER_ENABLED: bool = True
    #: Повторы генерации после отклонения верификатором (всего генераций = 1 + это значение).
    MEMBER_AGENT_VERIFIER_MAX_RETRIES: int = 2

    #: AI-персонализация напоминаний о продлении (7/5/3/1 день); fallback — REMINDER_CONFIG.
    MEMBER_RENEWAL_AI_ENABLED: bool = True
    #: AI-персонализация churn (+5/+10/+30); +18 остаётся шаблоном (опрос с кнопками).
    MEMBER_CHURN_AI_ENABLED: bool = True
    #: Извлечение stated_goals из реплик участника (только append, без перезаписи).
    MEMBER_GOALS_EXTRACT_ENABLED: bool = True
    #: Проактивные сообщения member-агента активным участникам.
    MEMBER_PROACTIVE_ENABLED: bool = True
    #: Часы запуска проактива (МСК), через запятую.
    MEMBER_PROACTIVE_HOURS: str = "9,12,15,18,21"
    MEMBER_PROACTIVE_MINUTE: int = 30
    #: Макс. проактивных сообщений за один проход планировщика.
    MEMBER_PROACTIVE_MAX_PER_RUN: int = 15

    #: Топик админ-группы «Расписание» (`message_thread_id`): вечерний дайджест и правки.
    CLUB_SCHEDULE_ADMIN_TOPIC_ID: int = 5655
    #: Ежедневная публикация расписания в топик (20:00 МСК по умолчанию).
    CLUB_SCHEDULE_TOPIC_DIGEST_ENABLED: bool = True
    CLUB_SCHEDULE_TOPIC_DIGEST_HOUR: int = 20
    CLUB_SCHEDULE_TOPIC_DIGEST_MINUTE: int = 0
    CLUB_SCHEDULE_TOPIC_DIGEST_DAYS: int = 14

    #: Сегмент 1 «застряли в диалоге»: LLM+RAG пинг и кнопка «Получить ответ».
    FOLLOWUP_STUCK_DIALOG_ENABLED: bool = True

    #: Временный вывод легаси (103 + диалог) в stuck_dialog: 100 чел./день в 10:00 МСК.
    LEGACY_103_REACTIVATION_ENABLED: bool = True
    LEGACY_103_REACTIVATION_BATCH_SIZE: int = 100
    LEGACY_103_REACTIVATION_HOUR: int = 10
    LEGACY_103_REACTIVATION_MINUTE: int = 0

    #: Ежедневный дайджест клубной группы для участников (отдельный топик форума).
    CLUB_DIGEST_ENABLED: bool = False
    CLUB_DIGEST_HOUR: int = 10
    CLUB_DIGEST_MINUTE: int = 0
    #: Топик, куда бот публикует дайджест (``message_thread_id``). Участники читают здесь.
    CLUB_DIGEST_TOPIC_ID: int = 0
    CLUB_DIGEST_LOOKBACK_HOURS: int = 24
    CLUB_DIGEST_MIN_MESSAGES: int = 5
    CLUB_DIGEST_MIN_PARTICIPANTS: int = 2

    #: Цитата из Писания в топик дайджеста по слотам (7/9/12/15/18/21 МСК, минута 1–15).
    CLUB_SCRIPTURE_PULSE_ENABLED: bool = False
    CLUB_SCRIPTURE_PULSE_HOURS: str = "7,9,12,15,18,21"
    CLUB_SCRIPTURE_PULSE_MINUTE_MIN: int = 1
    CLUB_SCRIPTURE_PULSE_MINUTE_MAX: int = 15
    CLUB_SCRIPTURE_PULSE_MIN_MESSAGES: int = 1

    #: Рассылки дайджеста и цитат в личку (пилот / full rollout).
    CLUB_OUTREACH_DM_ENABLED: bool = False
    CLUB_OUTREACH_DM_PILOT_ONLY: bool = True
    CLUB_OUTREACH_PILOT_SIZE: int = 30
    CLUB_OUTREACH_PILOT_LOOKBACK_DAYS: int = 30
    CLUB_OUTREACH_DAILY_LIMIT: int = 3

    def __post_init__(self):
        self._validate_required()

    def _validate_required(self):
        required = {
            "MIRON_BOT_TOKEN": self.MIRON_BOT_TOKEN,
            "OPENAI_API_KEY": self.OPENAI_API_KEY,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Отсутствуют обязательные переменные: {', '.join(missing)}")

    @property
    def database_url(self) -> str:
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def has_admin_channel(self) -> bool:
        return bool(self.ADMIN_CHANNEL_ID)

    def resolved_admin_group_id(self) -> int:
        """Числовой id супергруппы для регистрации admin_console; 0 — не задано."""
        if self.ADMIN_GROUP_ID:
            return int(self.ADMIN_GROUP_ID)
        raw = (self.ADMIN_CHANNEL_ID or "").strip()
        if not raw:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    @property
    def wish_board_active(self) -> bool:
        """Вкл. только при явном WISH_BOARD_ENABLED и заданных топиках модерации и дайджеста."""
        return (
            self.WISH_BOARD_ENABLED
            and self.WISH_BOARD_ADMIN_TOPIC_ID > 0
            and self.WISH_BOARD_DIGEST_TOPIC_ID > 0
        )

    @property
    def club_schedule_topic_active(self) -> bool:
        """Топик расписания в админ-группе: дайджест + приём правок."""
        return (
            self.CLUB_SCHEDULE_TOPIC_DIGEST_ENABLED
            and self.CLUB_SCHEDULE_ADMIN_TOPIC_ID > 0
            and self.resolved_admin_group_id() != 0
            and self.has_admin_channel
        )

    @property
    def club_outreach_dm_active(self) -> bool:
        """Дайджест и цитаты в личку (batch + LLM per user)."""
        return bool(self.CLUB_OUTREACH_DM_ENABLED and (self.DEEPSEEK_API_KEY or "").strip())

    @property
    def club_digest_group_active(self) -> bool:
        """Публикация дайджеста в топик группы (выкл. при outreach DM)."""
        if self.club_outreach_dm_active:
            return False
        return bool(self.CLUB_DIGEST_ENABLED)

    @property
    def club_scripture_group_active(self) -> bool:
        """Публикация цитат в топик группы (выкл. при outreach DM)."""
        if self.club_outreach_dm_active:
            return False
        return bool(self.CLUB_SCRIPTURE_PULSE_ENABLED)

    @property
    def has_yookassa(self) -> bool:
        return bool(self.YOOKASSA_SHOP_ID and self.YOOKASSA_SECRET_KEY)

    @property
    def media_inbound_archive_enabled(self) -> bool:
        v = str(self.MEDIA_INBOUND_ARCHIVE_DIR or "").strip().lower()
        if not v or v in ("0", "false", "no", "off"):
            return False
        return True

    @property
    def resolved_media_inbound_archive_root(self) -> Path:
        raw = Path(self.MEDIA_INBOUND_ARCHIVE_DIR.strip())
        if raw.is_absolute():
            return raw
        return Path(__file__).resolve().parent / raw

    @property
    def resolved_rag_chroma_persist_dir(self) -> Path:
        """Абсолютный путь к prod Chroma avatar_kostya (``RAG_CHROMA_PERSIST_DIR``)."""
        raw = Path(str(self.RAG_CHROMA_PERSIST_DIR or "").strip())
        if not raw:
            return raw
        if raw.is_absolute():
            return raw
        return Path(__file__).resolve().parent / raw


def _parse_gift_link_validity_days(raw: Optional[str]) -> int:
    """Дни жизни ссылки активации подарка; по умолчанию 30, допустимо 1–3650."""
    if raw is None or not str(raw).strip():
        return 30
    try:
        n = int(str(raw).strip(), 10)
    except ValueError:
        return 30
    return max(1, min(3650, n))


def russian_days_phrase(days: int) -> str:
    """«1 день», «21 день», «3 дня», «25 дней» — для текстов интерфейса."""
    n = int(days)
    if n <= 0:
        n = 1
    d100 = n % 100
    if 11 <= d100 <= 14:
        return f"{n} дней"
    d10 = n % 10
    if d10 == 1:
        return f"{n} день"
    if 2 <= d10 <= 4:
        return f"{n} дня"
    return f"{n} дней"


def _normalize_env_username(raw: str) -> Optional[str]:
    if not raw or not raw.strip():
        return None
    return raw.strip().lstrip("@") or None


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _env_bool_nastya_off(name: str, *, club_default: bool = True) -> bool:
    """Явный env перекрывает; для ``BOT_VARIANT=nastya`` без env — выкл."""
    raw = (os.getenv(name) or "").strip()
    if raw:
        return _env_bool(name, club_default)
    if (os.getenv("BOT_VARIANT") or "club").strip().lower() == "nastya":
        return False
    return club_default


def _safe_int_env(name: str, default: int, *, min_v: int, max_v: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw, 10)
    except ValueError:
        return default
    return max(min_v, min(max_v, value))


def _parse_subscription_chain_test_user_ids(raw: str) -> Tuple[int, ...]:
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(int(p, 10))
        except ValueError:
            continue
    return tuple(out)


def _norm_club_join_debug_log(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    s = raw.strip()
    if not s or s.lower() in ("0", "false", "off", "no"):
        return ""
    return s


def load_config() -> Config:
    media_raw = os.getenv("MEDIA_INBOUND_ARCHIVE_DIR", "data/media_inbound")
    _st_test = _env_bool("SUBSCRIPTION_CHAIN_TEST", False)
    _st_raw = (os.getenv("SUBSCRIPTION_CHAIN_TEST_USER_IDS") or "").strip()
    if _st_test:
        _st_uids = (
            _parse_subscription_chain_test_user_ids(_st_raw)
            if _st_raw
            else (367302291, 304631563)
        )
    else:
        _st_uids = ()
    try:
        _st_delay = int(float(os.getenv("SUBSCRIPTION_CHAIN_TEST_DELAY_SEC", "30") or "30"))
    except ValueError:
        _st_delay = 30
    if _st_delay < 0:
        _st_delay = 30

    return Config(
        MIRON_BOT_TOKEN=os.getenv("MIRON_BOT_TOKEN", ""),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        DEEPSEEK_API_KEY=(os.getenv("DEEPSEEK_API_KEY") or "").strip() or None,
        WORKFLOW_ID=os.getenv("WORKFLOW_ID", ""),
        YOOKASSA_SHOP_ID=os.getenv("YOOKASSA_SHOP_ID", ""),
        YOOKASSA_SECRET_KEY=os.getenv("YOOKASSA_SECRET_KEY", ""),
        BZB_API_KEY=os.getenv("BZB_API_KEY", ""),
        BZB_API_URL=os.getenv("BZB_API_URL", ""),
        PAYMENT_PROVIDER_RUB=(
            os.getenv("PAYMENT_PROVIDER_RUB", "yookassa") or "yookassa"
        ).strip().lower(),
        PAYMENT_PROVIDER_USD=(
            os.getenv("PAYMENT_PROVIDER_USD", "bzb") or "bzb"
        ).strip().lower(),
        SUBSCRIPTION_RECURRING_ENABLED=_env_bool(
            "SUBSCRIPTION_RECURRING_ENABLED", False
        ),
        DB_HOST=os.getenv("DB_HOST", "localhost"),
        DB_PORT=os.getenv("DB_PORT", ""),
        DB_NAME=os.getenv("DB_NAME", ""),
        DB_USER=os.getenv("DB_USER", ""),
        DB_PASSWORD=os.getenv("DB_PASSWORD", ""),
        BIBLIA_DB_HOST=os.getenv("BIBLIA_DB_HOST", ""),
        BIBLIA_DB_PORT=os.getenv("BIBLIA_DB_PORT", "5432"),
        BIBLIA_DB_NAME=os.getenv("BIBLIA_DB_NAME", ""),
        BIBLIA_DB_USER=os.getenv("BIBLIA_DB_USER", ""),
        BIBLIA_DB_PASSWORD=os.getenv("BIBLIA_DB_PASSWORD", ""),
        ADMIN_CHANNEL_ID=os.getenv("ADMIN_CHANNEL_ID"),
        SUPER_ADMIN_ID=int(os.getenv("SUPER_ADMIN_ID", "0") or "0"),
        ADMIN_GROUP_ID=int(os.getenv("ADMIN_GROUP_ID", "0") or "0"),
        ADMIN_DIALOG_THREAD_ID=int(os.getenv("ADMIN_DIALOG_THREAD_ID", "947")),
        DIALOG_FORUM_GROUP_ID=int(os.getenv("DIALOG_FORUM_GROUP_ID", "0") or "0"),
        PAYMENT_THREAD_ID=int(os.getenv("PAYMENT_THREAD_ID", "0")),
        SUPPORT_THREAD_ID=int(os.getenv("SUPPORT_THREAD_ID", "0")),
        MEDIA_ID_TOPIC_ID=int(os.getenv("MEDIA_ID_TOPIC_ID", "0")),
        CLUB_GROUP_ID=int(os.getenv("CLUB_GROUP_ID", "0")),
        CLUB_POST_LINK=(os.getenv("CLUB_POST_LINK") or "").strip(),
        CLUB_INVITE_TTL_HOURS=int(os.getenv("CLUB_INVITE_TTL_HOURS", "24")),
        CLUB_GROUP_AUDIT_HOUR_UTC=int(os.getenv("CLUB_GROUP_AUDIT_HOUR_UTC", "21")) % 24,
        CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS=max(
            0, int(os.getenv("CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS", "3") or "3")
        ),
        CLUB_GROUP_NIGHTLY_AUDIT_ENABLED=_env_bool_nastya_off(
            "CLUB_GROUP_NIGHTLY_AUDIT_ENABLED"
        ),
        SUBSCRIPTION_REMINDER_ENABLED=_env_bool_nastya_off(
            "SUBSCRIPTION_REMINDER_ENABLED"
        ),
        CLUB_REPORT_INCLUDE_DEEPSEEK=_env_bool_nastya_off(
            "CLUB_REPORT_INCLUDE_DEEPSEEK"
        ),
        REPORT_HOUR=max(0, min(23, int(os.getenv("REPORT_HOUR", "0") or "0"))),
        REPORT_MINUTE=max(0, min(59, int(os.getenv("REPORT_MINUTE", "1") or "1"))),
        CLUB_REPORT_THREAD_ID=int(os.getenv("CLUB_REPORT_THREAD_ID", "0") or "0"),
        REPORT_LEGACY_ENABLED=_env_bool("REPORT_LEGACY_ENABLED", True),
        WISH_BOARD_ENABLED=_env_bool("WISH_BOARD_ENABLED", False),
        WISH_BOARD_ADMIN_TOPIC_ID=int(os.getenv("WISH_BOARD_ADMIN_TOPIC_ID", "0") or "0"),
        WISH_BOARD_DIGEST_TOPIC_ID=int(
            os.getenv("WISH_BOARD_DIGEST_TOPIC_ID", "0") or "0"
        ),
        WISH_BOARD_DEFAULT_EXPIRE_DAYS=max(
            1, int(os.getenv("WISH_BOARD_DEFAULT_EXPIRE_DAYS", "30") or "30")
        ),
        WISH_BOARD_TAKEN_TIMEOUT_DAYS=max(
            1, int(os.getenv("WISH_BOARD_TAKEN_TIMEOUT_DAYS", "7") or "7")
        ),
        WISH_BOARD_DIGEST_HOUR=max(
            0, min(23, int(os.getenv("WISH_BOARD_DIGEST_HOUR", "10") or "10"))
        ),
        WISH_BOARD_DIGEST_MINUTE=max(
            0, min(59, int(os.getenv("WISH_BOARD_DIGEST_MINUTE", "0") or "0"))
        ),
        WISH_BOARD_GROUP_REMINDER_HOUR=max(
            0, min(23, int(os.getenv("WISH_BOARD_GROUP_REMINDER_HOUR", "17") or "17"))
        ),
        WISH_BOARD_GROUP_REMINDER_MINUTE=max(
            0,
            min(59, int(os.getenv("WISH_BOARD_GROUP_REMINDER_MINUTE", "0") or "0")),
        ),
        WISH_BOARD_GROUP_REMINDER_OPEN_DAYS=max(
            1, int(os.getenv("WISH_BOARD_GROUP_REMINDER_OPEN_DAYS", "2") or "2")
        ),
        WISH_BOARD_GROUP_REMINDER_GAP_DAYS=max(
            1, int(os.getenv("WISH_BOARD_GROUP_REMINDER_GAP_DAYS", "3") or "3")
        ),
        WISH_BOARD_GROUP_REMINDER_MAX=max(
            1, int(os.getenv("WISH_BOARD_GROUP_REMINDER_MAX", "3") or "3")
        ),
        WISH_BOARD_MAX_ACTIVE_PER_REQUESTER=max(
            1, int(os.getenv("WISH_BOARD_MAX_ACTIVE_PER_REQUESTER", "2") or "2")
        ),
        LEGACY_ADMIN_DB_URL=(os.getenv("LEGACY_ADMIN_DB_URL") or "").strip() or None,
        WELCOME_TOPIC_ID=int(os.getenv("WELCOME_TOPIC_ID", "0")),
        CLUB_JOIN_DEBUG_LOG=_norm_club_join_debug_log(os.getenv("CLUB_JOIN_DEBUG_LOG", "")),
        REACT_TOPIC_ID=int(os.getenv("REACT_TOPIC_ID", "0")),
        GIFT_LINK_VALIDITY_DAYS=_parse_gift_link_validity_days(os.getenv("GIFT_LINK_VALIDITY_DAYS")),
        TELEGRAM_BOT_USERNAME=_normalize_env_username(os.getenv("TELEGRAM_BOT_USERNAME", "")),
        PUBLIC_OFFER_PDF_FILE_ID=(os.getenv("PUBLIC_OFFER_PDF_FILE_ID") or "").strip() or None,
        PRIVACY_POLICY_PDF_FILE_ID=(os.getenv("PRIVACY_POLICY_PDF_FILE_ID") or "").strip() or None,
        PERSONAL_DATA_CONSENT_PDF_FILE_ID=(
            (os.getenv("PERSONAL_DATA_CONSENT_PDF_FILE_ID") or "").strip() or None
        ),
        BOT_VARIANT=(os.getenv("BOT_VARIANT") or "club").strip().lower(),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        LLM_AGENT_REQUEST_DUMP=_env_bool("LLM_AGENT_REQUEST_DUMP", False),
        MEDIA_INBOUND_ARCHIVE_DIR=media_raw,
        SUBSCRIPTION_CHAIN_TEST=_st_test,
        SUBSCRIPTION_CHAIN_TEST_DELAY_SEC=_st_delay,
        SUBSCRIPTION_CHAIN_TEST_USER_IDS=_st_uids,
        RAG_ENABLED=_env_bool("RAG_ENABLED", False),
        RAG_CHROMA_PERSIST_DIR=(
            os.getenv("RAG_CHROMA_PERSIST_DIR") or ""
        ).strip(),
        RAG_EXPERT_COLLECTION=os.getenv("RAG_EXPERT_COLLECTION", "expert_materials").strip()
        or "expert_materials",
        RAG_GOLDEN_COLLECTION=os.getenv("RAG_GOLDEN_COLLECTION", "golden_examples").strip()
        or "golden_examples",
        RAG_EMBEDDING_MODEL=os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small").strip()
        or "text-embedding-3-small",
        RAG_PLANNER_MAX_QUERIES=_safe_int_env("RAG_PLANNER_MAX_QUERIES", 8, min_v=1, max_v=16),
        RAG_TOP_K_PER_QUERY=_safe_int_env("RAG_TOP_K_PER_QUERY", 8, min_v=1, max_v=20),
        RAG_MAX_CHUNKS=_safe_int_env("RAG_MAX_CHUNKS", 24, min_v=4, max_v=40),
        RAG_METADATA_MAX_CHUNKS=_safe_int_env(
            "RAG_METADATA_MAX_CHUNKS", 24, min_v=4, max_v=50
        ),
        RAG_GOLDEN_TOP_K=_safe_int_env("RAG_GOLDEN_TOP_K", 3, min_v=1, max_v=10),
        RAG_GOLDEN_QUERY_COUNT=_safe_int_env(
            "RAG_GOLDEN_QUERY_COUNT", 4, min_v=1, max_v=8
        ),
        RAG_READ_API_HOST=(os.getenv("RAG_READ_API_HOST") or "127.0.0.1").strip()
        or "127.0.0.1",
        RAG_READ_API_PORT=_safe_int_env("RAG_READ_API_PORT", 8790, min_v=1, max_v=65535),
        RAG_READ_API_TOKEN=(os.getenv("RAG_READ_API_TOKEN") or "").strip(),
        MEMBER_AGENT_ENABLED=_env_bool("MEMBER_AGENT_ENABLED", True),
        MEMBER_AGENT_VERIFIER_ENABLED=_env_bool("MEMBER_AGENT_VERIFIER_ENABLED", True),
        MEMBER_AGENT_VERIFIER_MAX_RETRIES=_safe_int_env(
            "MEMBER_AGENT_VERIFIER_MAX_RETRIES", 2, min_v=0, max_v=5
        ),
        MEMBER_RENEWAL_AI_ENABLED=_env_bool("MEMBER_RENEWAL_AI_ENABLED", True),
        MEMBER_CHURN_AI_ENABLED=_env_bool("MEMBER_CHURN_AI_ENABLED", True),
        MEMBER_GOALS_EXTRACT_ENABLED=_env_bool("MEMBER_GOALS_EXTRACT_ENABLED", True),
        MEMBER_PROACTIVE_ENABLED=_env_bool_nastya_off(
            "MEMBER_PROACTIVE_ENABLED", club_default=True
        ),
        MEMBER_PROACTIVE_HOURS=os.getenv("MEMBER_PROACTIVE_HOURS", "9,12,15,18,21").strip(),
        MEMBER_PROACTIVE_MINUTE=_safe_int_env("MEMBER_PROACTIVE_MINUTE", 30, min_v=0, max_v=59),
        MEMBER_PROACTIVE_MAX_PER_RUN=_safe_int_env(
            "MEMBER_PROACTIVE_MAX_PER_RUN", 15, min_v=1, max_v=100
        ),
        CLUB_SCHEDULE_ADMIN_TOPIC_ID=int(
            os.getenv("CLUB_SCHEDULE_ADMIN_TOPIC_ID", "5655") or "0"
        ),
        CLUB_SCHEDULE_TOPIC_DIGEST_ENABLED=_env_bool(
            "CLUB_SCHEDULE_TOPIC_DIGEST_ENABLED", True
        ),
        CLUB_SCHEDULE_TOPIC_DIGEST_HOUR=_safe_int_env(
            "CLUB_SCHEDULE_TOPIC_DIGEST_HOUR", 20, min_v=0, max_v=23
        ),
        CLUB_SCHEDULE_TOPIC_DIGEST_MINUTE=_safe_int_env(
            "CLUB_SCHEDULE_TOPIC_DIGEST_MINUTE", 0, min_v=0, max_v=59
        ),
        CLUB_SCHEDULE_TOPIC_DIGEST_DAYS=_safe_int_env(
            "CLUB_SCHEDULE_TOPIC_DIGEST_DAYS", 14, min_v=1, max_v=60
        ),
        FOLLOWUP_STUCK_DIALOG_ENABLED=_env_bool("FOLLOWUP_STUCK_DIALOG_ENABLED", True),
        LEGACY_103_REACTIVATION_ENABLED=_env_bool(
            "LEGACY_103_REACTIVATION_ENABLED", True
        ),
        LEGACY_103_REACTIVATION_BATCH_SIZE=_safe_int_env(
            "LEGACY_103_REACTIVATION_BATCH_SIZE", 100, min_v=1, max_v=500
        ),
        LEGACY_103_REACTIVATION_HOUR=_safe_int_env(
            "LEGACY_103_REACTIVATION_HOUR", 10, min_v=0, max_v=23
        ),
        LEGACY_103_REACTIVATION_MINUTE=_safe_int_env(
            "LEGACY_103_REACTIVATION_MINUTE", 0, min_v=0, max_v=59
        ),
        CLUB_DIGEST_ENABLED=_env_bool("CLUB_DIGEST_ENABLED", False),
        CLUB_DIGEST_HOUR=_safe_int_env("CLUB_DIGEST_HOUR", 10, min_v=0, max_v=23),
        CLUB_DIGEST_MINUTE=_safe_int_env("CLUB_DIGEST_MINUTE", 0, min_v=0, max_v=59),
        CLUB_DIGEST_TOPIC_ID=int(os.getenv("CLUB_DIGEST_TOPIC_ID", "0") or "0"),
        CLUB_DIGEST_LOOKBACK_HOURS=_safe_int_env(
            "CLUB_DIGEST_LOOKBACK_HOURS", 24, min_v=1, max_v=72
        ),
        CLUB_DIGEST_MIN_MESSAGES=_safe_int_env(
            "CLUB_DIGEST_MIN_MESSAGES", 5, min_v=1, max_v=500
        ),
        CLUB_DIGEST_MIN_PARTICIPANTS=_safe_int_env(
            "CLUB_DIGEST_MIN_PARTICIPANTS", 2, min_v=1, max_v=50
        ),
        CLUB_SCRIPTURE_PULSE_ENABLED=_env_bool("CLUB_SCRIPTURE_PULSE_ENABLED", False),
        CLUB_SCRIPTURE_PULSE_HOURS=os.getenv(
            "CLUB_SCRIPTURE_PULSE_HOURS", "7,9,12,15,18,21"
        ),
        CLUB_SCRIPTURE_PULSE_MINUTE_MIN=_safe_int_env(
            "CLUB_SCRIPTURE_PULSE_MINUTE_MIN", 1, min_v=0, max_v=59
        ),
        CLUB_SCRIPTURE_PULSE_MINUTE_MAX=_safe_int_env(
            "CLUB_SCRIPTURE_PULSE_MINUTE_MAX", 15, min_v=1, max_v=59
        ),
        CLUB_SCRIPTURE_PULSE_MIN_MESSAGES=_safe_int_env(
            "CLUB_SCRIPTURE_PULSE_MIN_MESSAGES", 1, min_v=1, max_v=100
        ),
        CLUB_OUTREACH_DM_ENABLED=_env_bool("CLUB_OUTREACH_DM_ENABLED", False),
        CLUB_OUTREACH_DM_PILOT_ONLY=_env_bool("CLUB_OUTREACH_DM_PILOT_ONLY", True),
        CLUB_OUTREACH_PILOT_SIZE=_safe_int_env(
            "CLUB_OUTREACH_PILOT_SIZE", 30, min_v=1, max_v=500
        ),
        CLUB_OUTREACH_PILOT_LOOKBACK_DAYS=_safe_int_env(
            "CLUB_OUTREACH_PILOT_LOOKBACK_DAYS", 30, min_v=1, max_v=365
        ),
        CLUB_OUTREACH_DAILY_LIMIT=_safe_int_env(
            "CLUB_OUTREACH_DAILY_LIMIT", 3, min_v=1, max_v=20
        ),
    )


def rag_retrieval_settings_from_config(cfg: "Config") -> "RagRetrievalSettings":
    from openai_client.rag_search_planner import RagRetrievalSettings

    return RagRetrievalSettings(
        planner_max_queries=cfg.RAG_PLANNER_MAX_QUERIES,
        top_k_per_query=cfg.RAG_TOP_K_PER_QUERY,
        max_chunks_merged=cfg.RAG_MAX_CHUNKS,
        metadata_max_chunks=cfg.RAG_METADATA_MAX_CHUNKS,
        golden_top_k=cfg.RAG_GOLDEN_TOP_K,
        golden_query_count=cfg.RAG_GOLDEN_QUERY_COUNT,
    )


config = load_config()
