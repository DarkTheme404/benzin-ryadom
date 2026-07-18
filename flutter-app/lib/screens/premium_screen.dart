import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../services/api_service.dart';

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
                  const Text('Тарифы',
                      style: TextStyle(
                        color: AppTheme.textPrimary,
                        fontSize: 22,
                        fontWeight: FontWeight.w700,
                      )),
                  const SizedBox(height: 16),
                  ..._plans.map((plan) => _buildPlanCard(plan)),
                  const SizedBox(height: 16),
                  _buildFounderPack(),
                  const SizedBox(height: 24),
                  _buildFeatureComparison(),
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
      child: Column(
        children: [
          const Icon(Icons.workspace_premium,
              color: Colors.white, size: 48),
          const SizedBox(height: 12),
          const Text(
            'Бензин рядом Premium',
            style: TextStyle(
              color: Colors.white,
              fontSize: 22,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'История цен, прогнозы, маршруты,\nтопливные будильники и больше',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white.withValues(alpha: 0.9),
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
    final accent = name.toLowerCase() == 'standard'
        ? AppTheme.accent
        : name.toLowerCase() == 'elite'
            ? AppTheme.premium
            : AppTheme.info;

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
        border: name.toLowerCase() == 'standard'
            ? Border.all(color: AppTheme.accent, width: 2)
            : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: accent.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  name,
                  style: TextStyle(
                    color: accent,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              if (name.toLowerCase() == 'standard') ...[
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
                        fontWeight: FontWeight.w600,
                      )),
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
                      child: Text(f,
                          style: const TextStyle(
                            color: AppTheme.textSecondary,
                            fontSize: 13,
                          )),
                    ),
                  ],
                ),
              )),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: () => _purchasePlan(name.toLowerCase()),
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
          Row(
            children: [
              const Icon(Icons.star, color: Colors.white, size: 24),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  'Founder Pack',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 20,
                    fontWeight: FontWeight.w800,
                  ),
                ),
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
            '50% комиссии рефералам',
          ].map((f) => Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    const Icon(Icons.star, color: Colors.white70, size: 14),
                    const SizedBox(width: 8),
                    Text(f,
                        style: TextStyle(
                          color: Colors.white.withValues(alpha: 0.9),
                          fontSize: 14,
                        )),
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
              onPressed: _purchaseFounder,
              child: const Text('Стать основателем',
                  style: TextStyle(fontWeight: FontWeight.w700)),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFeatureComparison() {
    final features = [
      ('Поиск АЗС', true, true),
      ('Цены и наличие', true, true),
      ('Отчёты водителей', true, true),
      ('Экстренный поиск', true, true),
      ('История цен 30д', true, true),
      ('История цен 365д', false, true),
      ('Прогноз цен 7д', false, true),
      ('Маршруты A→B', false, true),
      ('Топливные будильники', false, true),
      ('Антитрафик', false, false),
      ('SOS-рассылка', false, false),
      ('CSV-экспорт', false, true),
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
            },
            children: [
              TableRow(
                children: [
                  const SizedBox(),
                  _tableHeader('Free'),
                  _tableHeader('Premium'),
                ],
              ),
              ...features.map((f) => TableRow(
                    children: [
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: Text(f.$1,
                            style: const TextStyle(
                              color: AppTheme.textSecondary,
                              fontSize: 13,
                            )),
                      ),
                      Center(
                        child: Icon(
                          f.$2
                              ? Icons.check_circle
                              : Icons.remove_circle_outline,
                          color: f.$2 ? AppTheme.success : AppTheme.muted,
                          size: 18,
                        ),
                      ),
                      Center(
                        child: Icon(
                          f.$3
                              ? Icons.check_circle
                              : Icons.remove_circle_outline,
                          color: f.$3 ? AppTheme.success : AppTheme.muted,
                          size: 18,
                        ),
                      ),
                    ],
                  )),
            ],
          ),
        ],
      ),
    );
  }

  Widget _tableHeader(String text) {
    return Padding(
      padding: const EdgeInsets.all(8),
      child: Text(text,
          textAlign: TextAlign.center,
          style: const TextStyle(
            color: AppTheme.textPrimary,
            fontSize: 13,
            fontWeight: FontWeight.w700,
          )),
    );
  }

  void _purchasePlan(String plan) {
    // TODO: integrate YooMoney payment
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Оплата через YooMoney')),
    );
  }

  void _purchaseFounder() {
    // TODO: integrate YooMoney payment
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Оплата через YooMoney')),
    );
  }
}
