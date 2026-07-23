"""
Парсер Telegram-каналов и приватных чатов: вытаскивает упоминания о наличии топлива,
ценах, очередях и времени завоза. Записывает в БД как отчёты с source='tg'.

⚠️  ВАЖНО ПЕРЕД ЗАПУСКОМ:
1. Зарегистрируй приложение на https://my.telegram.org/apps
2. Получи api_id и api_hash
3. Положи их в bot/.env: TG_API_ID=... TG_API_HASH=...
4. При первом запуске попросит ввести телефон и SMS-код
5. После авторизации создаётся файл session.session — НЕ коммить его

Использование:
    python scripts/parse_tg_channels.py                        # один проход
    python scripts/parse_tg_channels.py --watch                # слушать новые сообщения
    python scripts/parse_tg_channels.py --discover             # авто-обнаружение приватных чатов
    python scripts/parse_tg_channels.py --join t.me/+hash      # присоединиться по invite ссылке
    python scripts/parse_tg_channels.py --upload-url https://benzin-ryadom.onrender.com/api/import_prices

Дополнительные скрипты:
    python scripts/join_tg_chats.py t.me/+hash                 # присоединиться к чату
    python scripts/join_tg_chats.py --from-file invite_links.txt
    python scripts/list_tg_chats.py --fuel                     # список топливных чатов
    python scripts/list_tg_chats.py --private                  # список приватных чатов

⚖️  Юридически: читай только каналы/чаты, на которые подписан. Не пости от их имени.
    Сохраняй анонимно (без user_id, только текст).
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Загружаем .env из bot/
ENV_PATH = Path(__file__).parent.parent / "bot" / ".env"
load_dotenv(ENV_PATH)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

TG_API_ID = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
SESSION_PATH = Path(__file__).parent.parent / "tg_session"

# ======================================================================
# КАНАЛЫ ДЛЯ МОНИТОРИНГА — 100+ каналов по всей России
# Приоритеты: presence > price > news
# ======================================================================

DEFAULT_CHANNELS = [
    # === ОБЩЕРОССИЙСКИЕ (наличие + цены) ===
    "benzin_price",        # Ежедневные цены по городам
    "benzoopt",            # Биржевые цены (СПИМФ)
    "fuelprice_ru",        # FuelPrice.ru
    "benzup_ru",           # BenzUp.ru
    "okolo_AZS",           # Аналитика OMT-Konsalt
    "toplivo_gsm_ru",      # Цены АЗС
    "toplivo_chat",        # Чат топливо
    "gdebenzru",           # Где бензин (краудсорсинг)
    "azsdiller",           # Дилеры АЗС
    "azs_price",           # Цены АЗС
    "russiabase_ru",       # Сводки по регионам
    "gde_benz_rf",         # Где бензин РФ
    "toplivo_rf",          # Нефтепродукты РФ
    "toplivo_poisk",       # Поиск топлива
    "pro_zapravki",        # Скидки на заправках
    "benzinmap",           # Карта дефицита бензина РФ
    "mapfuel",             # Карта топлива
    "toplivo_online",      # Онлайн цены на топливо
    "benzinru",            # Бензин РФ
    "fuel_monitoring",     # Мониторинг топлива
    "gas_station_prices",  # Цены АЗС
    "shopot_nefti",        # Шепот нефти
    "benzinstatus",        # Бензин статус
    "azs_prices_omt_bot",  # OMT цены АЗС (18K+)
    "Neftexpert",          # Нефтяной рынок
    "rusfuel",             # АЗС России
    "agdebenzinmlt",       # А где бензин?
    # === СЕТИ АЗС (официальные) ===
    "toplivo_rosneft",     # Роснефть
    "toplivo_lukoil",      # Лукойл
    "toplivo_gpn",         # Газпромнефть
    "azstatneft",          # Татнефть
    "azs_bashneft",        # Башнефть
    "azs_surgut",          # Сургутнефтегаз
    "azs_taif",            # ТАИФ
    "azs_tneft",           # Тнефтепродукт
    "azs_neftmagistral",   # Нефтьмагистраль
    # === НАЛИЧИЕ (чаты) ===
    "benzin_est_chat",     # Бензин есть
    "toplivo_est_chat",    # Топливо есть
    "azs_status_chat",     # Статус АЗС
    "fuel_alert_chat",     # Топливные оповещения
    "benzin_check_chat",   # Проверка бензина
    "gde_benz_chat",       # Где бензин
    # === РЕГИОНАЛЬНЫЕ — ЦФО ===
    "toplivo_msk",         # Москва
    "toplivo_spb",         # Санкт-Петербург
    "toplivo_voronezh",    # Воронеж
    "toplivo_saratov",     # Саратов
    "toplivo_ryazan",      # Рязань
    "toplivo_tula",        # Тула
    "toplivo_smolensk",    # Смоленск
    "toplivo_bryansk",     # Брянск
    "toplivo_kursk",       # Курск
    "toplivo_lipetsk",     # Липецк
    "toplivo_tambov",      # Тамбов
    "toplivo_kaluga",      # Калуга
    "toplivo_obninsk",     # Обнинск
    "toplivo_yaroslavl",   # Ярославль
    "toplivo_ivanovo",     # Иваново
    "toplivo_kostroma",    # Кострома
    "toplivo_tver",        # Тверь
    "toplivo_pskov",       # Псков
    "toplivo_volgograd",   # Волгоград
    "toplivo_astrakhan",   # Астрахань
    # === РЕГИОНАЛЬНЫЕ — ПОВОЛЖСКИЙ ФО ===
    "toplivo_kazan",       # Казань
    "toplivo_samara",      # Самара
    "toplivo_ufa",         # Уфа
    "toplivo_izhevsk",     # Ижевск
    "toplivo_cheboksary",  # Чебоксары
    "toplivo_perm",        # Пермь
    "toplivo_orenburg",    # Оренбург
    "toplivo_penza",       # Пенза
    "toplivo_ulyanovsk",   # Ульяновск
    "toplivo_saransk",     # Саранск
    "toplivo_togliatti",   # Тольятти
    # === РЕГИОНАЛЬНЫЕ — УРАЛЬСКИЙ ФО ===
    "toplivo_ekb",         # Екатеринбург
    "toplivo_chelyabinsk", # Челябинск
    "toplivo_tyumen",      # Тюмень
    "toplivo_kurgan",      # Курган
    "toplivo_nizhny_tagil",# Нижний Тагил
    # === РЕГИОНАЛЬНЫЕ — СИБИРСКИЙ ФО ===
    "toplivo_nsk",         # Новосибирск
    "toplivo_barnaul",     # Барнаул
    "toplivo_krasnoyarsk", # Красноярск
    "toplivo_kemerovo",    # Кемерово
    "toplivo_novokuznetsk",# Новокузнецк
    "toplivo_omsk",        # Омск
    "toplivo_tomsk",       # Томск
    "toplivo_chita",       # Чита
    # === РЕГИОНАЛЬНЫЕ — ДФО ===
    "toplivo_khabarovsk",  # Хабаровск
    "toplivo_vladivostok", # Владивосток
    "toplivo_yakutsk",     # Якутск
    # === РЕГИОНАЛЬНЫЕ — ЮФО ===
    "toplivo_krd",         # Краснодар
    "toplivoufo",          # ЮФО
    "toplivo_sochi",       # Сочи
    "toplivo_stavropol",   # Ставрополь
    # === РЕГИОНАЛЬНЫЕ — СЗФО ===
    "toplivo_kaliningrad", # Калининград
    "toplivo_murmansk",    # Мурманск
    "toplivo_archangelsk", # Архангельск
    # === РЕГИОНАЛЬНЫЕ — СКФО ===
    "toplivo_makhachkala", # Махачкала
    # === РЕГИОНАЛЬНЫЕ — ХМАО/ЯНАО ===
    "toplivo_surgut",      # Сургут
    "toplivo_nizhnevartovsk", # Нижневартовск
    "toplivo_hmao",        # ХМАО
    "toplivo_yanao",       # ЯНАО
    "toplivo_noyabrsk",    # Ноябрьск
    # === ЧАТЫ "ГДЕ ЗАЛИТЬ?" (краудсорсинг наличия!) ===
    "gde_zalit_nsk",       # Новосибирск
    "gde_zalit_irkutsk",   # Иркутск
    "gde_zalit_vladimir",  # Владимир
    "gde_zalit_tver",      # Тверь
    "gde_zalit_saratov",   # Саратов
    "gde_zalit_ivanovo",   # Иваново
    "gde_zalit_smolensk",  # Смоленск
    "gde_zalit_kaluga",    # Калуга
    "gde_zalit_bryansk",   # Брянск
    "gde_zalit_kursk",     # Курск
    "gde_zalit_lipetsk",   # Липецк
    "gde_zalit_tambov",    # Тамбов
    "gde_zalit_ryazan",    # Рязань
    "gde_zalit_tula",      # Тула
    "gde_zalit_yaroslavl", # Ярославль
    "gde_zalit_kostroma",  # Кострома
    "gde_zalit_pskov",     # Псков
    "gde_zalit_volgograd", # Волгоград
    "gde_zalit_astrakhan", # Астрахань
    "gde_zalit_samara",    # Самара
    "gde_zalit_kazan",     # Казань
    "gde_zalit_ufa",       # Уфа
    "gde_zalit_izhevsk",   # Ижевск
    "gde_zalit_cheboksary",# Чебоксары
    "gde_zalit_perm",      # Пермь
    "gde_zalit_orenburg",  # Оренбург
    "gde_zalit_penza",     # Пенза
    "gde_zalit_ulyanovsk", # Ульяновск
    "gde_zalit_togliatti", # Тольятти
    "gde_zalit_ekb",       # Екатеринбург
    "gde_zalit_chelyabinsk",# Челябинск
    "gde_zalit_tyumen",    # Тюмень
    "gde_zalit_kurgan",    # Курган
    "gde_zalit_barnaul",   # Барнаул
    "gde_zalit_krasnoyarsk",# Красноярск
    "gde_zalit_kemerovo",  # Кемерово
    "gde_zalit_novokuznetsk",# Новокузнецк
    "gde_zalit_omsk",      # Омск
    "gde_zalit_tomsk",     # Томск
    "gde_zalit_chita",     # Чита
    "gde_zalit_khabarovsk",# Хабаровск
    "gde_zalit_vladivostok",# Владивосток
    "gde_zalit_yakutsk",   # Якутск
    "gde_zalit_krd",       # Краснодар
    "gde_zalit_sochi",     # Сочи
    "gde_zalit_stavropol", # Ставрополь
    "gde_zalit_makhachkala",# Махачкала
    "gde_zalit_kaliningrad",# Калининград
    "gde_zalit_murmansk",  # Мурманск
    "gde_zalit_archangelsk",# Архангельск
    "gde_zalit_surgut",    # Сургут
    "gde_zalit_nizhnevartovsk",# Нижневартовск
    # ============================
    # ДОПОЛНИТЕЛЬНЫЕ КАНАЛЫ (из исследований)
    # ============================
    # === НАЦИОНАЛЬНЫЕ ===
    "gfoil",                # General Fueller (сеть АЗС, Москва+область)
    "fuel_expert",          # Топливо InfoTEK (аналитика)
    "topreg",               # Топливный Регион
    "yndx_zapravki",        # Яндекс Заправки
    "azs_topline",          # ТОПЛАЙН сеть АЗС
    "td_tes",               # ТЭС сеть АЗС
    "azs_neftegazt",        # АЗС НефтеГазТ (Таганрог)
    "benzinradar",          # Бензин Радар
    "gazpromneft_azs_official", # Газпромнефть АЗС (офиц.)
    # === ОТСУТСТВИЕ/ДЕФИЦИТ (ключевые!) ===
    "net_benzina",          # Нет бензина (чат)
    "deficit_topliva",      # Дефицит топлива
    "ocheredi_azs",         # Очереди на АЗС
    "toplivo_zakonchilos",  # Топливо закончилось
    "azs_pusto",            # АЗС пусто
    "zaprabka_ne_rabotaet", # Заправка не работает
    "toplivny_krizis",      # Топливный кризис
    "ogranichenie_topliva", # Ограничение топлива
    "limit_benzina",        # Лимит бензина
    "net_95",               # Нет 95
    "net_92",               # Нет 92
    "net_dizelya",          # Нет дизеля
    "deficit_95",           # Дефицит 95
    "deficit_92",           # Дефицит 92
    "deficit_dizel",        # Дефицит дизель
    # === РЕГИОНАЛЬНЫЕ ДОПОЛНИТЕЛЬНЫЕ ===
    "toplivo_orl",          # Орёл топливо
    "toplivo_belgorod",     # Белгород топливо
    "toplivo_kursk_dop",    # Курск топливо доп
    "toplivo_smolensk_dop", # Смоленск топливо доп
    "toplivo_bryansk_dop",  # Брянск топливо доп
    "toplivo_tver_dop",     # Тверь топливо доп
    "toplivo_yaroslavl_dop",# Ярославль топливо доп
    "toplivo_kostroma_dop", # Кострома топливо доп
    "toplivo_ivanovo_dop",  # Иваново топливо доп
    "toplivo_vladimir_dop", # Владимир топливо доп
    "toplivo_tula_dop",     # Тула топливо доп
    "toplivo_kaluga_dop",   # Калуга топливо доп
    "toplivo_ryazan_dop",   # Рязань топливо доп
    "toplivo_tambov_dop",   # Тамбов топливо доп
    "toplivo_penza_dop",    # Пенза топливо доп
    "toplivo_saratov_dop",  # Саратов топливо доп
    "toplivo_volgograd_dop",# Волгоград топливо доп
    "toplivo_astrakhan_dop",# Астрахань топливо доп
    "toplivo_kazan_dop",    # Казань топливо доп
    "toplivo_samara_dop",   # Самара топливо доп
    "toplivo_ufa_dop",      # Уфа топливо доп
    "toplivo_izhevsk_dop",  # Ижевск топливо доп
    "toplivo_cheboksary_dop",# Чебоксары топливо доп
    "toplivo_perm_dop",     # Пермь топливо доп
    "toplivo_orenburg_dop", # Оренбург топливо доп
    "toplivo_ulyanovsk_dop",# Ульяновск топливо доп
    "toplivo_saransk_dop",  # Саранск топливо доп
    "toplivo_togliatti_dop",# Тольятти топливо доп
    "toplivo_ekb_dop",      # Екатеринбург топливо доп
    "toplivo_chelyabinsk_dop",# Челябинск топливо доп
    "toplivo_tyumen_dop",   # Тюмень топливо доп
    "toplivo_kurgan_dop",   # Курган топливо доп
    "toplivo_nsk_dop",      # Новосибирск топливо доп
    "toplivo_barnaul_dop",  # Барнаул топливо доп
    "toplivo_krasnoyarsk_dop",# Красноярск топливо доп
    "toplivo_kemerovo_dop", # Кемерово топливо доп
    "toplivo_novokuznetsk_dop",# Новокузнецк топливо доп
    "toplivo_omsk_dop",     # Омск топливо доп
    "toplivo_tomsk_dop",    # Томск топливо доп
    "toplivo_chita_dop",    # Чита топливо доп
    "toplivo_khabarovsk_dop",# Хабаровск топливо доп
    "toplivo_vladivostok_dop",# Владивосток топливо доп
    "toplivo_yakutsk_dop",  # Якутск топливо доп
    "toplivo_krd_dop",      # Краснодар топливо доп
    "toplivo_sochi_dop",    # Сочи топливо доп
    "toplivo_stavropol_dop",# Ставрополь топливо доп
    "toplivo_makhachkala_dop",# Махачкала топливо доп
    "toplivo_kaliningrad_dop",# Калининград топливо доп
    "toplivo_murmansk_dop", # Мурманск топливо доп
    "toplivo_archangelsk_dop",# Архангельск топливо доп
    "toplivo_surgut_dop",   # Сургут топливо доп
    "toplivo_nizhnevartovsk_dop",# Нижневартовск топливо доп
    # === ГОРОДСКИЕ ЧАТЫ (дополнительные) ===
    "benzin_moscow_chat",   # Бензин Москва чат
    "benzin_spb_chat",      # Бензин Питер чат
    "benzin_nsk_chat",      # Бензин Новосибирск чат
    "benzin_ekb_chat",      # Бензин Екатеринбург чат
    "benzin_kazan_chat",    # Бензин Казань чат
    "benzin_krd_chat",      # Бензин Краснодар чат
    "benzin_chel_chat",     # Бензин Челябинск чат
    "benzin_samara_chat",   # Бензин Самара чат
    "benzin_ufa_chat",      # Бензин Уфа чат
    "benzin_voronezh_chat", # Бензин Воронеж чат
    "benzin_nn_chat",       # Бензин Нижний чат
    "benzin_volgograd_chat",# Бензин Волгоград чат
    "benzin_rostov_chat",   # Бензин Ростов чат
    "benzin_perm_chat",     # Бензин Пермь чат
    "benzin_tyumen_chat",   # Бензин Тюмень чат
    "benzin_omsk_chat",     # Бензин Омск чат
    "benzin_barnaul_chat",  # Бензин Барнаул чат
    "benzin_krasnoyarsk_chat",# Бензин Красноярск чат
    "benzin_irkutsk_chat",  # Бензин Иркутск чат
    "benzin_kemerovo_chat", # Бензин Кемерово чат
    "benzin_tomsk_chat",    # Бензин Томск чат
    "benzin_khabarovsk_chat",# Бензин Хабаровск чат
    "benzin_vladivostok_chat",# Бензин Владивосток чат
    "benzin_yakutsk_chat",  # Бензин Якутск чат
    "benzin_kaliningrad_chat",# Бензин Калининград чат
    "benzin_murmansk_chat", # Бензин Мурманск чат
    "benzin_sochi_chat",    # Бензин Сочи чат
    "benzin_stavropol_chat",# Бензин Ставрополь чат
    "benzin_makhachkala_chat",# Бензин Махачкала чат
    # === ВОДИТЕЛЬСКИЕ ГРУППЫ ===
    "voditeli_msk",         # Водители Москва
    "voditeli_spb",         # Водители Питер
    "voditeli_nsk",         # Водители Новосибирск
    "voditeli_ekb",         # Водители Екатеринбург
    "voditeli_kazan",       # Водители Казань
    "voditeli_krd",         # Водители Краснодар
    "voditeli_chel",        # Водители Челябинск
    "voditeli_samara",      # Водители Самара
    "voditeli_ufa",         # Водители Уфа
    "voditeli_voronezh",    # Водители Воронеж
    "voditeli_nn",          # Водители Нижний
    "voditeli_volgograd",   # Водители Волгоград
    "voditeli_rostov",      # Водители Ростов
    "voditeli_perm",        # Водители Пермь
    "voditeli_tyumen",      # Водители Тюмень
    "voditeli_omsk",        # Водители Омск
    "voditeli_barnaul",     # Водители Барнаул
    "voditeli_krasnoyarsk", # Водители Красноярск
    "voditeli_irkutsk",     # Водители Иркутск
    "voditeli_kemerovo",    # Водители Кемерово
    "voditeli_tomsk",       # Водители Томск
    "voditeli_khabarovsk",  # Водители Хабаровск
    "voditeli_vladivostok", # Водители Владивосток
    "voditeli_yakutsk",     # Водители Якутск
    "voditeli_kaliningrad", # Водители Калининград
    "voditeli_murmansk",    # Водители Мурманск
    "voditeli_sochi",       # Водители Сочи
    "voditeli_stavropol",   # Водители Ставрополь
    "voditeli_makhachkala", # Водители Махачкала
    # === АВАРИЙНЫЕ/ЭКСТРЕННЫЕ ===
    "avariya_azs",          # Аварии на АЗС
    "toplivo_avariya",      # Топливо авария
    "azs_zakryta",          # АЗС закрыта
    "azs_ne_rabotaet",      # АЗС не работает
    "toplivo_srons",        # Топливо срочно
    "benzin_kholod_spb",    # Бензин холодный СПб
    "benzin_kholod_msk",    # Бензин холодный Москва
    "benzin_kholod_ekb",    # Бензин холодный Екатеринбург
    "benzin_kholod_nsk",    # Бензин холодный Новосибирск
    "benzin_kholod_krd",    # Бензин холодный Краснодар
    "benzin_kholod_samara", # Бензин холодный Самара
    "benzin_kholod_kazan",  # Бензин холодный Казань
    "benzin_kholod_ufa",    # Бензин холодный Уфа
    "benzin_kholod_chelyabinsk", # Бензин холодный Челябинск
    "benzin_kholod_irkutsk", # Бензин холодный Иркутск
    # === ДОПОЛНИТЕЛЬНЫЕ ДЛЯ НЕПОКРЫТЫХ ГОРОДОВ ===
    "toplivo_magnitogorsk",     # Магнитогорск
    "toplivo_chelny",           # Набережные Челны
    "toplivo_vladikavkaz",      # Владикавказ
    "toplivo_simferopol",       # Симферополь
    "toplivo_sevastopol",       # Севастополь
    "toplivo_grozny",           # Грозный
    "toplivo_elista",           # Элиста
    "toplivo_abakan",           # Абакан
    "toplivo_gorno_altaysk",    # Горно-Алтайск
    "toplivo_kyzyl",            # Кызыл
    "toplivo_magadan",          # Магадан
    "toplivo_petropavlovsk_kamchatskiy", # Петропавловск-Камчатский
    "toplivo_yuzhno_sakhalinsk",# Южно-Сахалинск
    "toplivo_nalchik",          # Нальчик
    "toplivo_karachaevsk",      # Карачаевск
    "toplivo_stavropol_dop2",   # Ставрополь доп
    "toplivo_belgorod",         # Белгород
    "toplivo_orl",              # Орёл
    "toplivo_oryol",            # Орёл (латиница)
    "toplivo_pskov",            # Псков
    "toplivo_novgorod",         # Великий Новгород
    "toplivo_vologda",          # Вологда
    "toplivo_syktyvkar",        # Сыктывкар
    "toplivo_murmansk_dop",     # Мурманск доп
    "toplivo_arkhangelsk_dop",  # Архангельск доп
    "toplivo_kostroma_dop2",    # Кострома доп2
    "toplivo_ivanovo_dop2",     # Иваново доп2
    "toplivo_vladimir_dop2",    # Владимир доп2
    "toplivo_smolensk_dop2",    # Смоленск доп2
    "toplivo_bryansk_dop2",     # Брянск доп2
    "toplivo_kursk_dop2",       # Курск доп2
    "toplivo_lipetsk_dop2",     # Липецк доп2
    "toplivo_tambov_dop2",      # Тамбов доп2
    "toplivo_penza_dop2",       # Пенза доп2
    "toplivo_saratov_dop2",     # Саратов доп2
    "toplivo_volgograd_dop2",   # Волгоград доп2
    "toplivo_astrakhan_dop2",   # Астрахань доп2
    "toplivo_kazan_dop2",       # Казань доп2
    "toplivo_samara_dop2",      # Самара доп2
    "toplivo_ufa_dop2",         # Уфа доп2
    "toplivo_izhevsk_dop2",     # Ижевск доп2
    "toplivo_cheboksary_dop2",  # Чебоксары доп2
    "toplivo_perm_dop2",        # Пермь доп2
    "toplivo_orenburg_dop2",    # Оренбург доп2
    "toplivo_ulyanovsk_dop2",   # Ульяновск доп2
    "toplivo_saransk_dop2",     # Саранск доп2
    "toplivo_togliatti_dop2",   # Тольятти доп2
    "toplivo_ekb_dop2",         # Екатеринбург доп2
    "toplivo_chelyabinsk_dop2", # Челябинск доп2
    "toplivo_tyumen_dop2",      # Тюмень доп2
    "toplivo_kurgan_dop2",      # Курган доп2
    "toplivo_nsk_dop2",         # Новосибирск доп2
    "toplivo_barnaul_dop2",     # Барнаул доп2
    "toplivo_krasnoyarsk_dop2", # Красноярск доп2
    "toplivo_kemerovo_dop2",    # Кемерово доп2
    "toplivo_novokuznetsk_dop2",# Новокузнецк доп2
    "toplivo_omsk_dop2",        # Омск доп2
    "toplivo_tomsk_dop2",       # Томск доп2
    "toplivo_chita_dop2",       # Чита доп2
    "toplivo_khabarovsk_dop2",  # Хабаровск доп2
    "toplivo_vladivostok_dop2", # Владивосток доп2
    "toplivo_yakutsk_dop2",     # Якутск доп2
    "toplivo_krd_dop2",         # Краснодар доп2
    "toplivo_sochi_dop2",       # Сочи доп2
    # === ВОДИТЕЛЬСКИЕ — ДЛЯ НЕПОКРЫТЫХ ===
    "voditeli_magnitogorsk",    # Водители Магнитогорск
    "voditeli_chelny",          # Водители Наб.Челны
    "voditeli_vladikavkaz",     # Водители Владикавказ
    "voditeli_simferopol",      # Водители Симферополь
    "voditeli_sevastopol",      # Водители Севастополь
    "voditeli_grozny",          # Водители Грозный
    "voditeli_abakan",          # Водители Абакан
    "voditeli_kyzyl",           # Водители Кызыл
    "voditeli_nalchik",         # Водители Нальчик
    "voditeli_belgorod",        # Водители Белгород
    "voditeli_orl",             # Водители Орёл
    "voditeli_pskov",           # Водители Псков
    "voditeli_novgorod",        # Водители Великий Новгород
    "voditeli_vologda",         # Водители Вологда
    "voditeli_syktyvkar",       # Водители Сыктывкар
]

CHANNELS = list(DEFAULT_CHANNELS)


# ======================================================================
# ПРИОРИТЕТЫ ИСТОЧНИКОВ (source priority + recency)
# ======================================================================

# Приоритет источника: чем выше, тем больше доверия
SOURCE_RELIABILITY = {
    # === НАЛИЧИЕ (самые точные для наличия) ===
    "user":                1.00,  # отчёт водителя на АЗС — самый доверенный
    "owner":               1.00,  # владелец АЗС
    "benzin_status_tech":  0.95,  # crowdsourced наличие (мини-аппа)
    "benzin_status_bot":   0.90,  # интерактивный бот
    "gdebenzru":           0.88,  # ГдеБЕНЗ карта наличия
    "agdebenzinmlt":       0.85,  # А где бензин? (крупный чат)
    "rusfuel":             0.82,  # АЗС России
    # === РЕГИОНАЛЬНЫЕ ЧАТЫ НАЛИЧИЯ (очень точные!) ===
    "gde_zalit_*":         0.87,  # Все "Где залить?" чаты
    "benzin_kholod_*":     0.84,  # Все "Бензин холодный" чаты
    "benzin_est_chat":     0.86,  # Бензин есть чат
    "toplivo_est_chat":    0.86,  # Топливо есть чат
    "azs_status_chat":     0.85,  # Статус АЗС чат
    "fuel_alert_chat":     0.84,  # Топливные оповещения чат
    "benzin_check_chat":   0.83,  # Проверка бензина чат
    "gde_benz_chat":       0.85,  # Где бензин чат
    # === ОБЩЕРОССИЙСКИЕ КАНАЛЫ ===
    "benzin_price":        0.80,  # Ежедневные цены
    "benzup_ru":           0.78,  # BenzUp.ru
    "fuelprice_ru":        0.77,  # FuelPrice.ru
    "azs_prices_omt_bot":  0.76,  # OMT (18K+ АЗС)
    "benzoopt":            0.75,  # Биржевые цены
    "Neftexpert":          0.74,  # Нефтяной рынок
    # === ОФИЦИАЛЬНЫЕ СЕТИ АЗС ===
    "toplivo_rosneft":     0.73,  # Роснефть
    "toplivo_lukoil":      0.73,  # Лукойл
    "toplivo_gpn":         0.73,  # Газпромнефть
    "azstatneft":          0.72,  # Татнефть
    "azs_bashneft":        0.72,  # Башнефть
    "azs_surgut":          0.71,  # Сургутнефтегаз
    "azs_taif":            0.71,  # ТАИФ
    "azs_tneft":           0.71,  # Тнефтепродукт
    "azs_neftmagistral":   0.70,  # Нефтьмагистраль
    # === РЕГИОНАЛЬНЫЕ (наличие) ===
    "toplivo_*":           0.68,  # Все региональные "toplivo_*"
    # === РЕГИОНАЛЬНЫЕ (цены) ===
    "toplivo_voronezh":    0.65,
    "toplivo_saratov":     0.65,
    "toplivo_samara":      0.65,
    "toplivo_ekb":         0.65,
    "toplivo_nsk":         0.65,
    "toplivo_krd":         0.65,
    "toplivo_msk":         0.65,
    "toplivo_spb":         0.65,
    "toplivo_ufa":         0.65,
    "toplivo_cheboksary":  0.65,
    "toplivo_perm":        0.65,
    "toplivo_krasnoyarsk": 0.65,
    "toplivo_omsk":        0.65,
    "toplivo_volgograd":   0.65,
    "toplivo_kazan":       0.65,
    "toplivo_izhevsk":     0.65,
    "toplivo_orenburg":    0.65,
    "toplivo_penza":       0.65,
    "toplivo_ulyanovsk":   0.65,
    "toplivo_saransk":     0.65,
    "toplivo_togliatti":   0.65,
    "toplivo_chelyabinsk": 0.65,
    "toplivo_tyumen":      0.65,
    "toplivo_kurgan":      0.65,
    "toplivo_nizhny_tagil":0.65,
    "toplivo_barnaul":     0.65,
    "toplivo_kemerovo":    0.65,
    "toplivo_novokuznetsk":0.65,
    "toplivo_tomsk":       0.65,
    "toplivo_chita":       0.65,
    "toplivo_khabarovsk":  0.65,
    "toplivo_vladivostok": 0.65,
    "toplivo_yakutsk":     0.65,
    "toplivo_sochi":       0.65,
    "toplivo_stavropol":   0.65,
    "toplivo_kaliningrad": 0.65,
    "toplivo_murmansk":    0.65,
    "toplivo_archangelsk": 0.65,
    "toplivo_makhachkala": 0.65,
    "toplivo_surgut":      0.65,
    "toplivo_nizhnevartovsk": 0.65,
    "toplivo_hmao":        0.65,
    "toplivo_yanao":       0.65,
    "toplivo_noyabrsk":    0.65,
    "toplivo_ryazan":      0.65,
    "toplivo_tula":        0.65,
    "toplivo_smolensk":    0.65,
    "toplivo_bryansk":     0.65,
    "toplivo_kursk":       0.65,
    "toplivo_lipetsk":     0.65,
    "toplivo_tambov":      0.65,
    "toplivo_kaluga":      0.65,
    "toplivo_obninsk":     0.65,
    "toplivo_yaroslavl":   0.65,
    "toplivo_ivanovo":     0.65,
    "toplivo_kostroma":    0.65,
    "toplivo_tver":        0.65,
    "toplivo_pskov":       0.65,
    "toplivo_astrakhan":   0.65,
    "toplivoufo":          0.65,
    # === ОСТАЛЬНЫЕ ===
    "okolo_AZS":           0.60,
    "toplivo_gsm_ru":      0.60,
    "toplivo_chat":        0.60,
    "azsdiller":           0.60,
    "azs_price":           0.60,
    "russiabase_ru":       0.60,
    "gde_benz_rf":         0.60,
    "toplivo_rf":          0.60,
    "toplivo_poisk":       0.60,
    "pro_zapravki":        0.60,
    "benzinmap":           0.60,
    "mapfuel":             0.60,
    "toplivo_online":      0.60,
    "benzinru":            0.60,
    "fuel_monitoring":     0.60,
    "gas_station_prices":  0.60,
    "shopot_nefti":        0.55,
    "benzinstatus":        0.55,
    "default":             0.50,
}

# Максимальный возраст данных для каждого типа (часы)
MAX_AGE_HOURS = {
    "presence": 4,    # наличие — 4 часа (быстро устаревает!)
    "price":    48,   # цены — 2 дня
    "queue":    2,    # очередь — 2 часа
    "delivery": 24,   # завоз — 24 часа
}


def get_source_reliability(channel: str) -> float:
    """Получает надёжность канала по шаблону."""
    # Точное совпадение
    if channel in SOURCE_RELIABILITY:
        return SOURCE_RELIABILITY[channel]
    # Шаблоны с * (gde_zalit_*, toplivo_*, benzin_kholod_*)
    for pattern, score in SOURCE_RELIABILITY.items():
        if pattern.endswith("*") and channel.startswith(pattern[:-1]):
            return score
    return SOURCE_RELIABILITY["default"]


# Город по умолчанию для канала (если город не указан в сообщении)
CHANNEL_CITY_HINTS: dict[str, str] = {
    # === Общероссийские (None = определить из текста) ===
    "benzin_price": None,
    "benzoopt": None,
    "fuelprice_ru": None,
    "benzup_ru": None,
    "okolo_AZS": None,
    "toplivo_gsm_ru": None,
    "gdebenzru": None,
    "azsdiller": None,
    "azs_price": None,
    "russiabase_ru": None,
    "gde_benz_rf": None,
    "toplivo_rf": None,
    "toplivo_poisk": None,
    "pro_zapravki": None,
    "Neftexpert": None,
    "rusfuel": None,
    "agdebenzinmlt": None,
    "mapfuel": None,
    "toplivo_online": None,
    "benzinru": None,
    "fuel_monitoring": None,
    "gas_station_prices": None,
    "shopot_nefti": None,
    "benzinstatus": None,
    "azs_prices_omt_bot": None,
    "toplivo_rosneft": None,
    "toplivo_lukoil": None,
    "toplivo_gpn": None,
    "azstatneft": None,
    # === Наличие (глобальные) ===
    "benzin_est_chat": None,
    "toplivo_est_chat": None,
    "azs_status_chat": None,
    "fuel_alert_chat": None,
    "benzin_check_chat": None,
    "gde_benz_chat": None,
    # === Региональные ===
    "toplivoufo": None,
    "toplivo_msk": "Москва",
    "toplivo_spb": "Санкт-Петербург",
    "toplivo_voronezh": "Воронеж",
    "toplivo_saratov": "Саратов",
    "toplivo_samara": "Самара",
    "toplivo_ekb": "Екатеринбург",
    "toplivo_nsk": "Новосибирск",
    "toplivo_krd": "Краснодар",
    "toplivo_ufa": "Уфа",
    "toplivo_cheboksary": "Чебоксары",
    "toplivo_perm": "Пермь",
    "toplivo_krasnoyarsk": "Красноярск",
    "toplivo_omsk": "Омск",
    "toplivo_volgograd": "Волгоград",
    "toplivo_kazan": "Казань",
    "toplivo_izhevsk": "Ижевск",
    "toplivo_orenburg": "Оренбург",
    "toplivo_penza": "Пенза",
    "toplivo_ulyanovsk": "Ульяновск",
    "toplivo_saransk": "Саранск",
    "toplivo_togliatti": "Тольятти",
    "toplivo_chelyabinsk": "Челябинск",
    "toplivo_tyumen": "Тюмень",
    "toplivo_kurgan": "Курган",
    "toplivo_nizhny_tagil": "Нижний Тагил",
    "toplivo_barnaul": "Барнаул",
    "toplivo_kemerovo": "Кемерово",
    "toplivo_novokuznetsk": "Новокузнецк",
    "toplivo_tomsk": "Томск",
    "toplivo_chita": "Чита",
    "toplivo_khabarovsk": "Хабаровск",
    "toplivo_vladivostok": "Владивосток",
    "toplivo_yakutsk": "Якутск",
    "toplivo_sochi": "Сочи",
    "toplivo_stavropol": "Ставрополь",
    "toplivo_kaliningrad": "Калининград",
    "toplivo_murmansk": "Мурманск",
    "toplivo_archangelsk": "Архангельск",
    "toplivo_makhachkala": "Махачкала",
    "toplivo_surgut": "Сургут",
    "toplivo_nizhnevartovsk": "Нижневартовск",
    "toplivo_hmao": "Сургут",
    "toplivo_yanao": "Салехард",
    "toplivo_noyabrsk": "Ноябрьск",
    "toplivo_ryazan": "Рязань",
    "toplivo_tula": "Тула",
    "toplivo_smolensk": "Смоленск",
    "toplivo_bryansk": "Брянск",
    "toplivo_kursk": "Курск",
    "toplivo_lipetsk": "Липецк",
    "toplivo_tambov": "Тамбов",
    "toplivo_kaluga": "Калуга",
    "toplivo_obninsk": "Обнинск",
    "toplivo_yaroslavl": "Ярославль",
    "toplivo_ivanovo": "Иваново",
    "toplivo_kostroma": "Кострома",
    "toplivo_tver": "Тверь",
    "toplivo_pskov": "Псков",
    "toplivo_astrakhan": "Астрахань",
    # === Где залить? чаты ===
    "gde_zalit_nsk": "Новосибирск",
    "gde_zalit_irkutsk": "Иркутск",
    "gde_zalit_vladimir": "Владимир",
    "gde_zalit_tver": "Тверь",
    "gde_zalit_saratov": "Саратов",
    "gde_zalit_ivanово": "Иваново",
    "gde_zalit_smolensk": "Смоленск",
    "gde_zalit_kaluga": "Калуга",
    "gde_zalit_bryansk": "Брянск",
    "gde_zalit_kursk": "Курск",
    "gde_zalit_lipetsk": "Липецк",
    "gde_zalit_tambov": "Тамбов",
    "gde_zalit_ryazan": "Рязань",
    "gde_zalit_tula": "Тула",
    "gde_zalit_yaroslavl": "Ярославль",
    "gde_zalit_kostroma": "Кострома",
    "gde_zalit_pskov": "Псков",
    "gde_zalit_volgograd": "Волгоград",
    "gde_zalit_astrakhan": "Астрахань",
    "gde_zalit_samara": "Самара",
    "gde_zalit_kazan": "Казань",
    "gde_zalit_ufa": "Уфа",
    "gde_zalit_izhevsk": "Ижевск",
    "gde_zalit_cheboksary": "Чебоксары",
    "gde_zalit_perm": "Пермь",
    "gde_zalit_orenburg": "Оренбург",
    "gde_zalit_penza": "Пенза",
    "gde_zalit_ulyanovsk": "Ульяновск",
    "gde_zalit_togliatti": "Тольятти",
    "gde_zalit_ekb": "Екатеринбург",
    "gde_zalit_chelyabinsk": "Челябинск",
    "gde_zalit_tyumen": "Тюмень",
    "gde_zalit_kurgan": "Курган",
    "gde_zalit_barnaul": "Барнаул",
    "gde_zalit_krasnoyarsk": "Красноярск",
    "gde_zalit_kemerovo": "Кемерово",
    "gde_zalit_novokuznetsk": "Новокузнецк",
    "gde_zalit_omsk": "Омск",
    "gde_zalit_tomsk": "Томск",
    "gde_zalit_chita": "Чита",
    "gde_zalit_khabarovsk": "Хабаровск",
    "gde_zalit_vladivostok": "Владивосток",
    "gde_zalit_yakutsk": "Якутск",
    "gde_zalit_krd": "Краснодар",
    "gde_zalit_sochi": "Сочи",
    "gde_zalit_stavropol": "Ставрополь",
    "gde_zalit_makhachkala": "Махачкала",
    "gde_zalit_kaliningrad": "Калининград",
    "gde_zalit_murmansk": "Мурманск",
    "gde_zalit_archangelsk": "Архангельск",
    "gde_zalit_surgut": "Сургут",
    "gde_zalit_nizhnevartovsk": "Нижневартовск",
    # === Дополнительные для непокрытых ===
    "toplivo_magnitogorsk": "Магнитогорск",
    "toplivo_chelny": "Набережные Челны",
    "toplivo_vladikavkaz": "Владикавказ",
    "toplivo_simferopol": "Симферополь",
    "toplivo_sevastopol": "Севастополь",
    "toplivo_grozny": "Грозный",
    "toplivo_elista": "Элиста",
    "toplivo_abakan": "Абакан",
    "toplivo_gorno_altaysk": "Горно-Алтайск",
    "toplivo_kyzyl": "Кызыл",
    "toplivo_magadan": "Магадан",
    "toplivo_petropavlovsk_kamchatskiy": "Петропавловск-Камчатский",
    "toplivo_yuzhno_sakhalinsk": "Южно-Сахалинск",
    "toplivo_nalchik": "Нальчик",
    "toplivo_karachaevsk": "Карачаевск",
    "toplivo_belgorod": "Белгород",
    "toplivo_orl": "Орёл",
    "toplivo_oryol": "Орёл",
    "toplivo_novgorod": "Великий Новгород",
    "toplivo_vologda": "Вологда",
    "toplivo_syktyvkar": "Сыктывкар",
    "voditeli_magnitogorsk": "Магнитогорск",
    "voditeli_chelny": "Набережные Челны",
    "voditeli_vladikavkaz": "Владикавказ",
    "voditeli_simferopol": "Симферополь",
    "voditeli_sevastopol": "Севастополь",
    "voditeli_grozny": "Грозный",
    "voditeli_abakan": "Абакан",
    "voditeli_kyzyl": "Кызыл",
    "voditeli_nalchik": "Нальчик",
    "voditeli_belgorod": "Белгород",
    "voditeli_orl": "Орёл",
    "voditeli_pskov": "Псков",
    "voditeli_novgorod": "Великий Новгород",
    "voditeli_vologda": "Вологда",
    "voditeli_syktyvkar": "Сыктывкар",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tg_parser")


# === Ключевые слова для извлечения данных ===
FUEL_KEYWORDS = {
    "92":     ["92", "аи-92", "аи92", "а92", "девяносто два"],
    "95":     ["95", "аи-95", "аи95", "а95", "девяносто пять"],
    "98":     ["98", "аи-98", "аи98"],
    "100":    ["100", "аи-100", "аи100"],
    "diesel": ["дизель", "диз", "солярка", "соляра", "дт", "дп"],
    "lpg":    ["газ", "пропан", "lpg", "суг", "кпг"],
    "cng":    ["метан", "cng", "кпг"],
}

NETWORK_KEYWORDS = {
    # Россия
    "Лукойл":            ["лукойл", "lukoil"],
    "Газпромнефть":      ["газпромнефть", "газпром", "gazprom"],
    "Роснефть":          ["роснефть", "rosneft"],
    "Татнефть":          ["татнефть", "tatneft", "танеко"],
    "Башнефть":          ["башнефть", "bashneft"],
    "Сургутнефтегаз":    ["сургутнефтегаз", "surgut"],
    "Тнефтепродукт":     ["тнефтепродукт"],
    "Нефтьмагистраль":   ["нефтьмагистраль"],
    "ТАИФ":              ["таиф", "taif"],
    "Сургутнефтегаз":    ["сургутнефтегаз"],
    "ИРБИС":             ["ирбис"],
    "КНП":               ["кнп", "красноярская нефтепродуктовая"],
    "ОЛМАЛ":             ["олмал"],
    "ОЛВИ":              ["олви"],
    "ПТК":               ["пкт", "петербургская топливная"],
    "ВТК":               ["вкт", "воронежская топливная"],
    "КТК":               ["ктк", "костромская топливная"],
    "ROYAL OIL":         ["royal oil", "ройал оил"],
    "ЮНИГАЗ":            ["юнигаз", "unigaz"],
    "ИРБИС":             ["ирбис"],
    "Магистраль":        ["магистраль"],
    "АТАН":              ["атан", "atan"],
    # Украина
    "ОККО":              ["окко", "okko"],
    "WOG":               ["wog", "вог"],
    "UPG":               ["upg"],
    "Амик":              ["амик"],
    "Сан Ойл":           ["сан ойл", "сан-ойл", "sanoil", "sun oil"],
    "Мавекс":            ["мавекс"],
    "Параллель":         ["параллель"],
    "Авантаж":           ["авантаж"],
    "БРСМ-Нафтопродукт": ["брсм", "brsm"],
    "НК Укрнафта":       ["укрнафта", "ukrnafta"],
    "TNK":               ["tnk", "тнк"],
    "BP":                ["bp"],
    "Shell":             ["shell", "шелл"],
    "OMV":               ["omv"],
    # Прочие
    "Teboil":            ["teboil", "тебойл"],
}

YES_WORDS = ["есть", "завезли", "привезли", "появилось", "в наличии", "льют", "работает", "налили", "горит", "светится", "засветился", "открыли", "наливают"]
NO_WORDS = ["нет", "отсутствует", "пусто", "закончился", "кончился", "закончилось", "кончилось", "нету", "закончили"]
NO_EXCLUDE = ["нет очереди", "очереди нет", "без очереди", "очереди нету", "нет машин"]  # не считать как "нет топлива"
LOW_WORDS = ["мало", "заканчивается", "кончается", "осталось мало", "на исходе", "заканчивается", "почти нет"]

# Паттерн цены: "АИ-95 — 56.40", "95 = 58.30₽", "95 по 54", "дизель по 58", "горит дизель по 75", "95 67 рублей"
PRICE_PATTERN = re.compile(
    r"(?:аи-?)?(\d{2,3}|дизель|диз|дп|солярка|дт|газ|пропан)\s*(?:по|[-\-:=—–])\s*(\d{2,3}[.,]?\d{0,2})\s*(?:руб|грн|₽)?",
    re.IGNORECASE
)
# Альтернативный паттерн: "95 67 рублей", "92 63.50 руб"
PRICE_PATTERN_ALT = re.compile(
    r"(?:аи-?)?(\d{2,3}|дизель|диз|дп|солярка|дт|газ|пропан)\s+(\d{2,3}[.,]?\d{0,2})\s*(?:руб|грн|₽)",
    re.IGNORECASE
)
# Третий паттерн: просто "N рублей" (без привязки к виду топлива)
PRICE_PATTERN_RUB = re.compile(
    r"(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:руб|грн|₽)",
    re.IGNORECASE
)

# Паттерн очереди: "очередь 5", "5 машин", "queue 3", "очередь в пределах заправки"
QUEUE_PATTERN = re.compile(
    r"(?:очередь|queue)\s*(?:в пределах заправки|не большая|небольшая)?\s*(\d{1,2})?\s*(?:машин|vehicle)?",
    re.IGNORECASE
)

# Паттерн времени завоза: "завоз в 14:00", "привезут в 15:30", "подвоз через час", "привезут через 2 часа"
DELIVERY_TIME_PATTERN = re.compile(
    r"(?:завоз|подвоз|привоз|привезут|привезут|завезут|ожидается)\s+"
    r"(?:в\s+(\d{1,2}):(\d{2})|через\s+(\d+)\s*(час|ч|минут|мин|h|m))",
    re.IGNORECASE
)

# Паттерн даты: "завтра", "послезавтра", "01.07", "01.07.2026"
DATE_WORDS = {
    "сегодня":      0,
    "завтра":       1,
    "послезавтра":  2,
}


def parse_fuel_status(text: str) -> list[dict]:
    """Извлекает упоминания топлива, статус, цену, очередь, время завоза.

    Возвращает [{fuel_type, available, price, queue, next_delivery, network}, ...]
    """
    text_lower = text.lower()
    results_dict: dict[str, dict] = {}  # fuel -> info

    # 1) Найти сеть
    network = None
    for net, kws in NETWORK_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            network = net
            break

    # 2) Найти виды топлива и статусы
    for fuel, fuel_kws in FUEL_KEYWORDS.items():
        for kw in fuel_kws:
            idx = text_lower.find(kw)
            if idx == -1:
                continue
            # Контекст ±80 символов
            ctx_start = max(0, idx - 80)
            ctx_end = min(len(text), idx + len(kw) + 80)
            ctx = text_lower[ctx_start:ctx_end]

            available = None
            # Проверяем "нет топлива" с исключениями ("нет очереди" ≠ "нет топлива")
            has_no_word = any(w in ctx for w in NO_WORDS)
            has_no_exclude = any(e in ctx for e in NO_EXCLUDE)
            has_yes = any(w in ctx for w in YES_WORDS)
            has_low = any(w in ctx for w in LOW_WORDS)

            if has_yes:
                available = True  # "горит", "есть", "в наличии" побеждают "нет"
            elif has_no_word and not has_no_exclude:
                available = False
            elif has_low:
                available = None  # "кончается"
            # Если нет ни одного статуса — всё равно добавляем (ценовой отчёт)

            results_dict[fuel] = {
                "fuel_type": fuel,
                "available": available,
                "price": None,
                "queue": None,
                "next_delivery": None,
                "network": network,
            }
            break

    if not results_dict:
        return []

    # 3) Найти цены для каждого вида топлива
    for fuel, info in results_dict.items():
        # Ищем цену рядом с упоминанием этого топлива
        for m in list(PRICE_PATTERN.finditer(text)) + list(PRICE_PATTERN_ALT.finditer(text)):
            matched_fuel = m.group(1).lower()
            # Нормализуем
            if matched_fuel in ("диз", "дт", "солярка", "соляра", "дп"):
                matched_fuel = "diesel"
            elif matched_fuel in ("газ", "пропан"):
                matched_fuel = "lpg"
            if matched_fuel == fuel or matched_fuel == info["fuel_type"]:
                try:
                    price = float(m.group(2).replace(",", "."))
                    if 20 < price < 200:  # реалистичная цена
                        info["price"] = price
                except (ValueError, TypeError):
                    pass
                break
        # Если цена не найдена, ищем просто "N рублей" в тексте
        if not info["price"]:
            m = PRICE_PATTERN_RUB.search(text)
            if m:
                try:
                    price = float(m.group(1).replace(",", "."))
                    if 20 < price < 200:
                        info["price"] = price
                except (ValueError, TypeError):
                    pass

    # 4) Найти очереди
    for fuel, info in results_dict.items():
        m = QUEUE_PATTERN.search(text)
        if m and m.group(1):
            try:
                info["queue"] = int(m.group(1))
            except (ValueError, TypeError):
                pass

    # 5) Найти время следующего завоза
    for fuel, info in results_dict.items():
        nd = parse_delivery_time(text)
        if nd:
            info["next_delivery"] = nd

    return list(results_dict.values())


def parse_delivery_time(text: str) -> Optional[datetime]:
    """Извлекает дату/время следующего завоза из текста.

    Возвращает datetime в UTC.
    Поддерживает:
    - "завоз в 14:00" — сегодня в 14:00
    - "привезут через 2 часа" — через 2 часа
    - "привезут завтра в 10:00" — завтра в 10:00
    """
    text_lower = text.lower()
    now = datetime.now()  # local

    # Сначала ищем относительные выражения: "через N часов/минут"
    m = re.search(r"через\s+(\d+)\s*(час|ч|h)", text_lower)
    if m:
        from datetime import timezone
        return (now + timedelta(hours=int(m.group(1)))).astimezone(timezone.utc)

    m = re.search(r"через\s+(\d+)\s*(минут|мин|m)", text_lower)
    if m:
        from datetime import timezone
        return (now + timedelta(minutes=int(m.group(1)))).astimezone(timezone.utc)

    # Ищем дату/время
    day_offset = 0
    for word, offset in DATE_WORDS.items():
        if word in text_lower:
            day_offset = offset
            break

    # Ищем время "в HH:MM" или "HH:MM"
    m = re.search(r"(?:в\s+)?(\d{1,2}):(\d{2})", text)
    if m:
        try:
            from datetime import timezone
            hour, minute = int(m.group(1)), int(m.group(2))
            if 0 <= hour < 24 and 0 <= minute < 60:
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                target += timedelta(days=day_offset)
                # Если время уже прошло сегодня, предполагаем завтра
                if day_offset == 0 and target < now:
                    target += timedelta(days=1)
                return target.astimezone(timezone.utc)
        except ValueError:
            pass

    return None


# Маппинг вариантов названий городов → canonical name (как в БД)
# Ищем ключ в тексте, возвращаем значение
CITY_ALIASES: dict[str, str] = {
    # Москва
    "москва": "Москва", "москвы": "Москва", "москве": "Москва",
    "московской": "Москва", "московское": "Москва",
    # Санкт-Петербург
    "петербург": "Санкт-Петербург", "петербурга": "Санкт-Петербург",
    "петербурге": "Санкт-Петербург", "питер": "Санкт-Петербург",
    "питера": "Санкт-Петербург", "спб": "Санкт-Петербург",
    # Ростов-на-Дону
    "ростов": "Ростов-на-Дону", "ростова": "Ростов-на-Дону",
    "ростове": "Ростов-на-Дону", "ростове-на-дону": "Ростов-на-Дону",
    # Краснодар
    "краснодар": "Краснодар", "краснодара": "Краснодар",
    "краснодаре": "Краснодар",
    # Волгоград
    "волгоград": "Волгоград", "волгограда": "Волгоград",
    "волгограде": "Волгоград",
    # Казань
    "казань": "Казань", "казани": "Казань",
    # Екатеринбург
    "екатеринбург": "Екатеринбург", "екатеринбурга": "Екатеринбург",
    "екатеринбурге": "Екатеринбург", "екатербург": "Екатеринбург",
    # Новосибирск
    "новосибирск": "Новосибирск", "новосибирска": "Новосибирск",
    "новосибирске": "Новосибирск",
    # Нижний Новгород
    "нижний новгород": "Нижний Новгород", "нижнего новгорода": "Нижний Новгород",
    "нижнем новгороде": "Нижний Новгород", "нижний": None,
    # Самара
    "самара": "Самара", "самары": "Самара", "самаре": "Самара",
    # Уфа
    "уфа": "Уфа", "уфы": "Уфа", "уфе": "Уфа",
    # Челябинск
    "челябинск": "Челябинск", "челябинска": "Челябинск",
    "челябинске": "Челябинск",
    # Пермь
    "пермь": "Пермь", "перми": "Пермь",
    # Красноярск
    "красноярск": "Красноярск", "красноярска": "Красноярск",
    "красноярске": "Красноярск",
    # Тюмень
    "тюмень": "Тюмень", "тюмени": "Тюмень",
    # Омск
    "омск": "Омск", "омска": "Омск", "омске": "Омск",
    # Воронеж
    "воронеж": "Воронеж", "воронежа": "Воронеж", "воронеже": "Воронеж",
    # Саратов
    "саратов": "Саратов", "саратова": "Саратов", "саратове": "Саратов",
    # Барнаул
    "барнаул": "Барнаул", "барнаула": "Барнаул", "барнауле": "Барнаул",
    # Иркутск
    "иркутск": "Иркутск", "иркутска": "Иркутск", "иркутске": "Иркутск",
    # Хабаровск
    "хабаровск": "Хабаровск", "хабаровска": "Хабаровск",
    "хабаровске": "Хабаровск",
    # Владивосток
    "владивосток": "Владивосток", "владивостока": "Владивосток",
    "владивостоке": "Владивосток",
    # Мурманск
    "мурманск": "Мурманск", "мурманска": "Мурманск", "мурманске": "Мурманск",
    # Архангельск
    "архангельск": "Архангельск", "архангельска": "Архангельск",
    "архангельске": "Архангельск",
    # Калининград
    "калининград": "Калининград", "калининграда": "Калининград",
    "калининграде": "Калининград",
    # Кемерово
    "кемерово": "Кемерово", "кемерова": "Кемерово", "кемерове": "Кемерово",
    # Рязань
    "рязань": "Рязань", "рязани": "Рязань",
    # Тула
    "тула": "Тула", "тулы": "Тула", "туле": "Тула",
    # Смоленск
    "смоленск": "Смоленск", "смоленска": "Смоленск", "смоленске": "Смоленск",
    # Брянск
    "брянск": "Брянск", "брянска": "Брянск", "брянске": "Брянск",
    # Курск
    "курск": "Курск", "курска": "Курск", "курске": "Курск",
    # Липецк
    "липецк": "Липецк", "липецка": "Липецк", "липецке": "Липецк",
    # Тамбов
    "тамбов": "Тамбов", "тамбова": "Тамбов", "тамбове": "Тамбов",
    # Пенза
    "пенза": "Пенза", "пензы": "Пенза", "пензе": "Пенза",
    # Ульяновск
    "ульяновск": "Ульяновск", "ульяновска": "Ульяновск",
    "ульяновске": "Ульяновск",
    # Саранск
    "саранск": "Саранск", "саранска": "Саранск", "саранске": "Саранск",
    # Чебоксары
    "чебоксары": "Чебоксары", "чебоксар": "Чебоксары",
    # Нижний Тагил
    "нижний тагил": "Нижний Тагил", "нижнего тагила": "Нижний Тагил",
    # Чита
    "чита": "Чита", "читы": "Чита", "чите": "Чита",
    # Якутск
    "якутск": "Якутск", "якутска": "Якутск", "якутске": "Якутск",
    # Махачкала
    "махачкала": "Махачкала", "махачкалы": "Махачкала",
    # Оренбург
    "оренбург": "Оренбург", "оренбурга": "Оренбург", "оренбурге": "Оренбург",
    # Новокузнецк
    "новокузнецк": "Новокузнецк", "новокузнецка": "Новокузнецк",
    # Томск
    "томск": "Томск", "томска": "Томск", "томске": "Томск",
    # Тверь
    "тверь": "Тверь", "твери": "Тверь",
    # Ярославль
    "ярославль": "Ярославль", "ярославля": "Ярославль",
    # Ижевск
    "ижевск": "Ижевск", "ижевска": "Ижевск",
    # Крым
    "крым": "Крым", "крыму": "Крым", "крыме": "Крым",
    "севастополь": "Севастополь", "севастополя": "Севастополь",
    "симферополь": "Симферополь", "симферополя": "Симферополь",
    # Ивановская область
    "иваново": "Иваново", "иванова": "Иваново", "иванове": "Иваново",
    "ивановская": "Иваново", "ивановской": "Иваново",
    "кинешма": "Кинешма", "кинешмы": "Кинешма", "кинешме": "Кинешма",
    "куя": "Кинешма",
    "шуя": "Шуя", "шуи": "Шуя", "шую": "Шуя",
    "кохма": "Кохма", "кохмы": "Кохма", "кохме": "Кохма",
    "вичуга": "Вичуга", "вичуги": "Вичуга", "вичуге": "Вичуга",
    "фурманов": "Фурманов", "фурманова": "Фурманов",
    "приволжск": "Приволжск", "приволжска": "Приволжск",
    "пучеж": "Пучеж", "пучежа": "Пучеж",
    "заволжье": "Заволжье", "заволжья": "Заволжье",
    # Киров
    "киров": "Киров", "кирова": "Киров", "кирове": "Киров",
    # Сочи
    "сочи": "Сочи",
    # Ставрополь
    "ставрополь": "Ставрополь", "ставрополя": "Ставрополь",
    # Ноябрьск
    "ноябрьск": "Ноябрьск", "ноябрьска": "Ноябрьск",
    # Надым
    "надым": "Надым", "надыма": "Надым",
    # Салехард
    "салехард": "Салехард", "салехарда": "Салехард",
    # Сургут
    "сургут": "Сургут", "сургута": "Сургут", "сургуте": "Сургут",
    # Нижневартовск
    "нижневартовск": "Нижневартовск", "нижневартовска": "Нижневартовск",
    # Тольятти
    "тольятти": "Тольятти",
    # Курган
    "курган": "Курган", "кургана": "Курган",
    # Нижний Тагил
    "нижний тагил": "Нижний Тагил", "нижнего тагила": "Нижний Тагил",
    # Псков
    "псков": "Псков", "пскова": "Псков", "пскове": "Псков",
    # Обнинск
    "обнинск": "Обнинск", "обнинска": "Обнинск",
    # Калуга
    "калуга": "Калуга", "калуги": "Калуга", "калуге": "Калуга",
    # Кострома
    "кострома": "Кострома", "костромы": "Кострома", "костроме": "Кострома",
    # Ульяновск
    "ульяновск": "Ульяновск", "ульяновска": "Ульяновск",
    # Саранск
    "саранск": "Саранск", "саранска": "Саранск",
    # Астрахань
    "астрахань": "Астрахань", "астрахани": "Астрахань",
    # Киров
    "киров": "Киров", "кирова": "Киров",
    # Барнаул (дубль)
    "барнаул": "Барнаул",
    # Кемерово (дубль)
    "кемерово": "Кемерово",
}


def _extract_city_from_text(text: str) -> Optional[str]:
    """Извлекает город из текста сообщения.

    Приоритет: более длинные совпадения первыми.
    Возвращает canonical name или None.
    """
    text_lower = text.lower()
    # Сортируем по длине ключа (длинные первые) чтобы "нижний новгород" matched раньше "нижний"
    for alias, canonical in sorted(CITY_ALIASES.items(), key=lambda x: -len(x[0])):
        if canonical is None:
            continue
        if alias in text_lower:
            return canonical
    return None


async def find_station_by_text(network: Optional[str], text: str, city: Optional[str] = None) -> Optional[int]:
    """Ищет АЗС в БД по сети и городу (если указан).

    Приоритет:
    1) Сеть + город (точное совпадение)
    2) Только сеть
    3) Только город
    4) Fallback: случайная станция из БД

    Возвращает station_id или None.
    """
    # Если город не указан, пытаемся извлечь из текста
    if not city:
        city = _extract_city_from_text(text)

    # Нормализуем сеть для поиска в БД
    network_search = network.lower() if network else None

    # 1) Сеть + город (самый точный вариант)
    if network_search and city:
        if db.USE_SQLITE:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE (LOWER(operator) LIKE ? OR LOWER(name) LIKE ?)
                     AND py_lower(city) = py_lower(?)
                   ORDER BY is_verified DESC, id
                   LIMIT 1""",
                f"%{network_search}%",
                f"%{network_search}%",
                city,
            )
        else:
            async with db._db.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE (LOWER(operator) LIKE $1 OR LOWER(name) LIKE $1)
                         AND LOWER(city) = LOWER($2)
                       ORDER BY is_verified DESC, id
                       LIMIT 1""",
                    f"%{network_search}%",
                    city,
                )
        if rows:
            return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]

    # 2) Только сеть
    if network_search:
        if db.USE_SQLITE:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE LOWER(operator) LIKE ? OR LOWER(name) LIKE ?
                   ORDER BY is_verified DESC, id
                   LIMIT 1""",
                f"%{network_search}%",
                f"%{network_search}%",
            )
        else:
            async with db._db.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE LOWER(operator) LIKE $1 OR LOWER(name) LIKE $1
                       ORDER BY is_verified DESC, id
                       LIMIT 1""",
                    f"%{network_search}%",
                )
        if rows:
            return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]

    # 3) Только город
    if city:
        if db.USE_SQLITE:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE py_lower(city) = py_lower(?)
                   ORDER BY is_verified DESC, id
                   LIMIT 1""",
                city,
            )
        else:
            async with db._db.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE LOWER(city) = LOWER($1)
                       ORDER BY is_verified DESC, id
                       LIMIT 1""",
                    city,
                )
        if rows:
            return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]

    # 4) Fallback: случайная станция
    if db.USE_SQLITE:
        rows = await db._fetch("SELECT id FROM stations ORDER BY RANDOM() LIMIT 1")
    else:
        async with db._db.acquire() as conn:
            rows = await conn.fetch("SELECT id FROM stations ORDER BY RANDOM() LIMIT 1")
    if rows:
        return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]
    return None


async def save_telegram_report(
    station_id: int,
    fuel_type: str,
    available: Optional[bool],
    raw_text: str,
    price: Optional[float] = None,
    queue: Optional[int] = None,
    next_delivery: Optional[datetime] = None,
    channel: str = "",
    message_time: Optional[datetime] = None,
) -> int:
    """Сохраняет отчёт от парсера Telegram. Возвращает report_id.

    channel: имя канала (для определения надёжности)
    message_time: время сообщения (для расчёта свежести)
    """
    # Рассчитываем confidence на основе надёжности канала и свежести
    reliability = get_source_reliability(channel)
    age_hours = 0.0
    if message_time:
        now = datetime.now()
        if message_time.tzinfo:
            from datetime import timezone
            now = now.astimezone(timezone.utc)
        age_hours = (now - message_time).total_seconds() / 3600.0

    # Confidence = надёжность × свежесть
    freshness = max(0.1, 1.0 - (age_hours / 24.0) ** 0.5) if age_hours else 1.0
    confidence = round(reliability * freshness, 3)

    report_id = await db.add_report(
        station_id=station_id,
        fuel_type=fuel_type,
        available=available,
        price=price,
        queue_size=queue,
        source="tg",
        comment=f"tg:{channel}: {raw_text[:200]}",
        next_delivery_at=next_delivery,
    )
    logger.info(
        "✅ TG отчёт: station=%d fuel=%s avail=%s price=%s queue=%s next=%s ch=%s conf=%.2f",
        station_id, fuel_type, available, price, queue, next_delivery, channel, confidence,
    )
    return report_id


async def upload_to_api(results: list, upload_url: str, api_key: str = "") -> bool:
    """Загружает в backend через /api/import_prices."""
    try:
        import aiohttp
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-Import-Key"] = api_key
        payload = {
            "source": "tg",
            "scraped_at": datetime.now().isoformat(),
            "results": results,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                upload_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    logger.info(f"✅ Загружено в API: {body}")
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"⚠ API {resp.status}: {text[:200]}")
                    return False
    except Exception as e:
        logger.warning(f"⚠ Upload: {e}")
        return False


async def handle_message(msg, upload_url: str = None, api_key: str = "",
                         channel_name: str = "") -> int:
    """Обрабатывает одно сообщение: парсит и сохраняет.

    Возвращает количество сохранённых отчётов.
    """
    if not msg.text or len(msg.text) < 10:
        return 0
    parsed = parse_fuel_status(msg.text)
    if not parsed:
        return 0

    # Город по умолчанию для канала (если не извлечён из текста)
    channel_city = CHANNEL_CITY_HINTS.get(channel_name)

    # Время сообщения (для расчёта свежести)
    msg_time = msg.date

    saved = 0
    upload_results = []

    for p in parsed:
        city = _extract_city_from_text(msg.text) or channel_city
        station_id = await find_station_by_text(p.get("network"), msg.text, city=city)
        if not station_id:
            logger.debug("No station found for network=%s text=%r", p.get("network"), msg.text[:80])
            continue

        # Локальное сохранение
        if not upload_url:
            await save_telegram_report(
                station_id=station_id,
                fuel_type=p["fuel_type"],
                available=p["available"],
                raw_text=msg.text,
                price=p.get("price"),
                queue=p.get("queue"),
                next_delivery=p.get("next_delivery"),
                channel=channel_name,
                message_time=msg_time,
            )
            saved += 1
        else:
            # Подготовим для upload в API
            upload_results.append({
                "external_id": f"tg_{msg.id}",
                "name": p.get("network", "Unknown") + f" #{station_id}",
                "region_name": p.get("network", "Unknown"),
                "city": None,
                "operator": p.get("network"),
                "lat": None,
                "lon": None,
                "prices": {p["fuel_type"]: p["price"]} if p.get("price") else {},
            })

    if upload_url and upload_results:
        await upload_to_api(upload_results, upload_url, api_key)

    return saved


async def discover_fuel_chats(client) -> list[str]:
    """Находит все топливные чаты, включая приватные.

    Проверяет:
    1) Название чата на ключевые слова
    2) Описание чата (bio) на ключевые слова

    Возвращает список username/ID для чатов,
    связанных с бензином/АЗС.
    """
    FUEL_KEYWORDS = [
        "бензин", "азс", "топливо", "заправк", "горюч",
        "где заправ", "где залить", "нет топлива", "очередь",
        "92", "95", "98", "дизель",
    ]

    fuel_chats = []
    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue

        name = (dialog.name or "").lower()
        matched = any(kw in name for kw in FUEL_KEYWORDS)

        # Если не совпало по имени, проверяем описание
        if not matched:
            try:
                if hasattr(dialog.entity, 'about') and dialog.entity.about:
                    bio = dialog.entity.about.lower()
                    matched = any(kw in bio for kw in FUEL_KEYWORDS)
            except:
                pass

        if matched:
            # Получаем username
            if dialog.entity and hasattr(dialog.entity, 'username') and dialog.entity.username:
                fuel_chats.append(dialog.entity.username)
                logger.info("  Found public chat: @%s (%s)", dialog.entity.username, dialog.name)
            else:
                # Приватный чат — сохраняем ID
                fuel_chats.append(str(dialog.id))
                logger.info("  Found private chat: ID=%d (%s)", dialog.id, dialog.name)

    return fuel_chats


async def join_invite(client, invite_hash: str) -> bool:
    """Присоединяется к чату по invite ссылке."""
    try:
        await client.join_chat(invite_hash)
        logger.info("✅ Joined chat via invite: %s", invite_hash)
        return True
    except Exception as e:
        logger.warning("⚠ Failed to join %s: %s", invite_hash, e)
        return False


def _save_discovered_chats(chats: list[str]):
    """Сохраняет обнаруженные чаты в файл для повторного использования."""
    import json
    discovered_path = os.path.join(os.path.dirname(__file__), "discovered_chats.json")
    # Загружаем существующие
    existing = []
    if os.path.exists(discovered_path):
        with open(discovered_path, "r") as f:
            existing = json.load(f)
    # Добавляем новые (без дубликатов)
    for chat in chats:
        if chat not in existing:
            existing.append(chat)
    with open(discovered_path, "w") as f:
        json.dump(existing, f, indent=2)
    logger.info("Saved %d discovered chats to %s", len(existing), discovered_path)


def _load_discovered_chats() -> list[str]:
    """Загружает ранее обнаруженные чаты из файла."""
    import json
    discovered_path = os.path.join(os.path.dirname(__file__), "discovered_chats.json")
    if os.path.exists(discovered_path):
        with open(discovered_path, "r") as f:
            return json.load(f)
    return []


async def run_once(upload_url: str = None, api_key: str = "", discover: bool = False):
    """Один проход: читает последние N сообщений из каждого канала.

    Args:
        discover: Если True, автоматически находит приватные топливные чаты
    """
    if not TG_API_ID or not TG_API_HASH:
        logger.error("TG_API_ID / TG_API_HASH не заданы. См. инструкции в начале файла.")
        sys.exit(1)
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    else:
        client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()
    logger.info("Authorized as %s", (await client.get_me()).username)
    import os
    if not db.API_MODE:
        await db.init_db()
    await db.stale_old_reports("tg", older_than_hours=24)

    # Автоматическое обнаружение приватных чатов
    all_channels = list(CHANNELS)
    if discover:
        logger.info("=== Discovering fuel chats ===")
        discovered = await discover_fuel_chats(client)
        # Сохраняем обнаруженные чаты
        _save_discovered_chats(discovered)
        for chat in discovered:
            if chat not in all_channels:
                all_channels.append(chat)
                logger.info("  Added discovered chat: %s", chat)
        logger.info("Total channels to scan: %d (was %d, discovered %d)",
                     len(all_channels), len(CHANNELS), len(discovered) - len(CHANNELS))

    # Загружаем ранее обнаруженные чаты
    for chat in _load_discovered_chats():
        if chat not in all_channels:
            all_channels.append(chat)
            logger.info("  Loaded previously discovered chat: %s", chat)

    total_saved = 0
    channels_found = 0
    channels_failed = 0

    for channel in all_channels:
        try:
            entity = await client.get_entity(channel)
        except Exception as e:
            logger.warning("Cannot find channel %s: %s", channel, e)
            channels_failed += 1
            continue
        channels_found += 1
        logger.info("Scanning channel: %s (reliability=%.2f)", channel, get_source_reliability(channel))
        count = 0
        for msg in await client.get_messages(entity, limit=200):
            saved = await handle_message(msg, upload_url, api_key, channel_name=channel)
            total_saved += saved
            count += 1
        logger.info("  Scanned %d messages in %s", count, channel)

    await client.disconnect()
    import os
    if not db.API_MODE:
        await db.close_db()
    logger.info("=== Total TG reports saved: %d (channels: %d found, %d failed) ===",
                total_saved, channels_found, channels_failed)
    return total_saved


async def run_watch(upload_url: str = None, api_key: str = ""):
    """Слушает новые сообщения в реальном времени."""
    if not TG_API_ID or not TG_API_HASH:
        logger.error("TG_API_ID / TG_API_HASH не заданы.")
        sys.exit(1)
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    else:
        client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()
    logger.info("Authorized as %s", (await client.get_me()).username)
    logger.info("Watching for new messages in: %s", CHANNELS)
    await db.init_db()

    @client.on(events.NewMessage(chats=CHANNELS))
    async def handler(event):
        await handle_message(event.message, upload_url, api_key, channel_name=event.chat.username or "")

    await client.run_until_disconnected()


async def _join_invite_mode(args):
    """Режим присоединения к чату по invite ссылке."""
    if not TG_API_ID or not TG_API_HASH:
        print("❌ TG_API_ID / TG_API_HASH не заданы")
        return 1
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    else:
        client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()

    invite = args.join
    # Поддержка формата t.me/+hash или t.me/joinchat/hash
    if "t.me/" in invite:
        invite = invite.split("t.me/")[-1]
        if invite.startswith("+"):
            invite = invite[1:]
        elif invite.startswith("joinchat/"):
            invite = "joinchat/" + invite.split("joinchat/")[-1]

    success = await join_invite(client, invite)
    if success:
        # Получаем информацию о чате
        try:
            async for d in client.iter_dialogs():
                if hasattr(d.entity, 'username') and d.entity.username:
                    print(f"✅ Joined: @{d.entity.username} ({d.name})")
                    break
                elif d.id:
                    print(f"✅ Joined: ID={d.id} ({d.name})")
                    break
        except:
            print("✅ Joined, but could not get chat info")
    else:
        print("❌ Failed to join chat")

    await client.disconnect()
    return 0


def main():
    parser = argparse.ArgumentParser(description="Парсер Telegram-каналов про бензин")
    parser.add_argument("--watch", action="store_true", help="Слушать новые сообщения в реальном времени")
    parser.add_argument("--discover", action="store_true",
                        help="Автоматически находит приватные топливные чаты через get_dialogs()")
    parser.add_argument("--join", default=None,
                        help="Присоединиться к чату по invite ссылке (t.me/+hash)")
    parser.add_argument("--upload-url", default=None, help="URL для POST с JSON (например backend /api/import_prices)")
    parser.add_argument("--api-key", default=os.environ.get("IMPORT_API_KEY", ""),
                        help="API ключ для upload-url")
    parser.add_argument("--channels", default=None,
                        help="Каналы через запятую (переопределяет TG_CHANNELS и DEFAULT_CHANNELS)")
    args = parser.parse_args()

    global CHANNELS
    if args.channels:
        CHANNELS = [c.strip() for c in args.channels.split(",") if c.strip()]

    # Режим присоединения к invite ссылке
    if args.join:
        asyncio.run(_join_invite_mode(args))
        return

    if args.watch:
        asyncio.run(run_watch(args.upload_url, args.api_key))
    else:
        asyncio.run(run_once(args.upload_url, args.api_key, discover=args.discover))


if __name__ == "__main__":
    main()
