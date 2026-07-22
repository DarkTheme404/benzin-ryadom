/**
 * Бензин рядом — Telegram + VK Mini App
 * Modern, single-page app with full bot functionality
 */
(function () {
  'use strict';

  // ============= PLATFORM DETECTION =============
  const platform = {
    tg: !!(window.Telegram && window.Telegram.WebApp),
    vk: false, // determined async via VK Bridge
    scheme: 'dark', // color scheme: dark / light / vkontakte_dark / bright_light
  };

  const tg = platform.tg ? window.Telegram.WebApp : null;

  if (tg) {
    tg.ready();
    tg.expand();
    // Force dark theme — light theme has white background + light text,
    // which makes cards/text unreadable. We always use dark.
    // To re-enable light theme support, set LOCAL_STORAGE_FORCE_LIGHT=1.
    if (tg.colorScheme === 'light' && localStorage.getItem('force_light') === '1') {
      document.body.classList.add('tg-light');
    }
  }

  // ============= STATE (must be before VK Bridge IIFE to avoid TDZ) =============
  const state = {
    screen: 'home',
    tab: 'home',
    city: '',
    cityRegion: '',
    fuel: '',
    maxPrice: 0,
    network: '',
    searchQuery: '',
    stations: [],
    userLocation: null, // { lat, lon }
    selectedStation: null,
    vkUserId: null,        // VK user ID
    vkLaunchParams: null,  // VK launch params
    tgUser: null,          // TG user info
    reportSheet: {
      stationId: null,
      stationName: '',
      fuel: '92',
      available: true,
      price: null,
      queue: null,
    },
    reviewSheet: {
      stationId: null,
      stationName: '',
      fuel: '92',
      rating: 0,
      comment: '',
    },
    cities: [], // popular cities
  };

  // VK Bridge detection + init
  const vkBridgePromise = (async () => {
    // Если bridge ещё не загружен — ждём до 3 сек
    for (let i = 0; i < 30; i++) {
      if (window.vkBridge) break;
      await new Promise(r => setTimeout(r, 100));
    }
    if (!window.vkBridge) {
      console.warn('VK Bridge not loaded after 3s — running without VK features');
      return false;
    }
    try {
      // Send init first
      await window.vkBridge.send('VKWebAppInit', {});
      // Get launch params (scheme, viewport, etc.)
      try {
        const launchParams = await window.vkBridge.send('VKWebAppGetLaunchParams', {});
        if (launchParams?.scheme) {
          platform.scheme = launchParams.scheme;
        }
        if (launchParams?.vk_user_id) {
          state.vkUserId = launchParams.vk_user_id;
        }
        // Store launch params for analytics
        state.vkLaunchParams = launchParams;
      } catch (e) {
        // Fallback: try get color scheme
        try {
          const colorScheme = await window.vkBridge.send('VKWebAppGetColorScheme', {});
          if (colorScheme === 'bright_light') platform.scheme = 'light';
          else if (colorScheme) platform.scheme = colorScheme;
        } catch (e2) {
          // ignore
        }
      }
      platform.vk = true;
      applyTheme();
      console.log('VK Bridge initialized', { scheme: platform.scheme, vk_user_id: state.vkUserId });
      return true;
    } catch (e) {
      console.warn('VK Bridge init failed:', e);
      return false;
    }
  })();

  function applyTheme() {
    // Force dark theme by default — light theme has white background + light text
    // which makes cards/text unreadable. To re-enable light theme, set
    // localStorage('force_light') = '1' before page load.
    const forceLight = (() => {
      try { return localStorage.getItem('force_light') === '1'; }
      catch (e) { return false; }
    })();

    if (forceLight && (platform.scheme === 'bright_light' || platform.scheme === 'light')) {
      document.body.classList.add('vk-light');
      document.body.classList.remove('vk-dark');
    } else {
      // Default: always dark (и в TG light, и в VK bright_light)
      document.body.classList.add('vk-dark');
      document.body.classList.remove('vk-light', 'tg-light');
    }
  }

  // ============= API =============
  const API = (() => {
    const params = new URLSearchParams(window.location.search);
    const apiBase = params.get('api') || '';
    return apiBase || window.location.origin;
  })();

  async function api(path, options = {}) {
    const url = `${API}${path}`;
    const headers = { 'Content-Type': 'application/json' };
    if (tg?.initData) headers['X-Telegram-Init-Data'] = tg.initData;
    // VK init data для backend auth (если доступен)
    if (platform.vk && state.vkLaunchParams) {
      try {
        const params = new URLSearchParams();
        for (const [k, v] of Object.entries(state.vkLaunchParams)) {
          if (typeof v !== 'object') params.set(k, String(v));
        }
        headers['X-VK-Init-Data'] = params.toString();
        if (state.vkUserId) headers['X-VK-User-Id'] = String(state.vkUserId);
      } catch (e) { /* ignore */ }
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000); // 15s timeout
    try {
      const res = await fetch(url, {
        ...options,
        signal: controller.signal,
        headers: { ...headers, ...(options.headers || {}) },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    } catch (e) {
      if (e.name === 'AbortError') throw new Error('Таймаут запроса (15с)');
      throw e;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function apiRetry(path, options = {}, maxRetries = 2) {
    // API call с автоматическим retry при сетевых ошибках
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        return await api(path, options);
      } catch (e) {
        const isNetworkError = e.message && (e.message.includes('Таймаут') || e.message.includes('Failed to fetch') || e.message.includes('NetworkError'));
        if (attempt === maxRetries || !isNetworkError) throw e;
        await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
      }
    }
  }

  // Detect VK from URL params (works even without bridge)
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const vkUid = urlParams.get('vk_user_id');
    if (vkUid) {
      state.vkUserId = parseInt(vkUid);
      platform.vk = true;
      console.log('VK detected from URL params:', state.vkUserId);
    }
  } catch (e) {}
  if (!state.vkUserId) {
    try {
      const hash = window.location.hash.substring(1);
      if (hash) {
        const hp = new URLSearchParams(hash);
        const vkUid = hp.get('vk_user_id');
        if (vkUid) {
          state.vkUserId = parseInt(vkUid);
          platform.vk = true;
          console.log('VK detected from hash:', state.vkUserId);
        }
      }
    } catch (e) {}
  }
  if (platform.vk) {
    try { applyTheme(); } catch (e) {}
  }

  // ============= DOM =============
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const dom = {
    app: $('#app'),
    main: $('#main'),
    stationsList: $('#stations-list'),
    emptyState: $('#empty-state'),
    resultsTitle: $('#results-title'),
    resultsCount: $('#results-count'),
    citySelector: $('#city-selector'),
    currentCity: $('#current-city'),
    searchInput: $('#search-input'),
    searchClear: $('#search-clear'),
    geoBtn: $('#btn-geo'),
    emergencyBtn: $('#btn-emergency'),
    profileAvatar: $('#profile-avatar'),
    profileBigAvatar: $('#profile-big-avatar'),
    profileName: $('#profile-name'),
    profileId: $('#profile-id'),
    statReports: $('#stat-reports'),
    statSavings: $('#stat-savings'),
    statBadges: $('#stat-badges'),
    badgesGrid: $('#badges-grid'),
    subsList: $('#subs-list'),
    citySearch: $('#city-search'),
    citiesList: $('#cities-list'),
    reportSheet: $('#report-sheet'),
    reportSheetStation: $('#report-sheet-station'),
    reportPrice: $('#report-price'),
    reportQueue: $('#report-queue'),
    reviewSheet: $('#review-sheet'),
    reviewSheetStation: $('#review-sheet-station'),
    reviewComment: $('#review-comment'),
    starsRow: $('#stars-row'),
    ratingHint: $('#rating-hint'),
    toast: $('#toast'),
    loadingOverlay: $('#loading-overlay'),
  };

  // ============= UTILS =============
  function showToast(message, type = '') {
    dom.toast.textContent = message;
    dom.toast.className = `toast ${type}`;
    dom.toast.hidden = false;
    clearTimeout(dom.toast._timer);
    dom.toast._timer = setTimeout(() => { dom.toast.hidden = true; }, 2400);
  }

  function showLoading() { dom.loadingOverlay.hidden = false; }
  function hideLoading() { dom.loadingOverlay.hidden = true; }

  // Inline skeleton (shown in stations list, not full-screen)
  function showSkeletons() {
    dom.stationsList.innerHTML = '';
    for (let i = 0; i < 3; i++) {
      const sk = document.createElement('div');
      sk.className = 'station-card skeleton';
      sk.innerHTML = `
        <div class="skeleton-line w70"></div>
        <div class="skeleton-line w40"></div>
        <div class="skeleton-line w90"></div>
      `;
      dom.stationsList.appendChild(sk);
    }
    dom.emptyState.hidden = true;
  }

  function formatTimeAgo(iso) {
    if (!iso) return '';
    const t = typeof iso === 'string' ? new Date(iso) : iso;
    const diff = Date.now() - t.getTime();
    if (diff < 0) return 'только что';
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'только что';
    if (m < 60) return `${m} мин назад`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} ч назад`;
    const d = Math.floor(h / 24);
    if (d < 30) return `${d} дн назад`;
    return t.toLocaleDateString('ru-RU');
  }

  function getDataAgeDays(iso) {
    if (!iso) return 999;
    const t = typeof iso === 'string' ? new Date(iso) : iso;
    return Math.floor((Date.now() - t.getTime()) / 86400000);
  }

  function dataAgeWarning(ageDays) {
    if (ageDays <= 1) return '';
    if (ageDays <= 3) return `<div style="font-size:11px;color:#fbbf24;margin-top:2px">⚠️ Данные обновлялись ${ageDays} дн назад</div>`;
    if (ageDays <= 7) return `<div style="font-size:11px;color:#f97316;margin-top:2px">⚠️ Данные устарели (${ageDays} дн). Сообщите актуальные цены!</div>`;
    return `<div style="font-size:11px;color:#ef4444;margin-top:2px">🚨 Данные очень старые (${ageDays} дн). Помогите — обновите цены!</div>`;
  }

  function fuelLabel(f) {
    if (f === 'diesel') return 'Дизель';
    if (f === 'lpg') return 'Газ';
    if (f === '92' || f === '95' || f === '98' || f === '100') return `АИ-${f}`;
    return f || '';
  }

  function getTgId() {
    if (tg?.initDataUnsafe?.user?.id) return tg.initDataUnsafe.user.id;
    if (platform.vk && state.vkUserId) return state.vkUserId;
    // Standalone app: app_user_id from native wrapper
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const appId = urlParams.get('app_user_id');
      if (appId && !isNaN(parseInt(appId))) {
        state.appUserId = parseInt(appId);
        state.isStandaloneApp = true;
        return state.appUserId;
      }
    } catch (e) {}
    // Fallback: parse from URL params or hash
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const vkUid = urlParams.get('vk_user_id');
      if (vkUid && !isNaN(parseInt(vkUid))) {
        state.vkUserId = parseInt(vkUid);
        platform.vk = true;
        try { localStorage.setItem('benzin_vk_user_id', vkUid); } catch (e) {}
        return state.vkUserId;
      }
    } catch (e) {}
    try {
      const hash = window.location.hash.substring(1);
      if (hash) {
        const hp = new URLSearchParams(hash);
        const vkUid = hp.get('vk_user_id');
        if (vkUid && !isNaN(parseInt(vkUid))) {
          state.vkUserId = parseInt(vkUid);
          platform.vk = true;
          try { localStorage.setItem('benzin_vk_user_id', vkUid); } catch (e) {}
          return state.vkUserId;
        }
      }
    } catch (e) {}
    // Last fallback: localStorage (saved from previous session)
    try {
      const saved = localStorage.getItem('benzin_vk_user_id');
      if (saved && !isNaN(parseInt(saved))) {
        state.vkUserId = parseInt(saved);
        platform.vk = true;
        return state.vkUserId;
      }
    } catch (e) {}
    return null;
  }

  // ============= HAPTIC =============
  function haptic(style) {
    if (tg?.HapticFeedback) {
      try { tg.HapticFeedback.impactOccurred(style || 'light'); } catch (e) {}
    } else if (platform.vk && window.vkBridge) {
      try { window.vkBridge.send('VKWebAppTapticImpactOccurred', { style: style || 'light' }); } catch (e) {}
    }
  }

  function hapticNotify(type) {
    if (tg?.HapticFeedback) {
      try { tg.HapticFeedback.notificationOccurred(type || 'success'); } catch (e) {}
    } else if (platform.vk && window.vkBridge) {
      try { window.vkBridge.send('VKWebAppTapticNotificationOccurred', { type: type || 'success' }); } catch (e) {}
    }
  }

  // ============= VK BRIDGE HELPERS =============
  function vkSend(method, params = {}) {
    if (!platform.vk || !window.vkBridge) return Promise.resolve(null);
    return window.vkBridge.send(method, params).catch(e => {
      console.warn('VK Bridge', method, 'failed:', e);
      return null;
    });
  }

  function closeApp() {
    if (tg?.close) {
      try { tg.close(); } catch (e) {}
    } else if (platform.vk) {
      vkSend('VKWebAppClose', { status: 'success' });
    }
  }

  function expandApp() {
    if (tg?.expand) {
      try { tg.expand(); } catch (e) {}
    } else if (platform.vk) {
      vkSend('VKWebAppExpand', {});
    }
  }

  function onBackButton(handler) {
    if (tg?.BackButton) {
      tg.BackButton.show();
      tg.BackButton.onClick(handler);
    } else if (platform.vk) {
      // VK doesn't have a built-in back button, but we can listen to history
      // or use a custom button. For now, no-op.
    }
  }

  function offBackButton() {
    if (tg?.BackButton) {
      tg.BackButton.hide();
      tg.BackButton.offClick();
    }
  }

  // ============= ROUTES LIST =============
  let _routesLoaded = false;

  async function loadRoutesList() {
    if (_routesLoaded) return;
    const routesListEl = document.getElementById('routes-list');
    const routesResults = document.getElementById('routes-results');
    if (!routesListEl) return;
    routesListEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary)">Загружаю трассы...</div>';
    try {
      const data = await api('/api/routes');
      const routes = data.routes || [];
      if (!routes.length) {
        routesListEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary)">Трассы не найдены</div>';
        return;
      }
      const federal = routes.filter(r => r.type === 'federal');
      const regional = routes.filter(r => r.type === 'regional');
      const other = routes.filter(r => r.type !== 'federal' && r.type !== 'regional');

      function renderGroup(title, list) {
        if (!list.length) return '';
        return `<div class="routes-group-title">${title}</div>` +
          list.map(r => {
            const km = r.length_km ? `${r.length_km} км` : '';
            const points = [r.start_point, r.end_point].filter(Boolean).join(' → ');
            const meta = [km, points].filter(Boolean).join(' · ');
            return `<div class="route-list-item" data-route-id="${r.id}" data-route-code="${escape(r.code)}">
              <div class="route-list-code">${escape(r.code)}</div>
              <div class="route-list-info">
                <div class="route-list-name">${escape(r.name)}</div>
                ${meta ? `<div class="route-list-meta">${escape(meta)}</div>` : ''}
              </div>
              <div class="route-list-arrow">›</div>
            </div>`;
          }).join('');
      }

      routesListEl.innerHTML =
        renderGroup('Федеральные', federal) +
        renderGroup('Региональные', regional) +
        renderGroup('Другие', other);

      _routesLoaded = true;

      $$('.route-list-item').forEach(el => {
        el.addEventListener('click', () => {
          const code = el.dataset.routeCode || '';
          const input = document.getElementById('routes-input');
          if (input) input.value = code;
          doRouteSearch(code);
        });
      });
    } catch (e) {
      routesListEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary)">Ошибка загрузки</div>';
    }
  }

  async function doRouteSearch(q) {
    q = (q || '').trim();
    if (q.length < 2) {
      showToast('Введи номер или название трассы', 'warning');
      return;
    }
    const routesListEl = document.getElementById('routes-list');
    const routesResults = document.getElementById('routes-results');
    if (!routesResults) return;
    showLoading();
    routesResults.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary)">🔍 Ищу трассу...</div>';
    if (routesListEl) routesListEl.hidden = true;
    try {
      const r = await api(`/api/routes?q=${encodeURIComponent(q)}`);
      const routes = r.routes || [];
      if (!routes.length) {
        routesResults.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary)">Ничего не найдено. Попробуй: М-4, М-7, Р-217, Дон, Кавказ.</div>';
        return;
      }
      const route = routes[0];
      routesResults.innerHTML = `
        <button class="btn btn-secondary" id="routes-back-btn" style="margin-bottom:8px">← Назад к списку</button>
        <div class="route-card">
          <div class="route-card-title">🛣 ${escape(route.code)} — ${escape(route.name)}</div>
          <div class="route-card-meta">📏 ${route.length_km || '?'} км · ${escape(route.start_point || '')} → ${escape(route.end_point || '')}</div>
          ${route.description ? `<div class="route-card-desc">${escape(route.description)}</div>` : ''}
        </div>
        <div id="route-stations-list" style="text-align:center;padding:12px;color:var(--text-secondary)">⛽ Загружаю АЗС...</div>
      `;
      const backBtn = document.getElementById('routes-back-btn');
      if (backBtn) backBtn.addEventListener('click', () => {
        routesResults.innerHTML = '';
        if (routesListEl) routesListEl.hidden = false;
      });
      const rs = await api(`/api/routes/${route.id}/stations?limit=30`);
      const stations = rs.stations || [];
      const listEl = document.getElementById('route-stations-list');
      if (!listEl) return;
      if (!stations.length) {
        listEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary)">АЗС не найдены</div>';
        return;
      }
      listEl.innerHTML = stations.map(s => {
        const hasFuel = s.has_fuel;
        const addr = s.address || s.city || '—';
        const km = s.km_marker ? ` (~${s.km_marker} км)` : '';
        const net = s.operator || s.brand || '';
        const netStr = net ? ` <i style="color:var(--text-secondary)">${escape(net)}</i>` : '';
        const status = hasFuel ? '✅ Есть топливо' : '❓ Нет данных';
        return `<div class="route-station ${hasFuel ? 'has-fuel' : ''}" data-station-id="${s.id}">
          <div class="route-station-name">#${s.id}${netStr} — ${escape(s.name || '')}</div>
          <div class="route-station-addr">📍 ${escape(s.city || '')}, ${escape(addr)}${km}</div>
          <div class="route-station-status">${status}</div>
        </div>`;
      }).join('');
      $$('.route-station').forEach(el => {
        el.addEventListener('click', () => {
          const sid = parseInt(el.dataset.stationId);
          openStationDetail({ id: sid });
        });
      });
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= NAVIGATION =============
  function setTab(tab) {
    state.tab = tab;
    $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    // Hide all tab-contents
    $$('.tab-content').forEach(el => el.hidden = true);
    // Hide all screens
    $$('.screen').forEach(s => s.classList.remove('active'));
    const mainEl = document.getElementById('main');
    if (tab === 'routes') {
      if (mainEl) mainEl.hidden = true;
      const el = document.getElementById('tab-routes');
      if (el) el.hidden = false;
      loadRoutesList();
    } else if (tab === 'route-fuel') {
      if (mainEl) mainEl.hidden = false;
      showScreen('route-fuel');
    } else if (tab === 'anti-traffic') {
      if (mainEl) mainEl.hidden = true;
      const el = document.getElementById('tab-anti-traffic');
      if (el) el.hidden = false;
    } else {
      if (mainEl) mainEl.hidden = false;
      if (tab === 'home') showScreen('home');
      else if (tab === 'map') {
        showScreen('map');
        loadMap();
      }
      else if (tab === 'report') openReportFlow();
      else if (tab === 'profile') {
        showScreen('profile');
        loadProfile();
      }
    }
  }

  function showScreen(name) {
    $$('.screen').forEach(s => s.classList.toggle('active', s.dataset.screen === name));
    state.screen = name;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // ============= CITY =============
  function setCity(city, region) {
    state.city = city;
    state.cityRegion = region || '';
    dom.currentCity.textContent = city;
    try {
      localStorage.setItem('benzin_city', city);
      if (region) localStorage.setItem('benzin_region', region);
    } catch (e) {}
    loadStations();
  }

  async function showCityPicker() {
    showScreen('cities');
    dom.citySearch.value = '';
    await renderCities();
  }

  async function renderCities(query = '') {
    if (state.cities.length === 0) {
      try {
        const data = await api('/api/search?q=');
        state.cities = (data.stations || []).slice(0, 20);
      } catch (e) {}
    }
    // For now show top cities - we don't have a /cities endpoint
    // Will use Moscow, SPb, etc as defaults if no data
    const popular = ['Москва', 'Санкт-Петербург', 'Новосибирск', 'Екатеринбург',
      'Казань', 'Нижний Новгород', 'Челябинск', 'Самара', 'Омск', 'Ростов-на-Дону',
      'Уфа', 'Красноярск', 'Воронеж', 'Пермь', 'Волгоград', 'Краснодар',
      'Саратов', 'Тюмень', 'Тольятти', 'Ижевск', 'Барнаул', 'Иркутск',
      'Ульяновск', 'Хабаровск', 'Владивосток', 'Ярославль', 'Махачкала',
      'Томск', 'Оренбург', 'Кемерово', 'Новокузнецк', 'Рязань', 'Астрахань',
      'Пенза', 'Липецк', 'Тула', 'Киров', 'Чебоксары', 'Калининград',
      'Брянск', 'Курск', 'Иваново', 'Магнитогорск', 'Улан-Удэ', 'Тверь',
      'Ставрополь', 'Белгород', 'Архангельск', 'Владимир', 'Сочи', 'Калуга',
      'Сургут', 'Смоленск', 'Вологда', 'Чита', 'Каменск-Уральский'];
    const q = query.trim().toLowerCase();
    const filtered = q ? popular.filter(c => c.toLowerCase().includes(q)) : popular;

    dom.citiesList.innerHTML = '';
    if (filtered.length === 0) {
      dom.citiesList.innerHTML = '<div class="empty-mini">Ничего не найдено</div>';
      return;
    }
    filtered.forEach(city => {
      const item = document.createElement('div');
      item.className = 'city-item';
      item.innerHTML = `
        <div class="city-item-icon">📍</div>
        <div class="city-item-name">${city}</div>
        <div class="city-item-count">›</div>
      `;
      item.addEventListener('click', () => {
        haptic('light');
        setCity(city);
        showScreen('home');
      });
      dom.citiesList.appendChild(item);
    });
  }

  // ============= STATIONS =============
  async function loadStations() {
    if (!state.city) {
      dom.stationsList.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">📍</div>
          <div class="empty-title">Выбери город</div>
          <div class="empty-subtitle">Нажми на панель города выше</div>
        </div>
      `;
      dom.emptyState.hidden = true;
      dom.resultsCount.textContent = '0';
      return;
    }
    // Show inline skeletons (not full-screen overlay)
    showSkeletons();
    try {
      const params = new URLSearchParams();
      params.set('city', state.city);
      if (state.cityRegion) params.set('region', state.cityRegion);
      if (state.fuel) params.set('fuel', state.fuel);
      if (state.maxPrice > 0) params.set('max_price', state.maxPrice);
      if (state.network) params.set('network', state.network);
      params.set('limit', '50');
      const data = await api('/api/stations/by-city?' + params);
      state.stations = data.stations || [];
      renderStations();
    } catch (e) {
      showToast('Ошибка загрузки: ' + e.message, 'error');
      state.stations = [];
      dom.stationsList.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">⚠️</div>
          <div class="empty-title">Не удалось загрузить</div>
          <div class="empty-subtitle">${escape(e.message)}</div>
        </div>
      `;
      dom.emptyState.hidden = true;
      dom.resultsCount.textContent = '0';
    }
  }

  function renderStations() {
    dom.stationsList.innerHTML = '';
    dom.emptyState.hidden = state.stations.length > 0;

    state.stations.forEach((s, i) => {
      const card = createStationCard(s);
      card.style.animationDelay = `${Math.min(i * 0.03, 0.2)}s`;
      dom.stationsList.appendChild(card);
    });
    dom.resultsCount.textContent = state.stations.length;
  }

  function createStationCard(s) {
    const card = document.createElement('div');
    card.className = 'station-card';

    const operator = s.operator || s.name || 'АЗС';
    const address = s.address || '';
    const city = s.city || '';
    const verified = s.is_verified ? '<span class="station-verified">✓</span>' : '';
    const rating = s.avg_rating || s.rating;

    // Format prices
    const statuses = s.statuses || [];
    const prices = statuses
      .filter(st => st.price != null || st.available !== null)
      .slice(0, 4);
    const pricesHtml = prices.map(st => {
      const has = st.available === true;
      const no = st.available === false;
      const empty = st.available === null;
      const price = st.price != null ? `${st.price.toFixed(2)}₽` : '';
      let cls = 'price-chip';
      if (has && price) cls += ' has';
      else if (no) cls += ' no';
      else cls += ' empty';
      const statusIcon = has ? '✓' : no ? '✗' : '?';
      return `<div class="${cls}">${fuelLabel(st.fuel_type)} ${price} ${statusIcon}</div>`;
    }).join('');

    // Updated
    const lastUpdate = statuses[0]?.created_at;
    const updated = lastUpdate ? formatTimeAgo(lastUpdate) : '';
    const ageDays = getDataAgeDays(lastUpdate);
    const ageWarning = dataAgeWarning(ageDays);

    card.innerHTML = `
      <div class="station-card-row">
        <div class="station-name">${escape(operator)} ${verified}</div>
        ${rating ? `<div class="station-rating">★ ${rating.toFixed(1)}</div>` : ''}
      </div>
      ${address || city ? `
        <div class="station-address">
          <span>${escape(address || city)}</span>
        </div>
      ` : ''}
      ${prices.length > 0 ? `<div class="station-prices">${pricesHtml}</div>` : ''}
      ${ageWarning}
      <div class="station-footer">
        <span class="station-updated">${updated ? '🕐 ' + updated : 'Нет данных'}</span>
        <div class="station-actions-mini">
          <button data-action="report" title="Сообщить">📝</button>
        </div>
      </div>
    `;

    card.addEventListener('click', (e) => {
      if (e.target.closest('[data-action="report"]')) {
        e.stopPropagation();
        openReportSheet(s.id, operator);
        return;
      }
      haptic('light');
      openStationDetail(s);
    });

    return card;
  }

  function escape(s) {
    if (!s) return '';
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  // ============= STATION DETAIL =============
  async function openStationDetail(s) {
    if (!s || !s.id) {
      showToast('Ошибка: нет данных об АЗС', 'error');
      return;
    }
    state.selectedStation = s;
    showScreen('station');
    // Render skeleton immediately
    const detailEl = $('#station-detail');
    if (detailEl) {
      detailEl.innerHTML = '<div class="map-empty">⏳ Загрузка...</div>';
    }
    try {
      // Load full station data
      const detail = await api(`/api/stations/${s.id}`).catch(e => {
        console.error('station detail failed:', e);
        return { station: s, statuses: s.statuses || [] };
      });
      // Prices is optional, don't fail if it errors
      const pricesData = await api(`/api/stations/${s.id}/prices`).catch(() => null);
      renderStationDetail(detail, pricesData);

      // Загружаем price history (Premium)
      await loadStationPriceHistory(s.id);
      // Загружаем forecast (Premium Стандарт)
      await loadStationForecast(s.id);
    } catch (e) {
      console.error('openStationDetail error:', e);
      showToast('Не удалось загрузить: ' + e.message, 'error');
      // Still try to render with what we have
      renderStationDetail({ station: s, statuses: s.statuses || [] }, null);
    }
  }

  async function loadStationPriceHistory(stationId) {
    // Загружает историю цен и рисует мини-график (Premium).
    const tgId = getTgId();
    const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
    const url = `/api/stations/${stationId}/price-history?days=30&fuel=95` + (tgId ? `&${idParam}=${tgId}` : '');
    const data = await api(url).catch(() => null);
    if (!data || !data.history) return;

    const container = document.getElementById('station-premium-features');
    if (!container) return;

    // Строим простой SVG график
    const history = data.history.filter(h => h.price != null);
    if (history.length === 0) {
      // Нет данных
      if (data.is_premium) {
        container.insertAdjacentHTML('afterbegin',
          '<div class="feature-card"><div class="feature-card-header">' +
          '<div class="feature-card-icon">📈</div>' +
          '<div class="feature-card-title">История цен</div>' +
          '<div class="feature-card-save">Premium</div></div>' +
          '<div class="feature-card-tagline">Нет данных за последние 30 дней</div></div>'
        );
      }
      return;
    }

    // Рисуем мини-график
    const w = 320, h = 80, pad = 6;
    const prices = history.map(h => h.price);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = max - min || 1;
    const stepX = (w - pad * 2) / Math.max(history.length - 1, 1);
    const points = history.map((entry, i) => {
      const x = pad + i * stepX;
      const y = pad + (entry.price - min) / range * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');

    const lastPrice = history[0].price.toFixed(2);
    const firstPrice = history[history.length - 1].price.toFixed(2);
    const diff = (history[0].price - history[history.length - 1].price).toFixed(2);
    const trend = history[0].price > history[history.length - 1].price ? '📉 дешевеет' :
                  history[0].price < history[history.length - 1].price ? '📈 дорожает' : '➡️ стабильно';

    const isPremium = data.is_premium;
    const period = isPremium ? '30 дней' : '3 дня';

    const historyHtml = `
      <div class="feature-card" ${!isPremium ? 'onclick="showUpsell({feature:\'price_history\'})"' : ''}>
        <div class="feature-card-header">
          <div class="feature-card-icon">📈</div>
          <div class="feature-card-title">История цен ${period}</div>
          <div class="feature-card-save" style="${isPremium ? 'color:#34d399;background:rgba(52,211,153,0.15);' : 'color:#fbbf24;background:rgba(251,191,36,0.15);'}">
            ${isPremium ? '✅ Активно' : '💎 Premium'}
          </div>
        </div>
        <svg viewBox="0 0 ${w} ${h}" style="width:100%;height:80px;background:rgba(255,255,255,0.02);border-radius:8px;margin-top:8px;">
          <polyline points="${points}" fill="none" stroke="#fbbf24" stroke-width="2"/>
        </svg>
        <div class="feature-card-tagline" style="margin-top:6px;">
          ${lastPrice}₽ ${trend} (было ${firstPrice}₽, Δ${diff}₽)
        </div>
        ${data.forecast ? `
          <div class="feature-card-urgency" style="color:#34d399;">
            🔮 Прогноз: ${data.forecast.low}₽ — ${data.forecast.high}₽ (средн. ${data.forecast.avg}₽)
          </div>
        ` : ''}
      </div>
    `;

    container.insertAdjacentHTML('afterbegin', historyHtml);
  }

  async function loadStationForecast(stationId) {
    // Загружает прогноз цен на 7 дней (Premium Стандарт).
    const tgId = getTgId();
    if (!tgId) return;
    const forecastIdParam = platform.vk ? 'vk_user_id' : 'telegram_id';
    const url = `/api/stations/${stationId}/forecast?days=7&fuel=95&${forecastIdParam}=${tgId}`;
    const data = await api(url).catch(() => null);
    if (!data || !data.ok) return;

    const container = document.getElementById('station-premium-features');
    if (!container) return;

    const forecast = data.forecast || [];
    if (forecast.length === 0) return;

    // Рисуем SVG-график прогноза
    const w = 320, h = 80, pad = 6;
    const prices = forecast.map(f => f.price);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = max - min || 1;
    const stepX = (w - pad * 2) / Math.max(forecast.length - 1, 1);
    const points = forecast.map((f, i) => {
      const x = pad + i * stepX;
      const y = pad + (f.price - min) / range * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');

    // Найдём сегодняшнюю точку
    const todayIdx = 0;

    const trend = data.trend || {};
    const best = data.best_day || {};
    const worst = data.worst_day || {};

    const forecastHtml = `
      <div class="feature-card" onclick="showUpsell({feature:'forecast_7d'})" style="cursor:default;">
        <div class="feature-card-header">
          <div class="feature-card-icon">🔮</div>
          <div class="feature-card-title">Прогноз цен на 7 дней</div>
          <div class="feature-card-save" style="color:#34d399;background:rgba(52,211,153,0.15);">✅ Активно</div>
        </div>
        <svg viewBox="0 0 ${w} ${h}" style="width:100%;height:80px;background:rgba(255,255,255,0.02);border-radius:8px;margin-top:8px;">
          <line x1="${pad + todayIdx * stepX}" y1="${pad}" x2="${pad + todayIdx * stepX}" y2="${h - pad}" stroke="#fbbf24" stroke-width="1" stroke-dasharray="3,2" opacity="0.5"/>
          <polyline points="${points}" fill="none" stroke="#34d399" stroke-width="2" stroke-dasharray="4,3"/>
          <circle cx="${pad + todayIdx * stepX}" cy="${pad + (forecast[0].price - min) / range * (h - pad * 2)}" r="4" fill="#fbbf24"/>
        </svg>
        <div class="feature-card-tagline" style="margin-top:6px;">
          ${trend.label} <span style="color:${trend.delta > 0 ? '#f87171' : trend.delta < 0 ? '#34d399' : 'var(--text-secondary)'};">(${trend.delta > 0 ? '+' : ''}${trend.delta}₽, ${trend.delta_pct > 0 ? '+' : ''}${trend.delta_pct}%)</span>
        </div>
        <div class="feature-card-urgency" style="color:#34d399;">
          💡 ${trend.advice}
        </div>
        <div style="display:flex;gap:6px;margin-top:8px;font-size:11px;">
          <div style="flex:1;background:rgba(52,211,153,0.1);padding:6px;border-radius:6px;text-align:center;">
            <div style="color:#34d399;font-weight:700;">📉 ${best.label || best.date}</div>
            <div>${best.price}₽</div>
            <div style="opacity:0.7;">экономия ${best.savings}₽</div>
          </div>
          <div style="flex:1;background:rgba(248,113,113,0.1);padding:6px;border-radius:6px;text-align:center;">
            <div style="color:#f87171;font-weight:700;">📈 ${worst.label || worst.date}</div>
            <div>${worst.price}₽</div>
            <div style="opacity:0.7;">переплата ${worst.loss}₽</div>
          </div>
        </div>
        <div style="font-size:10px;color:var(--text-secondary);margin-top:6px;text-align:center;">
          ${data.accuracy_note || ''}
        </div>
      </div>
    `;

    container.insertAdjacentHTML('beforeend', forecastHtml);
  }

  function renderStationDetail(detail, pricesData) {
    const s = detail.station || state.selectedStation;
    if (!s) return;
    const statuses = detail.statuses || [];
    const operator = s.operator || s.name || 'АЗС';
    const verified = s.is_verified ? ' ✓' : '';
    const premiumVerified = detail.premium_verified ? '<span class="premium-verified-badge">💎 Premium</span>' : '';
    const lat = s.lat;
    const lon = s.lon;

    // Fuel rows
    const fuelRows = statuses.length > 0 ? statuses.map(st => {
      const has = st.available === true;
      const no = st.available === false;
      const empty = st.available === null;
      const price = st.price != null ? `${st.price.toFixed(2)} ₽` : '—';
      let rowCls = 'fuel-row';
      if (has) rowCls += ' has-fuel';
      else if (no) rowCls += ' no-fuel';
      else rowCls += ' empty-fuel';
      const statusText = has ? 'В наличии' : no ? 'Нет в наличии' : 'Уточняйте';
      let limitHtml = '';
      if (st.has_limit && st.limit_liters) {
        limitHtml = `<span class="fuel-limit">🚫 лимит ${st.limit_liters}л</span>`;
      }
      if (st.canister_ban) {
        limitHtml += `<span class="fuel-canister-ban">❌ канистры запрещены</span>`;
      }
      // Детальные лимиты per fuel
      const detailParts = [];
      if (st.limit_per_visit) detailParts.push(`за раз: ${st.limit_per_visit}л`);
      if (st.limit_daily) detailParts.push(`в день: ${st.limit_daily}л`);
      if (st.limit_weekly) detailParts.push(`в неделю: ${st.limit_weekly}л`);
      if (detailParts.length > 0) {
        limitHtml += `<span class="fuel-limit-detail">📏 ${detailParts.join(' · ')}</span>`;
      }
      return `
        <div class="${rowCls}">
          <div class="fuel-name">${fuelLabel(st.fuel_type)}</div>
          <div class="fuel-status">
            <span>${statusText}</span>
            <span class="fuel-price">${price}</span>
          </div>
          ${limitHtml ? `<div class="fuel-limits">${limitHtml}</div>` : ''}
        </div>
      `;
    }).join('') : '<div class="empty-mini">Нет данных о ценах</div>';

    // Глобальные лимиты и запреты на канистры (fuel_type=all)
    const globalLimits = statuses.filter(st => st.fuel_type === 'all');
    let globalLimitsHtml = '';
    if (globalLimits.length > 0) {
      const gl = globalLimits[globalLimits.length - 1];
      const comment = (gl.comment || '').toUpperCase();
      const hasLimit = gl.has_limit;
      const limitLiters = gl.limit_liters;
      const limitPerVisit = gl.limit_per_visit;
      const limitDaily = gl.limit_daily;
      const limitWeekly = gl.limit_weekly;
      const canisterBan = gl.canister_ban || comment.includes('ЗАПРЕТ') || comment.includes('КАНИСТР');
      if (hasLimit || canisterBan || limitPerVisit || limitDaily || limitWeekly) {
        let limitText = '';
        if (hasLimit && limitLiters) {
          limitText = `🚫 <b>Лимит заправки:</b> до ${limitLiters}л`;
          if (canisterBan) limitText += ' · ❌ заправка в канистры запрещена';
        } else if (hasLimit) {
          limitText = '🚫 <b>Ограничения на заправку</b>';
          if (canisterBan) limitText += ' · ❌ заправка в канистры запрещена';
        } else if (canisterBan) {
          limitText = '🚫 <b>Запрет заправки в канистры</b>';
        }
        const detailParts = [];
        if (limitPerVisit) detailParts.push(`за раз: ${limitPerVisit}л`);
        if (limitDaily) detailParts.push(`в день: ${limitDaily}л`);
        if (limitWeekly) detailParts.push(`в неделю: ${limitWeekly}л`);
        if (detailParts.length > 0) {
          limitText += `<br><span style="font-size:0.85em;opacity:0.8">📏 ${detailParts.join(' · ')}</span>`;
        }
        globalLimitsHtml = `<div class="global-limits">${limitText}</div>`;
      }
    }

    // Last update
    const lastUpdate = statuses[0]?.created_at;
    const updated = lastUpdate ? formatTimeAgo(lastUpdate) : '—';
    const ageDays = getDataAgeDays(lastUpdate);
    const ageWarning = dataAgeWarning(ageDays);

    // Sources summary from prices API
    let sourcesHtml = '';
    if (pricesData && pricesData.total_sources) {
      const srcs = Object.entries(pricesData.sources_summary || {})
        .map(([src, count]) => `<span class="price-chip">${src}: ${count}</span>`)
        .join('');
      if (srcs) sourcesHtml = `<div class="station-prices">${srcs}</div>`;
    }

    $('#station-detail').innerHTML = `
      <div class="detail-back" data-action="back">‹ Назад</div>

      <div class="detail-card">
        <div class="detail-name">${escape(operator)}${verified} ${premiumVerified}</div>
        ${s.operator && s.name && s.operator !== s.name ?
          `<div class="detail-operator">${escape(s.name)}</div>` : ''}
        ${s.address ? `
          <div class="detail-address">
            <span>📍</span>
            <span>${escape(s.address)}</span>
          </div>
        ` : ''}
        <div class="detail-meta">
          <div class="meta-item">
            <div class="meta-label">Город</div>
            <div class="meta-value">${escape(s.city || '—')}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Обновлено</div>
            <div class="meta-value">${updated}</div>
          </div>
        </div>
        ${ageWarning ? `<div style="padding:8px 12px;background:rgba(239,68,68,0.08);border-radius:8px;margin-top:10px;">${ageWarning}</div>` : ''}
      </div>

      <div class="section-header">
        <h2 class="section-title">Цены и наличие</h2>
      </div>
      <div class="fuel-prices-list">${fuelRows}</div>
      ${globalLimitsHtml}

      ${sourcesHtml ? `
        <div class="section-header" style="margin-top:16px;">
          <h2 class="section-title">Источники</h2>
        </div>
        ${sourcesHtml}
      ` : ''}

      <div class="detail-actions">
        <button class="btn btn-primary" data-action="report">📝 Сообщить</button>
        <button class="btn btn-secondary" data-action="review">⭐ Оценить</button>
      </div>

      <!-- Premium features (price history, forecast, alarm) -->
      <div class="section-header" style="margin-top: 16px;">
        <h2 class="section-title">💎 Premium-фичи для этой АЗС</h2>
      </div>
      <div id="station-premium-features">
        ${window.PremiumUI && window.PremiumUI.getStatus().active ?
          window.PremiumUI.renderUnlockedCard('price_history') +
          window.PremiumUI.renderUnlockedCard('fuel_alarm') :
          window.PremiumUI.renderLockedCard('price_history') +
          window.PremiumUI.renderLockedCard('fuel_alarm')
        }
      </div>

      <div class="detail-actions">
        <button class="btn btn-secondary" data-action="route">🗺️ Маршрут</button>
        <button class="btn btn-secondary" data-action="subscribe">🔔 Подписаться</button>
      </div>

      <!-- Fuel Alarm section -->
      <div class="section-header" style="margin-top:16px;">
        <h2 class="section-title">⛽ Топливный будильник</h2>
      </div>
      <div id="fuel-alarm-section">
        <div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">
          Уведомим когда нужное топливо появится на АЗС
        </div>
        <div class="fuel-alarm-types" id="fuel-alarm-types" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
          <button class="btn btn-sm btn-secondary fuel-alarm-type" data-fuel="92" style="border-radius:20px;padding:6px 14px;font-size:13px;">АИ-92</button>
          <button class="btn btn-sm btn-secondary fuel-alarm-type" data-fuel="95" style="border-radius:20px;padding:6px 14px;font-size:13px;border:2px solid var(--accent);">АИ-95</button>
          <button class="btn btn-sm btn-secondary fuel-alarm-type" data-fuel="98" style="border-radius:20px;padding:6px 14px;font-size:13px;">АИ-98</button>
          <button class="btn btn-sm btn-secondary fuel-alarm-type" data-fuel="diesel" style="border-radius:20px;padding:6px 14px;font-size:13px;">ДТ</button>
        </div>
        <button class="btn btn-primary" id="fuel-alarm-btn" data-action="fuel-alarm-toggle" style="width:100%;border-radius:12px;padding:12px;font-size:15px;">
          🔔 Уведомить о появлении
        </button>
        <div id="fuel-alarm-status" style="margin-top:8px;font-size:12px;color:var(--text-secondary);display:none;"></div>
      </div>

      <div class="section-header" style="margin-top:20px;">
        <h2 class="section-title">Отзывы</h2>
        <span class="section-count" id="reviews-count">0</span>
      </div>
      <div class="reviews-list" id="reviews-list">
        <div class="empty-mini">Пока нет отзывов — будь первым!</div>
      </div>
    `;

    // Bind back button (scoped to station-detail)
    const detailEl2 = $('#station-detail');
    const backBtn = detailEl2.querySelector('[data-action="back"]');
    if (backBtn) backBtn.addEventListener('click', () => showScreen('home'));
    const reportBtn = detailEl2.querySelector('[data-action="report"]');
    if (reportBtn) reportBtn.addEventListener('click', () => openReportSheet(s.id, operator));
    const reviewBtn = detailEl2.querySelector('[data-action="review"]');
    if (reviewBtn) reviewBtn.addEventListener('click', () => openReviewSheet(s.id, operator));
    const routeBtn = detailEl2.querySelector('[data-action="route"]');
    if (routeBtn) routeBtn.addEventListener('click', () => openMap(lat, lon, operator));
    const subBtn = detailEl2.querySelector('[data-action="subscribe"]');
    if (subBtn) subBtn.addEventListener('click', () => subscribeStation(s.id));

    // Fuel alarm logic
    let selectedFuelType = '95';
    const fuelTypeBtns = detailEl2.querySelectorAll('.fuel-alarm-type');
    fuelTypeBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        fuelTypeBtns.forEach(b => b.style.borderColor = '');
        btn.style.borderColor = 'var(--accent)';
        selectedFuelType = btn.dataset.fuel;
        updateFuelAlarmBtn();
      });
    });

    const alarmBtn = detailEl2.querySelector('#fuel-alarm-btn');
    const alarmStatus = detailEl2.querySelector('#fuel-alarm-status');
    let activeAlarmId = null;

    async function updateFuelAlarmBtn() {
      try {
        const _uid = getTgId();
        const alarmListParam = platform.vk ? `vk_user_id=${_uid || ''}` : `telegram_id=${_uid || ''}`;
        const data = await api(`/api/fuel-alarm/list?${alarmListParam}`);
        const alarms = data.alarms || [];
        const match = alarms.find(a => a.station_id == s.id && a.fuel_type === selectedFuelType);
        if (match) {
          activeAlarmId = match.id;
          alarmBtn.textContent = '🔕 Отменить уведомление';
          alarmBtn.className = 'btn btn-outline';
          alarmBtn.style.cssText = 'width:100%;border-radius:12px;padding:12px;font-size:15px;border:2px solid #ef4444;color:#ef4444;background:transparent;';
          alarmStatus.style.display = 'block';
          alarmStatus.textContent = 'Будильник активен — уведомим когда появится';
        } else {
          activeAlarmId = null;
          alarmBtn.textContent = '🔔 Уведомить о появлении';
          alarmBtn.className = 'btn btn-primary';
          alarmBtn.style.cssText = 'width:100%;border-radius:12px;padding:12px;font-size:15px;';
          alarmStatus.style.display = 'none';
        }
      } catch (e) {
        // Not logged in or no premium
        alarmBtn.textContent = '🔔 Уведомить о появлении';
        alarmBtn.className = 'btn btn-primary';
        alarmBtn.style.cssText = 'width:100%;border-radius:12px;padding:12px;font-size:15px;';
        alarmStatus.style.display = 'none';
      }
    }

    alarmBtn.addEventListener('click', async () => {
      const _uid = getTgId();
      if (!_uid) {
        showToast(platform.vk ? 'Определение пользователя...' : 'Войдите через Telegram чтобы использовать будильник', 'warning');
        return;
      }
      if (activeAlarmId) {
        // Delete alarm
        try {
          const alarmIdParam = platform.vk ? 'vk_user_id' : 'telegram_id';
          await api('/api/fuel-alarm/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({[alarmIdParam]: _uid, station_id: s.id, fuel_type: selectedFuelType}),
          });
          activeAlarmId = null;
          updateFuelAlarmBtn();
          showToast('Будильник отменён', 'info');
        } catch (e) {
          showToast('Ошибка: ' + e.message, 'error');
        }
      } else {
        // Create alarm
        try {
          const alarmIdParam = platform.vk ? 'vk_user_id' : 'telegram_id';
          const resp = await api('/api/fuel-alarm/create', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({[alarmIdParam]: _uid, station_id: s.id, fuel_type: selectedFuelType}),
          });
          if (resp.error === 'premium_required') {
            window.PremiumUI.showUpsell({ feature: 'fuel_alarm' });
            return;
          }
          activeAlarmId = resp.alarm_id;
          updateFuelAlarmBtn();
          showToast('Будильник установлен! Уведомим когда появится ⛽', 'success');
          hapticNotify('success');
        } catch (e) {
          showToast('Ошибка: ' + e.message, 'error');
        }
      }
    });

    updateFuelAlarmBtn();

    // Load reviews
    loadReviews(s.id);
  }

  async function loadReviews(stationId) {
    // For now, we don't have a public /api/reviews endpoint
    // Reviews are loaded via TG bot. Show placeholder.
    try {
      // Future: GET /api/stations/{id}/reviews
    } catch (e) {}
  }

  // ============= EMERGENCY =============
  async function doEmergencySearch() {
    if (!state.city) {
      showToast('Сначала выбери город', 'warning');
      return;
    }
    showLoading();
    try {
      const data = await api(`/api/stations/emergency?city=${encodeURIComponent(state.city)}&fuel=${state.fuel || '92'}`);
      if (!data.stations || data.stations.length === 0) {
        showToast('К сожалению, в этом городе нет АЗС с подтверждённым наличием', 'warning');
        return;
      }
      state.stations = data.stations;
      dom.resultsTitle.textContent = '🚨 Экстренный поиск';
      renderStations();
      hapticNotify('success');
      showToast(`Найдено ${data.stations.length} АЗС с топливом`, 'success');
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= SOS (Elite) =============
  async function sendSOS() {
    const uid = getTgId();
    if (!uid) {
      showToast('Не удалось определить пользователя', 'error');
      return;
    }
    // Проверяем Premium Elite
    try {
      const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
      const premRes = await api(`/api/premium/status?${idParam}=${uid}`);
      if (!premRes || !premRes.active || premRes.tier !== 'elite') {
        window.PremiumUI.showUpsell({ feature: 'sos_elite' });
        return;
      }
    } catch (e) {
      window.PremiumUI.showUpsell({ feature: 'sos_elite' });
      return;
    }

    // Получаем геолокацию
    const pos = await getUserLocation();
    if (!pos) {
      showToast('Не удалось определить местоположение', 'error');
      return;
    }

    // Подтверждение
    const confirmed = confirm('🚨 Отправить SOS-сигнал?\n\nВодителям в радиусе 50 км придёт уведомление с твоими координатами.');
    if (!confirmed) return;

    showLoading();
    try {
      const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
      const resp = await api('/api/sos/broadcast', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          [idParam]: uid,
          lat: pos.lat,
          lon: pos.lon,
          message: 'Помогите! Нужна помощь на дороге!',
        }),
      });
      if (resp.error === 'elite_required') {
        showUpsell({feature:'sos_elite'});
        return;
      }
      if (resp.ok) {
        hapticNotify('success');
        showToast(`🚨 SOS отправлен! ${resp.broadcasted} пользователей уведомлены`, 'success');
      } else {
        showToast('Ошибка: ' + (resp.error || 'unknown'), 'error');
      }
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= GEO =============
  async function getUserLocation() {
    return new Promise((resolve) => {
      if (!navigator.geolocation) { resolve(null); return; }
      navigator.geolocation.getCurrentPosition(
        pos => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
        err => {
          showToast('Не удалось определить местоположение', 'warning');
          resolve(null);
        },
        { timeout: 10000, maximumAge: 60000 }
      );
    });
  }

  async function useGeo() {
    haptic('light');
    const loc = await getUserLocation();
    if (!loc) return;
    state.userLocation = loc;
    // Reverse geocode to get city
    showLoading();
    try {
      const data = await api(`/api/reverse-geocode?lat=${loc.lat}&lon=${loc.lon}`);
      if (data.city) {
        setCity(data.city, data.region);
        showToast(`📍 ${data.city}`, 'success');
      } else {
        showToast('Не удалось определить город', 'warning');
      }
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= MAP =============
  function openMap(lat, lon, name) {
    if (!lat || !lon) {
      showToast('Координаты не указаны', 'warning');
      return;
    }
    // Show route choice sheet
    const existing = document.getElementById('route-sheet');
    if (existing) existing.remove();

    // Route URLs — строят маршрут от текущего местоположения
    const yandexRoute = `https://yandex.ru/maps/?rtext=${lat},${lon}&rtt=auto`;
    const gmapsRoute = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&travelmode=driving`;
    const gis2Route = `https://2gis.ru/geo/${lon}/${lat}`;
    const appleRoute = `https://maps.apple.com/?daddr=${lat},${lon}&dirflg=d`;

    const sheet = document.createElement('div');
    sheet.id = 'route-sheet';
    sheet.className = 'route-sheet-overlay';
    sheet.innerHTML = `
      <div class="route-sheet-backdrop"></div>
      <div class="route-sheet-content">
        <div class="route-sheet-handle"></div>
        <div class="route-sheet-title">Построить маршрут</div>
        <div class="route-sheet-subtitle">Маршрут от тебя до ${escape(name || 'АЗС')}</div>

        <button class="route-nav-btn" data-url="${yandexRoute}">
          <div class="route-nav-icon" style="background:rgba(255,204,0,0.15);color:#ffcc00;">🗺</div>
          <div class="route-nav-info">
            <div class="route-nav-name" style="color:#ffcc00;">Яндекс Карты</div>
            <div class="route-nav-desc">Навигатор</div>
          </div>
          <div class="route-nav-arrow">›</div>
        </button>

        <button class="route-nav-btn" data-url="${gmapsRoute}">
          <div class="route-nav-icon" style="background:rgba(66,133,244,0.15);color:#4285f4;">🌍</div>
          <div class="route-nav-info">
            <div class="route-nav-name" style="color:#4285f4;">Google Maps</div>
            <div class="route-nav-desc">Навигатор</div>
          </div>
          <div class="route-nav-arrow">›</div>
        </button>

        <button class="route-nav-btn" data-url="${gis2Route}">
          <div class="route-nav-icon" style="background:rgba(244,67,54,0.15);color:#f44336;">📍</div>
          <div class="route-nav-info">
            <div class="route-nav-name" style="color:#f44336;">2ГИС</div>
            <div class="route-nav-desc">Карты и навигатор</div>
          </div>
          <div class="route-nav-arrow">›</div>
        </button>

        <button class="route-nav-btn" data-url="${appleRoute}">
          <div class="route-nav-icon" style="background:rgba(52,199,89,0.15);color:#34c759;">🍎</div>
          <div class="route-nav-info">
            <div class="route-nav-name" style="color:#34c759;">Apple Maps</div>
            <div class="route-nav-desc">Карты iPhone</div>
          </div>
          <div class="route-nav-arrow">›</div>
        </button>

        <button class="route-sheet-cancel" id="route-close">Отмена</button>
      </div>
    `;
    document.querySelector('.screen-station').appendChild(sheet);

    sheet.querySelector('#route-close').addEventListener('click', () => sheet.remove());
    sheet.querySelector('.route-sheet-backdrop').addEventListener('click', () => sheet.remove());
    sheet.querySelectorAll('.route-nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const url = btn.dataset.url;
        if (tg?.openLink) {
          tg.openLink(url);
        } else {
          window.open(url, '_blank');
        }
        sheet.remove();
      });
    });
  }

  // ============= REPORT FLOW =============
  function openReportFlow() {
    // If no city selected, ask to select first
    if (!state.city) {
      showToast('Сначала выбери город', 'warning');
      showCityPicker();
      return;
    }
    // If we already have stations loaded, show picker
    showStationPicker();
  }

  function showStationPicker() {
    showScreen('pick-station');
    renderStationPicker();
    // Focus search
    setTimeout(() => {
      const inp = document.getElementById('station-picker-search');
      if (inp) {
        inp.value = '';
        inp.addEventListener('input', onStationPickerSearch, { once: false });
      }
    }, 100);
  }

  function renderStationPicker(query = '') {
    const list = document.getElementById('station-picker-list');
    if (!list) return;

    const ql = query.trim();

    // If query is empty — show local stations from current city
    if (!ql) {
      let stations = state.stations || [];
      if (stations.length === 0) {
        // Load stations first
        showLoading();
        const params = new URLSearchParams();
        params.set('city', state.city);
        if (state.fuel) params.set('fuel', state.fuel);
        params.set('limit', '100');
        api('/api/stations/by-city?' + params).then(data => {
          state.stations = data.stations || [];
          renderStationPicker('');
          hideLoading();
        }).catch(e => {
          hideLoading();
          showToast('Ошибка: ' + e.message, 'error');
          list.innerHTML = '<div class="empty-mini">Не удалось загрузить АЗС</div>';
        });
        return;
      }
      renderStationList(stations);
      return;
    }

    // If query has 2+ chars — search entire DB via API
    if (ql.length >= 2) {
      showLoading();
      // Debounce not needed here (handler called only on input)
      const tgId = getTgId();
      let url = '/api/search?q=' + encodeURIComponent(ql);
      if (tgId) url += '&telegram_id=' + tgId;
      api(url).then(data => {
        hideLoading();
        const stations = data.stations || [];
        if (stations.length === 0) {
          list.innerHTML = `<div class="empty-mini">По запросу «${escape(ql)}» ничего не найдено.<br>Попробуйте изменить запрос.</div>`;
          return;
        }
        list.innerHTML = '';
        renderStationListInto(stations, list);
      }).catch(e => {
        hideLoading();
        showToast('Ошибка поиска: ' + e.message, 'error');
      });
      return;
    }
  }

  function renderStationList(stations) {
    renderStationListInto(stations, document.getElementById('station-picker-list'));
  }

  function renderStationListInto(stations, list) {
    if (!list) return;
    list.innerHTML = '';
    if (stations.length === 0) {
      list.innerHTML = '<div class="empty-mini">Нет АЗС</div>';
      return;
    }
    stations.forEach(s => {
      const op = s.operator || s.name || 'АЗС';
      const addr = s.address || s.city || '';
      const item = document.createElement('div');
      item.className = 'map-station-item';
      item.innerHTML = `
        <div class="map-station-icon">⛽</div>
        <div class="map-station-info">
          <div class="map-station-name">${escape(op)}</div>
          <div class="map-station-addr">${escape(addr)}</div>
        </div>
        <div class="map-station-arrow">›</div>
      `;
      item.addEventListener('click', () => {
        haptic('light');
        openReportSheet(s.id, op);
      });
      list.appendChild(item);
    });
  }

  function onStationPickerSearch(e) {
    clearTimeout(_stationPickerSearchTimer);
    const q = e.target.value;
    _stationPickerSearchTimer = setTimeout(() => {
      renderStationPicker(q);
    }, 300);
  }
  let _stationPickerSearchTimer = null;

  // ============= MAP =============
  let _leafletMap = null;
  let _leafletLayer = null;
  let _userMarker = null;
  let _userCircle = null;
  let _mapStations = [];
  let _mapLoaded = false;

  function _getMapAvailability(s, fuel) {
    // Возвращает: 'available' | 'partial' | 'unavailable' | 'unknown'
    const statuses = s.statuses || [];
    if (!statuses || statuses.length === 0) return 'unknown';
    // Если указан тип топлива — фильтруем
    const filtered = fuel ? statuses.filter(st => st.fuel_type === fuel) : statuses;
    const active = filtered.length > 0 ? filtered : statuses;
    if (active.length === 0) return 'unknown';
    const has = active.filter(st => st.available === true);
    const no = active.filter(st => st.available === false);
    if (has.length === active.length) return 'available';
    if (no.length === active.length) return 'unavailable';
    if (has.length > 0) return 'partial';
    return 'unknown';
  }

  function _makeMarkerIcon(status) {
    const colors = {
      available: '#22c55e',
      partial: '#eab308',
      unavailable: '#ef4444',
      unknown: '#6b7280',
    };
    const color = colors[status] || colors.unknown;
    return L.divIcon({
      className: 'custom-marker',
      html: `<div class="marker-pin" style="background:${color}"><span>⛽</span></div>`,
      iconSize: [32, 42],
      iconAnchor: [16, 42],
      popupAnchor: [0, -38],
    });
  }

  function _userLocationIcon() {
    return L.divIcon({
      className: 'user-marker',
      html: '<div class="user-pin"><div class="user-pulse"></div><div class="user-dot"></div></div>',
      iconSize: [20, 20],
      iconAnchor: [10, 10],
    });
  }

  function _popupHtml(s) {
    const op = escape(s.operator || s.name || 'АЗС');
    const addr = escape(s.address || '');
    const avail = _getMapAvailability(s, state.fuel);
    const labels = { available: 'Есть топливо', partial: 'Частично', unavailable: 'Нет топлива', unknown: 'Нет данных' };
    return `
      <div class="map-popup">
        <div class="map-popup-name">${op}</div>
        ${addr ? `<div class="map-popup-addr">${addr}</div>` : ''}
        <div class="map-popup-status status-${avail}">${labels[avail]}</div>
        <button class="map-popup-btn" data-station-id="${s.id}">Открыть ›</button>
      </div>
    `;
  }

  function loadMap() {
    const container = document.getElementById('map-container');
    const list = document.getElementById('map-stations-list');
    const locateBtn = document.getElementById('map-locate-btn');
    if (!container || !list) return;

    if (!state.city) {
      container.innerHTML = '<div class="map-empty">📍 Выбери город на главной</div>';
      list.innerHTML = '';
      if (locateBtn) locateBtn.style.display = 'none';
      return;
    }
    if (locateBtn) locateBtn.style.display = 'flex';

    // Init Leaflet map (once)
    if (!_leafletMap) {
      _leafletMap = L.map(container, {
        zoomControl: true,
        attributionControl: true,
      }).setView([55.7558, 37.6173], 11);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap',
      }).addTo(_leafletMap);
      _leafletLayer = L.layerGroup().addTo(_leafletMap);
      _leafletMap.on('popupopen', (e) => {
        const btn = e.popup.getElement()?.querySelector('[data-station-id]');
        if (btn) {
          btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.stationId, 10);
            const s = _mapStations.find(x => x.id === id);
            if (s) openStationDetail(s);
          });
        }
      });
      // Locate button
      if (locateBtn) {
        locateBtn.addEventListener('click', () => centerOnUser());
      }
    }

    // Invalidate size in case container was hidden
    setTimeout(() => _leafletMap && _leafletMap.invalidateSize(), 50);

    // Load stations
    const params = new URLSearchParams();
    params.set('city', state.city);
    params.set('with_coords', '1');
    if (state.fuel) params.set('fuel', state.fuel);
    api('/api/stations/by-city?' + params.toString()).then(data => {
      _mapStations = data.stations || [];
      if (_mapStations.length === 0) {
        list.innerHTML = '<div class="map-empty">😔 Нет АЗС с координатами в этом городе</div>';
        _leafletLayer.clearLayers();
        return;
      }

      // Center map on stations
      const lats = _mapStations.map(s => s.lat);
      const lons = _mapStations.map(s => s.lon);
      const centerLat = lats.reduce((a, b) => a + b, 0) / lats.length;
      const centerLon = lons.reduce((a, b) => a + b, 0) / lons.length;
      const bounds = L.latLngBounds(_mapStations.map(s => [s.lat, s.lon]));
      _leafletMap.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });

      // Add markers
      _leafletLayer.clearLayers();
      _mapStations.forEach(s => {
        const status = _getMapAvailability(s, state.fuel);
        const m = L.marker([s.lat, s.lon], { icon: _makeMarkerIcon(status) });
        m.bindPopup(_popupHtml(s), { maxWidth: 240, closeButton: true });
        m.on('click', () => {
          haptic('light');
        });
        m.addTo(_leafletLayer);
      });

      // Show user location if already known
      if (state.userLocation) {
        _updateUserMarker(state.userLocation);
      }

      // Render list
      renderMapStationsList(_mapStations);
    }).catch(e => {
      list.innerHTML = `<div class="map-empty">⚠️ ${escape(e.message)}</div>`;
      _leafletLayer.clearLayers();
    });
  }

  function renderMapStationsList(stations) {
    const list = document.getElementById('map-stations-list');
    if (!list) return;
    list.innerHTML = '';
    if (!stations || stations.length === 0) {
      list.innerHTML = '<div class="map-empty">Нет АЗС</div>';
      return;
    }
    stations.forEach(s => {
      const op = s.operator || s.name || 'АЗС';
      const addr = s.address || s.city || '';
      const status = _getMapAvailability(s, state.fuel);
      const item = document.createElement('div');
      item.className = 'map-station-item';
      item.dataset.stationId = s.id;
      item.innerHTML = `
        <div class="map-station-icon status-${status}">⛽</div>
        <div class="map-station-info">
          <div class="map-station-name">${escape(op)}</div>
          <div class="map-station-addr">${escape(addr)}</div>
          <div class="map-station-status status-${status}">${({available:'В наличии',partial:'Частично',unavailable:'Нет топлива',unknown:'Нет данных'})[status]}</div>
        </div>
        <div class="map-station-arrow">›</div>
      `;
      item.addEventListener('click', () => {
        // Center on station in map
        if (_leafletMap) {
          _leafletMap.setView([s.lat, s.lon], 16, { animate: true });
        }
        openStationDetail(s);
      });
      list.appendChild(item);
    });
  }

  function _updateUserMarker(loc) {
    if (!_leafletMap) return;
    if (_userMarker) {
      _userMarker.setLatLng([loc.lat, loc.lon]);
    } else {
      _userMarker = L.marker([loc.lat, loc.lon], { icon: _userLocationIcon(), interactive: false }).addTo(_leafletMap);
    }
    if (_userCircle) {
      _userCircle.setLatLng([loc.lat, loc.lon]);
    } else {
      _userCircle = L.circle([loc.lat, loc.lon], { radius: 50, color: '#3b82f6', fillColor: '#3b82f6', fillOpacity: 0.15, weight: 1 }).addTo(_leafletMap);
    }
  }

  async function centerOnUser() {
    haptic('light');
    const btn = document.getElementById('map-locate-btn');
    if (btn) btn.classList.add('loading');
    try {
      const loc = await getUserLocation();
      if (loc) {
        state.userLocation = loc;
        _updateUserMarker(loc);
        if (_leafletMap) {
          _leafletMap.setView([loc.lat, loc.lon], 14, { animate: true });
        }
      }
    } finally {
      if (btn) btn.classList.remove('loading');
    }
  }

  // ============= REPORT =============
  function openReportSheet(stationId, stationName) {
    state.reportSheet = {
      stationId: stationId || null,
      stationName: stationName || '',
      fuel: state.fuel || '92',
      available: true,
      price: null,
      queue: null,
      hasLimit: false,
      limitLiters: null,
      limitPerVisit: null,
      limitDaily: null,
      limitWeekly: null,
      canisterBan: false,
      comment: '',
    };
    dom.reportSheetStation.textContent = stationName || (state.stations.length > 0
      ? 'Выбери АЗС' : 'Сначала выбери АЗС');
    dom.reportPrice.value = '';
    dom.reportQueue.value = '';
    const hasLimitEl = $('#report-has-limit');
    if (hasLimitEl) hasLimitEl.checked = false;
    const limitFields = $('#report-limit-fields');
    if (limitFields) limitFields.hidden = true;
    const limitLitersEl = $('#report-limit-liters');
    if (limitLitersEl) limitLitersEl.value = '';
    const limitPVEl = $('#report-limit-per-visit');
    if (limitPVEl) limitPVEl.value = '';
    const limitDE = $('#report-limit-daily');
    if (limitDE) limitDE.value = '';
    const limitWE = $('#report-limit-weekly');
    if (limitWE) limitWE.value = '';
    const canisterEl = $('#report-canister-ban');
    if (canisterEl) canisterEl.checked = false;
    const commentEl = $('#report-comment');
    if (commentEl) commentEl.value = '';
    $$('.chip-fuel-sheet').forEach(c => c.classList.toggle('active', c.dataset.fuel === state.reportSheet.fuel));
    $$('.avail-btn').forEach(b => b.classList.toggle('active', String(b.dataset.avail) === String(state.reportSheet.available)));
    dom.reportSheet.hidden = false;
    haptic('light');
  }

  async function submitReport() {
    const { stationId, fuel, available, price, queue, hasLimit, limitLiters, limitPerVisit, limitDaily, limitWeekly, canisterBan, comment } = state.reportSheet;
    if (!stationId) {
      showToast('Сначала выбери АЗС', 'warning');
      return;
    }
    const tgId = getTgId();
    if (!tgId) {
      showToast('Не удалось определить пользователя', 'error');
      return;
    }
    showLoading();
    try {
      const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
      const body = {
        station_id: stationId,
        fuel_type: fuel,
        available,
        [idParam]: tgId,
        first_name: tg?.initDataUnsafe?.user?.first_name || state.vkFirstName || 'User',
      };
      if (price) body.price = parseFloat(price);
      if (queue !== undefined && queue !== null && queue !== '') body.queue_size = parseInt(queue);
      if (hasLimit) {
        body.has_limit = true;
        if (limitLiters) body.limit_liters = parseInt(limitLiters);
        if (limitPerVisit) body.limit_per_visit = parseInt(limitPerVisit);
        if (limitDaily) body.limit_daily = parseInt(limitDaily);
        if (limitWeekly) body.limit_weekly = parseInt(limitWeekly);
      }
      if (canisterBan) body.canister_ban = true;
      if (comment && comment.trim()) body.comment = comment.trim();
      await api('/api/reports', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      closeSheet('report-sheet');
      hapticNotify('success');
      showToast('✅ Отчёт отправлен!', 'success');
      // Reload station detail
      if (state.selectedStation) openStationDetail(state.selectedStation);
      // Switch to home tab if no station detail
      if (!state.selectedStation) loadStations();
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= REVIEW =============
  function openReviewSheet(stationId, stationName) {
    state.reviewSheet = {
      stationId,
      stationName: stationName || '',
      fuel: '92',
      rating: 0,
      comment: '',
    };
    dom.reviewSheetStation.textContent = stationName || 'АЗС';
    dom.reviewComment.value = '';
    $$('.chip-review-fuel').forEach(c => c.classList.toggle('active', c.dataset.fuel === '92'));
    $$('.star').forEach(s => s.classList.remove('active', 'filled'));
    dom.ratingHint.textContent = 'Нажми на звезду';
    dom.reviewSheet.hidden = false;
    haptic('light');
  }

  async function submitReview() {
    const { stationId, fuel, rating, comment } = state.reviewSheet;
    if (!stationId) { showToast('Выбери АЗС', 'warning'); return; }
    if (rating === 0) { showToast('Поставь оценку', 'warning'); return; }
    const tgId = getTgId();
    if (!tgId) { showToast('Не удалось определить пользователя', 'error'); return; }

    showLoading();
    try {
      const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
      const body = {
        station_id: stationId,
        fuel_type: fuel,
        rating: rating,
        [idParam]: tgId,
        first_name: tg?.initDataUnsafe?.user?.first_name || state.vkFirstName || 'User',
      };
      if (comment && comment.trim()) body.comment = comment.trim();
      await api('/api/reviews', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      closeSheet('review-sheet');
      hapticNotify('success');
      showToast('✅ Спасибо за отзыв!', 'success');
      if (state.selectedStation) openStationDetail(state.selectedStation);
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= SUBSCRIBE =============
  async function subscribeStation(stationId) {
    const tgId = getTgId();
    if (!tgId) { showToast('Не удалось определить пользователя', 'error'); return; }
    showLoading();
    try {
      // We don't have a direct /api/subscribe endpoint — use bot
      showToast('Подпишись через бота: /subscribe', 'info');
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= PROFILE =============
  async function loadProfile() {
    // Ensure VK Bridge is initialized before accessing VK data (only in VK context, skip if URL-detected)
    if (!platform.tg && !state.vkUserId) {
      try { await Promise.race([vkBridgePromise, new Promise(r => setTimeout(r, 3000))]); } catch (e) {}
    }

    const user = tg?.initDataUnsafe?.user;
    if (user) {
      const name = user.first_name + (user.last_name ? ' ' + user.last_name : '');
      dom.profileName.textContent = name;
      dom.profileId.textContent = 'ID: ' + user.id;
      dom.profileAvatar.textContent = user.first_name[0].toUpperCase();
      dom.profileBigAvatar.textContent = user.first_name[0].toUpperCase();
    } else if (platform.vk) {
      // Use launch params vk_user_id as fallback
      const fallbackVkId = state.vkUserId;
      let hasVKBridge = !!(window.vkBridge && state.vkLaunchParams);
      if (hasVKBridge) {
        // Real VK Bridge — try VKWebAppGetUserInfo with timeout
        try {
          const userInfo = await Promise.race([
            window.vkBridge.send('VKWebAppGetUserInfo', {}),
            new Promise((_, rej) => setTimeout(() => rej(new Error('bridge timeout')), 3000)),
          ]);
          dom.profileName.textContent = userInfo.first_name;
          dom.profileId.textContent = 'VK ID: ' + userInfo.id;
          state.vkUserId = userInfo.id;
          state.vkFirstName = userInfo.first_name;
          dom.profileAvatar.textContent = userInfo.first_name[0].toUpperCase();
          dom.profileBigAvatar.textContent = userInfo.first_name[0].toUpperCase();
          try {
            await api('/api/user/ensure-vk', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({vk_user_id: userInfo.id}),
            });
          } catch (e) { console.warn('ensure-vk failed:', e); }
        } catch (e) {
          console.warn('VKWebAppGetUserInfo failed:', e);
          hasVKBridge = false;
        }
      }
      if (!hasVKBridge && fallbackVkId) {
        // Browser mode or bridge failed — load from DB via ensure-vk
        let userName = 'VK User';
        try {
          const ensureRes = await api('/api/user/ensure-vk', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({vk_user_id: fallbackVkId}),
          });
          if (ensureRes && ensureRes.first_name) userName = ensureRes.first_name;
        } catch (e2) { console.warn('ensure-vk failed:', e2); }
        dom.profileName.textContent = userName;
        dom.profileId.textContent = 'VK ID: ' + fallbackVkId;
        dom.profileAvatar.textContent = userName[0].toUpperCase();
        dom.profileBigAvatar.textContent = userName[0].toUpperCase();
      } else if (!hasVKBridge && !fallbackVkId) {
        dom.profileName.textContent = 'Гость';
        dom.profileId.textContent = '';
      }
    } else {
      dom.profileName.textContent = 'Гость';
      dom.profileId.textContent = '';
    }

    // Show manual VK ID input if no ID detected
    const manualSection = document.getElementById('vk-id-manual');
    if (manualSection) {
      const currentId = getTgId();
      if (!currentId && !tg?.initDataUnsafe?.user?.id) {
        manualSection.style.display = 'block';
      } else {
        manualSection.style.display = 'none';
      }
    }

    // Load stats
    try {
      const tgId = getTgId();
      if (tgId) {
        const idParam = platform.vk ? `vk_user_id=${tgId}` : `telegram_id=${tgId}`;
        const stats = await api(`/api/user/stats?${idParam}`).catch(() => null);
        if (stats && stats.ok) {
          if (dom.statReports) dom.statReports.textContent = stats.reports || 0;
          if (dom.statBadges) dom.statBadges.textContent = (stats.badges || []).length || 0;
        }
      }
    } catch (e) {}

    // Check premium status
    try {
      const tgId = getTgId();
      if (tgId) {
        const idParam = platform.vk ? `vk_user_id=${tgId}` : `telegram_id=${tgId}`;
        const premRes = await api(`/api/premium/status?${idParam}`).catch(() => null);
        if (premRes && premRes.active) {
          const premCard = document.getElementById('premium-card');
          if (premCard) {
            premCard.querySelector('.premium-subtitle').textContent = `Активен до ${premRes.expires_at || ''}`;
            premCard.querySelector('.btn-premium').textContent = 'Управление';
          }
        }
      }
    } catch (e) {}

    // Load account info (unified: one user = one record with telegram_id + vk_id)
    try {
      const tgId = getTgId();
      if (tgId) {
        const idParam = platform.vk ? `vk_user_id=${tgId}` : `telegram_id=${tgId}`;
        const accRes = await api(`/api/account/info?${idParam}`).catch(() => null);
        if (accRes && accRes.ok) {
          // Определяем реальный ID юзера для отображения
          const tgEl = document.getElementById('account-tg-id');
          const isVkUser = platform.vk && accRes.vk_id;
          if (tgEl) {
            if (isVkUser) {
              tgEl.textContent = accRes.vk_id || '—';
            } else {
              tgEl.textContent = (accRes.telegram_id && accRes.telegram_id > 0) ? accRes.telegram_id : '—';
            }
          }

          // VK row
          const vkRow = document.getElementById('account-vk-row');
          const vkEl = document.getElementById('account-vk-id');
          if (accRes.vk_id) {
            if (vkRow) vkRow.style.display = 'flex';
            if (vkEl) vkEl.textContent = accRes.vk_id;
          } else {
            if (vkRow) vkRow.style.display = 'none';
          }

          // Link row — show "Привязано" if has both platforms
          const linkRow = document.getElementById('account-link-row');
          const linkEl = document.getElementById('account-link-to');
          const hasBoth = accRes.vk_id && accRes.telegram_id && accRes.telegram_id > 0;
          if (hasBoth) {
            if (linkRow) linkRow.style.display = 'flex';
            if (linkEl) linkEl.textContent = '✅ Обе платформы привязаны';
          } else {
            if (linkRow) linkRow.style.display = 'none';
          }

          // Hide group row (no longer exists)
          const groupRow = document.getElementById('account-group-row');
          if (groupRow) groupRow.style.display = 'none';

          // Premium статус
          const premRow = document.getElementById('account-premium-row');
          const premEl = document.getElementById('account-premium');
          const founderRow = document.getElementById('founder-badge-row');
          const founderEl = document.getElementById('account-founder');
          if (accRes.is_premium && accRes.premium_tier) {
            if (premRow) premRow.style.display = 'flex';
            const tierNames = {
              'economy': '📊 Эконом',
              'standard': '🗺️ Стандарт',
              'elite': '👑 Элит',
              'founder': '🏆 Founder',
            };
            const tierName = tierNames[accRes.premium_tier] || accRes.premium_tier;
            let expDate = '';
            if (accRes.premium_expires_at) {
              const d = new Date(accRes.premium_expires_at);
              if (!isNaN(d.getTime())) {
                expDate = accRes.premium_tier === 'founder' ? ' — навсегда' : ` до ${d.toLocaleDateString('ru-RU')}`;
              }
            }
            if (premEl) {
              premEl.textContent = `${tierName} ✅${expDate}`;
              premEl.style.color = '#fbbf24';
            }
            if (accRes.is_founder) {
              if (founderRow) founderRow.style.display = 'flex';
              if (founderEl) founderEl.textContent = 'Основатель 🏆';
            } else {
              if (founderRow) founderRow.style.display = 'none';
            }
            const trialBtnProfile2 = document.getElementById('btn-trial-profile');
            if (trialBtnProfile2) trialBtnProfile2.style.display = 'none';
          } else {
            if (premRow) premRow.style.display = 'none';
            if (founderRow) founderRow.style.display = 'none';
            const trialBtnProfile = document.getElementById('btn-trial-profile');
            if (trialBtnProfile) trialBtnProfile.style.display = 'block';
          }

          const statusEl = document.getElementById('accounts-status');
          if (statusEl) {
            if (hasBoth) {
              statusEl.textContent = '✅ Аккаунты привязаны — Premium работает везде';
              statusEl.style.color = '#34d399';
            } else if (accRes.is_premium) {
              statusEl.textContent = 'Premium активен. Привяжи второй аккаунт чтобы работал там тоже.';
            } else {
              statusEl.textContent = 'Привяжи аккаунт чтобы Premium работал везде.';
            }
          }

          // Show/hide link input vs unlink button
          const isLinked = hasBoth;
          const linkInputSection = document.getElementById('link-input-section');
          const unlinkSection = document.getElementById('link-unlink-section');
          if (linkInputSection) linkInputSection.style.display = isLinked ? 'none' : 'block';
          if (unlinkSection) unlinkSection.style.display = isLinked ? 'block' : 'none';
        }
      }
    } catch (e) {
      console.error('loadAccounts error:', e);
    }

    // Load fuel alarms
    try {
      const tgId = getTgId();
      if (tgId) {
        const idParam = platform.vk ? `vk_user_id=${tgId}` : `telegram_id=${tgId}`;
        const alarmsRes = await api(`/api/fuel-alarm/list?${idParam}`).catch(() => null);
        const alarmsList = document.getElementById('fuel-alarms-list');
        if (alarmsList && alarmsRes) {
          const alarms = alarmsRes.alarms || [];
          if (alarms.length === 0) {
            alarmsList.innerHTML = '<div class="empty-mini">Нет активных будильников</div>';
          } else {
            alarmsList.innerHTML = alarms.map(a => {
              const fuelLabel = a.fuel_type === '100' ? 'АИ-100' :
                a.fuel_type === 'diesel' ? 'ДТ' : `АИ-${a.fuel_type}`;
              return `
                <div class="alarm-item" style="display:flex;align-items:center;gap:12px;padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;">
                  <div style="font-size:24px;">⛽</div>
                  <div style="flex:1;">
                    <div style="font-weight:600;">${fuelLabel}</div>
                    <div style="font-size:13px;color:var(--text-secondary);">${a.name || 'АЗС'} · ${a.city || ''}</div>
                  </div>
                  <button class="btn btn-sm btn-outline" data-action="delete-fuel-alarm"
                    data-station="${a.station_id}" data-fuel="${a.fuel_type}"
                    style="color:#ef4444;border-color:#ef4444;">Удалить</button>
                </div>`;
            }).join('');
          }
        }
      }
    } catch (e) {
      console.error('loadFuelAlarms error:', e);
    }

    // Load savings
    try {
      const tgId = getTgId();
      if (tgId) {
        const savingsIdParam = platform.vk ? `vk_user_id=${tgId}` : `telegram_id=${tgId}`;
        const savingsRes = await api(`/api/user/savings?${savingsIdParam}`).catch(() => null);
        if (savingsRes && dom.statSavings) {
          const savings = savingsRes.savings || 0;
          if (savings > 0) {
            dom.statSavings.textContent = `${savings.toLocaleString('ru-RU')}₽`;
            dom.statSavings.style.color = '#34d399';
          } else {
            dom.statSavings.textContent = '—';
          }
        }
      }
    } catch (e) {
      console.error('loadSavings error:', e);
    }

    // Load referral data (balance + earnings + tier + selling texts)
    try {
      const tgId = getTgId();
      if (tgId) {
        const idParam = platform.vk ? `vk_user_id=${tgId}` : `telegram_id=${tgId}`;
        const [codeRes, balanceRes, tierRes, textsRes] = await Promise.all([
          api(`/api/referral/code?${idParam}`).catch(() => null),
          api(`/api/referral/balance?${idParam}`).catch(() => null),
          api(`/api/referral/tier?${idParam}`).catch(() => null),
          api(`/api/referral/selling-texts?${idParam}`).catch(() => null),
        ]);
        if (codeRes && codeRes.code) {
          const codeEl = document.getElementById('referral-code');
          const refLink = platform.vk
            ? `https://vk.com/benzyn_ryadom?start=ref_${codeRes.code}`
            : `https://t.me/benzyn_ryadom_bot?start=ref_${codeRes.code}`;
          if (codeEl) codeEl.textContent = refLink;
        }

        // Balance
        if (balanceRes && balanceRes.ok) {
          const bal = balanceRes.balance || {};
          const stats = balanceRes.stats || {};
          const referred = balanceRes.referred_users || [];

          // Balance card
          const balCard = document.getElementById('referrer-balance-card');
          const balAmount = document.getElementById('ref-balance-amount');
          const totalEarned = document.getElementById('ref-total-earned');
          const totalWithdrawn = document.getElementById('ref-total-withdrawn');
          if (balCard) balCard.style.display = 'block';
          if (balAmount) balAmount.textContent = (bal.balance || 0) + '₽';
          if (totalEarned) totalEarned.textContent = (bal.total_earned || 0) + '₽';
          if (totalWithdrawn) totalWithdrawn.textContent = (bal.total_withdrawn || 0) + '₽';

          // Stats
          const totalEl = document.getElementById('referral-total');
          const completedEl = document.getElementById('referral-completed');
          const earningsEl = document.getElementById('ref-referral-earnings');
          if (totalEl) totalEl.textContent = stats.total || 0;
          if (completedEl) completedEl.textContent = stats.completed || 0;
          if (earningsEl) earningsEl.textContent = (bal.total_earned || 0) + '₽';

          // Tier display
          if (tierRes && tierRes.ok) {
            const tierEl = document.getElementById('ref-tier-info');
            const tierNames = {basic: 'Базовый', ambassador: 'Посол', top_ref: 'Топ-реферер', legend: 'Легенда'};
            if (tierEl) {
              let tierHtml = `<div style="font-weight:700;font-size:15px;margin-bottom:6px;">🎯 Тир: ${tierNames[tierRes.tier] || 'Базовый'}</div>`;
              tierHtml += `<div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">Комиссия: ${tierRes.commission}%</div>`;
              if (tierRes.is_top3) tierHtml += `<div style="font-size:13px;color:#f59e0b;">🏆 Топ-3 за месяц!</div>`;
              if (tierRes.next_tier) tierHtml += `<div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">До «${tierRes.next_tier.name}»: ${tierRes.next_tier.need} рефералов</div>`;
              tierEl.innerHTML = tierHtml;
              tierEl.style.display = 'block';
            }

            // Calculator (for non-elite)
            const calcEl = document.getElementById('ref-calc');
            if (calcEl) {
              const examples = [[5,50],[20,50],[50,55],[100,60],[200,65]];
              let calcHtml = '<div style="font-weight:700;font-size:14px;margin-bottom:6px;">📊 Калькулятор дохода:</div>';
              examples.forEach(([n, rate]) => {
                calcHtml += `<div style="font-size:12px;color:var(--text-secondary);">${n} рефералов × ${rate}% = ~${Math.floor(n*250*rate/100)}₽/мес</div>`;
              });
              calcEl.innerHTML = calcHtml;
              calcEl.style.display = 'block';
            }
          }

          // Selling texts
          if (textsRes && textsRes.ok && textsRes.texts) {
            const textsEl = document.getElementById('ref-selling-texts');
            if (textsEl) {
              let html = '<div style="font-weight:700;font-size:14px;margin-bottom:8px;">📝 Продающие тексты:</div>';
              textsRes.texts.forEach(t => {
                const safeText = escape(t.text).replace(/'/g, '&#39;');
                html += `<div style="background:var(--bg-card);border-radius:10px;padding:10px;margin-bottom:8px;">
                  <div style="font-weight:600;font-size:12px;margin-bottom:4px;">${escape(t.platform)}</div>
                  <div style="font-size:12px;color:var(--text-secondary);white-space:pre-wrap;">${escape(t.text)}</div>
                  <button class="btn btn-secondary ref-copy-btn" style="margin-top:6px;padding:6px 12px;font-size:11px;border-radius:8px;"
                    data-copy="${safeText}">Копировать</button>
                </div>`;
              });
              textsEl.innerHTML = html;
              textsEl.style.display = 'block';
              textsEl.querySelectorAll('.ref-copy-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                  const txt = btn.getAttribute('data-copy').replace(/&#39;/g, "'");
                  navigator.clipboard.writeText(txt).then(() => showToast('Скопировано!', 'success')).catch(() => {});
                });
              });
            }
          }

          // Referred users list
          if (referred.length > 0) {
            const section = document.getElementById('referred-users-section');
            const list = document.getElementById('referred-users-list');
            if (section) section.style.display = 'block';
            if (list) {
              list.innerHTML = referred.map(r => `
                <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:var(--bg-card);border-radius:10px;margin-bottom:6px;">
                  <div>
                    <div style="font-size:13px;font-weight:600;">${escape(r.name || 'Пользователь')}</div>
                    <div style="font-size:11px;color:var(--text-secondary);">${r.payment_count || 0} оплат</div>
                  </div>
                  <div style="font-size:14px;font-weight:700;color:#f59e0b;">${r.total_commission || 0}₽</div>
                </div>
              `).join('');
            }
          }
        }

        // Bind referral event listeners only once
        if (!window._referralListenersBound) {
          window._referralListenersBound = true;

        // Withdraw button
        const withdrawBtn = document.getElementById('btn-withdraw');
        if (withdrawBtn) {
          withdrawBtn.addEventListener('click', async () => {
            const bal = balanceRes?.balance?.balance || 0;
            if (bal < 100) {
              showToast('Минимальная сумма вывода — 100₽', 'warning');
              return;
            }
            // Сначала согласие на документы
            const ok = await ensureConsent(['terms', 'privacy', 'referral']);
            if (!ok) return;
            // Сумма (по умолчанию весь баланс)
            const amountStr = prompt(`Сумма вывода (доступно ${bal}₽):`, String(bal));
            if (!amountStr) return;
            const amount = parseInt(amountStr, 10);
            if (!amount || amount < 100) {
              showToast('Минимальная сумма — 100₽', 'warning');
              return;
            }
            if (amount > bal) {
              showToast(`Недостаточно средств (доступно ${bal}₽)`, 'error');
              return;
            }
            // Номер карты
            const cardNumber = prompt('💳 Введите номер карты (16 цифр):\n\nПеревод придёт в течение 1-2 минут.', '');
            if (!cardNumber) return;
            const cardClean = cardNumber.replace(/\s|-/g, '');
            if (!/^\d{13,19}$/.test(cardClean)) {
              showToast('Неверный номер карты', 'error');
              return;
            }
            try {
              const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
              showLoading();
              const resp = await api('/api/referral/withdraw', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({[idParam]: tgId, amount, method: 'card', details: cardClean}),
              });
              hideLoading();
              if (resp.ok) {
                showToast(resp.message || '✅ Выплата отправлена!', 'success');
                loadProfile();
              } else {
                showToast(resp.error || 'Ошибка выплаты', 'error');
              }
            } catch (e) {
              hideLoading();
              showToast('Ошибка: ' + e.message, 'error');
            }
          });
        }

        // Share button
        const shareBtn = document.getElementById('btn-share-referral');
        if (shareBtn) {
          shareBtn.addEventListener('click', () => {
            const code = codeRes?.code || '';
            const refLink = platform.vk
              ? `https://vk.com/benzyn_ryadom?start=ref_${code}`
              : `https://t.me/benzyn_ryadom_bot?start=ref_${code}`;
            const text = `🎁 Перейди по ссылке — получи 15% скидку на Premium!\n${refLink}`;
            if (navigator.share) {
              navigator.share({ text }).catch(() => {});
            } else {
              navigator.clipboard.writeText(text).then(() => {
                showToast('Скопировано в буфер обмена!', 'success');
              }).catch(() => {});
            }
          });
        }

        // Apply button
        const applyBtn = document.getElementById('btn-apply-referral');
        const applyInput = document.getElementById('referral-input');
        if (applyBtn && applyInput) {
          applyBtn.addEventListener('click', async () => {
            // Согласие на правила реферальной программы
            const consentOk = await ensureConsent(['terms', 'referral']);
            if (!consentOk) return;
            let raw = applyInput.value.trim();
            if (!raw) {
              showToast('Вставь ссылку или код друга', 'warning');
              return;
            }
            // Parse code from link or use raw
            let code = raw.toUpperCase();
            const refMatch = raw.match(/start=ref[_=](\w+)/i);
            if (refMatch) code = refMatch[1].toUpperCase();
            if (!code) {
              showToast('Не удалось распознать код', 'warning');
              return;
            }
            try {
              const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
              const resp = await api('/api/referral/apply', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({[idParam]: tgId, code}),
              });
              if (resp.ok) {
                showToast('Реферал применён! 15% скидка тебе.', 'success');
                hapticNotify('success');
                applyInput.value = '';
                loadProfile();
              } else {
                showToast(resp.error || 'Ошибка', 'error');
              }
            } catch (e) {
              showToast('Ошибка: ' + e.message, 'error');
            }
          });
        }
        } // end if (!_referralListenersBound)
      } // end if (tgId)
    } catch (e) {
      console.error('loadReferral error:', e);
    }
  }

  // ============= SEARCH =============
  let searchTimer = null;
  function onSearchInput() {
    const q = dom.searchInput.value.trim();
    dom.searchClear.hidden = q.length === 0;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => doSearch(q), 350);
  }

  async function doSearch(q) {
    if (!q || q.length < 2) {
      if (state.city) {
        loadStations();
      } else {
        state.stations = [];
        renderStations();
      }
      return;
    }
    showLoading();
    try {
      // First try address search
      const params = new URLSearchParams();
      if (state.city) {
        params.set('city', state.city);
        const data = await api('/api/stations/by-city?' + params);
        state.stations = data.stations || [];
      } else {
        // General search
        const data = await api('/api/search?q=' + encodeURIComponent(q));
        state.stations = data.stations || [];
      }
      // Filter by query locally
      const ql = q.toLowerCase();
      state.stations = state.stations.filter(s => {
        const name = (s.name || '').toLowerCase();
        const op = (s.operator || '').toLowerCase();
        const addr = (s.address || '').toLowerCase();
        return name.includes(ql) || op.includes(ql) || addr.includes(ql);
      });
      dom.resultsTitle.textContent = q ? `Поиск: ${q}` : 'Результаты';
      renderStations();
    } catch (e) {
      showToast('Ошибка поиска: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= CLOSE SHEET =============
  function closeSheet(id) {
    $('#' + id).hidden = true;
  }

  // ============= EVENT BINDING =============
  // === ROUTE FUEL A→B ===
  let routeFuelCoords = { from: null, to: null };

  // === MAP PICKER (выбор точек A/B на карте) ===
  let _pickerMap = null;
  let _pickerMarker = null;
  let _pickerTarget = 'from';  // 'from' или 'to'
  let _pickerCallback = null;
  let _pickerEventsBound = false;

  function openMapPicker(target, callback) {
    _pickerTarget = target;
    _pickerCallback = callback;

    const overlay = document.getElementById('map-picker-overlay');
    const title = document.getElementById('map-picker-title');
    if (title) title.textContent = target === 'from' ? '📍 Выбери точку A' : '📍 Выбери точку B';
    if (overlay) {
      overlay.style.display = 'flex';
      // Закрытие по клику на фон
      overlay.onclick = (e) => {
        if (e.target === overlay) closeMapPicker();
      };
    }

    // Сброс маркера
    if (_pickerMarker && _pickerMap) {
      _pickerMap.removeLayer(_pickerMarker);
      _pickerMarker = null;
    }
    const confirmBtn = document.getElementById('map-picker-confirm');
    if (confirmBtn) confirmBtn.disabled = true;
    const coords = document.getElementById('map-picker-coords');
    if (coords) coords.textContent = 'Кликни по карте или найди адрес в поиске';

    // Инициализация карты
    setTimeout(() => initPickerMap(), 100);
  }

  function closeMapPicker() {
    const overlay = document.getElementById('map-picker-overlay');
    if (overlay) overlay.style.display = 'none';
    _pickerCallback = null;
  }

  function initPickerMap() {
    const container = document.getElementById('map-picker-container');
    if (!container) return;

    // Если карта уже создана — просто обновим размер
    if (_pickerMap) {
      _pickerMap.invalidateSize();
      return;
    }

    // Начальная позиция — Москва
    _pickerMap = L.map(container, {
      zoomControl: true,
      attributionControl: true,
    }).setView([55.7558, 37.6173], 5);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap',
    }).addTo(_pickerMap);

    // Клик по карте — установка маркера
    _pickerMap.on('click', (e) => {
      setPickerMarker(e.latlng.lat, e.latlng.lng);
    });

    // Привязываем обработчики ОДИН раз
    if (!_pickerEventsBound) {
      bindPickerEvents();
      _pickerEventsBound = true;
    }

    // Попробуем определить местоположение пользователя
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const userLat = pos.coords.latitude;
          const userLon = pos.coords.longitude;
          _pickerMap.setView([userLat, userLon], 11);
          // Добавляем специальный маркер "Я"
          const userIcon = L.divIcon({
            className: 'user-location-marker',
            html: '<div style="background:#3b82f6;width:16px;height:16px;border-radius:50%;border:3px solid #fff;box-shadow:0 0 0 2px #3b82f6;"></div>',
            iconSize: [16, 16],
            iconAnchor: [8, 8],
          });
          L.marker([userLat, userLon], { icon: userIcon }).addTo(_pickerMap)
            .bindPopup('📍 Вы здесь');
        },
        () => {
          // Игнорируем — пользователь не дал доступ или ошибка
        },
        { timeout: 5000 }
      );
    }
  }

  function bindPickerEvents() {
    const searchBtn = document.getElementById('map-picker-search-btn');
    const searchInput = document.getElementById('map-picker-search-input');
    const confirmBtn = document.getElementById('map-picker-confirm');
    const locateBtn = document.getElementById('map-picker-locate-btn');

    if (searchBtn) {
      searchBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        doPickerSearch();
      });
    }
    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          doPickerSearch();
        }
      });
    }
    if (confirmBtn) {
      confirmBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!_pickerMarker) {
          showToast('Сначала выбери точку на карте', 'error');
          return;
        }
        const ll = _pickerMarker.getLatLng();
        const name = (searchInput && searchInput.value) || `${ll.lat.toFixed(4)}, ${ll.lng.toFixed(4)}`;
        if (_pickerCallback) _pickerCallback({ lat: ll.lat, lon: ll.lng, name: name });
        closeMapPicker();
      });
    }
    if (locateBtn) {
      locateBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        locateUserInPicker();
      });
    }
  }

  function locateUserInPicker() {
    if (!navigator.geolocation) {
      showToast('Геолокация не поддерживается', 'error');
      return;
    }
    showLoading();
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        hideLoading();
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        setPickerMarker(lat, lon);
        if (_pickerMap) _pickerMap.setView([lat, lon], 13);
        showToast('📍 Вы здесь', 'success');
      },
      (err) => {
        hideLoading();
        showToast('Не удалось определить местоположение: ' + err.message, 'error');
      },
      { timeout: 10000, enableHighAccuracy: true }
    );
  }

  function setPickerMarker(lat, lon) {
    const coords = document.getElementById('map-picker-coords');
    const confirmBtn = document.getElementById('map-picker-confirm');
    if (coords) coords.textContent = `📍 ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
    if (confirmBtn) confirmBtn.disabled = false;

    if (_pickerMarker) {
      _pickerMarker.setLatLng([lat, lon]);
    } else {
      if (!_pickerMap) return;
      _pickerMarker = L.marker([lat, lon], { draggable: true }).addTo(_pickerMap);
      _pickerMarker.on('dragend', (e) => {
        const ll = e.target.getLatLng();
        setPickerMarker(ll.lat, ll.lng);
      });
    }
    // Увеличиваем масштаб если слишком далеко
    const currentZoom = _pickerMap.getZoom();
    if (currentZoom < 10) {
      _pickerMap.setView([lat, lon], 12);
    } else {
      _pickerMap.panTo([lat, lon]);
    }
  }

  async function doPickerSearch() {
    const input = document.getElementById('map-picker-search-input');
    const q = input?.value?.trim();
    if (!q) {
      showToast('Введи название города', 'error');
      return;
    }
    showLoading();
    const coords = await geocode(q);
    hideLoading();
    if (!coords) {
      showToast('Не нашёл: ' + q + '\nПопробуй ввести с городом/областью', 'error');
      return;
    }
    setPickerMarker(coords.lat, coords.lon);
    if (_pickerMap) _pickerMap.setView([coords.lat, coords.lon], 12);
  }

  // === Улучшенный геокодинг: Nominatim OpenStreetMap ===
  async function geocode(query) {
    // Сначала пробуем /api/search (по АЗС)
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}&limit=1`);
      if (data && data.results && data.results.length > 0) {
        const r = data.results[0];
        if (r.lat && r.lon) {
          return { lat: r.lat, lon: r.lon, name: r.name || r.city || query };
        }
      }
    } catch (e) {}

    // Если не нашли — пробуем Nominatim (OpenStreetMap)
    try {
      const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1&accept-language=ru`;
      const res = await fetch(url, { headers: { 'User-Agent': 'BenzinRyadom/1.0' } });
      if (res.ok) {
        const data = await res.json();
        if (data && data.length > 0) {
          const r = data[0];
          return { lat: parseFloat(r.lat), lon: parseFloat(r.lon), name: r.display_name || query };
        }
      }
    } catch (e) {
      console.error('Nominatim geocode:', e);
    }
    return null;
  }

  async function findRouteFuel() {
    const fromInput = $('#route-fuel-from')?.value?.trim();
    const toInput = $('#route-fuel-to')?.value?.trim();
    const fuel = $('#route-fuel-type')?.value || '95';
    const results = $('#route-fuel-results');

    if (!fromInput || !toInput) {
      showToast('Введи точки A и B', 'error');
      return;
    }

    showLoading();
    try {
      // Сбрасываем старые координаты чтобы геокодинг заново обработал текущий ввод
      let fromCoords = null;
      let toCoords = null;

      // Если у нас уже есть координаты из picker — проверяем что текст не изменился
      if (routeFuelCoords.from && routeFuelCoords.from.name === fromInput) {
        fromCoords = routeFuelCoords.from;
      }
      if (routeFuelCoords.to && routeFuelCoords.to.name === toInput) {
        toCoords = routeFuelCoords.to;
      }

      // Если нет — делаем геокодинг
      if (!fromCoords) {
        fromCoords = await geocode(fromInput);
        if (fromCoords) {
          fromCoords.name = fromInput;
          routeFuelCoords.from = fromCoords;
        }
      }
      if (!toCoords) {
        toCoords = await geocode(toInput);
        if (toCoords) {
          toCoords.name = toInput;
          routeFuelCoords.to = toCoords;
        }
      }

      if (!fromCoords) {
        hideLoading();
        showToast('Не нашёл точку A: ' + fromInput + '\nИли выбери на карте (кнопка 🗺)', 'error');
        return;
      }
      if (!toCoords) {
        hideLoading();
        showToast('Не нашёл точку B: ' + toInput + '\nИли выбери на карте (кнопка 🗺)', 'error');
        return;
      }

      // Показываем координаты
      const fromEl = $('#route-fuel-from-coords');
      const toEl = $('#route-fuel-to-coords');
      if (fromEl) fromEl.textContent = `📍 ${fromCoords.lat.toFixed(4)}, ${fromCoords.lon.toFixed(4)}`;
      if (toEl) toEl.textContent = `📍 ${toCoords.lat.toFixed(4)}, ${toCoords.lon.toFixed(4)}`;

      // Запрос
      const tgId = getTgId();
      const routeIdParam = platform.vk ? 'vk_user_id' : 'telegram_id';
      let url = `/api/route/fuel?from_lat=${fromCoords.lat}&from_lon=${fromCoords.lon}&to_lat=${toCoords.lat}&to_lon=${toCoords.lon}&fuel=${fuel}`;
      if (tgId) url += `&${routeIdParam}=${tgId}`;

      const data = await api(url);
      hideLoading();

      renderRouteFuelResults(data, results, fuel);

      // Anti-traffic button handler
      const antiTrafficBtn = $('#btn-anti-traffic');
      if (antiTrafficBtn) {
        antiTrafficBtn.addEventListener('click', () => loadAntiTraffic(fromCoords, toCoords, fuel));
      }
    } catch (e) {
      hideLoading();
      console.error('findRouteFuel:', e);
      showToast('Ошибка: ' + e.message, 'error');
    }
  }

  // === Anti-traffic (Elite) ===
  async function loadAntiTraffic(fromCoords, toCoords, fuel) {
    const results = $('#route-fuel-results');
    if (!results) return;
    showLoading();
    try {
      const tgId = getTgId();
      const antiIdParam = platform.vk ? 'vk_user_id' : 'telegram_id';
      const url = `/api/route/anti-traffic?from_lat=${fromCoords.lat}&from_lon=${fromCoords.lon}&to_lat=${toCoords.lat}&to_lon=${toCoords.lon}&fuel=${fuel}&${antiIdParam}=${tgId}`;
      const data = await api(url);
      hideLoading();

      if (data.error === 'elite_required') {
        window.PremiumUI.showUpsell({ feature: 'anti_traffic' });
        return;
      }

      // Показываем результаты
      const trafficColors = { low: '#34d399', medium: '#fbbf24', high: '#ef4444' };
      const trafficEmojis = { low: '🟢', medium: '🟡', high: '🔴' };
      const t = data.traffic || {};

      let html = `
        <div class="route-fuel-summary" style="background:linear-gradient(135deg,rgba(59,130,246,0.1),rgba(29,78,216,0.05));border-color:rgba(59,130,246,0.2);">
          <div class="route-fuel-summary-num" style="color:#3b82f6;">${trafficEmojis[t.level] || '🟢'} ${t.level === 'high' ? 'Пробки' : t.level === 'medium' ? 'Средне' : 'Свободно'}</div>
          <div class="route-fuel-summary-label">${t.description || ''}</div>
        </div>
        <div style="display:flex;gap:8px;margin:8px 0;">
          <div style="flex:1;padding:12px;background:var(--bg-card);border-radius:12px;text-align:center;">
            <div style="font-size:20px;font-weight:700;color:${trafficColors[t.level] || '#34d399'};">${t.eta_minutes || '?'} мин</div>
            <div style="font-size:11px;color:var(--text-secondary);">⏱ ETA с пробками</div>
          </div>
          <div style="flex:1;padding:12px;background:var(--bg-card);border-radius:12px;text-align:center;">
            <div style="font-size:20px;font-weight:700;color:var(--text-secondary);">${t.eta_without_traffic || '?'} мин</div>
            <div style="font-size:11px;color:var(--text-secondary);">⏱ Без пробок</div>
          </div>
          <div style="flex:1;padding:12px;background:var(--bg-card);border-radius:12px;text-align:center;">
            <div style="font-size:20px;font-weight:700;color:#ef4444;">+${t.delay_minutes || 0} мин</div>
            <div style="font-size:11px;color:var(--text-secondary);">📈 Задержка</div>
          </div>
        </div>
      `;

      if (data.best_time) {
        html += `<div style="padding:10px;background:rgba(52,211,153,0.08);border-radius:10px;margin:8px 0;font-size:13px;color:#34d399;">💡 ${data.best_time}</div>`;
      }

      if (data.stop_points && data.stop_points.length > 0) {
        html += '<div style="font-size:13px;font-weight:700;margin:12px 0 8px;">📍 Точки остановки:</div>';
        for (const sp of data.stop_points) {
          html += `<div style="padding:8px 12px;background:var(--bg-card);border-radius:8px;margin-bottom:6px;font-size:13px;">
            📍 <b>${sp.km_from_start} км</b> — ${sp.suggestion}
          </div>`;
        }
      }

      results.innerHTML = html;
      showToast('🚗 Данные о пробках загружены', 'success');
    } catch (e) {
      hideLoading();
      showToast('Ошибка: ' + e.message, 'error');
    }
  }

  // === Обработка выбора точки на карте ===
  function onMapPicked(target, coords) {
    if (target === 'from') {
      routeFuelCoords.from = coords;
      const fromEl = $('#route-fuel-from-coords');
      if (fromEl) fromEl.textContent = `📍 ${coords.lat.toFixed(4)}, ${coords.lon.toFixed(4)}`;
    } else {
      routeFuelCoords.to = coords;
      const toEl = $('#route-fuel-to-coords');
      if (toEl) toEl.textContent = `📍 ${coords.lat.toFixed(4)}, ${coords.lon.toFixed(4)}`;
    }
    // Reverse geocoding — определяем название места
    reverseGeocode(coords.lat, coords.lon).then(name => {
      if (!name) return;
      if (target === 'from') {
        const input = $('#route-fuel-from');
        if (input && !input.value) input.value = name;
        if (routeFuelCoords.from) routeFuelCoords.from.name = name;
      } else {
        const input = $('#route-fuel-to');
        if (input && !input.value) input.value = name;
        if (routeFuelCoords.to) routeFuelCoords.to.name = name;
      }
    });
  }

  // === Reverse geocoding (определяет город по координатам) ===
  async function reverseGeocode(lat, lon) {
    try {
      // Nominatim reverse geocoding
      const url = `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&accept-language=ru&zoom=10`;
      const res = await fetch(url, { headers: { 'User-Agent': 'BenzinRyadom/1.0' } });
      if (!res.ok) return null;
      const data = await res.json();
      if (data && data.address) {
        const a = data.address;
        // Приоритет: город → посёлок → деревня → район → штат
        return a.city || a.town || a.village || a.hamlet || a.suburb ||
               a.county || a.state || data.display_name?.split(',')[0] || 'Точка';
      }
    } catch (e) {
      console.error('reverseGeocode:', e);
    }
    return null;
  }

  function renderRouteFuelResults(data, container, fuel) {
    if (!data || !container) return;

    const isPremium = data.is_premium;
    const guaranteed = data.guaranteed_stations || [];
    const allStations = data.stations || [];

    let html = '';

    // Summary
    html += `
      <div class="route-fuel-summary">
        <div class="route-fuel-summary-num">${data.total_distance_km} км</div>
        <div class="route-fuel-summary-label">${fuel === 'diesel' ? 'ДТ' : 'АИ-' + fuel} · ${allStations.length} АЗС в коридоре</div>
      </div>
    `;

    if (isPremium && guaranteed.length > 0) {
      html += `
        <div class="route-fuel-summary" style="background: linear-gradient(135deg, rgba(52,211,153,0.1) 0%, rgba(16,185,129,0.05) 100%); border-color: rgba(52,211,153,0.2);">
          <div class="route-fuel-summary-num" style="color: #34d399;">${guaranteed.length} ✅</div>
          <div class="route-fuel-summary-label">АЗС с гарантией наличия</div>
        </div>
      `;
      if (data.savings_30l) {
        html += `
          <div class="route-fuel-summary" style="background: linear-gradient(135deg, rgba(52,211,153,0.1) 0%, rgba(16,185,129,0.05) 100%); border-color: rgba(52,211,153,0.2);">
            <div class="route-fuel-summary-num" style="color: #34d399;">до ${data.savings_30l}₽</div>
            <div class="route-fuel-summary-label">экономия на 30л между макс и мин ценой</div>
          </div>
        `;
      }
    } else if (!isPremium) {
      // Free: показать upsell после результатов
      html += `
        <div style="text-align: center; padding: 12px; background: linear-gradient(135deg, rgba(251,191,36,0.1) 0%, rgba(245,158,11,0.05) 100%); border-radius: 12px; margin: 12px 0; border: 1px solid rgba(251,191,36,0.2);">
          <div style="font-size: 14px; font-weight: 700; color: #fbbf24; margin-bottom: 6px;">
            💎 Premium покажет все АЗС с гарантией
          </div>
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">
            ${data.message}
          </div>
          <button class="btn btn-premium" onclick="showUpsell({feature:'route_fuel'})" style="width: 100%;">
            Купить Premium — от 100₽/мес
          </button>
        </div>
      `;
    }

    // Anti-traffic button (Elite)
    if (isPremium && data.user_tier === 'elite') {
      html += `
        <button class="btn btn-primary" id="btn-anti-traffic" style="width:100%;margin:12px 0;background:linear-gradient(135deg,#3b82f6,#1d4ed8);box-shadow:0 4px 16px rgba(59,130,246,0.3);">
          🚗 Антипробка — показать пробки и ETA
        </button>
      `;
    } else if (!isPremium) {
      html += `
        <div style="text-align:center;padding:10px;background:rgba(59,130,246,0.08);border-radius:12px;margin:12px 0;border:1px solid rgba(59,130,246,0.2);">
          <div style="font-size:13px;color:var(--text-secondary);">
            🚗 <b>Антипробка</b> — Elite фича: ETA + пробки + лучшее время
          </div>
        </div>
      `;
    }

    // Рекомендация (Premium)
    if (isPremium && data.recommendation) {
      const r = data.recommendation;
      html += `
        <div class="route-fuel-result route-fuel-result-guaranteed" style="border-width: 2px;">
          <div class="route-fuel-result-name">
            ⭐ Лучший выбор <span class="route-fuel-recommend">РЕКОМЕНДУЕМ</span>
          </div>
          <div class="route-fuel-result-name">${escape(r.operator || r.name || 'АЗС')}</div>
          ${r.address ? `<div style="font-size:12px;color:var(--text-secondary);">📍 ${escape(r.address)}</div>` : ''}
          <div class="route-fuel-result-meta">
            <span>📏 ${r.distance_from_route_km} км от маршрута</span>
            <span style="color:#34d399;">✅ В наличии</span>
            ${r.last_queue ? `<span>👥 ${r.last_queue}</span>` : ''}
            ${r.last_has_limit ? `<span>🚫 лимит</span>` : ''}
          </div>
          ${r.last_price ? `<div class="route-fuel-result-price">${r.last_price}₽/л — самая низкая цена</div>` : ''}
        </div>
      `;
    }

    // Все АЗС
    if (allStations.length > 0) {
      html += '<div style="font-size: 13px; font-weight: 700; color: var(--text); margin: 12px 0 8px;">Все АЗС в коридоре:</div>';
      for (const s of allStations) {
        const isGuaranteed = isPremium && s.last_available === true;
        const yandexUrl = `https://yandex.ru/maps/?rtext=${s.lat},${s.lon}&rtt=auto`;
        html += `
          <div class="route-fuel-result ${isGuaranteed ? 'route-fuel-result-guaranteed' : ''}">
            <div class="route-fuel-result-name">
              ${escape(s.operator || s.name || 'АЗС')}
              ${isGuaranteed ? '<span class="route-fuel-recommend">✅</span>' : ''}
            </div>
            ${s.address ? `<div style="font-size:11px;color:var(--text-secondary);">📍 ${escape(s.address)}</div>` : ''}
            <div class="route-fuel-result-meta">
              <span>📏 ${s.distance_from_route_km} км</span>
              ${s.last_available === true ? '<span style="color:#34d399;">✅ В наличии</span>' :
                s.last_available === false ? '<span style="color:#f87171;">❌ Нет</span>' :
                '<span style="color:var(--text-secondary);">❓ Уточняйте</span>'}
              ${s.last_price ? `<span style="color:#fbbf24;">${s.last_price}₽</span>` : ''}
              ${s.last_queue ? `<span>👥 ${s.last_queue}</span>` : ''}
            </div>
            <div class="route-fuel-result-actions">
              <a href="${yandexUrl}" target="_blank" class="route-fuel-result-btn">🗺 Маршрут</a>
              <button class="route-fuel-result-btn" data-station-id="${s.id}" data-action="show-station-detail">📊 Детали</button>
            </div>
          </div>
        `;
      }
    } else {
      html += `
        <div style="text-align: center; padding: 24px; color: var(--text-secondary);">
          😔 АЗС в этом коридоре не найдено. Попробуй расширить маршрут.
        </div>
      `;
    }

    container.innerHTML = html;
    container.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }

  function bindEvents() {
    // Глобальный обработчик data-action (включая back, show-station-detail и т.д.)
    document.body.addEventListener('click', (e) => {
      const target = e.target.closest('[data-action]');
      if (!target) return;
      const action = target.dataset.action;
      if (action === 'back') {
        e.preventDefault();
        e.stopPropagation();
        showScreen('home');
        return;
      }
      if (action === 'show-station-detail') {
        e.preventDefault();
        e.stopPropagation();
        const sid = parseInt(target.dataset.stationId, 10);
        if (sid) {
          // Минимальные данные — загрузим полные
          openStationDetail({ id: sid });
        }
        return;
      }
      if (action === 'delete-fuel-alarm') {
        e.preventDefault();
        e.stopPropagation();
        const stationId = parseInt(target.dataset.station, 10);
        const fuelType = target.dataset.fuel;
        const _delUid = getTgId();
        if (!stationId || !fuelType || !_delUid) return;
        const _delParam = platform.vk ? 'vk_user_id' : 'telegram_id';
        api('/api/fuel-alarm/delete', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({[_delParam]: _delUid, station_id: stationId, fuel_type: fuelType}),
        }).then(() => {
          showToast('Будильник удалён', 'info');
          loadProfile();
        }).catch(err => showToast('Ошибка: ' + err.message, 'error'));
        return;
      }
    });

    // Nav items
    $$('.nav-item').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));

    // Top buttons
    dom.citySelector.addEventListener('click', () => { haptic('light'); showCityPicker(); });
    dom.geoBtn.addEventListener('click', useGeo);
    dom.emergencyBtn.addEventListener('click', doEmergencySearch);
    const sosBtn = $('#btn-sos');
    if (sosBtn) sosBtn.addEventListener('click', sendSOS);
    $('#btn-profile').addEventListener('click', () => setTab('profile'));

    // Search
    dom.searchInput.addEventListener('input', onSearchInput);
    dom.searchClear.addEventListener('click', () => {
      dom.searchInput.value = '';
      dom.searchClear.hidden = true;
      loadStations();
    });

    // ============= ROUTES =============
    const routesInput = $('#routes-input');
    const routesBtn = $('#routes-search-btn');
    const routesResults = $('#routes-results');

    if (routesBtn && routesInput && routesResults) {
      routesBtn.addEventListener('click', () => doRouteSearch(routesInput.value));
      routesInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doRouteSearch(routesInput.value);
      });
    }

    // Fuel chips
    $$('.chip-fuel').forEach(c => {
      c.addEventListener('click', () => {
        $$('.chip-fuel').forEach(b => b.classList.remove('active'));
        c.classList.add('active');
        state.fuel = c.dataset.fuel;
        haptic('light');
        loadStations();
      });
    });

    // Advanced filters: price & network
    const priceSheet = document.getElementById('price-filter-sheet');
    const networkSheet = document.getElementById('network-filter-sheet');
    const btnPrice = document.getElementById('btn-price-filter');
    const btnNetwork = document.getElementById('btn-network-filter');

    if (btnPrice) {
      btnPrice.addEventListener('click', () => {
        priceSheet.hidden = !priceSheet.hidden;
        networkSheet.hidden = true;
        haptic('light');
      });
    }
    if (btnNetwork) {
      btnNetwork.addEventListener('click', () => {
        networkSheet.hidden = !networkSheet.hidden;
        priceSheet.hidden = true;
        haptic('light');
      });
    }

    // Price chips
    $$('.chip-price').forEach(c => {
      c.addEventListener('click', () => {
        $$('.chip-price').forEach(b => b.classList.remove('active'));
        c.classList.add('active');
        state.maxPrice = parseInt(c.dataset.price) || 0;
        haptic('light');
        loadStations();
      });
    });

    // Network chips
    $$('.chip-network').forEach(c => {
      c.addEventListener('click', () => {
        $$('.chip-network').forEach(b => b.classList.remove('active'));
        c.classList.add('active');
        state.network = c.dataset.network || '';
        haptic('light');
        loadStations();
      });
    });

    // Close buttons
    const priceClose = document.getElementById('price-close');
    const networkClose = document.getElementById('network-close');
    if (priceClose) priceClose.addEventListener('click', () => { priceSheet.hidden = true; });
    if (networkClose) networkClose.addEventListener('click', () => { networkSheet.hidden = true; });

    // Report sheet
    $$('.chip-fuel-sheet').forEach(c => {
      c.addEventListener('click', () => {
        $$('.chip-fuel-sheet').forEach(b => b.classList.remove('active'));
        c.classList.add('active');
        state.reportSheet.fuel = c.dataset.fuel;
      });
    });
    $$('.avail-btn').forEach(b => {
      b.addEventListener('click', () => {
        $$('.avail-btn').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        state.reportSheet.available = b.dataset.avail === 'true';
        if (b.dataset.avail === 'queue') state.reportSheet.queue = 5;
        else state.reportSheet.queue = null;
      });
    });
    dom.reportPrice.addEventListener('input', e => state.reportSheet.price = e.target.value);
    dom.reportQueue.addEventListener('input', e => state.reportSheet.queue = e.target.value);
    const hasLimitEl = $('#report-has-limit');
    if (hasLimitEl) {
      hasLimitEl.addEventListener('change', e => {
        state.reportSheet.hasLimit = e.target.checked;
        const limitFields = $('#report-limit-fields');
        if (limitFields) limitFields.hidden = !e.target.checked;
      });
    }
    const limitLitersEl = $('#report-limit-liters');
    if (limitLitersEl) limitLitersEl.addEventListener('input', e => state.reportSheet.limitLiters = e.target.value);
    const limitPVEl = $('#report-limit-per-visit');
    if (limitPVEl) limitPVEl.addEventListener('input', e => state.reportSheet.limitPerVisit = e.target.value);
    const limitDE = $('#report-limit-daily');
    if (limitDE) limitDE.addEventListener('input', e => state.reportSheet.limitDaily = e.target.value);
    const limitWE = $('#report-limit-weekly');
    if (limitWE) limitWE.addEventListener('input', e => state.reportSheet.limitWeekly = e.target.value);
    const canisterEl = $('#report-canister-ban');
    if (canisterEl) canisterEl.addEventListener('change', e => state.reportSheet.canisterBan = e.target.checked);
    const commentEl = $('#report-comment');
    if (commentEl) commentEl.addEventListener('input', e => state.reportSheet.comment = e.target.value);
    $('#report-submit').addEventListener('click', submitReport);

    // Review sheet
    $$('.chip-review-fuel').forEach(c => {
      c.addEventListener('click', () => {
        $$('.chip-review-fuel').forEach(b => b.classList.remove('active'));
        c.classList.add('active');
        state.reviewSheet.fuel = c.dataset.fuel;
      });
    });
    $$('.star').forEach(s => {
      s.addEventListener('click', () => {
        const r = parseInt(s.dataset.rating);
        state.reviewSheet.rating = r;
        $$('.star').forEach(x => {
          const xr = parseInt(x.dataset.rating);
          x.classList.toggle('active', xr <= r);
        });
        const hints = ['', 'Ужасно', 'Плохо', 'Нормально', 'Хорошо', 'Отлично!'];
        dom.ratingHint.textContent = hints[r] || '';
        haptic('medium');
      });
    });
    dom.reviewComment.addEventListener('input', e => state.reviewSheet.comment = e.target.value);
    $('#review-submit').addEventListener('click', submitReview);

    // Sheet close
    $$('[data-action="close-sheet"]').forEach(el => {
      el.addEventListener('click', () => {
        closeSheet('report-sheet');
        closeSheet('review-sheet');
      });
    });

    // Back button in station picker goes to home
    $$('[data-action="back-to-report"]').forEach(el => {
      el.addEventListener('click', () => showScreen('home'));
    });

    // City picker
    dom.citySearch.addEventListener('input', () => renderCities(dom.citySearch.value));

    // Manual VK ID save
    const saveVkIdBtn = document.getElementById('btn-save-vk-id');
    if (saveVkIdBtn) {
      saveVkIdBtn.addEventListener('click', () => {
        const input = document.getElementById('vk-id-input');
        const val = (input?.value || '').trim().replace(/^@/, '');
        if (!val) {
          showToast('Введи VK username или ID', 'error');
          return;
        }
        const numId = parseInt(val);
        if (!isNaN(numId)) {
          state.vkUserId = numId;
        } else {
          state.vkUserId = val;
        }
        platform.vk = true;
        try { localStorage.setItem('benzin_vk_user_id', val); } catch (e) {}
        showToast('VK ID сохранён!', 'success');
        const manual = document.getElementById('vk-id-manual');
        if (manual) manual.style.display = 'none';
        loadProfile();
      });
    }

    // Profile actions
    $('#btn-share').addEventListener('click', () => {
      haptic('light');
      const url = 'https://t.me/benzyn_ryadom';
      if (tg?.openTelegramLink) tg.openTelegramLink(url);
      else if (navigator.share) navigator.share({ title: 'Бензин рядом', url });
      else {
        navigator.clipboard?.writeText(url);
        showToast('Ссылка скопирована', 'success');
      }
    });
    $('#btn-donate').addEventListener('click', () => {
      haptic('light');
      if (tg?.openTelegramLink) tg.openTelegramLink('https://t.me/benzyn_ryadom?start=donate');
      else showToast('Перейди в бота: t.me/benzyn_ryadom', 'info');
    });
    $('#btn-help').addEventListener('click', () => {
      showToast('Бот: @benzyn_ryadom\nVK: vk.com/benzyn_ryadom', 'info');
    });
    const routeFuelBtn = $('#route-fuel-submit');
    if (routeFuelBtn) {
      routeFuelBtn.addEventListener('click', () => {
        haptic('medium');
        findRouteFuel();
      });
    }

    // === Anti-Traffic ===
    const antiTrafficBtn = $('#anti-traffic-submit');
    if (antiTrafficBtn) {
      antiTrafficBtn.addEventListener('click', async () => {
        haptic('medium');
        const fromVal = ($('#anti-traffic-from')?.value || '').trim();
        const toVal = ($('#anti-traffic-to')?.value || '').trim();
        const resultsEl = $('#anti-traffic-results');
        if (!fromVal || !toVal) {
          if (resultsEl) resultsEl.innerHTML = '<p style="color:#ef4444">Введи обе точки</p>';
          return;
        }
        const uid = getTgId();
        if (!uid) {
          if (resultsEl) resultsEl.innerHTML = '<p style="color:#ef4444">ID не определён</p>';
          return;
        }
        // Resolve coordinates via search API
        async function resolveCoords(q) {
          const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
          const results = r?.results || [];
          if (results.length > 0) return { lat: results[0].lat, lon: results[0].lon };
          const parts = q.replace(/\s/g, '').split(',');
          if (parts.length === 2) { const la = parseFloat(parts[0]), lo = parseFloat(parts[1]); if (!isNaN(la) && !isNaN(lo)) return { lat: la, lon: lo }; }
          return null;
        }
        try {
          if (resultsEl) resultsEl.innerHTML = '<p style="color:var(--text-secondary)">Загрузка...</p>';
          const from = await resolveCoords(fromVal);
          const to = await resolveCoords(toVal);
          if (!from || !to) {
            if (resultsEl) resultsEl.innerHTML = '<p style="color:#ef4444">Не удалось распознать координаты</p>';
            return;
          }
          const idParam = platform.vk ? `vk_user_id=${uid}` : `telegram_id=${uid}`;
          const res = await api(`/api/route/anti-traffic?from_lat=${from.lat}&from_lon=${from.lon}&to_lat=${to.lat}&to_lon=${to.lon}&fuel=95&${idParam}`);
          if (res.error) {
            if (res.error === 'elite_required') {
              if (resultsEl) resultsEl.innerHTML = '<div style="text-align:center;padding:20px"><p>🚗 Анти-пробка — Elite-фича</p><button class="btn btn-primary" onclick="setTab(\'profile\')">💎 Premium</button></div>';
            } else {
              if (resultsEl) resultsEl.innerHTML = `<p style="color:#ef4444">${res.message || res.error}</p>`;
            }
            return;
          }
          const t = res.traffic || {};
          const emoji = { low: '🟢', medium: '🟡', high: '🔴' }[t.level] || '⚪';
          let html = `<div class="route-fuel-result" style="padding:12px">`;
          html += `<div style="font-size:15px;font-weight:600;margin-bottom:8px">🚗 Анти-пробка</div>`;
          html += `<div>📏 <b>${res.total_distance_km} км</b></div>`;
          html += `<div>${emoji} Пробки: <b>${t.description || ''}</b></div>`;
          html += `<div>⏱ Время: <b>${t.eta_minutes || 0} мин</b>`;
          if (t.delay_minutes > 0) html += ` (+${t.delay_minutes} мин)`;
          html += `</div>`;
          if (t.eta_without_traffic) html += `<div>🏎 Без пробок: ${t.eta_without_traffic} мин</div>`;
          if (res.best_time) html += `<div style="margin-top:8px;color:#fbbf24">💡 ${res.best_time}</div>`;
          if (res.stop_points && res.stop_points.length > 0) {
            html += `<div style="margin-top:8px"><b>⛽ Заправки:</b></div>`;
            res.stop_points.slice(0, 5).forEach(sp => {
              html += `<div style="font-size:13px;color:var(--text-secondary)">• ${sp.km_from_start} км — ${sp.suggestion}</div>`;
            });
          }
          html += `</div>`;
          if (resultsEl) resultsEl.innerHTML = html;
        } catch (e) {
          if (resultsEl) resultsEl.innerHTML = `<p style="color:#ef4444">Ошибка: ${e.message}</p>`;
        }
      });
    }
    // Кнопки "Выбрать на карте" для A и B
    $$('.route-fuel-pick-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        haptic('light');
        const target = btn.dataset.pick;
        // Запоминаем текущее значение в input как дефолт
        const inputId = target === 'from' ? '#route-fuel-from' : '#route-fuel-to';
        const currentVal = $(inputId)?.value || '';
        const searchInput = $('#map-picker-search-input');
        if (searchInput && currentVal) searchInput.value = currentVal;
        openMapPicker(target, (coords) => onMapPicked(target, coords));
      });
    });
    const exportBtn = $('#btn-export');
    if (exportBtn) {
      exportBtn.addEventListener('click', async () => {
        const uid = getTgId();
        if (!uid) {
          showToast('Не удалось определить ID', 'error');
          return;
        }
        // Проверяем premium статус
        const status = window.PremiumUI ? window.PremiumUI.getStatus() : { active: false };
        if (!status.active) {
          showUpsell({ feature: 'export_csv' });
          return;
        }
        // Скачиваем CSV
        try {
          showLoading();
          const csvIdParam = platform.vk ? 'vk_user_id' : 'telegram_id';
          const res = await fetch(`${API}/api/export/csv?${csvIdParam}=${uid}&type=reports&days=30`, {
            headers: tg?.initData ? { 'X-Telegram-Init-Data': tg.initData } : {},
          });
          if (!res.ok) throw new Error('Ошибка скачивания');
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `benzin_reports_${new Date().toISOString().slice(0,10)}.csv`;
          a.click();
          URL.revokeObjectURL(url);
          showToast('CSV скачан ✅', 'success');
        } catch (e) {
          showToast('Ошибка: ' + e.message, 'error');
        } finally {
          hideLoading();
        }
      });
    }
    // Load pending confirmations
    loadPendingConfirmations();

    // === Link VK from Mini App ===
    const linkVkBtn = $('#btn-link-vk');
    if (linkVkBtn) {
      linkVkBtn.addEventListener('click', async () => {
        const input = $('#link-vk-username');
        const status = $('#link-initiate-status');
        const target = (input?.value || '').trim().replace(/^@/, '');
        if (!target) {
          if (status) { status.textContent = '❌ Введи VK username или ID'; status.style.color = '#ef4444'; }
          return;
        }
        const uid = getTgId();
        if (!uid) {
          const hint = 'Открой Mini App через кнопку "📱 Открыть приложение" в VK боте.';
          if (status) { status.textContent = '❌ ID не определён. ' + hint; status.style.color = '#ef4444'; }
          return;
        }
        const body = platform.vk
          ? { vk_user_id: uid, profile_url: target }
          : { telegram_id: uid, profile_url: target };
        try {
          const res = await api('/api/account/link-by-profile', {
            method: 'POST',
            body: JSON.stringify(body),
          });
          if (res.ok) {
            if (status) { status.textContent = '✅ ' + (res.message || 'Аккаунты привязаны!'); status.style.color = '#34d399'; }
            showToast('Аккаунты привязаны!', 'success');
            input.value = '';
            loadProfile();
          } else {
            if (status) { status.textContent = '❌ ' + (res.error || 'Ошибка'); status.style.color = '#ef4444'; }
          }
        } catch (e) {
          if (status) { status.textContent = '❌ ' + (e.message || 'Ошибка соединения'); status.style.color = '#ef4444'; }
        }
      });
    }

    // === Link TG from Mini App ===
    const linkTgBtn2 = $('#btn-link-tg');
    if (linkTgBtn2) {
      linkTgBtn2.addEventListener('click', async () => {
        const input = $('#link-tg-username');
        const status = $('#link-initiate-status');
        const target = (input?.value || '').trim().replace(/^@/, '');
        if (!target) {
          if (status) { status.textContent = '❌ Введи TG username или ID'; status.style.color = '#ef4444'; }
          return;
        }
        const uid = getTgId();
        if (!uid) {
          const hint = 'Открой Mini App через кнопку "📱 Открыть приложение" в VK боте.';
          if (status) { status.textContent = '❌ ID не определён. ' + hint; status.style.color = '#ef4444'; }
          return;
        }
        const body = platform.vk
          ? { vk_user_id: uid, profile_url: target }
          : { telegram_id: uid, profile_url: target };
        try {
          const res = await api('/api/account/link-by-profile', {
            method: 'POST',
            body: JSON.stringify(body),
          });
          if (res.ok) {
            if (status) { status.textContent = '✅ ' + (res.message || 'Аккаунты привязаны!'); status.style.color = '#34d399'; }
            showToast('Аккаунты привязаны!', 'success');
            input.value = '';
            loadProfile();
          } else {
            if (status) { status.textContent = '❌ ' + (res.error || 'Ошибка'); status.style.color = '#ef4444'; }
          }
        } catch (e) {
          if (status) { status.textContent = '❌ ' + (e.message || 'Ошибка соединения'); status.style.color = '#ef4444'; }
        }
      });
    }

    // === Unlink ===
    const unlinkBtn = $('#btn-unlink');
    if (unlinkBtn) {
      unlinkBtn.addEventListener('click', async () => {
        if (!confirm('Отвязать аккаунт? Premium перестанет работать на другом аккаунте.')) return;
        haptic('medium');
        const uid = getTgId();
        if (!uid) return;
        const body = platform.vk
          ? { vk_user_id: uid }
          : { telegram_id: uid };
        try {
          const res = await api('/api/account/unlink', {
            method: 'POST',
            body: JSON.stringify(body),
          });
          if (res.ok) {
            showToast('Аккаунт отвязан', 'info');
            await loadProfile();
          } else {
            showToast('Ошибка: ' + (res.error || 'неизвестно'), 'error');
          }
        } catch (e) {
          showToast('Ошибка соединения', 'error');
        }
      });
    }

    // Pending confirmations
    async function loadPendingConfirmations() {
      const uid = getTgId();
      if (!uid) return;
      const container = document.getElementById('pending-confirmations');
      if (!container) return;
      const idParam = platform.vk ? `vk_user_id=${uid}` : `telegram_id=${uid}`;
      try {
        const res = await api(`/api/account/link/pending?${idParam}`);
        if (!res.ok || !res.confirmations || !res.confirmations.length) {
          container.innerHTML = '';
          return;
        }
        container.innerHTML = res.confirmations.map(c => `
          <div style="background:var(--bg-elev);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:12px;margin-bottom:8px;">
            <div style="font-size:14px;font-weight:600;margin-bottom:6px;">🔗 Запрос на привязку</div>
            <div style="font-size:13px;color:var(--text-secondary);margin-bottom:10px;">
              VK ID: ${c.from_vk_id || '?'} хочет привязать аккаунт
            </div>
            <div style="display:flex;gap:8px;">
              <button class="btn btn-primary" onclick="confirmLink(${c.id})" style="flex:1;padding:8px;font-size:13px;border-radius:8px;">✅ Подтвердить</button>
              <button class="btn btn-outline" onclick="rejectLink(${c.id})" style="flex:1;padding:8px;font-size:13px;border-radius:8px;color:#ef4444;border-color:#ef4444;">❌ Отклонить</button>
            </div>
          </div>
        `).join('');
      } catch (e) {
        container.innerHTML = '';
      }
    }

    window.confirmLink = async function(confirmId) {
      haptic('medium');
      try {
        const res = await api('/api/account/link/confirm', {
          method: 'POST',
          body: JSON.stringify({ confirmation_id: confirmId }),
        });
        if (res.ok) {
          showToast('✅ Аккаунт привязан!', 'success');
          loadPendingConfirmations();
          loadProfile();
        } else {
          showToast('Ошибка: ' + (res.error || 'неизвестно'), 'error');
        }
      } catch (e) {
        showToast('Ошибка соединения', 'error');
      }
    };

    window.rejectLink = async function(confirmId) {
      haptic('light');
      try {
        await api('/api/account/link/reject', {
          method: 'POST',
          body: JSON.stringify({ confirmation_id: confirmId }),
        });
        showToast('Привязка отклонена', 'info');
        loadPendingConfirmations();
      } catch (e) {
        showToast('Ошибка соединения', 'error');
      }
    };

    $('#btn-premium').addEventListener('click', () => {
      haptic('medium');
      const tiers = document.getElementById('premium-tiers');
      if (tiers) {
        tiers.style.display = tiers.style.display === 'none' ? 'flex' : 'none';
      }
    });
    // Trial button в профиле
    const trialBtn = document.getElementById('btn-trial-profile');
    if (trialBtn) {
      trialBtn.addEventListener('click', () => {
        haptic('heavy');
        if (window.PremiumUI && window.PremiumUI.activateTrial) {
          window.PremiumUI.activateTrial();
        }
      });
    }

    // Premium tier buttons
    document.querySelectorAll('.btn-tier').forEach(btn => {
      btn.addEventListener('click', async () => {
        const tier = btn.dataset.tier;
        haptic('heavy');
        await buyPremiumTier(tier);
      });
    });
  }

  async function buyPremiumTier(tier) {
    try {
      const uid = getTgId();
      if (!uid) {
        showToast('Не удалось определить ID пользователя', 'error');
        return;
      }
      // Проверяем/получаем согласие на юридические документы
      const ok = await ensureConsent(['terms', 'privacy', 'consent', 'offer']);
      if (!ok) return;

      const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
      const res = await api('/api/premium/create-payment', {
        method: 'POST',
        body: JSON.stringify({ [idParam]: uid, tier: tier }),
      });
      if (res.ok && res.payment_url) {
        if (platform.vk && window.vkBridge) {
          vkBridge.send('VKWebAppOpenLink', { url: res.payment_url });
        } else if (window.Telegram && Telegram.WebApp) {
          Telegram.WebApp.openLink(res.payment_url);
        } else {
          window.open(res.payment_url, '_blank');
        }
        showToast('Перейдите по ссылке для оплаты', 'info');
      } else {
        showToast('Ошибка: ' + (res.error || 'YooMoney не настроен'), 'error');
      }
    } catch (e) {
      console.error('buyPremiumTier error:', e);
      showToast('Ошибка: ' + (e.message || 'соединения'), 'error');
    }
  }

  // === Модалка согласия с юридическими документами ===
  async function ensureConsent(requiredDocs) {
    const uid = getTgId();
    if (!uid) return false;
    const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
    // Проверяем есть ли уже согласие
    try {
      const check = await api(`/api/consent/check?${idParam}=${uid}&documents=${requiredDocs.join(',')}`);
      if (check && check.has_consent) return true;
    } catch (e) {
      // Продолжаем — покажем модалку
    }
    // Показываем модалку
    return new Promise((resolve) => {
      const docNames = {
        terms: 'Пользовательское соглашение',
        privacy: 'Политика конфиденциальности',
        consent: 'Согласие на обработку ПДн',
        offer: 'Публичная оферта',
        referral: 'Правила реферальной программы',
      };
      const docsHtml = requiredDocs.map(d =>
        `<div style="margin:6px 0;font-size:13px">
          <a href="/legal/${d}.html" target="_blank" style="color:#f59e0b;text-decoration:underline">${docNames[d] || d}</a>
        </div>`
      ).join('');
      const overlay = document.createElement('div');
      overlay.id = 'consent-modal-overlay';
      overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:99999;display:flex;align-items:center;justify-content:center;padding:20px';
      overlay.innerHTML = `
        <div style="background:var(--bg-card,#1f2937);border-radius:14px;padding:24px;max-width:380px;width:100%;color:#fff">
          <h3 style="margin:0 0 12px;font-size:17px">📄 Согласие с документами</h3>
          <p style="font-size:13px;color:#aaa;margin:0 0 14px">Перед продолжением необходимо принять:</p>
          <div style="margin-bottom:16px">${docsHtml}</div>
          <label style="display:flex;gap:8px;align-items:flex-start;font-size:13px;margin-bottom:14px;cursor:pointer">
            <input type="checkbox" id="consent-cb" style="margin-top:3px">
            <span>Я ознакомлен(-а) и согласен(-на) со всеми документами выше</span>
          </label>
          <div style="display:flex;gap:8px">
            <button id="consent-cancel" style="flex:1;padding:10px;background:#374151;color:#fff;border:none;border-radius:8px;cursor:pointer">Отмена</button>
            <button id="consent-ok" style="flex:1;padding:10px;background:#f59e0b;color:#fff;border:none;border-radius:8px;cursor:pointer" disabled>Принять</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      const cb = document.getElementById('consent-cb');
      const ok = document.getElementById('consent-ok');
      const cancel = document.getElementById('consent-cancel');
      cb.addEventListener('change', () => { ok.disabled = !cb.checked; });
      cancel.addEventListener('click', () => { overlay.remove(); resolve(false); });
      ok.addEventListener('click', async () => {
        try {
          await api('/api/consent', {
            method: 'POST',
            body: JSON.stringify({
              [idParam]: uid,
              documents: requiredDocs,
              version: '2026-07-21',
            }),
          });
          overlay.remove();
          resolve(true);
        } catch (e) {
          showToast('Ошибка записи согласия', 'error');
        }
      });
    });
  }

  // ============= GLOBAL EXPORTS (for premium-ui.js and other scripts) =============
  window.getTgId = getTgId;
  window.api = api;
  window.showToast = showToast;
  window.showLoading = showLoading;
  window.hideLoading = hideLoading;
  window.loadProfile = loadProfile;

  // Debug: log ID detection state on boot
  console.log('getTgId debug:', {
    tg_user: tg?.initDataUnsafe?.user?.id || null,
    platform_vk: platform.vk,
    state_vkUserId: state.vkUserId,
    url: window.location.href,
    ls_vk: localStorage.getItem('benzin_vk_user_id'),
  });

  // ============= INIT =============
  // === Обязательное принятие юридических документов перед использованием ===
  const LEGAL_DOCS_VERSION = '2026-07-21';
  const REQUIRED_LEGAL = [
    { id: 'terms', name: 'Пользовательское соглашение' },
    { id: 'privacy', name: 'Политика конфиденциальности' },
    { id: 'consent', name: 'Согласие на обработку ПДн' },
    { id: 'disclaimer', name: 'Дисклеймер (disclaimer)' },
  ];

  async function requireLegalAcceptance() {
    const uid = getTgId();
    if (!uid) {
      // Без ID вообще ничего не показываем
      showLegalBlocked('Не удалось определить пользователя');
      return false;
    }
    const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
    try {
      const status = await api(`/api/user/legal-status?${idParam}=${uid}`);
      if (status && status.legal_accepted && status.version === LEGAL_DOCS_VERSION) {
        return true;
      }
    } catch (e) {
      // Если API не отвечает — не блокируем, но покажем
    }
    // Показываем блокирующую модалку
    return showLegalModal(uid);
  }

  function showLegalBlocked(reason) {
    document.body.innerHTML = `
      <div style="position:fixed;inset:0;background:#0f172a;color:#fff;display:flex;align-items:center;justify-content:center;padding:20px;text-align:center">
        <div>
          <h1 style="font-size:24px;margin-bottom:16px">⛔ Сервис недоступен</h1>
          <p style="color:#94a3b8;margin-bottom:24px">${reason}</p>
          <button onclick="location.reload()" style="padding:12px 24px;background:#f59e0b;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:15px">Повторить</button>
        </div>
      </div>
    `;
  }

  async function showLegalModal(uid) {
    return new Promise((resolve) => {
      const idParam = platform.vk ? 'vk_user_id' : 'telegram_id';
      const docsHtml = REQUIRED_LEGAL.map(d => `
        <label style="display:flex;gap:10px;align-items:flex-start;padding:10px;background:rgba(255,255,255,0.05);border-radius:8px;margin-bottom:8px;cursor:pointer">
          <input type="checkbox" class="legal-cb" data-doc="${d.id}" style="margin-top:3px;width:18px;height:18px;flex-shrink:0">
          <div style="flex:1">
            <div style="font-size:14px;font-weight:500">${d.name}</div>
            <a href="/legal/${d.id}.html" target="_blank" style="color:#fbbf24;font-size:12px;text-decoration:underline">Открыть документ</a>
          </div>
        </label>
      `).join('');

      const overlay = document.createElement('div');
      overlay.id = 'legal-block-overlay';
      overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.95);z-index:999999;display:flex;align-items:center;justify-content:center;padding:16px;overflow-y:auto';
      overlay.innerHTML = `
        <div style="background:#1e293b;border-radius:16px;padding:24px;max-width:420px;width:100%;color:#fff;max-height:90vh;overflow-y:auto">
          <div style="text-align:center;margin-bottom:18px">
            <div style="font-size:36px;margin-bottom:8px">📄</div>
            <h2 style="font-size:19px;margin:0 0 6px">Добро пожаловать в «Бензин рядом»</h2>
            <p style="font-size:13px;color:#94a3b8;margin:0">Чтобы пользоваться сервисом, примите документы:</p>
          </div>
          <div style="margin-bottom:16px">${docsHtml}</div>
          <label style="display:flex;gap:8px;align-items:flex-start;padding:12px;background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);border-radius:8px;margin-bottom:14px;cursor:pointer">
            <input type="checkbox" id="legal-master-cb" style="margin-top:3px;width:18px;height:18px;flex-shrink:0">
            <span style="font-size:13px;line-height:1.4">Я ознакомлен(-а) и согласен(-на) со всеми документами. Подтверждаю, что мне исполнилось 18 лет.</span>
          </label>
          <button id="legal-ok" disabled style="width:100%;padding:14px;background:#f59e0b;color:#fff;border:none;border-radius:10px;cursor:pointer;font-size:15px;font-weight:600;opacity:0.5">Принять и продолжить</button>
          <p style="font-size:11px;color:#64748b;text-align:center;margin:10px 0 0">Без согласия с документами сервис недоступен.<br>Документы: ${LEGAL_DOCS_VERSION}</p>
        </div>
      `;
      document.body.appendChild(overlay);

      const masterCb = document.getElementById('legal-master-cb');
      const docCbs = overlay.querySelectorAll('.legal-cb');
      const okBtn = document.getElementById('legal-ok');

      function checkAll() {
        const allChecked = Array.from(docCbs).every(cb => cb.checked);
        masterCb.checked = allChecked;
        okBtn.disabled = !(allChecked && masterCb.checked);
        okBtn.style.opacity = okBtn.disabled ? '0.5' : '1';
      }

      masterCb.addEventListener('change', () => {
        docCbs.forEach(cb => { cb.checked = masterCb.checked; });
        checkAll();
      });
      docCbs.forEach(cb => cb.addEventListener('change', checkAll));

      okBtn.addEventListener('click', async () => {
        try {
          const resp = await api('/api/user/accept-legal', {
            method: 'POST',
            body: JSON.stringify({
              [idParam]: uid,
              version: LEGAL_DOCS_VERSION,
            }),
          });
          if (resp && resp.ok) {
            overlay.remove();
            resolve(true);
          } else {
            showToast('Ошибка: ' + (resp?.error || 'неизвестно'), 'error');
          }
        } catch (e) {
          showToast('Ошибка соединения: ' + e.message, 'error');
        }
      });
    });
  }

  async function init() {
    // === ОБЯЗАТЕЛЬНОЕ согласие с юридическими документами ===
    // Блокирует весь сервис до принятия
    const legalOk = await requireLegalAcceptance();
    if (!legalOk) {
      return;
    }

    bindEvents();

    // === Загружаем Premium статус и обновляем UI ===
    try {
      await window.PremiumUI.loadStatus();
      // Hero CTA на главном экране
      const heroEl = document.getElementById('hero-premium-cta');
      if (heroEl) heroEl.innerHTML = window.PremiumUI.renderHeroCTA();
    } catch (e) {
      console.error('PremiumUI init:', e);
    }

    // === Welcome screen (первый запуск) ===
    try {
      if (!localStorage.getItem('benzin_welcomed')) {
        setTimeout(() => {
          const overlay = document.getElementById('welcome-overlay');
          if (overlay) overlay.style.display = 'flex';
        }, 800);
      }
    } catch (e) {}

    // === Offline map service worker (Premium Economy) ===
    try {
      if ('serviceWorker' in navigator) {
        const premStatus = window.PremiumUI?.getStatus();
        if (premStatus?.active) {
          navigator.serviceWorker.register('/sw.js').catch(() => {});
        }
      }
    } catch (e) {}

    // Load saved city
    try {
      const savedCity = localStorage.getItem('benzin_city');
      if (savedCity) {
        state.city = savedCity;
        state.cityRegion = localStorage.getItem('benzin_region') || '';
        dom.currentCity.textContent = savedCity;
      } else {
        dom.currentCity.textContent = 'Выбери город';
      }
    } catch (e) {
      dom.currentCity.textContent = 'Выбери город';
    }

    // Try to get user location for city auto-detect
    if (!state.city) {
      // Don't ask for location automatically; wait for user action
    }

    // Wait for VK bridge if VK
    if (platform.tg || platform.vk) {
      // Already detected
    }

    // Load stations
    if (state.city) {
      loadStations();
    } else {
      // Show welcome state immediately (no API needed)
      dom.stationsList.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">⛽</div>
          <div class="empty-title">Найди ближайшую АЗС</div>
          <div class="empty-subtitle">Выбери город наверху или нажми кнопку ниже</div>
          <button class="btn btn-primary" style="margin-top:16px; max-width:200px;" data-action="pick-city">📍 Выбрать город</button>
        </div>
      `;
      dom.emptyState.hidden = true;
      dom.resultsCount.textContent = '0';
      // Bind the button
      const btn = dom.stationsList.querySelector('[data-action="pick-city"]');
      if (btn) btn.addEventListener('click', () => showCityPicker());
    }
  }

  // Boot
  // Version check — force reload if old version is cached
  const APP_VERSION = '13';
  try {
    const stored = localStorage.getItem('benzin_app_version');
    if (stored && stored !== APP_VERSION) {
      console.log('App version changed, reloading...');
      localStorage.setItem('benzin_app_version', APP_VERSION);
      // Clear caches and force reload
      if ('caches' in window) {
        caches.keys().then(keys => keys.forEach(k => caches.delete(k)));
      }
      window.location.reload(true);
    } else {
      localStorage.setItem('benzin_app_version', APP_VERSION);
    }
  } catch (e) {
    // Ignore localStorage errors
  }

  window.addEventListener('error', (e) => {
    console.error('App error:', e.error);
    if (e.error && dom && dom.toast) {
      dom.toast.textContent = 'Ошибка: ' + (e.error.message || 'unknown');
      dom.toast.className = 'toast error';
      dom.toast.hidden = false;
    }
  });

  // Welcome modal
  window.closeWelcome = function() {
    const overlay = document.getElementById('welcome-overlay');
    if (overlay) overlay.style.display = 'none';
    try { localStorage.setItem('benzin_welcomed', '1'); } catch (e) {}
  };

  window.closeMapPicker = closeMapPicker;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
