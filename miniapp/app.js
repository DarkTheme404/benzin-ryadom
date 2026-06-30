/**
 * Бензин рядом — Telegram Mini App
 */
(function () {
  'use strict';

  // === Telegram WebApp init ===
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    // Apply theme
    document.documentElement.style.setProperty('--bg', tg.backgroundColor || '#ffffff');
  }

  // === API base ===
  // In production: API is on the same host as the bot (Render)
  // For dev: use localhost:8080
  const API = (function () {
    // If opened inside Telegram — use the server that hosts both bot + API
    // The bot's main.py runs API on PORT (default 8080)
    // Mini App URL is set in bot config → points to static hosting of this HTML
    // API calls go to the same Render service
    const params = new URLSearchParams(window.location.search);
    const apiBase = params.get('api') || '';
    // Fallback: same origin
    return apiBase || window.location.origin;
  })();

  const tgId = tg?.initDataUnsafe?.user?.id || null;

  // === State ===
  let state = {
    city: '',
    fuel: '',
    maxPrice: null,
    network: '',
    stations: [],
    reportStationId: null,
    reportStationName: '',
    reportFuel: '92',
    searchQuery: '',
  };

  // === DOM ===
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const citySelect = $('#city-select');
  const geoBtn = $('#geo-btn');
  const emergencyBtn = $('#emergency-btn');
  const searchInput = $('#search-input');
  const stationsList = $('#stations-list');
  const disclaimer = $('#disclaimer');
  const reportOverlay = $('#report-overlay');
  const toast = $('#toast');

  // === Top cities ===
  const TOP_CITIES = [
    'Иваново', 'Москва', 'Санкт-Петербург', 'Кинешма',
    'Шуя', 'Фурманов', 'Приволжск', 'Кохма',
    'Тейково', 'Южа', 'Лежнево', 'Плёс',
    'Нижний Новгород', 'Кострома', 'Ярославль', 'Владимир',
    'Рязань', 'Тула', 'Калуга', 'Тверь',
    'Смоленск', 'Орёл', 'Брянск', 'Курск',
    'Липецк', 'Тамбов', 'Пенза', 'Саратов',
    'Волгоград', 'Екатеринбург', 'Казань', 'Челябинск',
    'Новосибирск', 'Красноярск', 'Омск', 'Барнаул',
    'Пермь', 'Уфа', 'Самара', 'Оренбург',
    'Краснодар', 'Ставрополь', 'Сочи',
  ];

  // === Init city select ===
  function initCities() {
    citySelect.innerHTML = '<option value="">Выбери город</option>';
    TOP_CITIES.forEach((c) => {
      const opt = document.createElement('option');
      opt.value = c;
      opt.textContent = c;
      if (c === state.city) opt.selected = true;
      citySelect.appendChild(opt);
    });
    // Custom option
    const custom = document.createElement('option');
    custom.value = '__custom__';
    custom.textContent = '✏️ Другой город...';
    citySelect.appendChild(custom);
  }
  initCities();

  // === Geolocation ===
  geoBtn.addEventListener('click', async () => {
    if (!navigator.geolocation) {
      showToast('Геолокация недоступна');
      return;
    }
    geoBtn.textContent = '⏳';
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        geoBtn.textContent = '📍';
        const { latitude: lat, longitude: lon } = pos.coords;
        try {
          const resp = await fetch(
            `${API}/api/reverse-geocode?lat=${lat}&lon=${lon}`
          );
          const data = await resp.json();
          if (data.city) {
            setCity(data.city);
          } else {
            showToast('Не удалось определить город');
          }
        } catch {
          showToast('Ошибка определения города');
        }
      },
      () => {
        geoBtn.textContent = '📍';
        showToast('Доступ к геолокации запрещён');
      },
      { timeout: 10000 }
    );
  });

  // === City change ===
  citySelect.addEventListener('change', () => {
    const val = citySelect.value;
    if (val === '__custom__') {
      const name = prompt('Напиши название города:');
      if (name && name.trim()) {
        setCity(name.trim());
      } else {
        citySelect.value = state.city || '';
      }
      return;
    }
    if (val) setCity(val);
  });

  function setCity(city) {
    state.city = city;
    citySelect.value = city;
    // Update header
    $('#header-subtitle').textContent = `АЗС в г. ${city}`;
    // Save to localStorage
    try { localStorage.setItem('benzin_city', city); } catch {}
    loadStations();
  }

  // === Load saved city ===
  try {
    const saved = localStorage.getItem('benzin_city');
    if (saved && TOP_CITIES.includes(saved)) {
      setCity(saved);
    }
  } catch {}

  // === Filters ===
  $$('#fuel-filters .filter-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      $$('#fuel-filters .filter-chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      state.fuel = chip.dataset.fuel;
      loadStations();
    });
  });

  $$('#price-filters .filter-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      $$('#price-filters .filter-chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      state.maxPrice = chip.dataset.price ? parseFloat(chip.dataset.price) : null;
      loadStations();
    });
  });

  $$('#network-filters .network-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      $$('#network-filters .network-chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      state.network = chip.dataset.net;
      loadStations();
    });
  });

  // === Search ===
  let searchTimer = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.searchQuery = searchInput.value.trim();
      if (state.searchQuery.length >= 2) {
        doSearch(state.searchQuery);
      } else if (state.searchQuery.length === 0) {
        loadStations();
      }
    }, 300);
  });

  // === API calls ===
  async function loadStations() {
    if (!state.city) {
      stationsList.innerHTML = `
        <div class="empty-state">
          <div class="icon">⛽</div>
          <h3>Выбери город</h3>
          <p>Или нажми 📍 для определения по геолокации</p>
        </div>`;
      return;
    }

    stationsList.innerHTML = '<div class="loading"><div class="spinner"></div><div>Загрузка АЗС...</div></div>';

    try {
      const params = new URLSearchParams({
        city: state.city,
        has_stock: '1',
        include_nearby_regions: '1',
        limit: '50',
      });
      if (state.fuel) params.set('fuel', state.fuel);
      if (state.network) params.set('network', state.network);
      if (state.maxPrice) params.set('max_price', state.maxPrice);
      if (tgId) params.set('telegram_id', tgId);

      const resp = await fetch(`${API}/api/stations/by-city?${params}`);
      const data = await resp.json();

      if (data.disclaimer) {
        disclaimer.style.display = 'block';
        disclaimer.textContent = '⚠️ ' + data.disclaimer;
      }

      state.stations = data.stations || [];
      renderStations(state.stations, data.city);
    } catch (err) {
      stationsList.innerHTML = `
        <div class="empty-state">
          <div class="icon">⚠️</div>
          <h3>Ошибка загрузки</h3>
          <p>${err.message}</p>
        </div>`;
    }
  }

  async function doSearch(query) {
    stationsList.innerHTML = '<div class="loading"><div class="spinner"></div><div>Поиск...</div></div>';
    try {
      const params = new URLSearchParams({ q: query });
      if (tgId) params.set('telegram_id', tgId);

      const resp = await fetch(`${API}/api/search?${params}`);
      const data = await resp.json();
      state.stations = data.stations || [];
      renderStations(state.stations, null, true);
    } catch (err) {
      stationsList.innerHTML = `
        <div class="empty-state">
          <div class="icon">⚠️</div>
          <h3>Ошибка поиска</h3>
          <p>${err.message}</p>
        </div>`;
    }
  }

  // === Render stations ===
  function renderStations(stations, city, isSearch) {
    if (!stations || stations.length === 0) {
      stationsList.innerHTML = `
        <div class="empty-state">
          <div class="icon">🔍</div>
          <h3>Ничего не найдено</h3>
          <p>Попробуй другой город, фильтр или поищи по названию</p>
        </div>`;
      return;
    }

    const html = stations.map((s) => {
      const statuses = s.statuses || [];
      const fuelMap = {};
      statuses.forEach((st) => {
        if (!fuelMap[st.fuel_type]) fuelMap[st.fuel_type] = st;
      });

      const fuelBadges = Object.entries(fuelMap).map(([fuel, st]) => {
        const avail = st.available;
        const cls = avail === true ? 'available' : avail === false ? 'unavailable' : 'unknown';
        const priceStr = st.price ? ` <span class="price">${fmtPrice(st.price)}₽</span>` : '';
        const icon = avail === true ? '🟢' : avail === false ? '🔴' : '⚪';
        return `<span class="fuel-badge ${cls}"><span class="dot"></span>${fmtFuel(fuel)}${priceStr}</span>`;
      }).join('');

      const dist = s.distance_km != null ? `${s.distance_km.toFixed(1)} км` : '';
      const operator = s.operator || s.name || '';
      const badge = s.is_verified ? '<span class="badge verified">✓</span>' : '';

      return `
        <div class="station-card" data-id="${s.id}" onclick="window._openStation(${s.id})">
          <div class="top-row">
            <div>
              <div class="name">${esc(operator)} ${badge}</div>
            </div>
            <div class="distance">${dist}</div>
          </div>
          <div class="address">${esc(s.address || '')}</div>
          <div class="fuels">${fuelBadges || '<span class="fuel-badge unknown"><span class="dot"></span>Нет данных</span>'}</div>
          <div class="meta">
            <span>📊 ${statuses.length} отчётов</span>
            ${s.city ? `<span>📍 ${esc(s.city)}</span>` : ''}
          </div>
        </div>`;
    }).join('');

    stationsList.innerHTML = html;
  }

  // === Station detail (inline expand) ===
  window._openStation = async function (stationId) {
    const card = document.querySelector(`.station-card[data-id="${stationId}"]`);
    if (!card) return;

    // Toggle detail
    let detail = card.querySelector('.station-detail');
    if (detail) {
      detail.classList.toggle('open');
      return;
    }

    // Fetch detail
    try {
      const resp = await fetch(`${API}/api/stations/${stationId}`);
      const data = await resp.json();
      const s = data.station;
      const statuses = data.statuses || [];

      detail = document.createElement('div');
      detail.className = 'station-detail open';

      let priceHistory = '';
      if (statuses.length > 0) {
        priceHistory = `
          <div class="detail-section">
            <h4>Текущие цены</h4>
            ${statuses.map((st) => `
              <div class="price-row">
                <span class="label">${fmtFuel(st.fuel_type)} ${st.available ? '🟢' : st.available === false ? '🔴' : '⚪'}</span>
                <span class="value">${st.price ? fmtPrice(st.price) + ' ₽/л' : '—'}</span>
              </div>
            `).join('')}
          </div>`;
      }

      detail.innerHTML = `
        ${priceHistory}
        <div class="detail-section">
          <button class="btn btn-primary" style="width:100%" onclick="window._openReport(${stationId}, '${esc(s.name || s.operator || '')}')">
            📝 Сообщить о наличии
          </button>
        </div>`;

      card.appendChild(detail);
    } catch (err) {
      showToast('Ошибка загрузки');
    }
  };

  // === Report ===
  window._openReport = function (stationId, stationName) {
    state.reportStationId = stationId;
    state.reportStationName = stationName;
    state.reportFuel = state.fuel || '92';
    $('#report-title').textContent = `Сообщить — ${stationName || 'АЗС #' + stationId}`;
    // Reset fuel selection
    $$('#report-fuel-options .fuel-opt').forEach((o) => {
      o.classList.toggle('selected', o.dataset.fuel === state.reportFuel);
    });
    $$('#report-avail .avail-opt').forEach((o) => {
      o.classList.toggle('selected', o.dataset.avail === 'true');
    });
    $('#report-price').value = '';
    reportOverlay.classList.add('open');
    tg?.MainButton?.hide();
  };

  $$('#report-fuel-options .fuel-opt').forEach((opt) => {
    opt.addEventListener('click', () => {
      $$('#report-fuel-options .fuel-opt').forEach((o) => o.classList.remove('selected'));
      opt.classList.add('selected');
      state.reportFuel = opt.dataset.fuel;
    });
  });

  $$('#report-avail .avail-opt').forEach((opt) => {
    opt.addEventListener('click', () => {
      $$('#report-avail .avail-opt').forEach((o) => o.classList.remove('selected'));
      opt.classList.add('selected');
    });
  });

  $('#report-cancel').addEventListener('click', () => {
    reportOverlay.classList.remove('open');
  });

  reportOverlay.addEventListener('click', (e) => {
    if (e.target === reportOverlay) reportOverlay.classList.remove('open');
  });

  $('#report-submit').addEventListener('click', async () => {
    const avail = $('#report-avail .avail-opt.selected')?.dataset.avail === 'true';
    const price = parseFloat($('#report-price').value) || null;

    try {
      const body = {
        station_id: state.reportStationId,
        fuel_type: state.reportFuel,
        available: avail,
        price: price,
        telegram_id: tgId,
        first_name: tg?.initDataUnsafe?.user?.first_name || 'MiniApp User',
      };

      const resp = await fetch(`${API}/api/reports`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();

      if (data.ok) {
        showToast('✅ Отчёт отправлен!');
        if (data.new_badges?.length) {
          data.new_badges.forEach((b) => {
            setTimeout(() => showToast(`${b.emoji} Получен бейдж: ${b.name}`), 1500);
          });
        }
        reportOverlay.classList.remove('open');
        loadStations(); // refresh
      } else {
        showToast('❌ Ошибка: ' + (data.error || 'неизвестная'));
      }
    } catch (err) {
      showToast('❌ Ошибка отправки');
    }
  });

  // === Emergency ===
  emergencyBtn.addEventListener('click', async () => {
    if (!state.city) {
      showToast('Сначала выбери город');
      return;
    }
    stationsList.innerHTML = '<div class="loading"><div class="spinner"></div><div>🚨 Ищем ближайшую АЗС...</div></div>';
    try {
      const resp = await fetch(
        `${API}/api/stations/emergency?city=${encodeURIComponent(state.city)}&fuel=${state.fuel || '92'}`
      );
      const data = await resp.json();
      state.stations = data.stations || [];
      if (state.stations.length === 0) {
        stationsList.innerHTML = `
          <div class="empty-state">
            <div class="icon">😰</div>
            <h3>Не нашли АЗС с подтверждённым наличием</h3>
            <p>Попробуй проверить телефоном nearby станции или подожди обновлений от пользователей</p>
          </div>`;
      } else {
        renderStations(state.stations, state.city);
      }
    } catch (err) {
      stationsList.innerHTML = `
        <div class="empty-state">
          <div class="icon">⚠️</div>
          <h3>Ошибка</h3>
          <p>${err.message}</p>
        </div>`;
    }
  });

  // === Helpers ===
  function fmtFuel(f) {
    const map = { '92': 'АИ-92', '95': 'АИ-95', '98': 'АИ-98', 'diesel': 'ДТ', '100': 'АИ-100', 'lpg': 'ГАЗ' };
    return map[f] || f;
  }

  function fmtPrice(p) {
    return p != null ? Number(p).toFixed(2) : '—';
  }

  function esc(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function showToast(msg) {
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
  }

})();
