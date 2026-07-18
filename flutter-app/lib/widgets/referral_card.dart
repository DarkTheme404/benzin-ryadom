import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../config/theme.dart';
import '../services/api_service.dart';

class ReferralCard extends StatefulWidget {
  final int? userId;

  const ReferralCard({super.key, this.userId});

  @override
  State<ReferralCard> createState() => _ReferralCardState();
}

class _ReferralCardState extends State<ReferralCard> {
  final ApiService _api = ApiService();
  String? _referralCode;
  int _referredCount = 0;
  double _earnings = 0;

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    try {
      final results = await Future.wait([
        _api.getReferralCode(),
        _api.getReferralStats(),
      ]);
      setState(() {
        _referralCode = results[0]['code']?.toString();
        _referredCount = results[0]['referred_count'] ?? 0;
        _earnings = (results[1]['total_earnings'] ?? 0).toDouble();
      });
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.people_outline, color: AppTheme.accent, size: 20),
              const SizedBox(width: 8),
              const Text('Реферальная программа',
                  style: TextStyle(
                    color: AppTheme.textPrimary,
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                  )),
            ],
          ),
          const SizedBox(height: 12),
          const Text(
            'Приглашай друзей — получай 50% комиссии с их оплат.',
            style: TextStyle(color: AppTheme.muted, fontSize: 13),
          ),
          if (_referralCode != null) ...[
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppTheme.bgCardLight,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      't.me/benzyn_ryadom_bot?start=ref_$_referralCode',
                      style: const TextStyle(
                        color: AppTheme.info,
                        fontSize: 12,
                        fontFamily: 'monospace',
                      ),
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.copy, size: 18, color: AppTheme.accent),
                    onPressed: _copyLink,
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 12),
          Row(
            children: [
              _buildStat('Приглашено', '$_referredCount'),
              const SizedBox(width: 24),
              _buildStat('Заработано', '${_earnings.round()} ₽'),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildStat(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: const TextStyle(color: AppTheme.muted, fontSize: 12)),
        const SizedBox(height: 4),
        Text(value,
            style: const TextStyle(
              color: AppTheme.textPrimary,
              fontSize: 16,
              fontWeight: FontWeight.w700,
            )),
      ],
    );
  }

  void _copyLink() {
    if (_referralCode != null) {
      final link = 'https://t.me/benzyn_ryadom_bot?start=ref_$_referralCode';
      Clipboard.setData(ClipboardData(text: link));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Ссылка скопирована')),
      );
    }
  }
}
