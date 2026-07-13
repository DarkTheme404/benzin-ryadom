"""
Общие тексты для Premium-тарифов.
Используется в TG боте, VK боте и Mini App.
"""

# Человеческие описания фич (короткие, для кнопок)
FEATURE_NAMES = {
    "price_history": "📈 График цен 30 дней",
    "export_csv": "📦 Экспорт в CSV/Excel",
    "offline_map": "🗺️ Офлайн-карта региона",
    "route_fuel": "🆕 Маршрут A→B с гарантией топлива",
    "forecast_7d": "🆕 Прогноз наличия на 7 дней",
    "fuel_alarm": "🔔 Топливный будильник",
    "anti_traffic": "🚗 Анти-пробка (пробки+цены)",
    "sos_elite": "🆘 SOS-режим",
}

# Иконки тарифов
TIER_ICONS = {
    "economy": "📊",
    "standard": "🗺️",
    "elite": "👑",
}

# Описания тарифов
TIER_TITLES = {
    "economy": "Эконом",
    "standard": "Стандарт",
    "elite": "Элит",
}


def format_tier_short(tier: str) -> str:
    """Короткое описание тарифа для кнопок/строк."""
    icon = TIER_ICONS.get(tier, "")
    title = TIER_TITLES.get(tier, tier)
    return f"{icon} {title}"


def format_features(features: list[str]) -> str:
    """Форматирует список фич с человеческими названиями."""
    return "\n".join([f"  {FEATURE_NAMES.get(f, f)}" for f in features])


def format_tier_text(tier: str, plan: dict, show_features: bool = True) -> str:
    """Полное описание тарифа для текста."""
    icon = TIER_ICONS.get(tier, "•")
    title = TIER_TITLES.get(tier, plan.get("name", tier))
    price = plan["price"]
    period = plan["period_days"]

    text = f"{icon} <b>{title}</b> — {price}₽/{period}дн.\n"
    if show_features:
        for f in plan["features"]:
            text += f"  {FEATURE_NAMES.get(f, f)}\n"
    return text
