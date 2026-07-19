import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../services/api_service.dart';

const Map<String, String> featureNames = {
  'price_history': '📈 График цен 30 дней',
  'export_csv': '📊 Экспорт в Excel',
  'offline_map': '🗺️ Офлайн-карта',
  'route_fuel': '🛣️ Маршрут A→B с топливом',
  'forecast_7d': '🔮 Прогноз цен на 7 дней',
  'fuel_alarm': '🔔 Топливный будильник',
  'anti_traffic': '🚗 Анти-пробка',
  'sos_elite': '🆘 SOS-режим',
};

const Map<String, String> tierNames = {
  'economy': 'Эконом',
  'standard': 'Стандарт',
  'elite': 'Элит',
  'founder': 'Founder',
};

String premiumTierName(String tier) {
  return tierNames[tier.toLowerCase()] ?? tier;
}

class PremiumScreen extends StatefulWidget {
  const PremiumScreen({super.key});

  @override
  State<PremiumScreen> createState() => _PremiumScreenState();
}

class _PremiumScreenState extends State<PremiumScreen> {
  final ApiService _api = ApiService();
  List<Map<String, dynamic>> _plans = [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    try {
      final results = await Future.wait([
        _api.getPremiumPlans(),
        _api.getFounderList(),
      ]);
      final plans = results[0];
      setState(() {
        _plans = (plans['plans'] as List? ?? [])
            .map((e) => Map<String, dynamic>.from(e))
            .toList();
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Премиум')),
      body: _isLoading
          ? const Center(
              child: CircularProgressIndicator(color: AppTheme.accent))
          : SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHero(),
                  const SizedBox(height: 24),
                  ..._plans.map((plan) => _buildPlanCard(plan)),
                  const SizedBox(height: 16),
                  _buildFounderPack(),
                  const SizedBox(height: 24),
                  _buildComparisonTable(),
                ],
              ),
            ),
    );
  }

  Widget _buildHero() {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFFff6b35), Color(0xFFf7931e)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(20),
      ),
      child: const Column(
        children: [
          Icon(Icons.workspace_premium, color: Colors.white, size: 48),
          SizedBox(height: 12),
          Text(
            'Бензин рядом Premium',
            style: TextStyle(
              color: Colors.white,
              fontSize: 22,
              fontWeight: FontWeight.w800,
            ),
          ),
          SizedBox(height: 8),
          Text(
            'История цен, прогнозы, маршруты,\nтопливные будильники и больше',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white70,
              fontSize: 14,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPlanCard(Map<String, dynamic> plan) {
    final name = plan['name']?.toString() ?? '';
    final price = plan['price'] ?? 0;
    final period = plan['period'] ?? 'month';
    final features = (plan['features'] as List? ?? [])
        .map((e) => e.toString())
        .toList();
    final periodText = period == 'forever' ? 'навсегда' : '/мес';

    final code = name.toLowerCase();
    final accent = code.contains('стандарт')
        ? AppTheme.accent
        : code.contains('элит')
            ? AppTheme.premium
            : AppTheme.info;

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
        border: code.contains('стандарт')
            ? Border.all(color: AppTheme.accent, width: 2)
            : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: accent.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  name,
                  style: TextStyle(color: accent, fontSize: 12, fontWeight: FontWeight.w700),
                ),
              ),
              if (code.contains('стандарт')) ...[
                const SizedBox(width: 8),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: AppTheme.success.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Text('Популярный',
                      style: TextStyle(
                          color: AppTheme.success,
                          fontSize: 10,
                          fontWeight: FontWeight.w600)),
                ),
              ],
              const Spacer(),
              Text(
                '$price ₽$periodText',
                style: const TextStyle(
                  color: AppTheme.textPrimary,
                  fontSize: 18,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          ...features.map((f) => Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    const Icon(Icons.check_circle,
                        color: AppTheme.success, size: 16),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        featureNames[f] ?? f,
                        style: const TextStyle(
                          color: AppTheme.textSecondary,
                          fontSize: 13,
                        ),
                      ),
                    ),
                  ],
                ),
              )),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: () {},
              child: Text('Выбрать $name'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFounderPack() {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFFfbbf24), Color(0xFFd97706)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.star, color: Colors.white, size: 24),
              SizedBox(width: 8),
              Expanded(
                child: Text('Founder Pack',
                    style: TextStyle(
                        color: Colors.white,
                        fontSize: 20,
                        fontWeight: FontWeight.w800)),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            '1 990 ₽ навсегда',
            style: TextStyle(
              color: Colors.white.withValues(alpha: 0.9),
              fontSize: 24,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 12),
          ...[
            'Elite навсегда',
            'Бейдж Founder',
            'Имя в списке основателей',
          ].map((f) => Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    const Icon(Icons.star, color: Colors.white70, size: 14),
                    const SizedBox(width: 8),
                    Text(f,
                        style: TextStyle(
                            color: Colors.white.withValues(alpha: 0.9),
                            fontSize: 14)),
                  ],
                ),
              )),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.white,
                foregroundColor: const Color(0xFFd97706),
              ),
              onPressed: () {},
              child: const Text('Стать основателем',
                  style: TextStyle(fontWeight: FontWeight.w700)),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildComparisonTable() {
    const tiers = ['Free', 'Эконом', 'Стандарт', 'Элит'];
    const tierColors = [
      AppTheme.muted,
      AppTheme.info,
      AppTheme.accent,
      AppTheme.premium,
    ];
    const features = [
      ('Поиск АЗС', true, true, true, true),
      ('Цены и наличие', true, true, true, true),
      ('Отчёты водителей', true, true, true, true),
      ('Экстренный поиск', true, true, true, true),
      ('📈 График цен 30 дней', false, true, true, true),
      ('📊 Экспорт в Excel', false, true, true, true),
      ('🗺️ Офлайн-карта', false, true, true, true),
      ('🛣️ Маршрут A→B', false, false, true, true),
      ('🔮 Прогноз цен 7 дней', false, false, true, true),
      ('🔔 Топливный будильник', false, false, true, true),
      ('🚗 Анти-пробка', false, false, false, true),
      ('🆘 SOS-режим', false, false, false, true),
    ];

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Сравнение тарифов',
              style: TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 16),
          Table(
            columnWidths: const {
              0: FlexColumnWidth(3),
              1: FlexColumnWidth(1),
              2: FlexColumnWidth(1),
              3: FlexColumnWidth(1),
              4: FlexColumnWidth(1),
            },
            children: [
              TableRow(
                children: [
                  const SizedBox(),
                  ...List.generate(4, (i) => Padding(
                        padding: const EdgeInsets.all(4),
                        child: Text(tiers[i],
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              color: tierColors[i],
                              fontSize: 11,
                              fontWeight: FontWeight.w700,
                            )),
                      )),
                ],
              ),
              ...features.map((f) => TableRow(
                    children: [
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 6),
                        child: Text(f.$1,
                            style: const TextStyle(
                              color: AppTheme.textSecondary,
                              fontSize: 12,
                            )),
                      ),
                      ...List.generate(4, (i) => Center(
                            child: Icon(
                              [f.$2, f.$3, f.$4, f.$5][i]
                                  ? Icons.check_circle
                                  : Icons.remove_circle_outline,
                              color: [f.$2, f.$3, f.$4, f.$5][i]
                                  ? AppTheme.success
                                  : AppTheme.muted,
                              size: 16,
                            ),
                          )),
                    ],
                  )),
            ],
          ),
        ],
      ),
    );
  }
}
