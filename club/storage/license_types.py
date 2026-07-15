"""Константы типов лицензий (license.license_type)."""

LICENSE_TYPE_SUBSCRIPTION = "subscription"
LICENSE_TYPE_ADMIN_GRANT = "admin_grant"
LICENSE_TYPE_ADMIN_SUBSCRIPTION = "admin_subscription"
LICENSE_TYPE_BONUS = "bonus"
LICENSE_TYPE_BONUS_EXTENSION = "bonus_extension"

# Типы с цепочкой напоминаний об окончании подписки.
REMINDER_ELIGIBLE_LICENSE_TYPES = (
    LICENSE_TYPE_SUBSCRIPTION,
    LICENSE_TYPE_ADMIN_GRANT,
)

# Типы, считающиеся «платной» аудиторией в отчётах (не бонус / не служебные).
PAID_ANALYTICS_LICENSE_TYPES = (
    LICENSE_TYPE_SUBSCRIPTION,
    LICENSE_TYPE_ADMIN_GRANT,
)
