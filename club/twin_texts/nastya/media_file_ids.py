"""
Telegram file_id для бота «Настоящая Я» (twin nastya).

Меняйте только значения file_id здесь — тексты остаются в ru_*.py.
"""

# OnboardingFeature — video note (кружки) после /start
ONBOARDING_VIDEO_NOTE_FILE_IDS: tuple[str, ...] = (
    "DQACAgIAAxkBAANracKl6MdwYJd4sZSBhWka6VsXKLMAAt-0AAKuNhBKL-K6a_y-dJk6BA",
)

VIDEO_CIRCLE_FILE_ID = ONBOARDING_VIDEO_NOTE_FILE_IDS[0]

# BenefitFeature — аудио подарков
PRAYER_260408_FILE_ID = (
    "CQACAgIAAyEFAATnayVSAAICT2nV25zfb95IL5IS-tLz9p5pCGfKAAJpkgAC5n6xSq_kfz8HkrtzOwQ"
)
PRAYER_260425_FILE_ID = (
    "CQACAgIAAyEFAATnayVSAAIHZmnslDrHX98ZlX10nsjQaf7cUT1MAAJ9owACZiFoS5BXNs7DNAbWOwQ"
)

# PaymentFeature — PDF публичной оферты (callback «Скачать оферту»)
# file_id у каждого бота свой: загрузите PDF в бота Насти и возьмите id через /code_id.
PUBLIC_OFFER_PDF_FILE_ID = ""

PRIVACY_POLICY_PDF_FILE_ID = (
    "BQACAgIAAxkBAALu8GpE5b9X9QZAzq8BLC1D3fIbqP_lAALNpQACvzUpSjC93rcNTlZnPAQ"
)
PERSONAL_DATA_CONSENT_PDF_FILE_ID = (
    "BQACAgIAAxkBAALu7GpE5QwfsMGfExDGFvpdeaCWjGWdAALGpQACvzUpSjmNomvij8aSPAQ"
)
