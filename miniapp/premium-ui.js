/**
 * Premium upsell & feature management для Mini App.
 *
 * Используется во всех местах, где Free-юзер видит Premium-фичу:
 * - showUpsell(feature) — открывает модалку с описанием конкретной фичи
 * - showUpsell(bundle) — открывает модалку с набором фич (savings/trips/business)
 * - showUpsell() — открывает модалку со всеми тарифами
 * - isFeatureLocked(feature) — проверяет доступ
 * - requireFeature(feature) — вызывает showUpsell если locked
 */
(function() {
  'use strict';

  let userPremiumStatus = null;  // { active, tier, expires_at }

  async function loadPremiumStatus() {
    const tgId = getTgId();
    if (!tgId) {
      userPremiumStatus = { active: false, tier: null };
      return userPremiumStatus;
    }
    try {
      const res = await api('/api/premium/status?telegram_id=' + tgId);
      userPremiumStatus = res || { active: false, tier: null };
    } catch (e) {
      userPremiumStatus = { active: false, tier: null };
    }
    return userPremiumStatus;
  }

  function getPremiumStatus() {
    return userPremiumStatus || { active: false, tier: null };
  }

  function isFeatureLocked(featureId) {
    const status = getPremiumStatus();
    const feature = window.PREMIUM_CATALOG[featureId];
    if (!feature) return false;
    if (!status.active) return true;  // Free — все заблокировано
    // Проверяем уровень тарифа
    const tierOrder = { economy: 1, standard: 2, elite: 3 };
    const requiredTier = feature.tier || 'economy';
    const userTier = status.tier || 'economy';
    if ((tierOrder[userTier] || 0) < (tierOrder[requiredTier] || 0)) {
      return true;  // Тариф недостаточный
    }
    return false;
  }

  function requireFeature(featureId, callback) {
    if (isFeatureLocked(featureId)) {
      showUpsell({ feature: featureId });
      return false;
    }
    if (callback) callback();
    return true;
  }

  // === Upsell Modal ===

  function showUpsell(opts) {
    opts = opts || {};
    const overlay = document.getElementById('upsell-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';

    const heroEmoji = document.getElementById('upsell-hero-emoji');
    const heroTitle = document.getElementById('upsell-hero-title');
    const heroSubtitle = document.getElementById('upsell-hero-subtitle');
    const socialEl = document.getElementById('upsell-social-proof');
    const featuresEl = document.getElementById('upsell-features');
    const tiersEl = document.getElementById('upsell-tiers');

    // === Если открыли для одной фичи ===
    if (opts.feature) {
      const f = window.PREMIUM_CATALOG[opts.feature];
      if (!f) return;
      heroEmoji.textContent = f.icon;
      heroTitle.textContent = f.name;
      heroSubtitle.textContent = f.tagline;
      socialEl.innerHTML = `
        <div class="upsell-counter">
          <span class="upsell-counter-num">${f.savings}</span>
          <span class="upsell-counter-label">экономия</span>
        </div>
      `;
      featuresEl.innerHTML = renderFeatures([opts.feature]);
      // Скроллим до нужного тарифа
      const tier = f.tier;
      tiersEl.innerHTML = renderTiers(tier);
    }
    // === Если открыли для набора (bundle) ===
    else if (opts.bundle) {
      const b = window.PREMIUM_BUNDLES[opts.bundle];
      if (!b) return;
      heroEmoji.textContent = '💎';
      heroTitle.textContent = b.title.replace(/💎|🛣|🛣️|💼|💰/g, '').trim();
      heroSubtitle.textContent = b.subtitle;
      socialEl.innerHTML = `
        <div class="upsell-counter">
          <span class="upsell-counter-num">2 400+</span>
          <span class="upsell-counter-label">водителей уже с нами</span>
        </div>
      `;
      featuresEl.innerHTML = renderFeatures(b.features);
      // Минимальный тариф из набора
      const minTier = getMinTier(b.features);
      tiersEl.innerHTML = renderTiers(minTier);
    }
    // === Полный список тарифов ===
    else {
      heroEmoji.textContent = '💎';
      heroTitle.textContent = 'Premium';
      heroSubtitle.textContent = 'Экономь на бензине каждый день';
      socialEl.innerHTML = `
        <div class="upsell-counter">
          <span class="upsell-counter-num">1 800₽</span>
          <span class="upsell-counter-label">средняя экономия в месяц</span>
        </div>
      `;
      featuresEl.innerHTML = renderFeatures(['price_history', 'forecast_7d', 'route_fuel', 'fuel_alarm']);
      tiersEl.innerHTML = renderTiers('standard');
    }
  }

  function closeUpsell() {
    const overlay = document.getElementById('upsell-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  function renderFeatures(featureIds) {
    return featureIds.map(id => {
      const f = window.PREMIUM_CATALOG[id];
      if (!f) return '';
      return `
        <div class="upsell-feature">
          <div class="upsell-feature-icon">${f.icon}</div>
          <div class="upsell-feature-body">
            <div class="upsell-feature-name">${f.name}</div>
            <div class="upsell-feature-tagline">${f.tagline}</div>
            <div class="upsell-feature-savings">💰 ${f.savings}</div>
            <div class="upsell-feature-urgency">${f.urgency}</div>
          </div>
        </div>
      `;
    }).join('');
  }

  function renderTiers(highlightTier) {
    return window.PREMIUM_TIERS_DISPLAY.map(tier => {
      const featured = tier.code === highlightTier || tier.code === 'standard';
      const badge = tier.badge ? `<div class="upsell-tier-badge">${tier.badge}</div>` : '';
      return `
        <button class="upsell-tier ${featured ? 'upsell-tier-featured' : ''}" onclick="selectTier('${tier.code}')">
          ${badge}
          <div class="upsell-tier-header">
            <div class="upsell-tier-icon">${tier.icon}</div>
            <div class="upsell-tier-name">${tier.name}</div>
            <div>
              <span class="upsell-tier-price">${tier.price}₽</span>
              <span class="upsell-tier-price-suffix">/мес</span>
            </div>
          </div>
          <div class="upsell-tier-headline">${tier.headline}</div>
          <div class="upsell-tier-pitch">${tier.pitch}</div>
        </button>
      `;
    }).join('');
  }

  function getMinTier(featureIds) {
    const ranks = { economy: 1, standard: 2, elite: 3 };
    let min = 3;
    featureIds.forEach(id => {
      const f = window.PREMIUM_CATALOG[id];
      if (f && ranks[f.tier] < min) min = ranks[f.tier];
    });
    return Object.keys(ranks).find(k => ranks[k] === min);
  }

  async function selectTier(tier) {
    haptic && haptic('heavy');
    // Закрываем модалку и вызываем покупку
    closeUpsell();
    if (typeof buyPremiumTier === 'function') {
      await buyPremiumTier(tier);
    } else {
      console.error('buyPremiumTier not defined');
    }
  }

  // === Premium Toast (короткий) ===
  function showPremiumToast(featureId) {
    const f = window.PREMIUM_CATALOG[featureId];
    if (!f) return;
    const toast = document.getElementById('premium-toast');
    const text = toast.querySelector('.premium-toast-text');
    if (text) text.textContent = `${f.icon} ${f.name} — Premium`;
    if (toast) {
      toast.style.display = 'flex';
      setTimeout(() => { toast.style.display = 'none'; }, 6000);
    }
  }

  // === PremiumBadge (показать бейдж в углу) ===
  function renderPremiumBadge() {
    const status = getPremiumStatus();
    if (!status.active || !status.tier) return '';
    const tier = status.tier;
    const labels = { economy: '💎 Эконом', standard: '💎 Стандарт', elite: '💎 Элит' };
    return `<span class="premium-badge premium-badge-${tier}">${labels[tier] || '💎 Premium'}</span>`;
  }

  // === Render Premium Block (встраиваемый) ===
  function renderPremiumBlock(opts) {
    opts = opts || {};
    const title = opts.title || '💎 Premium';
    const subtitle = opts.subtitle || 'Экономия до 3 000₽/мес';
    const features = opts.features || ['price_history', 'forecast_7d', 'route_fuel'];
    const minTier = getMinTier(features);
    const tier = window.PREMIUM_TIERS_DISPLAY.find(t => t.code === minTier) || window.PREMIUM_TIERS_DISPLAY[0];
    const html = `
      <div class="upsell-feature" style="cursor:pointer;" onclick="showUpsell({bundle:'${opts.bundle || 'savings'}'})">
        <div class="upsell-feature-icon">💎</div>
        <div class="upsell-feature-body">
          <div class="upsell-feature-name">${title}</div>
          <div class="upsell-feature-tagline">${subtitle}</div>
          <div class="upsell-feature-savings">от ${tier.price}₽/мес</div>
        </div>
      </div>
    `;
    return html;
  }

  // === Render Locked Feature Card ===
  function renderLockedCard(featureId) {
    const f = window.PREMIUM_CATALOG[featureId];
    if (!f) return '';
    return `
      <div class="feature-card feature-card-locked" onclick="showUpsell({feature:'${featureId}'})">
        <div class="feature-card-header">
          <div class="feature-card-icon">${f.icon}</div>
          <div class="feature-card-title">${f.name}</div>
          <div class="feature-card-save">💎 ${f.savings}</div>
        </div>
        <div class="feature-card-tagline">${f.tagline}</div>
        <div class="feature-card-urgency">🔒 ${f.urgency}</div>
      </div>
    `;
  }

  function renderUnlockedCard(featureId) {
    const f = window.PREMIUM_CATALOG[featureId];
    if (!f) return '';
    return `
      <div class="feature-card">
        <div class="feature-card-header">
          <div class="feature-card-icon">${f.icon}</div>
          <div class="feature-card-title">${f.name}</div>
          <div class="feature-card-save" style="color: #34d399; background: rgba(52,211,153,0.15);">✅ Активно</div>
        </div>
        <div class="feature-card-tagline">${f.tagline}</div>
      </div>
    `;
  }

  // === Hero CTA (на главном экране) ===
  function renderHeroCTA() {
    const status = getPremiumStatus();
    if (status.active) {
      // Premium активен — показываем streak/savings виджет
      return '';  // Можно добавить streak widget отдельно
    }
    return `
      <div class="hero-premium-cta" onclick="showUpsell()">
        <div class="hero-premium-cta-icon">💎</div>
        <div class="hero-premium-cta-body">
          <div class="hero-premium-cta-title">Premium: экономь до 3 000₽/мес</div>
          <div class="hero-premium-cta-subtitle">Графики цен, маршруты, прогнозы</div>
        </div>
        <div class="hero-premium-cta-arrow">→</div>
      </div>
    `;
  }

  // Экспортируем в глобальную область
  window.PremiumUI = {
    loadStatus: loadPremiumStatus,
    getStatus: getPremiumStatus,
    isFeatureLocked: isFeatureLocked,
    requireFeature: requireFeature,
    showUpsell: showUpsell,
    closeUpsell: closeUpsell,
    showToast: showPremiumToast,
    renderBadge: renderPremiumBadge,
    renderBlock: renderPremiumBlock,
    renderLockedCard: renderLockedCard,
    renderUnlockedCard: renderUnlockedCard,
    renderHeroCTA: renderHeroCTA,
  };
})();
