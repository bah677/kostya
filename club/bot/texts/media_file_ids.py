"""
Telegram file_id медиа, которые бот отправляет пользователям.

Для каждого бота (club / twin) — своя копия в bot/texts или twin_texts/<name>/.
Пустая строка = медиа не подключено (кнопка/отправка скрывается где предусмотрено).
"""

# OnboardingFeature — video note (кружки) после /start, по порядку.
# Пустые строки пропускаются. Для twin — свой список в twin_texts/<name>/media_file_ids.py.
ONBOARDING_VIDEO_NOTE_FILE_IDS: tuple[str, ...] = (
    "DQACAgIAAxkBAAKgiWoevhsjUy7v5ygQPj1QJZ_lUhbwAAJJmQACbSfoSEtp4C246bPdOwQ",
    "DQACAgIAAxkBAAKgjWoeviUYLfi-amAHfBifpw44EG1_AAJSmQACbSfoSM9aq8GHvUo6OwQ",  # кружок 2 — подставьте file_id
    "DQACAgIAAxkBAAKgkWoevi218Zga1JXsN47dUbsMMCECAAJdmQACbSfoSMvkZqlIhOayOwQ",  # кружок 3
    "DQACAgIAAxkBAAKglWoevjegyhYkmES5WTMWY_lOpzAtAAJimQACbSfoSJENO6FgS9fUOwQ",  # кружок 4
)

# Обратная совместимость (первый непустой id)
VIDEO_CIRCLE_FILE_ID = next(
    (x for x in ONBOARDING_VIDEO_NOTE_FILE_IDS if x and str(x).strip()),
    "",
)

# BenefitFeature — аудио подарков
PRAYER_260408_FILE_ID = (
    "CQACAgIAAyEFAATnayVSAAICT2nV25zfb95IL5IS-tLz9p5pCGfKAAJpkgAC5n6xSq_kfz8HkrtzOwQ"
)
PRAYER_260425_FILE_ID = (
    "CQACAgIAAyEFAATnayVSAAIHZmnslDrHX98ZlX10nsjQaf7cUT1MAAJ9owACZiFoS5BXNs7DNAbWOwQ"
)
PRAYER_GRATITUDE_FILE_ID = (
    "CQACAgIAAxkBAAL1DmpJgez8xvT4Srfnxk8_ZZoC6gxcAAIOogAC9iHZSUN_UUpf-3NiPAQ"
)

# PaymentFeature — PDF публичной оферты (callback «Скачать оферту»)
# file_id привязан к боту: для twin — свой id в twin_texts/<name>/media_file_ids.py
PUBLIC_OFFER_PDF_FILE_ID = (
    "BQACAgIAAxkBAAIHHWn2gLClUcHdksGPX2qe9unfZbTIAALznQACVgqwS_EsowffoNBDOwQ"
)

# LegalConsentFeature — политика и согласие на ПДн (кнопки экрана согласия)
PRIVACY_POLICY_PDF_FILE_ID = (
    "BQACAgIAAxkBAALu8GpE5b9X9QZAzq8BLC1D3fIbqP_lAALNpQACvzUpSjC93rcNTlZnPAQ"
)
PERSONAL_DATA_CONSENT_PDF_FILE_ID = (
    "BQACAgIAAxkBAALu7GpE5QwfsMGfExDGFvpdeaCWjGWdAALGpQACvzUpSjmNomvij8aSPAQ"
)
